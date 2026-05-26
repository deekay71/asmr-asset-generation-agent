# Shine It Asset Agent — V6

AI asset-generation pipeline for **single-item ASMR cleaning games**.
GDD → structured prompts → I2I generation → review → learn → ship.

V6's new headline feature: **a self-improving prompt agent** that gets smarter every time you give feedback.

## What V6 Adds

| Feature | What it does |
|---|---|
| **`prompt_agent/`** | Slot-filled template per `step_type` (foam, scrub, dust, stain, etc.). Replaces V3/V4/V5's free-form envelope wrapper. Tight, structured prompts (~600 chars vs ~3,500). |
| **`step_patterns.json` learning library** | 13 reusable patterns with `best_practices`, `forbid`, and `candidates` arrays. Permanent rules accumulate from feedback. |
| **`--learn` CLI flag** | After a review session (`regen_queue.json` saved), runs Gemini Flash text-only to distil comments into structured rules, tracks candidates, promotes to permanent after 2nd recurrence. |
| **Approve / Reject / Regen-with-comment HTML** | Per-asset tri-state radio + free-text comment input. Saves both `approved_ids.json` (for Phase 7 promote) and `regen_queue.json` (for `--learn` + next-regen prompts). |
| **"Rules learned" banner** | Review HTML shows a banner when the agent has promoted rules since your last visit. |
| **V5 Backend abstraction** | Vertex Flash default (`gemini-2.5-flash-image`); Fal NB-Pro available for hero shots. |
| **V4 Sub-flow composite anchor** | New phase 1b for items that open up to reveal detached parts (AC filters, keyboard keycaps). |
| **V3+ schema fields** | `chain_consistency_contract`, `asmr_framing`, `style_mode`, `containment_rule` — all optional, auto-injected. |
| **V2 features still work** | Per-asset I2I via `source`, multi-anchor `sources: [...]`, `--state` / `--sprite` / `--tool` selectors, `--dry-run`, `--yes`, partial-promote via `--only-approved`. |

## Quick Start

```bash
unzip shine_it_asset_agent_v6.zip
cd shine_it_asset_agent_v6
pip install -r pipeline/requirements.txt

# Add your Fal key (for fal_nb2 / fal_nb_pro backends):
cp .env.example .env
# edit .env: FAL_KEY=...

# (Optional) Add a Google service-account JSON for Vertex Flash backend:
cp /path/to/gemini_service_account.json .

# Smoke test (free):
python pipeline/shine_it_pipeline.py --level 5 --phase 2
python pipeline/shine_it_pipeline.py --level 6 --phase 2
python pipeline/shine_it_pipeline.py --level 7 --phase 2
python pipeline/shine_it_pipeline.py --level 8 --phase 2

# Cost preview (free):
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --dry-run

# Generate for real (Vertex Flash default):
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --yes

# Learning pass after review (free except for ~$0.001/feedback distillation):
python pipeline/shine_it_pipeline.py --level 7 --learn
```

## Canonical Levels Included

- **L5 Plushie** — V2 schema, simple chain
- **L6 AC** — V3 schema, sub-parts heavy, plastic-bag tools
- **L7 Keyboard** — **V6 schema** (canonical example for new levels), sub-flow with keycap_row composite
- **L8 Fireplace** — V3 schema, brick repair + mortar restoration

## Read These In Order

1. **`README.md`** ← you are here
2. **`docs/WORKFLOW.md`** — phase-by-phase walkthrough + V6 review/learn loop
3. **`docs/BEST_PRACTICES.md`** — what's worked across levels
4. **`docs/V6_CHANGELOG.md`** — V5 → V6 delta with rationale
5. **`docs/TOOL_RULES.md`** — tool sprite orientation rule
6. **Look at** `pipeline/step_patterns.json` — the agent's permanent memory. Adds rules every learn pass.

## V6 Architecture at a Glance

```
shine_it_asset_agent_v6/
├── README.md
├── docs/
│   ├── WORKFLOW.md
│   ├── BEST_PRACTICES.md
│   ├── V6_CHANGELOG.md
│   └── TOOL_RULES.md
├── pipeline/
│   ├── shine_it_pipeline.py    ← CLI orchestrator
│   ├── fal_helper.py           ← Fal.ai client + rembg + cropping
│   ├── i2i_backend.py          ← V5 backend abstraction (Fal NB-2 / Pro, Vertex Flash)
│   ├── prompt_agent/           ← V6 NEW
│   │   ├── __init__.py
│   │   ├── compose.py          ← assembles structured prompts per step_type
│   │   ├── learn.py            ← distils feedback into rules
│   │   ├── memory.py           ← step_patterns.json I/O
│   │   └── templates/          ← 13 step_type templates
│   ├── step_patterns.json      ← V6 NEW — the agent's permanent memory
│   └── requirements.txt
├── references/                 ← style refs, GDD PDFs, tool orientation examples
└── projects/
    ├── level_05_plushie/       ← V2 canonical
    ├── level_06_aircon/        ← V3 canonical
    ├── level_07_keyboard/      ← V6 canonical
    ├── level_08_fireplace/     ← V3 canonical
    └── tools/                  ← shared tool sprite library
```

## Backwards Compatibility

V6 is **fully backwards compatible**. V2/V3/V4/V5 configs (Levels 5, 6, 8) keep working unchanged. Only levels that opt in to V6 (by declaring `step_type` or `spec` blocks on assets) route through the new prompt agent.

To force V5-style envelope for a level even after migration, set `"pipeline_version": 5` at the top of items_config.json.
