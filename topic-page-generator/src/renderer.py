"""
Renders page_schema.json → page.html using an embedded Jinja2 template.

Usage:
    python -m src.renderer <slug-dir>
    python -m src.renderer outputs/openai-rolled-out-gpt55-instant-as
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from jinja2 import Environment, BaseLoader
from markupsafe import Markup

OUTPUTS_DIR = Path(__file__).parent.parent / "outputs"

# ---------------------------------------------------------------------------
# CSS
# ---------------------------------------------------------------------------

_CSS = """\
@import url('https://fonts.googleapis.com/css2?family=Source+Serif+Pro:ital,wght@0,300;0,400;0,600;0,700;1,400;1,600&family=JetBrains+Mono:wght@400;500&display=swap');

:root {
  --color-bg:          #fafaf7;
  --color-text:        #1a1a1a;
  --color-text-muted:  #6b6b6b;
  --color-border:      #e5e5e0;
  --color-accent:      #8b0000;
  --color-link:        #8b0000;
  --color-primary:     #1a5d1a;
  --color-corroborated:#1d4e89;
  --color-single:      #8a6d00;

  --font-serif: 'Source Serif Pro', Georgia, serif;
  --font-mono:  'JetBrains Mono', monospace;

  --max-width:          720px;
  --line-height-body:   1.65;
  --line-height-heading:1.2;

  --size-h1:     2.5rem;
  --size-h2:     1.75rem;
  --size-h3:     1.25rem;
  --size-body:   1.0625rem;
  --size-small:  0.875rem;
  --size-caption:0.75rem;

  --space-xs:  0.25rem;
  --space-sm:  0.5rem;
  --space-md:  1rem;
  --space-lg:  2rem;
  --space-xl:  3rem;
  --space-2xl: 4rem;
}

*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }

html { font-size: 16px; }

body {
  background: var(--color-bg);
  color: var(--color-text);
  font-family: var(--font-serif);
  font-size: var(--size-body);
  line-height: var(--line-height-body);
}

a { color: var(--color-link); text-decoration: underline; text-underline-offset: 2px; }
a:hover { text-decoration: none; }

/* ── Top bar ──────────────────────────────────────────────────────────── */
.topbar {
  border-bottom: 1px solid var(--color-border);
  padding: var(--space-sm) var(--space-lg);
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  font-size: var(--size-caption);
  font-family: var(--font-mono);
  color: var(--color-text-muted);
  letter-spacing: 0.04em;
}
.topbar-brand {
  font-weight: 500;
  color: var(--color-text);
  text-decoration: none;
  letter-spacing: 0.08em;
  text-transform: uppercase;
  font-size: var(--size-caption);
}

/* ── Layout wrapper ───────────────────────────────────────────────────── */
.page-wrap {
  max-width: var(--max-width);
  margin: 0 auto;
  padding: 0 var(--space-lg) var(--space-2xl);
}

/* ── Hero ─────────────────────────────────────────────────────────────── */
.hero {
  padding: var(--space-xl) 0 var(--space-lg);
  border-bottom: 1px solid var(--color-border);
}
.hero-eyebrow {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  letter-spacing: 0.12em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-md);
}
.hero-eyebrow .event-type {
  color: var(--color-accent);
  font-weight: 500;
}
.hero h1 {
  font-size: var(--size-h1);
  font-weight: 700;
  line-height: var(--line-height-heading);
  margin-bottom: var(--space-md);
  letter-spacing: -0.01em;
}
.hero-rule {
  width: 3rem;
  height: 3px;
  background: var(--color-accent);
  margin-bottom: var(--space-md);
}
.hero-summary {
  font-size: 1.125rem;
  line-height: 1.6;
  color: var(--color-text);
  margin-bottom: var(--space-md);
}
.hero-meta {
  font-size: var(--size-small);
  color: var(--color-text-muted);
  font-family: var(--font-mono);
}
.hero-meta span + span::before { content: " · "; }

/* ── Section headers ──────────────────────────────────────────────────── */
.section {
  margin-top: var(--space-xl);
}
.section-header {
  margin-bottom: var(--space-md);
}
.section-header h2 {
  font-size: var(--size-h2);
  font-weight: 700;
  line-height: var(--line-height-heading);
  letter-spacing: -0.01em;
  margin-bottom: var(--space-xs);
}
.section-divider {
  border: none;
  border-top: 1px solid var(--color-border);
  margin: 0;
}

/* ── At-a-glance 5W ──────────────────────────────────────────────────── */
.five-w-grid {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: var(--space-md) var(--space-lg);
  margin-top: var(--space-md);
}
.five-w-item {}
.five-w-label {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-xs);
}
.five-w-text {
  font-size: var(--size-body);
  line-height: var(--line-height-body);
}

/* ── Timeline ─────────────────────────────────────────────────────────── */
.timeline {
  margin-top: var(--space-md);
  position: relative;
}
.timeline-item {
  display: grid;
  grid-template-columns: 6rem 1.25rem 1fr;
  gap: 0 var(--space-sm);
  margin-bottom: var(--space-md);
  position: relative;
}
.timeline-date {
  font-family: var(--font-mono);
  font-size: var(--size-small);
  color: var(--color-text-muted);
  padding-top: 2px;
  text-align: right;
  letter-spacing: -0.02em;
}
.timeline-spine {
  display: flex;
  flex-direction: column;
  align-items: center;
}
.timeline-dot {
  width: 8px;
  height: 8px;
  border-radius: 50%;
  border: 2px solid var(--color-accent);
  background: var(--color-bg);
  flex-shrink: 0;
  margin-top: 4px;
}
.timeline-line {
  width: 1px;
  background: var(--color-border);
  flex: 1;
  margin-top: 2px;
}
.timeline-item:last-child .timeline-line { display: none; }
.timeline-content {}
.timeline-title {
  font-weight: 600;
  font-size: var(--size-body);
  margin-bottom: var(--space-xs);
}
.timeline-desc {
  font-size: var(--size-small);
  color: var(--color-text);
  line-height: var(--line-height-body);
}

/* ── Credibility dot + label ──────────────────────────────────────────── */
.cred {
  font-family: var(--font-mono);
  font-size: 0.7rem;
  white-space: nowrap;
  margin-left: var(--space-sm);
  font-weight: 400;
}
.cred-primary     { color: var(--color-primary); }
.cred-corroborated{ color: var(--color-corroborated); }
.cred-single      { color: var(--color-single); }
.cred-low         { color: var(--color-text-muted); }

/* ── Source superscripts ──────────────────────────────────────────────── */
sup.src-ref {
  font-family: var(--font-mono);
  font-size: 0.65rem;
  color: var(--color-text-muted);
  letter-spacing: -0.02em;
  margin-left: 1px;
}
sup.src-ref a {
  color: inherit;
  text-decoration: none;
}
sup.src-ref a:hover {
  color: var(--color-accent);
  text-decoration: underline;
}

/* ── Version comparison table ────────────────────────────────────────── */
.comparison-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--size-small);
  margin-top: var(--space-md);
}
.comparison-table th {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-align: left;
  padding: var(--space-sm) var(--space-md);
  background: transparent;
  border-bottom: 2px solid var(--color-border);
  color: var(--color-text-muted);
}
.comparison-table td {
  padding: var(--space-sm) var(--space-md);
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
  line-height: 1.5;
}
.comparison-table tr:last-child td { border-bottom: none; }
.comparison-table .col-attr {
  font-weight: 600;
  width: 38%;
  color: var(--color-text);
}
.comparison-table .col-prev {
  width: 28%;
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  font-size: var(--size-caption);
}
.comparison-table .col-new {
  width: 28%;
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  color: var(--color-text);
}
.comparison-table .col-src {
  width: 6%;
  text-align: right;
}

/* ── Reception ────────────────────────────────────────────────────────── */
.reception-group {
  margin-top: var(--space-md);
}
.reception-group-label {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  letter-spacing: 0.1em;
  text-transform: uppercase;
  color: var(--color-text-muted);
  margin-bottom: var(--space-sm);
}
.reception-item {
  border-left: 4px solid var(--color-text-muted);
  padding: var(--space-sm) var(--space-md);
  margin-bottom: var(--space-md);
}
.reception-item.positive { border-left-color: var(--color-primary); }
.reception-item.critical { border-left-color: var(--color-accent); }
.reception-group-label.positive { color: var(--color-primary); }
.reception-group-label.critical { color: var(--color-accent); }
.reception-quote {
  font-size: var(--size-body);
  line-height: var(--line-height-body);
  font-style: italic;
  margin-bottom: var(--space-xs);
}
.reception-attribution {
  font-size: var(--size-small);
  color: var(--color-text-muted);
  font-family: var(--font-mono);
  letter-spacing: -0.01em;
}
.reception-attribution::before { content: "— "; }

/* ── Schedule table ──────────────────────────────────────────────────── */
.schedule-table {
  width: 100%;
  border-collapse: collapse;
  font-size: var(--size-small);
  margin-top: var(--space-md);
}
.schedule-table th {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  letter-spacing: 0.08em;
  text-transform: uppercase;
  text-align: left;
  padding: var(--space-sm) var(--space-md);
  border-bottom: 2px solid var(--color-border);
  color: var(--color-text-muted);
}
.schedule-table td {
  padding: var(--space-sm) var(--space-md);
  border-bottom: 1px solid var(--color-border);
  vertical-align: top;
  line-height: 1.5;
}
.schedule-table tr:last-child td { border-bottom: none; }
.schedule-table .col-date {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  width: 18%;
  white-space: nowrap;
}
.schedule-table .col-title { font-weight: 600; width: 55%; }
.schedule-table .col-venue { color: var(--color-text-muted); width: 21%; }
.schedule-table .col-src   { width: 6%; text-align: right; }

/* ── Participants chips ──────────────────────────────────────────────── */
.participants-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  margin-top: var(--space-md);
}
.participant-chip {
  border: 1px solid var(--color-border);
  padding: var(--space-xs) var(--space-sm);
  font-size: var(--size-small);
}
.participant-chip-name {
  font-weight: 600;
  margin-bottom: 1px;
}
.participant-chip-role {
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  font-family: var(--font-mono);
}

/* ── Live status ─────────────────────────────────────────────────────── */
.live-status-bar {
  display: flex;
  align-items: center;
  gap: var(--space-md);
  padding: var(--space-md);
  border: 1px solid var(--color-border);
  margin-top: var(--space-md);
  font-family: var(--font-mono);
  font-size: var(--size-small);
}
.live-dot {
  width: 10px;
  height: 10px;
  border-radius: 50%;
  background: var(--color-accent);
  flex-shrink: 0;
}
.live-status-text { font-weight: 500; }
.live-update { color: var(--color-text-muted); }

/* ── Background prose ────────────────────────────────────────────────── */
.background-prose p {
  font-size: var(--size-body);
  line-height: var(--line-height-body);
  margin-top: var(--space-md);
}
.background-prose p:first-child { margin-top: var(--space-md); }

/* ── Key entities chips ──────────────────────────────────────────────── */
.entities-grid {
  display: flex;
  flex-wrap: wrap;
  gap: var(--space-sm);
  margin-top: var(--space-md);
}
.entity-chip {
  border: 1px solid var(--color-border);
  padding: var(--space-xs) var(--space-sm);
  max-width: 340px;
}
.entity-chip-header {
  display: flex;
  align-items: baseline;
  gap: var(--space-sm);
  margin-bottom: 2px;
}
.entity-chip-name { font-weight: 600; font-size: var(--size-small); }
.entity-chip-type {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  letter-spacing: 0.06em;
  text-transform: uppercase;
}
.entity-chip-desc {
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  line-height: 1.5;
}

/* ── Sources list ────────────────────────────────────────────────────── */
.sources-list {
  list-style: none;
  margin-top: var(--space-md);
}
.sources-list li {
  display: grid;
  grid-template-columns: 2rem 1fr;
  gap: 0 var(--space-sm);
  padding: var(--space-md) 0;
  border-bottom: 1px solid var(--color-border);
  font-size: var(--size-small);
}
.sources-list li:last-child { border-bottom: none; }
.src-num {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  padding-top: 2px;
  text-align: right;
}
.src-title {
  font-weight: 600;
  margin-bottom: 2px;
}
.src-meta {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  color: var(--color-text-muted);
  margin-bottom: 2px;
}
.src-tier { display: inline; }
.src-url {
  font-family: var(--font-mono);
  font-size: var(--size-caption);
  word-break: break-all;
  color: var(--color-text-muted);
}
.src-url a { color: var(--color-link); }

/* ── Tier labels — primary source gets semantic green; media tiers muted ── */
.tier-primary      { color: var(--color-primary); }
.tier-tier1        { color: var(--color-text-muted); }
.tier-tier2        { color: var(--color-text-muted); }
.tier-tier3        { color: var(--color-text-muted); }
.tier-low          { color: var(--color-text-muted); }

/* ── Responsive ──────────────────────────────────────────────────────── */
@media (max-width: 600px) {
  .page-wrap { padding: 0 var(--space-md) var(--space-xl); }

  .hero h1 { font-size: 1.75rem; }
  .hero-summary { font-size: 1rem; }

  .five-w-grid { grid-template-columns: 1fr; }

  .timeline-item { grid-template-columns: 5rem 1.25rem 1fr; }
  .timeline-date { font-size: var(--size-caption); }

  .comparison-table th,
  .comparison-table td { padding: var(--space-xs) var(--space-sm); }
  .comparison-table .col-prev { display: none; }
  .comparison-table .col-attr { width: 55%; }
  .comparison-table .col-new  { width: 38%; }

  .section-header h2 { font-size: 1.375rem; }
}
"""

# ---------------------------------------------------------------------------
# Jinja2 template
# ---------------------------------------------------------------------------

_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{{ page.meta.title }}</title>
<style>
{{ css }}
</style>
</head>
<body>

<!-- Top bar -->
<header class="topbar">
  <span class="topbar-brand">Topic Page Generator</span>
  <span>Generated {{ page.meta.generated_at | fmt_date }} &nbsp;·&nbsp; v{{ page.meta.pipeline_version }}</span>
</header>

<div class="page-wrap">

<!-- ── Hero ─────────────────────────────────────────────────────────── -->
<section class="hero">
  <p class="hero-eyebrow">
    <span class="event-type">{{ page.meta.event_type | replace("_", " ") | upper }}</span>
    &nbsp;·&nbsp; {{ page.hero.key_date | upper }}
  </p>
  <h1>{{ page.hero.headline }}</h1>
  <div class="hero-rule"></div>
  <p class="hero-summary">{{ page.hero.summary }}</p>
  <p class="hero-meta">
    <span>{{ page.meta.generated_at | fmt_date }}</span>
    <span>Compiled from {{ page.sources | length }} sources</span>
  </p>
</section>

<!-- ── At-a-glance ──────────────────────────────────────────────────── -->
<section class="section">
  <div class="section-header">
    <h2>At a Glance</h2>
    <hr class="section-divider">
  </div>
  <div class="five-w-grid">
    {% for key in ["who","what","when","where","why"] %}
    {% set item = page.at_a_glance[key] %}
    <div class="five-w-item">
      <div class="five-w-label">{{ key | upper }}</div>
      <div class="five-w-text">{{ item.text }}{{ item.source_ids | srcref }}</div>
    </div>
    {% endfor %}
  </div>
</section>

<!-- ── Timeline ─────────────────────────────────────────────────────── -->
<section class="section">
  <div class="section-header">
    <h2>Timeline</h2>
    <hr class="section-divider">
  </div>
  <div class="timeline">
    {% for entry in page.timeline %}
    <div class="timeline-item">
      <div class="timeline-date">{{ entry.date }}</div>
      <div class="timeline-spine">
        <div class="timeline-dot"></div>
        <div class="timeline-line"></div>
      </div>
      <div class="timeline-content">
        <div class="timeline-title">
          {{ entry.title }}<span class="{{ entry.credibility | cred_class }}">● {{ entry.credibility | cred_label }}</span>{{ entry.source_ids | srcref }}
        </div>
        <div class="timeline-desc">{{ entry.description }}</div>
      </div>
    </div>
    {% endfor %}
  </div>
</section>

{% set m = page.modules %}

<!-- ── Version Comparison ────────────────────────────────────────────── -->
{% if m.version_comparison and m.version_comparison.active %}
<section class="section">
  <div class="section-header">
    <h2>What Changed</h2>
    <hr class="section-divider">
  </div>
  <table class="comparison-table">
    <thead>
      <tr>
        <th class="col-attr">Attribute</th>
        <th class="col-prev">Previous</th>
        <th class="col-new">New</th>
        <th class="col-src">Src</th>
      </tr>
    </thead>
    <tbody>
      {% for row in m.version_comparison.rows %}
      <tr>
        <td class="col-attr">{{ row.attribute }}</td>
        <td class="col-prev">{{ row.previous_value or "—" }}</td>
        <td class="col-new">{{ row.new_value }}</td>
        <td class="col-src">{{ row.source_ids | srcref }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

<!-- ── Reception ─────────────────────────────────────────────────────── -->
{% if m.reception and m.reception.active %}
<section class="section">
  <div class="section-header">
    <h2>Reception</h2>
    <hr class="section-divider">
  </div>
  {% if m.reception.positive %}
  <div class="reception-group">
    <div class="reception-group-label positive">● Positive</div>
    {% for item in m.reception.positive %}
    <div class="reception-item positive">
      <div class="reception-quote">"{{ item.quote }}"{{ item.source_ids | srcref }}</div>
      <div class="reception-attribution">{{ item.attribution }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
  {% if m.reception.critical %}
  <div class="reception-group">
    <div class="reception-group-label critical">○ Critical</div>
    {% for item in m.reception.critical %}
    <div class="reception-item critical">
      <div class="reception-quote">"{{ item.quote }}"{{ item.source_ids | srcref }}</div>
      <div class="reception-attribution">{{ item.attribution }}</div>
    </div>
    {% endfor %}
  </div>
  {% endif %}
</section>
{% endif %}

<!-- ── Schedule ──────────────────────────────────────────────────────── -->
{% if m.schedule and m.schedule.active %}
<section class="section">
  <div class="section-header">
    <h2>Schedule</h2>
    <hr class="section-divider">
  </div>
  {% set ns = namespace(has_venue=false) %}
  {% for entry in m.schedule.entries %}{% if entry.venue and entry.venue != "—" %}{% set ns.has_venue = true %}{% endif %}{% endfor %}
  <table class="schedule-table">
    <thead>
      <tr>
        <th class="col-date">Date</th>
        <th class="col-title">Event</th>
        {% if ns.has_venue %}<th class="col-venue">Venue</th>{% endif %}
        <th class="col-src">Src</th>
      </tr>
    </thead>
    <tbody>
      {% for entry in m.schedule.entries %}
      <tr>
        <td class="col-date">{{ entry.date or "TBC" }}</td>
        <td class="col-title">{{ entry.title }}</td>
        {% if ns.has_venue %}<td class="col-venue">{{ entry.venue or "—" }}</td>{% endif %}
        <td class="col-src">{{ entry.source_ids | srcref }}</td>
      </tr>
      {% endfor %}
    </tbody>
  </table>
</section>
{% endif %}

<!-- ── Participants ───────────────────────────────────────────────────── -->
{% if m.participants and m.participants.active %}
<section class="section">
  <div class="section-header">
    <h2>Participants</h2>
    <hr class="section-divider">
  </div>
  <div class="participants-grid">
    {% for p in m.participants.participants %}
    <div class="participant-chip">
      <div class="participant-chip-name">{{ p.name }}{{ p.source_ids | srcref }}</div>
      <div class="participant-chip-role">{{ p.role }}</div>
      {% if p.description %}
      <div class="entity-chip-desc">{{ p.description }}</div>
      {% endif %}
    </div>
    {% endfor %}
  </div>
</section>
{% endif %}

<!-- ── Live Status ───────────────────────────────────────────────────── -->
{% if m.live_status and m.live_status.active %}
<section class="section">
  <div class="section-header">
    <h2>Live Status</h2>
    <hr class="section-divider">
  </div>
  <div class="live-status-bar">
    <div class="live-dot"></div>
    <span class="live-status-text">{{ m.live_status.status }}</span>
    {% if m.live_status.last_update %}
    <span class="live-update">Updated {{ m.live_status.last_update }}</span>
    {% endif %}
    {{ (m.live_status.source_ids or []) | srcref }}
  </div>
</section>
{% endif %}

<!-- ── Background ────────────────────────────────────────────────────── -->
{% if m.background and m.background.active %}
<section class="section">
  <div class="section-header">
    <h2>Background</h2>
    <hr class="section-divider">
  </div>
  <div class="background-prose">
    {% for para in m.background.paragraphs %}
    <p>{{ para.text }}{{ para.source_ids | srcref }}</p>
    {% endfor %}
  </div>
</section>
{% endif %}

<!-- ── Key Entities ──────────────────────────────────────────────────── -->
{% if page.key_entities %}
<section class="section">
  <div class="section-header">
    <h2>Key Entities</h2>
    <hr class="section-divider">
  </div>
  <div class="entities-grid">
    {% for ent in page.key_entities %}
    <div class="entity-chip">
      <div class="entity-chip-header">
        <span class="entity-chip-name">{{ ent.name }}</span>
        <span class="entity-chip-type">{{ ent.type }}</span>
      </div>
      <div class="entity-chip-desc">{{ ent.description }}{{ (ent.source_ids or []) | srcref }}</div>
    </div>
    {% endfor %}
  </div>
</section>
{% endif %}

<!-- ── Sources ───────────────────────────────────────────────────────── -->
<section class="section">
  <div class="section-header">
    <h2>Sources</h2>
    <hr class="section-divider">
  </div>
  <ol class="sources-list">
    {% for src in page.sources %}
    <li id="src-{{ loop.index }}">
      <div class="src-num">{{ loop.index }}</div>
      <div>
        <div class="src-title">{{ src.title }}</div>
        <div class="src-meta">
          <span class="{{ src.credibility_tier | tier_class }} src-tier">● {{ src.credibility_tier | tier_label }}</span>
          &nbsp;·&nbsp; {{ src.domain }}
          {% if src.published_at %}&nbsp;·&nbsp; {{ src.published_at }}{% endif %}
        </div>
        <div class="src-url"><a href="{{ src.url }}" target="_blank" rel="noopener">{{ src.url }}</a></div>
      </div>
    </li>
    {% endfor %}
  </ol>
</section>

</div><!-- /page-wrap -->
</body>
</html>
"""

# ---------------------------------------------------------------------------
# Template filters
# ---------------------------------------------------------------------------

def _build_src_index(sources: list[dict]) -> dict[str, int]:
    return {s["id"]: i + 1 for i, s in enumerate(sources)}


def _make_env(src_index: dict[str, int]) -> Environment:
    env = Environment(loader=BaseLoader(), autoescape=True)

    def srcref(ids: list[str] | None) -> Markup:
        if not ids:
            return Markup("")
        nums = [str(src_index[i]) for i in ids if i in src_index]
        if not nums:
            return Markup("")
        return Markup("".join(
            f'<sup class="src-ref"><a href="#src-{n}">[{n}]</a></sup>'
            for n in nums
        ))

    def fmt_date(iso: str) -> str:
        try:
            from datetime import datetime, timezone
            dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
            return dt.strftime("%B %d, %Y %H:%M UTC")
        except Exception:
            return iso

    def cred_class(cred: str) -> str:
        return {
            "primary":       "cred cred-primary",
            "corroborated":  "cred cred-corroborated",
            "single_source": "cred cred-single",
            "low_credibility":"cred cred-low",
        }.get(cred, "cred cred-low")

    def cred_symbol(cred: str) -> str:
        return {
            "primary":        "●",
            "corroborated":   "●",
            "single_source":  "○",
            "low_credibility":"○",
        }.get(cred, "○")

    def cred_label(cred: str) -> str:
        return {
            "primary":        "primary",
            "corroborated":   "corroborated",
            "single_source":  "single source",
            "low_credibility":"low credibility",
        }.get(cred, cred)

    def tier_class(tier: str) -> str:
        return {
            "primary":          "tier-primary",
            "primary_disputed": "tier-primary",
            "tier_1_media":     "tier-tier1",
            "tier_2_media":     "tier-tier2",
            "tier_3_media":     "tier-tier3",
            "low_quality":      "tier-low",
        }.get(tier, "tier-low")

    def tier_label(tier: str) -> str:
        return {
            "primary":          "Primary source",
            "primary_disputed": "Primary (disputed)",
            "tier_1_media":     "Tier-1 media",
            "tier_2_media":     "Tier-2 media",
            "tier_3_media":     "Tier-3 media",
            "low_quality":      "Low quality",
        }.get(tier, tier)

    env.filters["srcref"]     = srcref
    env.filters["fmt_date"]   = fmt_date
    env.filters["cred_class"] = cred_class
    env.filters["cred_symbol"]= cred_symbol
    env.filters["cred_label"] = cred_label
    env.filters["tier_class"] = tier_class
    env.filters["tier_label"] = tier_label

    return env


# ---------------------------------------------------------------------------
# Main render
# ---------------------------------------------------------------------------

def render(slug_dir: Path) -> Path:
    schema_path = slug_dir / "page_schema.json"
    if not schema_path.exists():
        raise FileNotFoundError(f"page_schema.json not found in {slug_dir}")

    page = json.loads(schema_path.read_text(encoding="utf-8"))

    src_index = _build_src_index(page["sources"])
    env = _make_env(src_index)
    tmpl = env.from_string(_TEMPLATE)

    html = tmpl.render(page=_DotDict(page), css=Markup(_CSS))

    out_path = slug_dir / "page.html"
    out_path.write_text(html, encoding="utf-8")
    return out_path


class _DotDict:
    """Wrap a dict/list tree so Jinja2 templates can use dot-access."""
    def __init__(self, data):
        self._data = data

    def __getattr__(self, name: str):
        try:
            val = self._data[name]
        except (KeyError, TypeError):
            return None
        return _DotDict(val) if isinstance(val, dict) else (
            [_DotDict(v) if isinstance(v, dict) else v for v in val]
            if isinstance(val, list)
            else val
        )

    def __getitem__(self, key):
        val = self._data[key]
        return _DotDict(val) if isinstance(val, dict) else val

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        if isinstance(self._data, dict):
            return iter(self._data)
        return iter(
            _DotDict(v) if isinstance(v, dict) else v
            for v in self._data
        )

    def __bool__(self):
        return bool(self._data)


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m src.renderer <outputs/slug-dir>")
        sys.exit(1)

    target = Path(sys.argv[1])
    if not target.is_absolute():
        target = Path(__file__).parent.parent / target

    out = render(target)
    print(f"Rendered → {out}")
