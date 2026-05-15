# Topic Page Generator

A five-stage LLM pipeline that takes one sentence describing a news event and produces a fully sourced, citation-tracked HTML topic page.

For architecture and design rationale, see [`DESIGN.md`](../DESIGN.md).

---

## Operations Guide

### 1. Setup

```bash
python -m venv .venv

.venv\Scripts\activate        # Windows
# source .venv/bin/activate   # macOS / Linux

pip install -r requirements.txt

cp .env.example .env
# Edit .env and fill in:
#   ANTHROPIC_API_KEY=...
#   TAVILY_API_KEY=...
```

---

### 2. Generate a Page

**Step 1 — run the pipeline (Stages A through E):**

```bash
python -m src.pipeline "Your one-sentence event description here."
```

**Step 2 — render to HTML:**

```bash
python -m src.renderer outputs/<slug>
```

The slug is the first six words of your input sentence, lowercased and hyphenated. For example:

```
"The 2026 FIFA World Cup kicks off at Estadio Azteca on June 11, 2026."
→ outputs/the-2026-fifa-world-cup-kicks/
```

Open `outputs/<slug>/page.html` in any browser. No server required.

---

### 3. Output Location

```
outputs/<slug>/
└── page.html          ← the deliverable; open directly in a browser
```

The pipeline also saves intermediate stage files locally (used as checkpoints for re-running individual stages):

```
outputs/<slug>/
├── plan.json          # Stage A: event classification, search queries
├── sources.json       # Stage B: retrieved sources and full page text
├── facts.json         # Stage C: extracted facts per source
├── reconciled.json    # Stage D: deduplicated, conflict-tagged facts
├── page_schema.json   # Stage E: structured page content (input to renderer)
└── page.html          # Final output
```

Only `page.html` is tracked in git. All JSON files are local only.

---

### 4. Re-run from a Stage

All Tavily searches, HTTP fetches, and LLM calls are cached in `cache.db`. To re-run a specific stage, delete its cache entries and re-run the full pipeline — earlier stages complete instantly from cache.

**Re-run Stage E only** (e.g., after changing the synthesis prompt):

```bash
python -c "
import sqlite3, json
conn = sqlite3.connect('cache.db')
cur = conn.cursor()
cur.execute(\"SELECT key, value FROM cache WHERE key LIKE 'llm:%'\")
for key, val in cur.fetchall():
    if 'hero' in json.loads(val) and 'timeline' in json.loads(val):
        cur.execute('DELETE FROM cache WHERE key=?', (key,))
conn.commit()
conn.close()
"
python -m src.pipeline "Your original input sentence."
python -m src.renderer outputs/<slug>
```

**Re-run Stage B only** (re-fetch all source pages):

```bash
python -c "
import sqlite3
conn = sqlite3.connect('cache.db')
conn.execute(\"DELETE FROM cache WHERE key LIKE 'fetch:%'\")
conn.commit(); conn.close()
"
python -m src.pipeline "Your original input sentence."
python -m src.renderer outputs/<slug>
```

---

### 5. Cache Management

All caches live in `cache.db` (SQLite, in `topic-page-generator/`).

| Goal | Command |
|---|---|
| Clear everything and start fully fresh | `del cache.db` (Windows) / `rm cache.db` (macOS/Linux) |
| Re-fetch all web pages (keep search + LLM cache) | `sqlite3 cache.db "DELETE FROM cache WHERE key LIKE 'fetch:%'"` |
| Re-run all LLM calls (keep search + fetch cache) | `sqlite3 cache.db "DELETE FROM cache WHERE key LIKE 'llm:%'"` |
| Re-run all search queries | `sqlite3 cache.db "DELETE FROM cache WHERE key LIKE 'search:%'"` |

---

### 6. Environment Variables

| Variable | Purpose |
|---|---|
| `ANTHROPIC_API_KEY` | Claude Sonnet (Stages A, E) and Haiku (Stage C) |
| `TAVILY_API_KEY` | Web search (Stage B) |
