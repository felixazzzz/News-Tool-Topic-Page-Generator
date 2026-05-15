"""Stress-test runner: Stage A → D for two inputs. Read-only w.r.t. stage code."""
from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv
from tavily import TavilyClient

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent / "outputs"

INPUTS = [
    "NVIDIA reported Q1 FY2027 earnings on May 28, 2026, beating analyst expectations on data center revenue.",
    "Eurovision 2026 is being held in Vienna from May 12 to May 16.",
]


def run_one(sentence: str, tavily: TavilyClient) -> dict:
    from src.stage_a_plan import run as stage_a
    from src.stage_b_retrieve import run as stage_b
    from src.stage_c_extract import run as stage_c
    from src.stage_d_reconcile import run as stage_d

    funnel = {"input": sentence}

    # ── Stage A ──────────────────────────────────────────────
    logger.info("[A] Planning: %s", sentence[:60])
    plan = stage_a(sentence)
    if plan is None:
        return {**funnel, "error": "Stage A returned None"}
    funnel["stage_a"] = {
        "normalized_title": plan.normalized_title,
        "event_type": plan.event_type,
        "temporal_status": plan.temporal_status,
        "key_date": plan.key_date,
        "primary_source_candidates": plan.primary_source_candidates,
        "safety_flag": plan.safety_flag,
    }

    # ── Stage B ──────────────────────────────────────────────
    logger.info("[B] Retrieving sources …")
    sources_output = stage_b(plan, sentence, tavily)
    if sources_output is None:
        return {**funnel, "error": "Stage B returned None"}
    funnel["stage_b"] = {
        "sources": len(sources_output.sources),
        "tier_dist": {},
    }
    from collections import Counter
    tier_c = Counter(s.credibility_tier for s in sources_output.sources)
    funnel["stage_b"]["tier_dist"] = dict(tier_c.most_common())

    # ── Stage C ──────────────────────────────────────────────
    logger.info("[C] Extracting facts …")
    facts_output = stage_c(sources_output, plan, sentence)
    if facts_output is None:
        return {**funnel, "error": "Stage C returned None"}
    funnel["stage_c"] = {"facts": len(facts_output.facts)}

    # ── Stage D ──────────────────────────────────────────────
    logger.info("[D] Reconciling …")
    reconciled = stage_d(facts_output, sources_output, plan, sentence)
    if reconciled is None:
        return {**funnel, "error": "Stage D returned None"}

    s = reconciled.stats
    funnel["stage_d"] = {
        "input_facts": s.input_facts,
        "output_facts": s.output_facts,
        "merged_clusters": s.merged_clusters,
        "llm_assisted_merges": s.llm_assisted_merges,
        "conflicts_detected": s.conflicts_detected,
    }

    cred_c = Counter(f.credibility for f in reconciled.facts)
    funnel["stage_d"]["credibility_dist"] = dict(cred_c.most_common())

    funnel["reconciled_facts"] = [
        {
            "fact_id": f.fact_id,
            "claim": f.claim,
            "category": f.category,
            "tier": f.tier,
            "credibility": f.credibility,
            "source_ids": f.source_ids,
            "date": f.date,
            "conflicts": [{"value": c.value, "source_ids": c.source_ids} for c in f.conflicts],
        }
        for f in reconciled.facts
    ]

    return funnel


def main():
    tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    results = {}

    for sentence in INPUTS:
        print(f"\n{'='*70}")
        print(f"INPUT: {sentence}")
        print('='*70)
        try:
            result = run_one(sentence, tavily)
        except Exception as exc:
            logger.exception("Pipeline crashed for: %s", sentence[:60])
            result = {"input": sentence, "error": str(exc)}
        results[sentence[:40]] = result

        # Print funnel
        if "error" in result:
            print(f"\nERROR: {result['error']}")
            continue

        print("\n── Funnel ─────────────────────────────")
        if "stage_b" in result:
            print(f"  Stage B sources : {result['stage_b']['sources']}")
            print(f"  Tier dist       : {result['stage_b']['tier_dist']}")
        if "stage_c" in result:
            print(f"  Stage C facts   : {result['stage_c']['facts']}")
        if "stage_d" in result:
            d = result["stage_d"]
            print(f"  Stage D in/out  : {d['input_facts']} → {d['output_facts']}")
            print(f"  Merged clusters : {d['merged_clusters']}")
            print(f"  LLM merges      : {d['llm_assisted_merges']}")
            print(f"  Conflicts       : {d['conflicts_detected']}")
            print(f"  Credibility     : {d['credibility_dist']}")

        print("\n── Reconciled facts ───────────────────")
        for f in result.get("reconciled_facts", []):
            conf_str = ""
            if f["conflicts"]:
                vals = [c["value"] for c in f["conflicts"]]
                conf_str = f"  ** CONFLICT: {vals}"
            print(f"  [{f['fact_id']}] [{f['credibility']}] [{f['category']}/{f['tier']}]")
            print(f"    {f['claim'][:120]}")
            print(f"    sources={f['source_ids']}  date={f['date']}{conf_str}")

    # Save full results
    out = OUTPUTS_DIR / "stress_test_results.json"
    out.write_text(json.dumps(results, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"\n\nFull results saved to {out}")


if __name__ == "__main__":
    main()
