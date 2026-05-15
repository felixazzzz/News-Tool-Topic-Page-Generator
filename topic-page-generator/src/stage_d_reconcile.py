from __future__ import annotations

import json
import logging
import re
from collections import defaultdict
from pathlib import Path
from pydantic import BaseModel, Field

import numpy as np
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

from .llm_client import LLMClient, HAIKU
from .schemas import (
    ConflictEntry, FactCredibility, FactsOutput, Plan,
    ReconciledFact, ReconciledOutput, ReconcileStats, SourcesOutput,
)

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

_EMBED_MODEL = "all-MiniLM-L6-v2"
_HIGH_SIM = 0.85     # cosine similarity: auto-merge
_LOW_SIM = 0.72      # below this: separate; [_LOW_SIM, _HIGH_SIM): LLM-assisted
_MAX_LLM_PAIRS = 40  # cap on LLM merge decisions per run
_LLM_BATCH = 5       # pairs per Haiku call

# Credibility tier order (lower = more credible)
_CRED_RANK: dict[str, int] = {
    "primary": 0, "primary_disputed": 1,
    "tier_1_media": 2, "tier_2_media": 3,
    "tier_3_media": 4, "low_quality": 5,
}


# ---------------------------------------------------------------------------
# Internal schema for LLM merge decisions
# ---------------------------------------------------------------------------

class _MergeBatch(BaseModel):
    decisions: list[bool] = Field(
        description=(
            "One boolean per pair, in order. "
            "True = A and B state the SAME underlying fact and should be merged. "
            "False = different facts, keep separate."
        )
    )


# ---------------------------------------------------------------------------
# Union-Find
# ---------------------------------------------------------------------------

class _UnionFind:
    def __init__(self, n: int) -> None:
        self.parent = list(range(n))

    def find(self, x: int) -> int:
        while self.parent[x] != x:
            self.parent[x] = self.parent[self.parent[x]]
            x = self.parent[x]
        return x

    def union(self, a: int, b: int) -> None:
        self.parent[self.find(a)] = self.find(b)

    def clusters(self) -> dict[int, list[int]]:
        groups: dict[int, list[int]] = defaultdict(list)
        for i in range(len(self.parent)):
            groups[self.find(i)].append(i)
        return dict(groups)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _best_fact_idx(members: list[int], facts: list, tier_map: dict[str, str]) -> int:
    """Pick representative: best credibility tier, then longest claim."""
    def rank(i: int):
        t = tier_map.get(facts[i].source_id, "tier_3_media")
        return (_CRED_RANK.get(t, 5), -len(facts[i].claim))
    return min(members, key=rank)


def _assign_credibility(
    source_ids: list[str],
    tier_map: dict[str, str],
    domain_map: dict[str, str],
) -> FactCredibility:
    tiers = [tier_map.get(sid, "tier_3_media") for sid in source_ids]
    domains = {domain_map.get(sid, "") for sid in source_ids if domain_map.get(sid)}

    if any(t in ("primary", "primary_disputed") for t in tiers):
        return "primary"
    if all(t == "low_quality" for t in tiers):
        return "low_credibility"
    if len(domains) >= 2:
        return "corroborated"
    return "single_source"


def _detect_conflicts(members: list[int], facts: list) -> list[ConflictEntry]:
    """Detect date-value conflicts within a cluster."""
    date_groups: dict[str, list[str]] = defaultdict(list)
    for i in members:
        d = facts[i].date
        if d:
            date_groups[d].append(facts[i].source_id)

    if len(date_groups) <= 1:
        return []

    return [
        ConflictEntry(value=date_val, source_ids=sorted(set(sids)))
        for date_val, sids in sorted(date_groups.items())
    ]


# ---------------------------------------------------------------------------
# LLM-assisted merge for ambiguous pairs
# ---------------------------------------------------------------------------

def _llm_merge_batch(
    batch: list[tuple[int, int, float]],
    facts: list,
    uf: _UnionFind,
    client: LLMClient,
) -> int:
    pair_lines = []
    for k, (i, j, sim) in enumerate(batch, start=1):
        pair_lines.append(
            f"Pair {k} (cos={sim:.2f}):\n"
            f"  A: {facts[i].claim}\n"
            f"  B: {facts[j].claim}"
        )

    user_msg = (
        "For each pair of factual claims, decide whether A and B describe the "
        "SAME underlying fact (same subject, same attribute, same event) "
        "and should be merged into one entry.\n"
        "Be conservative: merge only if the claims clearly refer to the same specific fact.\n\n"
        + "\n\n".join(pair_lines)
    )
    result = client.call_structured(
        messages=[{"role": "user", "content": user_msg}],
        model=HAIKU,
        schema_class=_MergeBatch,
        tool_description="Output one merge decision per pair in order.",
        system="You are a fact deduplication assistant. Merge only clearly identical claims.",
        max_tokens=256,
    )
    if not result:
        return 0
    merged = 0
    for (i, j, _), should_merge in zip(batch, result.decisions):
        if should_merge:
            uf.union(i, j)
            merged += 1
    return merged


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run(
    facts_output: FactsOutput,
    sources_output: SourcesOutput,
    plan: Plan,
    input_sentence: str,
    client: LLMClient | None = None,
) -> ReconciledOutput | None:
    if client is None:
        client = LLMClient()

    facts = facts_output.facts
    if not facts:
        logger.warning("Stage D received empty facts list.")
        return None

    tier_map = {s.id: s.credibility_tier for s in sources_output.sources}
    domain_map = {s.id: s.domain for s in sources_output.sources}

    # 1. Embed all claims
    logger.info("Loading embedding model %s …", _EMBED_MODEL)
    embed_model = SentenceTransformer(_EMBED_MODEL)
    logger.info("Embedding %d facts …", len(facts))
    embeddings = embed_model.encode(
        [f.claim for f in facts],
        batch_size=64,
        show_progress_bar=False,
        normalize_embeddings=True,
    )  # shape (N, 384), already L2-normalised

    # 2. Cosine similarity matrix (dot product on normalised vectors)
    sim_matrix: np.ndarray = embeddings @ embeddings.T  # (N, N)

    # 3. Auto-merge pairs with cos ≥ HIGH_SIM
    uf = _UnionFind(len(facts))
    for i in range(len(facts)):
        for j in range(i + 1, len(facts)):
            if sim_matrix[i, j] >= _HIGH_SIM:
                uf.union(i, j)

    # 4. LLM-assisted merges for same-category pairs in [LOW_SIM, HIGH_SIM)
    ambiguous = [
        (i, j, float(sim_matrix[i, j]))
        for i in range(len(facts))
        for j in range(i + 1, len(facts))
        if _LOW_SIM <= sim_matrix[i, j] < _HIGH_SIM
        and facts[i].category == facts[j].category
    ]
    ambiguous.sort(key=lambda x: -x[2])
    ambiguous = ambiguous[:_MAX_LLM_PAIRS]

    llm_merges = 0
    for batch_start in range(0, len(ambiguous), _LLM_BATCH):
        batch = ambiguous[batch_start : batch_start + _LLM_BATCH]
        llm_merges += _llm_merge_batch(batch, facts, uf, client)
    logger.info("LLM-assisted merges: %d / %d ambiguous pairs", llm_merges, len(ambiguous))

    # 5. Build ReconciledFacts from clusters
    clusters = uf.clusters()
    reconciled: list[ReconciledFact] = []
    merged_clusters = 0
    conflicts_detected = 0

    for root in sorted(clusters):
        members = clusters[root]
        rep_idx = _best_fact_idx(members, facts, tier_map)
        rep = facts[rep_idx]

        all_src_ids = sorted({facts[i].source_id for i in members})
        conflicts = _detect_conflicts(members, facts)
        credibility = _assign_credibility(all_src_ids, tier_map, domain_map)

        if len(members) > 1:
            merged_clusters += 1
        if conflicts:
            conflicts_detected += 1

        reconciled.append(ReconciledFact(
            fact_id=f"f_merged_{len(reconciled) + 1:03d}",
            claim=rep.claim,
            category=rep.category,
            tier=rep.tier,
            source_ids=all_src_ids,
            credibility=credibility,
            conflicts=conflicts,
            date=rep.date,
        ))

    stats = ReconcileStats(
        input_facts=len(facts),
        output_facts=len(reconciled),
        merged_clusters=merged_clusters,
        llm_assisted_merges=llm_merges,
        conflicts_detected=conflicts_detected,
    )
    output = ReconciledOutput(facts=reconciled, stats=stats)
    _save(output, input_sentence)
    return output


def _make_slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return "-".join(clean.split()[:6])


def _save(output: ReconciledOutput, input_sentence: str) -> None:
    slug = _make_slug(input_sentence)
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "reconciled.json"
    path.write_text(
        json.dumps(output.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Stage D → %s", path)


# ---------------------------------------------------------------------------
# __main__ — test harness
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    from collections import Counter
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")

    _INPUT = "OpenAI rolled out GPT-5.5 Instant as the default model in ChatGPT in May 2026."
    _SLUG = "openai-rolled-out-gpt55-instant-as"

    plan = Plan.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "plan.json").read_text(encoding="utf-8")
    )
    sources_output = SourcesOutput.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "sources.json").read_text(encoding="utf-8")
    )
    facts_output = FactsOutput.model_validate_json(
        (OUTPUTS_DIR / _SLUG / "facts.json").read_text(encoding="utf-8")
    )

    output = run(facts_output, sources_output, plan, _INPUT)

    if not output:
        print("No output.")
    else:
        s = output.stats
        print(f"\n=== Reconcile stats ===")
        print(f"  {s.input_facts} facts in  →  {s.output_facts} merged facts out")
        print(f"  merged clusters:      {s.merged_clusters}")
        print(f"  LLM-assisted merges:  {s.llm_assisted_merges}")
        print(f"  conflicts detected:   {s.conflicts_detected}")

        cred_dist = Counter(f.credibility for f in output.facts)
        print(f"\n=== Credibility distribution ===")
        for cred, n in [
            ("primary", cred_dist["primary"]),
            ("corroborated", cred_dist["corroborated"]),
            ("single_source", cred_dist["single_source"]),
            ("low_credibility", cred_dist["low_credibility"]),
        ]:
            print(f"  {cred:16s}  {n:3d}")

        cat_dist = Counter(f.category for f in output.facts)
        print(f"\n=== Category distribution (post-merge) ===")
        for cat, n in cat_dist.most_common():
            print(f"  {cat:20s}  {n:3d}")

        # Interesting merges: clusters that absorbed the most facts
        multi = sorted(
            [f for f in output.facts if len(f.source_ids) > 1],
            key=lambda f: -len(f.source_ids),
        )
        print(f"\n=== Top 8 merged clusters (most sources) ===")
        for f in multi[:8]:
            print(f"  [{f.fact_id}] [{f.credibility}] sources={f.source_ids}")
            print(f"    {f.claim[:120]}")

        # Conflicts
        conflicts = [f for f in output.facts if f.conflicts]
        print(f"\n=== Conflicts ({len(conflicts)}) ===")
        for f in conflicts[:5]:
            print(f"  [{f.fact_id}] {f.claim[:80]}")
            for c in f.conflicts:
                print(f"    value={c.value!r}  sources={c.source_ids}")
