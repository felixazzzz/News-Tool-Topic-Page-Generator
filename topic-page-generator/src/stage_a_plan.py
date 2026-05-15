from __future__ import annotations

import json
import logging
import re
import sys
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

from .llm_client import LLMClient, SONNET
from .schemas import Plan

load_dotenv()

logger = logging.getLogger(__name__)

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

_SYSTEM = """\
You are the planning stage of a news topic-page generator. Given a one-sentence \
event description, produce a structured research plan that downstream stages will \
use to retrieve, extract, and synthesize a publishable topic page.\
"""


def run(input_sentence: str, client: LLMClient | None = None) -> Plan | None:
    if client is None:
        client = LLMClient()

    today = date.today().isoformat()

    user_msg = f"""\
Input sentence: "{input_sentence}"

Today's date: {today}

Produce a research plan for building a topic page about this event.

Constraints:
- Use today's date ({today}) to determine temporal_status accurately.
- Do not invent entities or facts not present in or directly implied by the input.
- search_queries must be diverse: vary phrasing, include at least one site:-scoped \
query targeting a primary domain and at least one broad news query.
- primary_source_candidates must only contain domains the EVENT SUBJECT itself \
controls (e.g. openai.com for an OpenAI event, fifa.com for a FIFA event). \
News outlets, media sites, and third-party coverage domains (TechCrunch, The Verge, \
BBC, etc.) must NOT appear here — they are found via search automatically.\
"""

    plan = client.call_structured(
        messages=[{"role": "user", "content": user_msg}],
        model=SONNET,
        schema_class=Plan,
        tool_description="Output the structured event research plan.",
        system=_SYSTEM,
    )

    if plan is None:
        logger.error("Stage A produced no plan for: %s", input_sentence)
        return None

    _save(plan, input_sentence)
    return plan


def _save(plan: Plan, input_sentence: str) -> None:
    slug = _make_slug(input_sentence)
    out_dir = OUTPUTS_DIR / slug
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "plan.json"
    path.write_text(
        json.dumps(plan.model_dump(), indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    logger.info("Stage A → %s", path)


def _make_slug(text: str) -> str:
    clean = re.sub(r"[^a-z0-9\s]", "", text.lower())
    return "-".join(clean.split()[:6])


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    if len(sys.argv) < 2:
        print("Usage: python -m src.stage_a_plan '<input sentence>'")
        sys.exit(1)
    result = run(sys.argv[1])
    if result:
        print(json.dumps(result.model_dump(), indent=2, ensure_ascii=False))
