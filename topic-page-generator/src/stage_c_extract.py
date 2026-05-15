from __future__ import annotations

import json
import logging
import re
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from dotenv import load_dotenv

from .llm_client import LLMClient, HAIKU
from .schemas import Fact, FactsOutput, Plan, Source, SourcesOutput

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

_SYSTEM = """\
You are a fact extraction engine for a news topic-page pipeline.

Given a source document, extract every discrete factual claim present in the text.

Rules:
- Extract ONLY facts explicitly stated in the document. Do NOT add background knowledge, \
inferences, general context, or anything not written in the document text.
- Each claim is a single, atomic, verifiable statement — one idea per fact.
- Skip: navigation menus, cookie notices, ads, author bios, "related articles" links, \
and all other boilerplate.
- If the document contains no relevant factual claims, return an empty facts list.\
"""

_MAX_WORKERS = 5   # concurrent Haiku calls
_MAX_TOKENS = 8192  # per call; Haiku supports up to 8192 output tokens


def _extract_one(source: Source, plan_title: str, client: LLMClient) -> list[Fact]:
    user_msg = (
        f"Topic: {plan_title}\n"
        f"Source-ID: {source.id}  |  Domain: {source.domain}  |  "
        f"Published: {source.published_at or 'unknown'}\n"
        f"URL: {source.url}\n\n"
        f"--- BEGIN DOCUMENT ---\n"
        f"{source.content}\n"
        f"--- END DOCUMENT ---\n\n"
        f"Extract all factual claims from the document above.\n"
        f"Every fact must have source_id = \"{source.id}\".\n"
        f"Number fact_ids sequentially: f_001, f_002, … within this document.\n"
        f"Do NOT include any fact not explicitly stated in the document text above."
    )
    result = client.call_structured(
        messages=[{"role": "user", "content": user_msg}],
        model=HAIKU,
        schema_class=FactsOutput,
        tool_description="Output the list of extracted facts from this source document.",
        system=_SYSTEM,
        max_tokens=_MAX_TOKENS,
    )
    return result.facts if result else []


def run(
    sources_output: SourcesOutput,
    plan: Plan,
    input_sentence: str,
    client: LLMClient | None = None,
) -> FactsOutput | None:
    if client is None:
        client = LLMClient()

    per_source: dict[str, list[Fact]] = {}

    with ThreadPoolExecutor(max_workers=_MAX_WORKERS) as executor:
        futures = {
            executor.submit(_extract_one, src, plan.normalized_title, client): src
            for src in sources_output.sources
        }
        for future in as_completed(futures):
            src = futures[future]
            try:
                facts = future.result()
                per_source[src.id] = facts
                logger.info("Extracted %d fact(s) from %s (%s)", len(facts), src.id, src.domain)
            except Exception as exc:
                logger.warning("Extraction failed for %s: %s", src.id, exc)
                per_source[src.id] = []

    # Merge in source order, then globally renumber fact_ids
    all_facts: list[Fact] = []
    for src in sources_output.sources:
        all_facts.extend(per_source.get(src.id, []))

    for i, fact in enumerate(all_facts, start=1):
        fact.fact_id = f"f_{i:03d}"

    if not all_facts:
        logger.warning("Stage C produced no facts for: %s", plan.normalized_title)
        return None

    output = FactsOutput(facts=all_facts)
    _save(output, input_sentence)
    return output


def _make_slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return "-".join(clean.split()[:6])


def _save(output: FactsOutput, input_sentence: str) -> None:
    slug = _make_slug(input_sentence)
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "facts.json"
    path.write_text(
        json.dumps(output.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Stage C → %s", path)


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

    output = run(sources_output, plan, _INPUT)

    if not output:
        print("No facts extracted.")
    else:
        facts = output.facts

        # Per-source breakdown
        source_counts = Counter(f.source_id for f in facts)
        print(f"\n=== Per-source extraction ({len(facts)} total facts) ===")
        print(f"  {'id':8s}  {'domain':30s}  facts")
        print(f"  {'-'*8}  {'-'*30}  -----")
        for src in sources_output.sources:
            n = source_counts.get(src.id, 0)
            print(f"  {src.id:8s}  {src.domain:30s}  {n}")

        # Category distribution
        cat_dist = Counter(f.category for f in facts)
        print(f"\n=== Category distribution ===")
        for cat, n in cat_dist.most_common():
            print(f"  {cat:20s}  {n:3d}  {'#' * n}")

        # Tier distribution
        tier_dist = Counter(f.tier for f in facts)
        print(f"\n=== Tier distribution ===")
        for tier, n in tier_dist.most_common():
            print(f"  {tier:20s}  {n:3d}")

        # Sample facts
        print(f"\n=== Sample facts (first 15) ===")
        for fact in facts[:15]:
            print(f"  [{fact.fact_id}] src={fact.source_id} [{fact.category}/{fact.tier}]")
            print(f"        {fact.claim[:120]}")
