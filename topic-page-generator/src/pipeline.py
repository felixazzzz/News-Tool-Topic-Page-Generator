"""
CLI orchestrator: runs Stage A → E for a single input sentence.

Usage:
    python -m src.pipeline "<one-sentence event description>"
"""
from __future__ import annotations

import os
import re
import sys
import time
import logging
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"


def _slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return "-".join(clean.split()[:6])


def run(sentence: str) -> None:
    from tavily import TavilyClient
    from .stage_a_plan import run as stage_a
    from .stage_b_retrieve import run as stage_b
    from .stage_c_extract import run as stage_c
    from .stage_d_reconcile import run as stage_d
    from .stage_e_synthesize import run as stage_e

    tavily = TavilyClient(api_key=os.environ["TAVILY_API_KEY"])
    timings: dict[str, float] = {}

    print(f"\n{'='*72}")
    print(f"INPUT: {sentence}")
    print(f"{'='*72}\n")

    # ── Stage A ──────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    print("[A] Planning …")
    try:
        plan = stage_a(sentence)
    except Exception as exc:
        logger.exception("Stage A crashed")
        print(f"\nERROR  Stage A crashed: {exc}")
        sys.exit(1)
    timings["A"] = time.perf_counter() - t0

    if plan is None:
        print("\nERROR  Stage A returned None — check logs for detail.")
        sys.exit(1)
    if plan.safety_flag:
        print(f"\nERROR  Stage A safety flag: {plan.safety_note}")
        sys.exit(1)
    print(f"       event_type={plan.event_type!r}  temporal_status={plan.temporal_status!r}")
    print(f"       normalized_title={plan.normalized_title!r}")

    # ── Stage B ──────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    print("[B] Retrieving sources …")
    try:
        sources_output = stage_b(plan, sentence, tavily)
    except Exception as exc:
        logger.exception("Stage B crashed")
        print(f"\nERROR  Stage B crashed: {exc}")
        sys.exit(1)
    timings["B"] = time.perf_counter() - t0

    if sources_output is None:
        print("\nERROR  Stage B returned None — no sources retrieved.")
        sys.exit(1)
    print(f"       {len(sources_output.sources)} sources retrieved")

    # ── Stage C ──────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    print("[C] Extracting facts …")
    try:
        facts_output = stage_c(sources_output, plan, sentence)
    except Exception as exc:
        logger.exception("Stage C crashed")
        print(f"\nERROR  Stage C crashed: {exc}")
        sys.exit(1)
    timings["C"] = time.perf_counter() - t0

    if facts_output is None:
        print("\nERROR  Stage C returned None — no facts extracted.")
        sys.exit(1)
    print(f"       {len(facts_output.facts)} facts extracted")

    # ── Stage D ──────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    print("[D] Reconciling …")
    try:
        reconciled_output = stage_d(facts_output, sources_output, plan, sentence)
    except Exception as exc:
        logger.exception("Stage D crashed")
        print(f"\nERROR  Stage D crashed: {exc}")
        sys.exit(1)
    timings["D"] = time.perf_counter() - t0

    if reconciled_output is None:
        print("\nERROR  Stage D returned None.")
        sys.exit(1)
    s = reconciled_output.stats
    print(
        f"       {s.input_facts} → {s.output_facts} facts  "
        f"({s.merged_clusters} merged clusters, {s.conflicts_detected} conflicts)"
    )

    # ── Stage E ──────────────────────────────────────────────────────────────
    t0 = time.perf_counter()
    print("[E] Synthesizing page …")
    try:
        page = stage_e(reconciled_output, sources_output, plan, sentence)
    except Exception as exc:
        logger.exception("Stage E crashed")
        print(f"\nERROR  Stage E crashed: {exc}")
        sys.exit(1)
    timings["E"] = time.perf_counter() - t0

    if page is None:
        print("\nERROR  Stage E returned None.")
        sys.exit(1)

    # ── Summary ──────────────────────────────────────────────────────────────
    total = sum(timings.values())
    print(f"\n{'='*72}")
    print(f"SUMMARY  {plan.normalized_title}")
    print(f"{'='*72}")
    print(f"  Stage A  {timings['A']:6.1f}s  plan ready")
    print(f"  Stage B  {timings['B']:6.1f}s  {len(sources_output.sources)} sources")
    print(f"  Stage C  {timings['C']:6.1f}s  {len(facts_output.facts)} facts extracted")
    print(f"  Stage D  {timings['D']:6.1f}s  {s.input_facts} -> {s.output_facts} reconciled facts")
    print(f"  Stage E  {timings['E']:6.1f}s  page schema written")
    print(f"  TOTAL    {total:6.1f}s\n")

    m = page.modules
    module_pairs = [
        ("version_comparison", m.version_comparison),
        ("reception",          m.reception),
        ("schedule",           m.schedule),
        ("participants",       m.participants),
        ("live_status",        m.live_status),
        ("background",         m.background),
    ]
    active_names   = [n for n, mod in module_pairs if mod.active]
    inactive_names = [n for n, mod in module_pairs if not mod.active]
    print(f"  Active modules   : {active_names}")
    print(f"  Inactive modules : {inactive_names}")

    # File inventory
    out_dir = OUTPUTS_DIR / _slug(sentence)
    print(f"\n  Output dir: {out_dir}")
    for fname in ["plan.json", "sources.json", "facts.json", "reconciled.json", "page_schema.json"]:
        fpath = out_dir / fname
        if fpath.exists():
            print(f"    {fname:26s}  {fpath.stat().st_size:>9,} bytes")
        else:
            print(f"    {fname:26s}  MISSING")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print('Usage: python -m src.pipeline "<one-sentence event description>"')
        sys.exit(1)
    run(sys.argv[1])
