# DESIGN.md — One-Sentence Topic Page Generator

## 0. TL;DR

A one-sentence event description goes in. A publishable topic page comes out. In between sits a 5-stage pipeline that mirrors how a real newsroom works: plan → report → extract facts → reconcile → write. LLMs do the language-bound work (understanding, extraction, synthesis). Deterministic code does the auditable work (search, filtering, scoring, clustering). Citations are first-class and tracked end-to-end.

---

## 1. Product Decisions

### 1.1 What is a topic page?

We treated this as a real product question, not a templating exercise. After looking at how newsrooms (Reuters, NYT, FT, The Verge) actually build topic pages and reading journalism handbooks on story structure, we landed on a simple thesis:

**A topic page is a structured, sourced, time-stamped surface that lets a reader understand a developing event in 30 seconds, or in 10 minutes, depending on how deep they want to go.**

This thesis gave us three concrete commitments:

1. **Information must be ranked, not just present.** Hard news has used the *inverted pyramid* for a century: most important facts first, decreasing importance downward. We adopted this as the page's spine.
2. **Every fact must carry its source.** A topic page without citations is a blog post. With citations, it's reference material.
3. **Freshness must be visible.** Stale facts on a "live" page are a worse failure than missing facts. Every fact and the page itself carry timestamps.

### 1.2 How does the page shape adapt to event type?

We rejected two extremes:

- **One template per event type** ("product launch template", "live event template") would be the brief's explicit anti-pattern: *"the same template with the words swapped."*
- **One generic template** would fail at being purpose-fit — a World Cup page and a model launch page genuinely need different things.

Our answer: **a fixed core skeleton + optional modules that activate per event type.**

**Core skeleton (every page has this):**
- Hero (headline, 2–3 sentence summary, key date)
- At-a-glance (5W facts: who, what, when, where, why)
- Timeline (chronological events)
- Key entities (people, organizations, products involved)
- Sources (full bibliography with credibility tier)

**Optional modules (activated by event type and information availability):**
- `version_comparison` — product launches (e.g. GPT-5 vs GPT-5.5)
- `schedule` — scheduled events (e.g. Eurovision shows, World Cup matches)
- `participants` — competitive events (teams, contestants)
- `reception` — events with public/critical response
- `live_status` — currently-unfolding events
- `background` — events benefiting from historical context

Module activation is **code-enforced**, not LLM-decided. A `MODULE_ACTIVATION` lookup table in `stage_e_synthesize.py` maps each `event_type` to its eligible modules; the LLM only ever sees schema fields for the active set. This is a deliberate LLM/code boundary (§2.3): activation is a rule task, not a language task, and handing it to the model risks the "feed-the-beast" anti-pattern — generating plausible-sounding content for modules that don't fit the event type.

### 1.3 What we intentionally left out

- **Auth / accounts / persistence.** Brief explicitly de-prioritizes these.
- **Multi-language output.** All pages in English to keep scope honest.
- **Image generation / sourcing.** Image rights and quality are a project unto themselves. We surface text + cited links and stop there.
- **Refresh on a schedule.** The page is generated once at request time. A "next steps" section discusses what continuous refresh would require.
- **An "independent fact-check" LLM stage.** Tempting to add, but two LLM calls have correlated failure modes — one LLM cannot meaningfully check another's hallucinations. We pushed that responsibility to the UI layer via credibility tiers and visible sources (see §7).

---

## 2. System Architecture

### 2.1 Pipeline overview

```
[one-sentence input]
        ↓
   Stage A: PLAN          (LLM, 1 call — Sonnet)
   "What is this event? What do we need to find? Where do we look?"
        ↓
   Stage B: RETRIEVE      (deterministic code, 0 LLM calls)
   Tier 1 primary sources → Tier 2 trusted media → Tier 3 broad
        ↓
   Stage C: EXTRACT       (LLM, one parallel Haiku call per source — 9–18 in practice)
   For each source: pull atomic facts, each bound to a source_id
        ↓
   Stage D: RECONCILE     (mostly deterministic, ≤1 LLM call — Haiku)
   Embedding-based clustering → conflict detection → credibility scoring
        ↓
   Stage E: SYNTHESIZE    (LLM, 1 call — Sonnet)
   Compose page schema: lead, timeline, modules, etc.
        ↓
   [page_schema.json]  →  renderer.py  →  page.html
```

### 2.2 Why this pipeline (and not an agent loop)

A single agent loop with `web_search` and `fetch` tools would be shorter to write but fails several criteria the brief weights heavily:

- **Observability**: opaque tool-use trace is hard to debug per-stage
- **Reproducibility**: agent decisions vary run-to-run, even with temperature 0
- **Citation integrity**: agents tend to summarize-then-cite, losing the binding between a specific claim and the specific source paragraph
- **Cost control**: no upper bound on tool calls

The 5-stage pipeline trades flexibility for **per-stage isolation**: each stage has a single responsibility, a typed input, a typed output, and can be unit-tested or replayed independently.

### 2.3 LLM vs. deterministic code: the boundary

Our principle: **LLMs do what only LLMs can. Code does everything else.**

| Task | Who does it | Why |
|---|---|---|
| Understand a fuzzy sentence | LLM | Language understanding |
| Generate search queries | LLM | Requires world knowledge of the topic |
| Call search APIs | Code | Deterministic, no judgment needed |
| Filter by domain / time / length | Code | Rule-based, auditable |
| Score domain credibility | Code | Pre-maintained lookup table |
| Extract facts from prose | LLM | Language task, NLP-hard otherwise |
| Cluster semantically similar facts | Embedding model (local) | Cheap, deterministic, well-understood |
| Detect conflicts within a cluster | Code | Field-level comparison after clustering |
| Compute credibility tier | Code | Rule: primary > corroborated > single-source |
| Edge-case cluster decisions | LLM | Only ambiguous similarity scores [0.72, 0.85) |
| Write lead / organize narrative | LLM | Composition is a language task |
| Render HTML | Code | Deterministic templating |

This boundary isn't aesthetic — it's how we keep the pipeline debuggable, auditable, and cheap.

---

## 3. Data Contract & Schema

### 3.1 Per-stage outputs

Each stage produces a versioned, validated JSON artifact (we use Pydantic for schema enforcement). The pipeline can be paused, inspected, or replayed at any stage boundary.

**Stage A → `plan.json`**

```json
{
  "normalized_title": "OpenAI launches GPT-5.5 Instant as default ChatGPT model",
  "event_type": "product_launch",
  "event_type_confidence": 0.9,
  "temporal_status": "recently_occurred",
  "key_entities": [{"name": "OpenAI", "type": "organization"}],
  "key_date": "2026-05",
  "primary_source_candidates": ["openai.com", "openai.com/blog"],
  "search_queries": ["OpenAI GPT-5.5 Instant launch"],
  "information_needs": ["what_changed", "official_announcement"]
}
```

**Stage B → `sources.json`**

```json
{
  "sources": [
    {
      "id": "src_001",
      "url": "https://openai.com/blog/gpt-5-5-instant",
      "title": "Introducing GPT-5.5 Instant",
      "domain": "openai.com",
      "fetched_at": "2026-05-10T14:32:00Z",
      "published_at": "2026-05-08",
      "credibility_tier": "primary",
      "content": "<cleaned full text>"
    }
  ]
}
```

**Stage C → `facts.json`**

```json
{
  "facts": [
    {
      "fact_id": "f_001",
      "source_id": "src_001",
      "claim": "GPT-5.5 Instant became the default ChatGPT model on May 8, 2026",
      "category": "key_event",
      "date": "2026-05-08",
      "tier": "core_5w"
    }
  ]
}
```

`category` is the **union of all categories across all event types**; a single event will only use a subset, and unused categories are never an error. Full set:

| Value | Meaning |
|---|---|
| `key_event` | Landmark event node that has already occurred |
| `capability` | Product / service capability or feature |
| `quote` | Direct quotation |
| `reaction` | Public or industry reaction |
| `metric` | Numeric data or statistic |
| `context` | Currently relevant background information |
| `access_policy` | Access method, permission, or policy |
| `schedule_item` | Specific future activity or session |
| `participant` | Competitor, contestant, or key participant |
| `historical_context` | Historical background or precedent |

`tier` ∈ {`core_5w`, `supporting`, `context`, `quote`, `reaction`} — drives placement in the inverted pyramid during synthesis.

**Stage D → `reconciled.json`**

```json
{
  "facts": [
    {
      "fact_id": "f_merged_001",
      "claim": "GPT-5.5 Instant has ~40% lower latency than GPT-5",
      "category": "capability",
      "tier": "supporting",
      "source_ids": ["src_001", "src_003", "src_005"],
      "credibility": "corroborated",
      "conflicts": []
    },
    {
      "fact_id": "f_merged_007",
      "claim": "Free-tier message limit on GPT-5.5",
      "category": "access_policy",
      "credibility": "single_source",
      "conflicts": [
        {"value": "20 per 3 hours", "source_ids": ["src_001"]},
        {"value": "30 per 3 hours", "source_ids": ["src_002"]}
      ]
    }
  ]
}
```

Conflicts are **preserved, not resolved**. The synthesis stage decides how to surface them; we never silently pick one number over another.

**Stage E → `page_schema.json`** (the artifact the frontend consumes)
See §3.2 below.

### 3.2 Page schema

```json
{
  "meta": {
    "title": "...",
    "event_type": "product_launch",
    "generated_at": "2026-05-10T14:45:00Z",
    "input_sentence": "OpenAI rolled out GPT-5.5 Instant...",
    "pipeline_version": "0.1.0"
  },
  "hero": {
    "headline": "...",
    "summary": "...",
    "key_date": "May 8, 2026",
    "source_ids": ["src_001", "src_002"]
  },
  "at_a_glance": {
    "who": {"text": "OpenAI", "source_ids": []},
    "what": {"text": "...", "source_ids": []},
    "when": {"text": "...", "source_ids": []},
    "where": {"text": "...", "source_ids": []},
    "why": {"text": "...", "source_ids": []}
  },
  "timeline": [
    {
      "date": "2026-05-08",
      "title": "...",
      "description": "...",
      "source_ids": ["src_001"],
      "credibility": "primary"
    }
  ],
  "key_entities": [],
  "modules": {
    "version_comparison": {"active": true, "rows": []},
    "reception": {"active": true, "positive": [], "critical": []},
    "schedule": {"active": false},
    "participants": {"active": false},
    "live_status": {"active": false},
    "background": {"active": true, "paragraphs": []}
  },
  "sources": [
    {
      "id": "src_001",
      "url": "...",
      "title": "...",
      "domain": "openai.com",
      "credibility_tier": "primary",
      "published_at": "...",
      "fetched_at": "..."
    }
  ]
}
```

### 3.3 How the schema survives three different event types

| Event type | Activated modules | Hero emphasis |
|---|---|---|
| `product_launch` (GPT-5.5) | `version_comparison`, `reception`, `background` | What changed, who's affected |
| `live_event` (Eurovision) | `schedule`, `participants`, `live_status` | What's happening now, when's next |
| `sports_event` (World Cup) | `schedule`, `participants`, `background` | Countdown, format, who's in |

The core skeleton (`hero` / `at_a_glance` / `timeline` / `entities` / `sources`) is constant. Only `modules` differs. This is the brief's "purpose-fit, not template-swapped" test.

**Note on `sports_event` vs `scheduled_event`:** The World Cup was classified as `sports_event` by Stage A, not `scheduled_event`. Both types activate `schedule + participants + background`. `sports_event` is the correct label for recurring competitive formats; `scheduled_event` covers one-off ceremonies, conferences, or single-date happenings. The module overlap is intentional — both formats need the same information surface.

### 3.4 Enforcing structured outputs

Every LLM call uses **tool-use / structured output** with a Pydantic-derived JSON schema. The model's response is parsed and validated; on validation failure, we retry once with the error message included; on second failure, we fall back to a partial-result mode for that stage and log it.

We do not use free-form JSON-in-a-codeblock parsing. It's the single most common source of pipeline brittleness in LLM applications and it's unnecessary in 2026.

---

## 4. Information Sourcing

### 4.1 Tiered retrieval strategy

We learned from journalism handbooks that **search engines are signposts, not endpoints**. Good reporters chase queries toward primary sources, not the other way around. We mirrored this:

**Tier 1 — Primary source direct fetch**

The planning stage outputs candidate primary domains (openai.com for an OpenAI launch, fifa.com for a FIFA event). Stage B fetches these directly before doing any general search. If the candidate URL 404s, we fall back to `site:` queries on that domain. Tier 1 sources are auto-tagged `credibility: primary` and bypass general-search filtering.

**Tier 2 — Trusted media search**

We call a search API (Tavily) with the LLM-generated queries. Results are scored against a pre-maintained `domain_credibility.json` lookup table (seeded from Media Bias / Fact Check and Wikipedia's perennial-sources list, hand-reviewed). Domains map to one of:

- `tier_1_media` — Reuters, AP, BBC, FT, NYT, WSJ, Bloomberg
- `tier_2_media` — TechCrunch, The Verge, Wired, CNN, Axios; The Athletic (NYT-owned, professional sports); science outlets (Nature, Scientific American)
- `tier_3_media` — other reputable outlets with mixed records; this includes tabloids (NY Post, Daily Mail) rated "Mixed Factual" by MBFC and sanctioned by IPSO for accuracy failures, and Wikipedia (useful for background, but excluded from Stage D corroboration counts)
- `low_quality` — content farms and low-editorial sites
- `blocklist` — known disinformation sources and state propaganda outlets (RT, Sputnik)
- `primary_disputed` — a blocklist domain identified by Stage A as the event's own primary source (e.g. a Russian government announcement); fetched under Tier 1 but flagged so downstream stages can weight it appropriately

**Important:** Tier 1 retrieval (direct fetch of `primary_source_candidates`) runs on a separate path from the domain_credibility lookup. A domain not in the table (e.g. `fifa.com`) is still fetched as a Tier 1 primary source — the two paths are independent.

Results from Tavily are sorted by tier, then recency, then deduplicated.

**Tier 3 — Broad search (de-weighted)**

Everything that survives filtering but isn't in tiers 1–2 is kept but flagged `low_credibility`. Facts extracted from these require Tier 1/2 corroboration before they're accepted by the synthesis stage.

### 4.2 Filtering (all deterministic)

Applied to all search results before fetch:

- **Domain blocklist** (known low-quality)
- **URL pattern blocklist** (`/tag/`, `/category/`, aggregator pages)
- **Time window** (configurable per event recency; default last 90 days)
- **Language** (English only for v0)
- **Deduplication** (exact URL match)

After fetch:

- **Content length** (drop <200 chars or >50k chars)
- **Near-duplicate detection** (MinHash on extracted text — same story syndicated across sites)

### 4.3 Citations & freshness

- Every source has `fetched_at` (when we got it) and `published_at` (when it was published, parsed from page metadata when available).
- Every fact carries `source_id`; merged facts carry `source_ids[]`.
- The page-level `generated_at` timestamp is rendered in the UI.
- Facts with only `low_credibility` sources are surfaced with an "according to..." attribution prefix.

### 4.4 Handling conflicts

When two independent sources disagree, we **preserve the disagreement** in the schema (see §3.1, `conflicts[]`). The synthesis stage decides presentation:

- If a Tier 1 primary source and a Tier 3 source disagree → take the primary
- If two Tier 2 sources disagree on a `core_5w` fact → render both with attribution
- If sources disagree on a `supporting` detail and no primary is available → drop the fact rather than guess

This is one of the few places we explicitly choose **omission over invention**.

### 4.5 Cost and latency

Calibrated against three production runs (GPT-5.5 Instant, Eurovision 2026, FIFA World Cup 2026):

| Stage | Cold (uncached) | Warm (LLM cached) | Notes |
|---|---|---|---|
| A | 4–7s | < 1s | 1 Sonnet call |
| B | 10–30s | 1–3s | 8–14 concurrent fetches; network latency dominates |
| C | 20–90s | < 1s | 9–14 Haiku calls, `MAX_WORKERS=5`; scales with source count |
| D | 3–10s | 3–10s | Embedding never cached — `sentence-transformers` runs fresh every time |
| E | 20–120s | < 1s | 1 Sonnet call; scales with reconciled fact count (197–345 in test runs) |
| **Total** | **60–250s** | **10–15s** | Wide range driven by source count and response sizes |

**A note on Stage D caching:** the embedding step (all-MiniLM-L6-v2 on CPU) is deterministic but not persisted in SQLite. Stage D always takes 3–10s even on warm runs. The LLM merge-decision calls within Stage D are cached; on a warm run, Stage D's wall time is dominated by the embedding computation alone.

**Cost:** the ceiling is Stage C (Haiku extraction). 9–14 sources at up to 8k output tokens each ≈ $0.04–$0.08. A full cold run costs roughly $0.05–$0.15 total, well under $0.20 per page.

We cache aggressively: search API responses (keyed by query), fetched pages (keyed by URL), and LLM responses (keyed by prompt hash). Warm runs drop wall-clock time to ~10–15s and effective LLM cost to near-zero.

---

## 5. Prompt Engineering & LLM Craft

### 5.1 Structured outputs, always

All five LLM-using stages use tool-use / response_format constraints with Pydantic-derived JSON schemas. We never parse free-form text into structured data. Validation failures trigger one retry with the validation error echoed back to the model; second failure triggers per-stage fallback.

### 5.2 Per-stage prompt shape

Each stage's prompt follows the same shape:

1. **Role** — what this LLM is doing in our pipeline
2. **Inputs** — what we're handing it
3. **Task** — exactly what to produce
4. **Schema** — the JSON shape required
5. **Constraints** — what to avoid (e.g. "do not invent dates", "every claim must be traceable to the input")
6. **Examples** — 1–2 worked examples for stages C and E

### 5.3 Anti-hallucination measures

- **Stage A** — output `event_type_confidence`; downstream stages can branch on low confidence (we currently warn but don't reroute)
- **Stage C** — prompt explicitly says "extract only from this document; do not add background knowledge"; facts that can't be tied to specific text are dropped
- **Stage D** — clustering is embedding-driven, not LLM-driven, so the model can't merge facts that aren't semantically close
- **Stage E** — synthesis prompt receives **only the reconciled facts**, not the source texts; this prevents the model from re-introducing claims that didn't survive extraction

### 5.4 Why we don't use a larger model everywhere

Sonnet on extraction is overkill; the task is "read this prose, emit JSON-shaped facts." Haiku does it for ~10x less and we haven't measured quality loss. Sonnet earns its place on planning (event-type judgment) and synthesis (composition / narrative). This is a deliberate cost-quality tradeoff with measured justification.

### 5.5 Stage D: NLP design for fact reconciliation

Stage D is the only stage that touches NLP beyond LLM calls. Its job is to take ~100–400 atomic facts extracted across 8–22 sources and merge near-duplicates, assign credibility, and surface conflicts.

**Bi-encoder embeddings (all-MiniLM-L6-v2)**

Each claim is encoded into a 384-dimensional vector using `sentence-transformers` with `all-MiniLM-L6-v2`. The model runs locally with no API cost. Vectors are L2-normalised, so cosine similarity reduces to a dot product; the full pairwise similarity matrix is computed as a single numpy BLAS operation (`embeddings @ embeddings.T`), which is O(N²·d) but fast in practice at our scale (N ≤ 500 in all test runs).

*Why all-MiniLM-L6-v2:* We weighed three factors — embedding quality on short factual sentences, CPU inference speed, and model size. At 384 dimensions and ~80 MB on disk, all-MiniLM-L6-v2 encodes 400 claims in 2–5s on CPU (measured: 2.3s for 228 facts, 6.6s for 326 facts, 9.1s for 403 facts across three runs). Larger models such as all-mpnet-base-v2 (768 dimensions) improve STS benchmark scores by 2–3 points but are 2–3× slower on CPU and produce a similarity matrix 4× larger in memory — not a justified trade-off at N ≤ 500. The quality delta on news-style factual claims — where the discriminating signal is entity overlap and numerical precision, not abstract semantic nuance — is minimal.

**Threshold hybrid: rule first, LLM only for ambiguity**

We use two thresholds:

- `cos ≥ 0.85` → auto-merge (deterministic)
- `cos < 0.72` → keep separate (deterministic)
- `cos ∈ [0.72, 0.85)` + same category → LLM merge decision

This hybrid matters because the bi-encoder is a *similarity* model, not a *semantics* model — high similarity indicates topical overlap, not logical equivalence. Two facts from the same press conference have high embedding similarity regardless of whether they describe the same event. The ambiguous band is where the model genuinely cannot decide; we defer to an LLM there with a conservative merge instruction.

*Threshold calibration:* the thresholds were set empirically against an early GPT-5.5 fact set. At cos ≥ 0.85, merged pairs consistently contained the same specific measurement restated across articles (e.g. three outlets all citing "$68.1 billion Q4 revenue" or "52.5% fewer hallucinations"). At cos 0.72–0.85, pairs frequently share a topic but differ on specificity or framing (e.g. "OpenAI improved hallucinations" vs "OpenAI reduced hallucinations by 52.5% on high-risk topics") — the correct merge decision genuinely requires reading both claims, justifying LLM judgment. Below 0.72, pairs reliably describe different facts even when topically related.

**LLM-as-NLI (Haiku, batched)**

Ambiguous pairs are batched into groups of 5 and sent to Haiku with the instruction: "Merge only if A and B describe the same underlying fact — same subject, same attribute, same event. Be conservative." The LLM effectively performs natural language inference (does A entail B?) in the limited case where similarity is high enough to be plausible but not high enough to be certain.

We cap LLM pairs at 40 per run to bound cost. Observed across three runs:

| Input | Facts in | Facts out | Auto-merged clusters | LLM merges | Conflicts |
|---|---|---|---|---|---|
| GPT-5.5 Instant (11 sources) | 326 | 240 | 26 | 13 | 5 |
| Eurovision 2026 (9 sources) | 228 | 197 | 19 | 7 | 5 |
| FIFA World Cup (14 sources) | 403 | 345 | 26 | 5 | 6 |

Reduction rate: 15–26%. Most merging is deterministic (auto-merge via cos ≥ 0.85); the LLM resolves 5–13 tail cases per run.

**Union-Find for cluster management**

Merge decisions are tracked with a union-find structure (with path compression). When A merges with B and B with C, A and C are in the same cluster without needing to re-examine the A–C pair. This transitivity is the correct behavior for near-duplicates (three articles restating the same earnings figure should become one fact) but can produce over-merging if the similarity thresholds are too aggressive — a chain A≈B≈C does not guarantee A≈C semantically. The 0.85 threshold was set conservatively to minimize this effect.

**Credibility scoring (purely rule-based)**

After clustering, credibility is assigned deterministically from the source tiers of all sources in the cluster:
- `primary` — at least one `primary` or `primary_disputed` source
- `low_credibility` — all sources are `low_quality`
- `corroborated` — 2+ distinct domains (Wikipedia excluded), none qualifying as primary
- `single_source` — everything else

**Conflict detection (field-level comparison)**

Within each cluster, we compare the `date` fields across member facts. If members disagree on date (a concrete, structured value), we emit `ConflictEntry` records preserving all values and their source attributions. We detect date conflicts because they represent a factual disagreement (two sources cite different dates for the same event) rather than a phrasing difference. We do not detect phrasing-level conflicts — claims that are semantically contradictory but not structurally so — because that would require the LLM to check every pair in every cluster, which reverses the cost/accuracy trade-off we designed around.

---

## 6. HTML Rendering

### 6.1 What the renderer does

`renderer.py` reads `page_schema.json` and writes a single self-contained `page.html` (CSS embedded). It is the final stage of the pipeline.

The renderer's job is narrow: faithfully map the structured JSON to readable HTML. It makes no editorial decisions — all content, ordering, and credibility signals come from the schema. This keeps the rendering layer thin and replaceable.

### 6.2 Why the design is deliberately understated

The HTML output is intentionally a **first-draft scaffold**, not a finished product UI. The reasoning:

- **The backend output is the real artifact.** The pipeline produces a structured, sourced, conflict-annotated JSON object — that is what this project demonstrates. Heavy styling would compete with the information rather than serve it.
- **Fast to change.** Because `page_schema.json` is the stable contract, the HTML layer can be redesigned for any specific event or use case in hours without touching the pipeline. A bolder, more customized layout for a live election or sports final is a renderer change, not a pipeline change.
- **Draft framing is honest.** Presenting it as a polished final product would misrepresent the scope. The page is what a journalist would call a "clean printout" — correct and readable, not art-directed.

The visual style (Source Serif Pro, narrow column, 1px dividers, no shadows or gradients) is chosen to keep attention on the content, not on the chrome.

### 6.3 How the visual choices map to pipeline design

The renderer directly reflects decisions made in §1 and §3:

| Visual element | Maps to |
|---|---|
| Hero (headline + 2–3 sentence summary at top) | Inverted pyramid principle (§1.1) |
| At-a-glance 5W grid | The 5W snapshot established in §1.1 |
| Active modules only rendered | Code-enforced `MODULE_ACTIVATION` (§1.2) |
| Credibility dot + label on timeline entries | `FactCredibility` field from Stage D (§3.1) |
| Reception green/red border | Semantic color for a genuine binary — positive vs. critical (§3.2) |
| Source tier ● green for primary | Distinguishes first-hand sources from media reporting (§4.1) |
| `[N]` superscripts → anchor links to Sources | Citation-first design principle (§1.1) |

Color is used only where it carries semantic meaning (credibility level, sentiment polarity). It is not used decoratively.

### 6.4 Single-file delivery

The page is a single `.html` file with embedded CSS and no JavaScript. This was a deliberate choice: reviewers open it directly in a browser with no server, no build step, and no network dependency beyond the Google Fonts import. The tradeoff is that CSS updates require re-rendering, which is a one-command operation (`python -m src.renderer <slug>`).

---

## 7. Failure Modes

### 6.1 LLM hallucinations

**Mitigations:**

- Extraction prompts forbid background knowledge; only what's in the document
- Synthesis stage sees facts, not raw sources → can't reintroduce dropped claims
- Every claim in the final schema carries `source_ids[]`; absence of sources is an automatic flag
- Embedding-based clustering is unaffected by LLM hallucination

**Known limitation:** if an extracted fact misrepresents what the source actually said (subtle paraphrase drift), we will not catch it. A real fact-checker reads the source and the claim side-by-side; we don't simulate that with a second LLM call because two LLM calls have correlated failure modes. This is the strongest argument for surfacing sources prominently in the UI: **the reader is the final verifier.**

### 6.2 Ambiguous input

Examples: `"Tell me about the World Cup"` (which one?), `"the AI thing yesterday"` (which one?), `"Microsoft"` (not an event).

**Mitigations:**

- Stage A is asked to detect inputs that are not specific event descriptions and emit `event_type: "unclassifiable"` with explanation
- The CLI surfaces this to the user with a request to specify
- We do **not** silently guess an event and produce a confidently-wrong page

### 6.3 Adversarial / off-topic input

Examples: `"Generate a page about how vaccines cause autism"`, prompt injections embedded in the sentence, requests for clearly fictional events.

**Mitigations:**

- Stage A includes a safety check: the LLM is asked to flag inputs that ask for misinformation or are not genuine event descriptions
- Stage B's retrieval can't find authoritative sources for fabricated events; the page either degrades to "insufficient information" or refuses
- We don't try to filter every adversarial input — defense in depth, not defense at one layer

### 6.4 Insufficient information

Examples: event is too recent (no coverage), too obscure, or just-happened.

**Mitigations:**

- Stage B reports source count and tier distribution
- Stage E receives this and degrades gracefully: fewer modules activated, shorter summary, clear "limited information available" framing rather than padding with low-credibility material
- This is the explicit anti-pattern from live-blog research: never "feed the beast" with junk to look busy

### 6.5 Source-time drift

A "current" page generated last week is stale this week.

**Mitigations:**

- `generated_at` is prominent in the UI
- This is a known limitation, not a solved problem; see §8.

### 6.6 What we acknowledge as unsolved

- We don't detect coordinated inauthentic behavior across sources (e.g. five outlets republishing the same press release as independent reporting)
- We don't verify quotes against original recordings/transcripts
- We don't translate or cross-check non-English coverage
- **openai.com/index/* returns HTTP 403 to all UA-based crawlers.** System cards and release notes hosted there are discovered by Tavily but cannot be fetched by `httpx`. Fixing this requires browser automation (Playwright) which is out of scope for this pipeline. Accepted structural gap; the system card URL is preserved in the candidate list for human inspection.

---

## 8. Tech Stack & Rationale

| Choice | Why |
|---|---|
| Python | Best ecosystem for LLM + HTTP + JSON work; team strength |
| Anthropic API (Claude) | Strong structured output, Sonnet/Haiku split fits cost-quality needs |
| Tavily | LLM-optimized search results (pre-cleaned), free tier covers dev |
| `httpx` (async) | Concurrent fetches without ceremony |
| `trafilatura` | Best-in-class news-article body extraction |
| `sentence-transformers` (local `all-MiniLM-L6-v2`) | Embedding for clustering — local, free, fast, no API quota |
| Pydantic | Schema as code; the data contract enforced everywhere |
| SQLite-backed cache | Zero-config, file-based, dev-friendly |
| Jinja2 + embedded CSS | Single-file HTML renderer — no build step, opens directly in browser |

We deliberately did **not** pick:

- A vector DB (overkill for ~50 facts per page)
- A workflow framework (LangChain / LlamaIndex — adds abstraction without removing real complexity at this scale)
- A frontend framework (we generate static HTML; rendering is a separate next phase)

---

## 9. What I'd Do With Another Week

In priority order:

1. **Independent verification UI layer** — a "claim provenance" hover panel showing the exact source excerpt the claim came from, so the reader can verify in one click. Closer to the Two-Layer Principle than any LLM self-check could be.
2. **Frontend polish** — currently the schema renders to functional but plain HTML. A serious second pass on typography, visual hierarchy, credibility-tier visual encoding, and responsive layout.
3. **Continuous refresh** — re-run Stage B on a schedule, diff against the prior `reconciled.json`, surface new/changed facts in a "what's new since X" rail. The schema already supports it; the runtime doesn't.
4. **Per-module quality thresholds** — currently any active module renders. A module should suppress itself if its content density is low (e.g. `reception` with only 1 quote isn't worth showing).
5. **Source diversity check** — flag pages where all sources trace back to a single press release (the "coordinated inauthentic" problem from §7.6).
6. **Evaluation harness** — a small set of input sentences with manually curated expected facts, used to regression-test extraction quality across model/prompt changes.
7. **Event boundary enforcement in Stage D** — the NVIDIA stress test (Q1 FY2027 earnings preview inputs returning both Q1 forward guidance facts and Q4 FY2026 reported results) exposed that when a single query captures two temporally adjacent events on the same entity, Stage D has no mechanism to separate them. The facts differ by source `published_at` date but the embedding model treats them as topically identical. A temporal-windowing pass before clustering — partitioning facts by source publication window — would resolve most of these cases without touching the clustering logic.
8. **Browser-based fetch for JS-rendered primary sources** — several high-value Tier 1 URLs (openai.com/index/*, Wikipedia, fandom wikis) return HTTP 403 to `httpx` because they require browser headers or cookie consent. An optional Playwright fallback specifically for Tier 1 sources that fail with 403 would close the most impactful coverage gap. For the GPT-5.5 run, this would have unlocked the official system card — currently the most credible source but unfetchable (see §7.6).
9. **Non-English source coverage for live events** — Stage B currently filters to English-only. For `live_event` types like Eurovision, the most reliable facts about individual contestants come from their home country's public broadcaster (NRK for Norway, SVT for Sweden, RAI for Italy). The English filter discards this primary-language reporting entirely. A per-event-type exception, gated on `event_type == "live_event"` and participant country detection from Stage A's `key_entities`, would meaningfully improve `participants` module coverage.
