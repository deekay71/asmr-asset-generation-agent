# Shine It Asset Agent — V2

AI asset-generation pipeline for **single-item cleaning games**.
Takes a level design + art-style reference, produces production-ready transparent
PNGs for every state in an item's cleaning chain — chain states, sub-parts,
trash overlays, tools, style variants, and scene backgrounds.

**Two canonical examples included** so you can run something the moment you set up:
- **Level 5 — Plushie / Teddy Bear** — single item, 7-state chain + 3 trash overlays
- **Level 6 — Air Conditioner** — complex item, 7-state chain + 20 sub-parts + 2 special tools + 3 style variants + textured wall background

## What's New in V2 (vs V1)

V1 worked but every fix required inline Python scripts. V2 makes everything CLI:

| Feature | V1 | V2 |
|---|---|---|
| Regen one state without redoing the chain | inline script | `--state 04_foam` |
| Regen one sub-part | inline script | `--sprite cover_dusty` |
| Regen one tool | edit manifest + run | `--tool brush_A --force` |
| Preview cost before spending | manual math | `--dry-run` |
| Track lifetime cost | manual | `cost_log.jsonl` |
| Multi-anchor I2I for sprites | only on chain | `sources: ["style_ref", "chain:00_..."]` |
| Partial promote | edit files manually | `--only-approved` (via review HTML) |
| Schema validation | runtime crash | `phase 2` lints, fails fast |
| Sub-part bg removal | hybrid (kept dust fringes) | local rembg (clean cutouts) |
| Scene background generation | not supported | `backgrounds` section |
| Tool orientation rule | inconsistent | locked diagonal side-profile |
| B-tier alternative tools | not supported | first-class category |

## Quick Start (3 minutes)

```bash
unzip shine_it_asset_agent_v2.zip
cd shine_it_asset_agent_v2

# 1. Install Python deps
pip install -r pipeline/requirements.txt

# 2. Add your Fal.ai key
cp .env.example .env
# Edit .env, paste your FAL_KEY from https://fal.ai/dashboard/keys

# 3. Smoke-test the package (free)
python pipeline/shine_it_pipeline.py --level 5 --phase 2
python pipeline/shine_it_pipeline.py --level 6 --phase 2
# Both should print: "[OK] items_config.json valid — ... All sources resolve."

# 4. See what a phase WOULD cost (free):
python pipeline/shine_it_pipeline.py --level 6 --phase 3 --dry-run

# 5. Generate something for real (only when ready):
python pipeline/shine_it_pipeline.py --level 6 --phase 3 --state 04_coil_foamed --yes
# That's a single chain state — costs $0.03
```

**👉 Read [WORKFLOW.md](WORKFLOW.md) BEFORE running anything that spends money.** It walks through every phase, where you (the human) need to decide before continuing, and which CLI flags save you the most when iterating.

## Package Layout

```
shine_it_asset_agent_v2/
├── README.md                            ← you are here
├── SETUP.md                             ← dependencies, env, fal credit
├── WORKFLOW.md                          ← ★ phase walkthrough + human gates ★
├── BEST_PRACTICES.md                    ← chain design lessons learned
├── CHANGELOG.md                         ← V1 → V2 changes
├── docs/
│   └── TOOL_RULES.md                    ← tool sprite orientation rule
├── .env.example
│
├── pipeline/
│   ├── shine_it_pipeline.py             ← V2 CLI orchestrator
│   ├── fal_helper.py                    ← Fal.ai client + rembg + cropping
│   └── requirements.txt
│
├── references/
│   ├── style_anchor_compact.png         ← canonical I2I style reference
│   ├── tool_orientation/                ← reference images for tool orientation
│   │   ├── co_trang_diem 1.png          (powder brush — bristles into frame)
│   │   ├── nhip 1.png                   (tweezers — tips into frame)
│   │   ├── khan_lau 1.png / khan_nano 1.png  (cloths)
│   │   └── tamper 1.png                 (tamper)
│   └── gdd/
│       └── level_design.pdf             ← original 6-level GDD
│
└── projects/
    ├── level_05_plushie/
    │   ├── items_config.json            ← canonical small-scope example
    │   ├── final/                       ← 10 production PNGs
    │   └── approved/                    (target for Phase 7)
    │
    ├── level_06_aircon/
    │   ├── items_config.json            ← canonical complex example (30 assets)
    │   ├── final/                       ← 30 production PNGs
    │   ├── approved/                    (target for Phase 7)
    │   └── staging/                     ← anchor preserved so chain regen works
    │
    └── tools/
        ├── tools_manifest.json          ← shared tool library registry
        └── final/                       ← 14 production tool sprites
```

## Two Canonical Examples — What to Study

### Level 5 — Plushie (`projects/level_05_plushie/`)
Simplest possible level: one item, one chain, three independent trash overlays. Read this first if you're new to the schema. Use it as the template for any simple "single item, cleaning steps" level (shoe, jewelry, kettle, etc.).

### Level 6 — AC (`projects/level_06_aircon/`)
Maximally complex example: chain + sub-parts that reference chain states via `source:` field + style variants + textured wall background + special-purpose tools (plastic bags extracted from a specific chain state). Read this when you need to design something that's assembled from parts or has more than one cleaning surface.

## Core Rules (locked in V2)

- **Always I2I.** T2I drifts off-style. Use `source:` or `sources:` to provide visual context.
- **Multi-anchor I2I for sprites that need both style AND content reference.** Pass `sources: ["style_ref", "chain:00_closed_dusty"]`.
- **No half-states.** Each chain state is a clean snapshot — never an in-progress transition.
- **Dirt should be DRAMATIC.** "Mildly dirty" reads as "nothing to clean."
- **Tools point away — diagonal side profile.** See `docs/TOOL_RULES.md`.
- **Trash items are SEPARATE sprites.** Don't bake them into the chain.
- **Backgrounds are opaque scene plates.** No rembg, drawn behind everything.

## Approximate Costs

| Action | Cost |
|---|---|
| Anchor exploration (FLUX + NB-2) | $0.08 |
| Chain state | $0.03 each |
| Sub-part sprite | $0.03 each |
| Tool sprite | $0.03 each |
| Background plate | $0.03 each |
| Full new level (~16 assets) | ~$0.50 |
| With safe retry buffer | ~$1.00 |
| Level 6 (30+ assets, expensive variant) | ~$2.50–3.00 |

## When Things Go Wrong

See `BEST_PRACTICES.md` → "When Things Go Wrong" for the troubleshooting table. Most common: identity drift across chain (tighten `color_constant`), warped subparts (force "perfectly flat rectangular" in prompt), bg fringe (rembg algorithm choice in Phase 5).

## Credits

Built on `art-asset-producer` pipeline. Uses Fal.ai (Nano-Banana 2 Edit, FLUX Pro 1.1), rembg (U2Net), Pillow. Style reference adapted from premium product-photography render references.

Total V1 → V2 dev: 1 session. Most of the friction-fixing happened by writing actual levels and watching them break.
