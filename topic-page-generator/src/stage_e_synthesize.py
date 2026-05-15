from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from pathlib import Path

from pydantic import BaseModel, Field, create_model
from dotenv import load_dotenv

from .llm_client import LLMClient, SONNET
from .schemas import (
    AtAGlance, BackgroundModule, BackgroundParagraph, CitedText,
    Hero, KeyEntity, LiveStatusModule, Modules,
    PageMeta, PageSchema, PageSource, Participant, ParticipantsModule,
    Plan, ReceptionItem, ReceptionModule, ReconciledFact, ReconciledOutput,
    ScheduleEntry, ScheduleModule, SourcesOutput, TimelineEntry,
    VersionComparisonModule, VersionComparisonRow,
)

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

_MAX_TOKENS = 8192
_MAX_FACTS_IN_PROMPT = 300


# ---------------------------------------------------------------------------
# Code-enforced module activation (LLM/code boundary per DESIGN.md §2.3)
# ---------------------------------------------------------------------------

# Which modules are eligible per event type (all others → active=false, not shown to LLM)
MODULE_ACTIVATION: dict[str, list[str]] = {
    "product_launch":  ["version_comparison", "reception", "background"],
    "live_event":      ["schedule", "participants", "live_status"],
    "scheduled_event": ["schedule", "participants", "background"],
    "sports_event":    ["schedule", "participants", "background"],
    "cultural_event":  ["schedule", "participants", "reception"],
    "political_event": ["reception", "background"],
    "natural_disaster":["live_status", "background"],
    "unclassifiable":  ["background"],
}

_MODULE_TYPE_MAP: dict[str, type[BaseModel]] = {
    "version_comparison": VersionComparisonModule,
    "reception":          ReceptionModule,
    "schedule":           ScheduleModule,
    "participants":       ParticipantsModule,
    "live_status":        LiveStatusModule,
    "background":         BackgroundModule,
}

_MODULE_DESCRIPTIONS: dict[str, str] = {
    "version_comparison": (
        "Version comparison table. active must be true. "
        "Populate rows with attribute, previous_value (or null), new_value, source_ids."
    ),
    "reception": (
        "Reception quotes and reactions. active must be true. "
        "Populate positive and/or critical lists from reaction/quote facts."
    ),
    "schedule": (
        "Schedule entries. active must be true. "
        "Populate entries from schedule_item facts. Include date, title, and source_ids."
    ),
    "participants": (
        "Key participants. active must be true. "
        "Populate from participant facts: name, role, optional description, source_ids."
    ),
    "live_status": (
        "Live status panel. active must be true. "
        "Set status to a short string (e.g. 'Live now', 'Ongoing', 'Concluded'). "
        "Set last_update to the most recent fact date (ISO or human-readable)."
    ),
    "background": (
        "Background context. active must be true. "
        "Write 1–3 cohesive paragraphs using historical_context and context facts."
    ),
}

_INACTIVE: dict[str, BaseModel] = {
    "version_comparison": VersionComparisonModule(active=False),
    "reception":          ReceptionModule(active=False),
    "schedule":           ScheduleModule(active=False),
    "participants":       ParticipantsModule(active=False),
    "live_status":        LiveStatusModule(active=False),
    "background":         BackgroundModule(active=False),
}

_ALL_MODULE_NAMES = list(_MODULE_TYPE_MAP.keys())


def _active_modules_for(plan: Plan) -> list[str]:
    """Determine which modules to activate, enforcing temporal_status constraints."""
    active = list(MODULE_ACTIVATION.get(plan.event_type, ["background"]))
    # live_status only makes sense for currently_unfolding events, except for live_event
    # (where it's always present but may say "Concluded")
    if (
        "live_status" in active
        and plan.event_type != "live_event"
        and plan.temporal_status not in ("currently_unfolding", "imminent")
    ):
        active.remove("live_status")
    return active


def _make_synthesis_schema(active_module_names: list[str]) -> type[BaseModel]:
    """Build a dynamic _SynthesisOutput model whose modules field only covers active modules.

    Unique model names per module set prevent LLM cache collisions across event types.
    """
    module_fields: dict[str, tuple] = {
        name: (_MODULE_TYPE_MAP[name], Field(description=_MODULE_DESCRIPTIONS[name]))
        for name in active_module_names
        if name in _MODULE_TYPE_MAP
    }
    suffix = "_".join(sorted(active_module_names))
    ActiveModulesModel = create_model(f"_Modules_{suffix}", **module_fields)

    return create_model(
        f"_SynthesisOutput_{suffix}",
        hero=(
            Hero,
            Field(description=(
                "Publishable headline (no quotes/trailing punctuation), "
                "2–3 sentence summary covering who/what/when/where/why, "
                "human-readable key_date (e.g. 'May 12–16, 2026'), "
                "source_ids from facts used."
            )),
        ),
        at_a_glance=(
            AtAGlance,
            Field(description=(
                "5W snapshot. Each of who/what/when/where/why is CitedText: "
                "text (1–2 sentences) and source_ids from the facts used. "
                "Draw from core_5w facts."
            )),
        ),
        timeline=(
            list[TimelineEntry],
            Field(description=(
                "Chronological milestones of THIS specific event's own narrative arc: "
                "lead-up, the event itself, and direct consequences only. "
                "NEVER include general historical background, prior unrelated incidents, "
                "or venue/organization history — those belong in the background module. "
                "Facts with category=historical_context must NOT appear here. "
                "Each entry: date string, short title, 1–2 sentence description, "
                "source_ids, and credibility. Include only entries with known dates, "
                "ordered ascending."
            )),
        ),
        key_entities=(
            list[KeyEntity],
            Field(description=(
                "3–8 key people, organizations, products, and locations central to "
                "this event. 1–2 sentence description using facts. "
                "source_ids preferred."
            )),
        ),
        modules=(
            ActiveModulesModel,
            Field(description=(
                "ONLY the modules listed in this schema are eligible for this event type. "
                "ALL included module fields must have active=true and populated content. "
                "Do not leave any module empty — if you cannot populate it, "
                "this is a schema error."
            )),
        ),
    )


def _assemble_modules(active_result: BaseModel, active_module_names: list[str]) -> Modules:
    """Build the full Modules object. Active modules come from LLM; rest are active=false."""
    kwargs: dict[str, BaseModel] = {}
    for name in _ALL_MODULE_NAMES:
        if name in active_module_names:
            kwargs[name] = getattr(active_result.modules, name)
        else:
            kwargs[name] = _INACTIVE[name]
    return Modules(**kwargs)


# ---------------------------------------------------------------------------
# System prompt (no module activation rules — those live in code)
# ---------------------------------------------------------------------------

_SYSTEM = """\
You are the synthesis stage of a news topic-page pipeline.

You receive reconciled, citation-tracked facts extracted from multiple sources.
Compose a structured topic page from those facts only.

CRITICAL RULES:
1. Every source_ids[] in your output must contain ONLY IDs that appear in the
   provided facts' source_ids. Do not invent source IDs.
2. Do not introduce any claim, number, date, or name not present in the provided facts.
3. Do not paraphrase in a way that changes the meaning or precision of a fact.

SECTION GUIDANCE:
- hero.summary: 2–3 sentences covering who/what/when/where/why. Use core_5w facts.
- at_a_glance: Each 5W answer is 1–2 sentences. Use the most credible facts available.
- timeline: The event's OWN narrative arc only — lead-up milestones, the event itself,
  and direct consequences. Do NOT include general historical background, prior unrelated
  incidents at the same location, or venue/organization history. Those belong in the
  background module. If a fact is categorized historical_context, it must NOT appear in
  the timeline. Include only entries with known dates, ordered ascending.
- key_entities: Focus on directly involved entities. Deduplicate across facts.
- modules: The schema shows exactly which modules apply to this event type.
  Produce substantive content for ALL included modules — they are pre-selected
  based on the event type.

CONFLICT HANDLING:
- If a fact has CONFLICT_VALUES, use "according to [source]" attribution for each value.
  Never silently pick one conflicting value.
- When attributing a conflicting value, write "according to one source" or "according to
  another report" — never write the raw source ID (e.g. "src_007") in prose.

CITATION RULE:
- Never write internal source IDs (e.g. "src_001", "src_007") in any user-facing text
  field: description, summary, title, claim, paragraphs, quote, attribution. Source
  attribution belongs only in the source_ids[] array, never in prose.
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _format_facts(facts: list[ReconciledFact]) -> str:
    lines = []
    for f in facts[:_MAX_FACTS_IN_PROMPT]:
        conf_str = ""
        if f.conflicts:
            vals = [c.value for c in f.conflicts]
            conf_str = f"  CONFLICT_VALUES={vals}"
        lines.append(
            f"[{f.fact_id}] [{f.credibility}] [{f.category}/{f.tier}]"
            f"  srcs={f.source_ids}  date={f.date}\n"
            f"  {f.claim}{conf_str}"
        )
    return "\n".join(lines)


def _make_slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return "-".join(clean.split()[:6])


def _save(output: PageSchema, input_sentence: str) -> None:
    slug = _make_slug(input_sentence)
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "page_schema.json"
    path.write_text(
        json.dumps(output.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Stage E → %s", path)


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    reconciled_output: ReconciledOutput,
    sources_output: SourcesOutput,
    plan: Plan,
    input_sentence: str,
    client: LLMClient | None = None,
) -> PageSchema | None:
    if client is None:
        client = LLMClient()

    facts = reconciled_output.facts
    if not facts:
        logger.warning("Stage E received empty facts list.")
        return None

    active_module_names = _active_modules_for(plan)
    synthesis_schema = _make_synthesis_schema(active_module_names)
    logger.info(
        "Stage E: event_type=%s → active modules: %s",
        plan.event_type, active_module_names,
    )

    facts_text = _format_facts(facts)
    user_msg = (
        f"INPUT SENTENCE: {input_sentence}\n\n"
        f"PLAN:\n"
        f"  normalized_title: {plan.normalized_title}\n"
        f"  event_type: {plan.event_type}\n"
        f"  temporal_status: {plan.temporal_status}\n"
        f"  key_date: {plan.key_date}\n"
        f"  key_entities: {[e.name for e in plan.key_entities]}\n\n"
        f"RECONCILED FACTS ({len(facts)} total):\n"
        f"{facts_text}\n\n"
        f"Compose the topic page from the facts above."
    )

    logger.info("Synthesizing %r (%d facts) …", plan.normalized_title[:60], len(facts))

    result = client.call_structured(
        messages=[{"role": "user", "content": user_msg}],
        model=SONNET,
        schema_class=synthesis_schema,
        tool_description="Output the structured topic page composed from the provided facts.",
        system=_SYSTEM,
        max_tokens=_MAX_TOKENS,
    )
    if not result:
        logger.error("Stage E: LLM returned None.")
        return None

    modules = _assemble_modules(result, active_module_names)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    meta = PageMeta(
        title=plan.normalized_title,
        event_type=plan.event_type,
        generated_at=now,
        input_sentence=input_sentence,
        pipeline_version="0.1.0",
    )
    sources = [
        PageSource(
            id=s.id,
            url=s.url,
            title=s.title,
            domain=s.domain,
            credibility_tier=s.credibility_tier,
            published_at=s.published_at,
            fetched_at=s.fetched_at,
        )
        for s in sources_output.sources
    ]

    page = PageSchema(
        meta=meta,
        hero=result.hero,
        at_a_glance=result.at_a_glance,
        timeline=result.timeline,
        key_entities=result.key_entities,
        modules=modules,
        sources=sources,
    )

    _save(page, input_sentence)
    return page


# ---------------------------------------------------------------------------
# __main__ — test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    _INPUT = "OpenAI rolled out GPT-5.5 Instant as the default model in ChatGPT in May 2026."
    _SLUG = "openai-rolled-out-gpt55-instant-as"

    plan = Plan.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "plan.json").read_text(encoding="utf-8")
    )
    sources_output = SourcesOutput.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "sources.json").read_text(encoding="utf-8")
    )
    reconciled_output = ReconciledOutput.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "reconciled.json").read_text(encoding="utf-8")
    )

    page = run(reconciled_output, sources_output, plan, _INPUT)

    if not page:
        print("No page produced.")
    else:
        m = page.modules
        print(f"\n=== Modules (active only) ===")
        print(f"  version_comparison : active={m.version_comparison.active}  rows={len(m.version_comparison.rows)}")
        print(f"  reception          : active={m.reception.active}  pos={len(m.reception.positive)}  crit={len(m.reception.critical)}")
        print(f"  schedule           : active={m.schedule.active}  entries={len(m.schedule.entries)}")
        print(f"  participants       : active={m.participants.active}  n={len(m.participants.participants)}")
        print(f"  live_status        : active={m.live_status.active}")
        print(f"  background         : active={m.background.active}  paras={len(m.background.paragraphs)}")

        print(f"\n=== Hero ===")
        print(f"  headline : {page.hero.headline}")
        print(f"  summary  : {page.hero.summary}")
        print(f"  key_date : {page.hero.key_date}")

        print(f"\n=== Timeline ({len(page.timeline)} entries) ===")
        for e in page.timeline:
            print(f"  [{e.date}] [{e.credibility}] {e.title}")

        print(f"\n=== Key entities ({len(page.key_entities)}) ===")
        for e in page.key_entities:
            print(f"  [{e.type}] {e.name}")
