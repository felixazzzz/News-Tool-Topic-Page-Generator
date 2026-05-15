# News Tool: Topic Page Generator

A five-stage LLM pipeline that turns a single sentence describing a news event into a fully sourced, citation-tracked HTML topic page — structured like a newsroom reference document, not a blog post.

Give it: `"The 2026 FIFA World Cup kicks off at Estadio Azteca on June 11, 2026."`
Get back: a publishable page with timeline, key entities, sourced 5W snapshot, and event-specific modules (schedule, participants, background, reception, and more).

## Quick Start

```bash
git clone https://github.com/felixazzzz/News-Tool-Topic-Page-Generator
cd News-Tool-Topic-Page-Generator/topic-page-generator
```

Then follow the **Operations Guide** in [`topic-page-generator/README.md`](topic-page-generator/README.md).

## System Design

Architecture decisions, pipeline stages, prompt engineering rationale, NLP design, and failure modes are documented in [`DESIGN.md`](DESIGN.md).

## Repository Structure

```
News-Tool-Topic-Page-Generator/
├── DESIGN.md                         # Full system architecture and design rationale
├── README.md
├── .gitignore
└── topic-page-generator/
    ├── README.md                     # Operations guide
    ├── .env.example
    ├── requirements.txt
    ├── src/
    │   ├── pipeline.py               # CLI entry point (runs all 5 stages)
    │   ├── stage_a_plan.py           # Event classification + search plan (Sonnet)
    │   ├── stage_b_retrieve.py       # Tiered web retrieval (Tavily + httpx)
    │   ├── stage_c_extract.py        # Fact extraction per source (Haiku, parallel)
    │   ├── stage_d_reconcile.py      # Embedding-based dedup + conflict detection
    │   ├── stage_e_synthesize.py     # Page schema synthesis (Sonnet)
    │   ├── renderer.py               # page_schema.json → page.html
    │   ├── schemas.py                # Pydantic models for all stage I/O
    │   ├── llm_client.py             # Anthropic API wrapper with caching
    │   ├── cache.py                  # SQLite-backed cache (search, fetch, LLM)
    │   └── domain_credibility.json
    └── outputs/
        ├── openai-rolled-out-gpt55-instant-as/
        │   └── page.html
        ├── eurovision-2026-is-being-held-in/
        │   └── page.html
        ├── the-2026-fifa-world-cup-kicks/
        │   └── page.html
        ├── a-62-magnitude-earthquake-struck-naples/
        │   └── page.html
        └── donald-trump-made-a-state-visit/
            └── page.html
```

Ignored from git: `.venv/`, `outputs/**` (except `page.html`), `cache.db`, `.env`.

## Contact

Questions or feedback: [felixzhang4027@gmail.com](mailto:felixzhang4027@gmail.com)

## License

MIT License — Copyright 2026 Felix Zhang
