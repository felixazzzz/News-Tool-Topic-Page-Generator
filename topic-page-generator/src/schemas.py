from __future__ import annotations

from typing import Literal
from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Shared literals
# ---------------------------------------------------------------------------

EventType = Literal[
    "product_launch",
    "live_event",
    "scheduled_event",
    "political_event",
    "natural_disaster",
    "cultural_event",
    "sports_event",
    "unclassifiable",
]

TemporalStatus = Literal[
    "recently_occurred",
    "currently_unfolding",
    "imminent",
    "scheduled_future",
    "historical",
]

SourceCredibilityTier = Literal[
    "primary",
    "primary_disputed",  # primary source on a domain with known reliability concerns (e.g. state media)
    "tier_1_media",
    "tier_2_media",
    "tier_3_media",
    "low_quality",
]

FactCategory = Literal[
    "key_event",
    "capability",
    "quote",
    "reaction",
    "metric",
    "context",
    "access_policy",
    "schedule_item",
    "participant",
    "historical_context",
]

FactTier = Literal["core_5w", "supporting", "context", "quote", "reaction"]

FactCredibility = Literal["primary", "corroborated", "single_source", "low_credibility"]


# ---------------------------------------------------------------------------
# Stage A — Plan
# ---------------------------------------------------------------------------

class EntityMention(BaseModel):
    name: str = Field(description="Entity name as it appears in context.")
    type: Literal["person", "organization", "product", "location", "event"] = Field(
        description="Entity category."
    )


class Plan(BaseModel):
    normalized_title: str = Field(
        description="Clean publishable headline for the event. Not the input sentence verbatim; omit quotes and trailing punctuation."
    )
    event_type: EventType = Field(
        description="Single best-fit event type. Use 'unclassifiable' if the input is not a real, specific event."
    )
    event_type_confidence: float = Field(
        ge=0.0, le=1.0,
        description="Confidence in the event_type classification (0.0–1.0)."
    )
    temporal_status: TemporalStatus = Field(
        description="When this event occurs relative to today's date."
    )
    key_entities: list[EntityMention] = Field(
        description="All people, organizations, products, and locations mentioned or clearly implied by the event."
    )
    key_date: str = Field(
        description="Primary event date in ISO 8601. Partial dates are fine (e.g. '2026-05'). Use the most specific date available."
    )
    primary_source_candidates: list[str] = Field(
        description=(
            "Bare domain names of the event subject's OWN official domains "
            "(e.g. 'openai.com' for an OpenAI event, 'fifa.com' for a FIFA event). "
            "Do NOT include news outlets, media sites, or third-party coverage — "
            "those are discovered via search. Only list domains the event subject itself controls."
        )
    )
    search_queries: list[str] = Field(
        description="4–6 diverse queries. Must include at least one site:-restricted query targeting a primary domain and at least one broad news query."
    )
    information_needs: list[str] = Field(
        description="Specific information the topic page needs, e.g. 'what_changed', 'official_announcement', 'schedule', 'participant_list', 'reception', 'background'."
    )
    safety_flag: bool = Field(
        default=False,
        description="True if the input is not a real event, requests misinformation, or contains adversarial content."
    )
    safety_note: str | None = Field(
        default=None,
        description="Required explanation when safety_flag is true. Null otherwise."
    )


# ---------------------------------------------------------------------------
# Stage B — Retrieve
# ---------------------------------------------------------------------------

class Source(BaseModel):
    id: str  # "src_001"
    url: str
    title: str
    domain: str
    fetched_at: str           # ISO datetime
    published_at: str | None  # ISO date, parsed from page metadata
    credibility_tier: SourceCredibilityTier
    retrieval_tier: Literal[1, 2, 3]  # which retrieval pass found this
    content: str              # clean text from trafilatura


class SourcesOutput(BaseModel):
    sources: list[Source]


# ---------------------------------------------------------------------------
# Stage C — Extract
# ---------------------------------------------------------------------------

class Fact(BaseModel):
    fact_id: str = Field(
        description="Sequential identifier within this extraction (f_001, f_002, …). Will be globally renumbered after merging."
    )
    source_id: str = Field(
        description="The Source.id this fact was extracted from. Must match the source_id provided in the prompt exactly."
    )
    claim: str = Field(
        description=(
            "Single atomic factual statement, exact quote or close paraphrase from the document. "
            "One idea per claim. Do NOT add background knowledge not present in the document text."
        )
    )
    category: FactCategory = Field(
        description=(
            "Taxonomy category — pick the single best fit: "
            "key_event=something that happened or was announced; "
            "capability=what a product/model/person can do; "
            "quote=direct or near-direct speech from a named person; "
            "reaction=a response, opinion, or assessment; "
            "metric=a number, stat, benchmark, or measurement; "
            "context=background that helps explain the event (present in doc, not inferred); "
            "access_policy=pricing, availability, subscription tier, or access rules; "
            "schedule_item=a date, deadline, or calendar milestone; "
            "participant=a named person, org, or entity involved; "
            "historical_context=background predating the event."
        )
    )
    tier: FactTier = Field(
        description=(
            "Importance tier: "
            "core_5w=who/what/when/where/why — the essential facts a reader must know; "
            "supporting=important context that enriches but isn't the lead; "
            "context=background / scene-setting; "
            "quote=a notable direct quote (also set category=quote); "
            "reaction=a reaction or opinion (also set category=reaction)."
        )
    )
    date: str | None = Field(
        default=None,
        description="ISO date (YYYY-MM-DD or YYYY-MM) if the fact is tied to a specific date mentioned in the document. Null otherwise."
    )


class FactsOutput(BaseModel):
    facts: list[Fact] = Field(
        description="All factual claims extracted from this source document. Empty list if no relevant facts are present."
    )


# ---------------------------------------------------------------------------
# Stage D — Reconcile
# ---------------------------------------------------------------------------

class ConflictEntry(BaseModel):
    value: str = Field(description="One conflicting value (e.g. a date string, a number, a claim variant).")
    source_ids: list[str] = Field(description="Source IDs that reported this particular value.")


class ReconciledFact(BaseModel):
    fact_id: str = Field(description="Globally unique merged fact identifier (f_merged_001, f_merged_002, …).")
    claim: str = Field(description="Best representative claim, taken from the highest-credibility source in the cluster.")
    category: FactCategory = Field(description="Fact category inherited from the representative fact.")
    tier: FactTier = Field(description="Importance tier inherited from the representative fact.")
    source_ids: list[str] = Field(description="All Source IDs whose facts were clustered into this entry.")
    credibility: FactCredibility = Field(
        description=(
            "primary=at least one primary/primary_disputed source; "
            "corroborated=2+ independent domains agree; "
            "single_source=only one domain; "
            "low_credibility=only low_quality sources."
        )
    )
    conflicts: list[ConflictEntry] = Field(
        default=[],
        description="Detected value conflicts within the cluster (e.g. two sources cite different dates). Never silently resolved."
    )
    date: str | None = Field(default=None, description="ISO date from the representative fact, null if absent.")


class ReconcileStats(BaseModel):
    input_facts: int = Field(description="Total facts received from Stage C.")
    output_facts: int = Field(description="Total reconciled facts after merging.")
    merged_clusters: int = Field(description="Number of clusters that absorbed 2+ original facts.")
    llm_assisted_merges: int = Field(description="Pairs merged via LLM decision (ambiguous similarity range).")
    conflicts_detected: int = Field(description="Number of clusters with at least one detected value conflict.")


class ReconciledOutput(BaseModel):
    facts: list[ReconciledFact] = Field(description="Merged and reconciled facts for Stage E synthesis.")
    stats: ReconcileStats


# ---------------------------------------------------------------------------
# Stage E — Synthesize (PageSchema)
# ---------------------------------------------------------------------------

class CitedText(BaseModel):
    text: str
    source_ids: list[str]


class Hero(BaseModel):
    headline: str
    summary: str        # 2–3 sentences
    key_date: str       # human-readable, e.g. "May 8, 2026"
    source_ids: list[str]


class AtAGlance(BaseModel):
    who: CitedText
    what: CitedText
    when: CitedText
    where: CitedText
    why: CitedText


class TimelineEntry(BaseModel):
    date: str
    title: str
    description: str
    source_ids: list[str]
    credibility: FactCredibility


class KeyEntity(BaseModel):
    name: str
    type: Literal["person", "organization", "product", "location", "event"]
    description: str | None = None
    source_ids: list[str] = []


# --- Modules ---

class VersionComparisonRow(BaseModel):
    attribute: str
    previous_value: str | None = None
    new_value: str
    source_ids: list[str]


class VersionComparisonModule(BaseModel):
    active: bool
    rows: list[VersionComparisonRow] = []


class ReceptionItem(BaseModel):
    quote: str
    attribution: str
    source_ids: list[str]


class ReceptionModule(BaseModel):
    active: bool
    positive: list[ReceptionItem] = []
    critical: list[ReceptionItem] = []


class ScheduleEntry(BaseModel):
    date: str
    time: str | None = None
    title: str
    description: str | None = None
    source_ids: list[str]


class ScheduleModule(BaseModel):
    active: bool
    entries: list[ScheduleEntry] = []


class Participant(BaseModel):
    name: str
    role: str   # e.g. "team", "contestant", "country"
    description: str | None = None
    source_ids: list[str] = []


class ParticipantsModule(BaseModel):
    active: bool
    participants: list[Participant] = []


class LiveStatusModule(BaseModel):
    active: bool
    status: str | None = None         # e.g. "Live now", "Starting in 2 hours"
    last_update: str | None = None    # ISO datetime
    source_ids: list[str] = []


class BackgroundParagraph(BaseModel):
    text: str
    source_ids: list[str]


class BackgroundModule(BaseModel):
    active: bool
    paragraphs: list[BackgroundParagraph] = []


class Modules(BaseModel):
    version_comparison: VersionComparisonModule
    reception: ReceptionModule
    schedule: ScheduleModule
    participants: ParticipantsModule
    live_status: LiveStatusModule
    background: BackgroundModule


class PageMeta(BaseModel):
    title: str
    event_type: EventType
    generated_at: str       # ISO datetime
    input_sentence: str
    pipeline_version: str = "0.1.0"


class PageSource(BaseModel):
    id: str
    url: str
    title: str
    domain: str
    credibility_tier: SourceCredibilityTier
    published_at: str | None = None
    fetched_at: str


class PageSchema(BaseModel):
    meta: PageMeta
    hero: Hero
    at_a_glance: AtAGlance
    timeline: list[TimelineEntry]
    key_entities: list[KeyEntity]
    modules: Modules
    sources: list[PageSource]
