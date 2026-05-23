# Changelog

## V2 (this package)

### New CLI flags

| Flag | Phases | Purpose |
|---|---|---|
| `--dry-run` | 3, 3b, 4, 7 | Print plan + cost estimate, no API calls. **Free.** |
| `--yes` | 3, 3b, 4 | Skip the interactive cost-confirmation prompt |
| `--state X` | 3 | Regen only matching chain state(s). Glob supported (`'foam_*'`) |
| `--sprite X` | 3b | Regen only matching sprite(s). Glob supported (`'cover_*'`) |
| `--tool X` | 4 | Regen only matching tool(s). Glob supported |
| `--force` | 4 | Regen tool even if cached in `tools_manifest.json` |
| `--only-approved` | 7 | Promote only IDs listed in `approved_ids.json` (written by review HTML) |

### New schema fields in `items_config.json`

| Field | Where | Purpose |
|---|---|---|
| `sources: [...]` | any sprite/tool | Multi-anchor I2I. Ordered list of source specs. Supersedes single-string `source:`. |
| `style_ref` | source spec value | Explicit "use the style reference image". Same as omitting `source:`. |
| `chain:STATE_ID` | source spec value | I2I from a chain state staged image |
| `subpart:SUBPART_ID` | source spec value | I2I from another sub-part's staged image |
| `tool:TOOL_ID` | source spec value | I2I from another tool's staged image |
| `backgrounds: [...]` | top-level | Opaque scene plates (wall/table/floor) — skip rembg in Phase 5 |
| `tool_orientation_rule` | top-level | Injected into every default-source tool prompt as a mandatory rule block |
| `style_variants: [...]` | top-level | Alternate appearance variants of the anchor (A/B/C colour palettes) |
| `escalation_rule`, `no_half_states_rule`, `color_constant` | top-level | Documentation-style fields the prompts cite |

Backwards-compatible: legacy single-string `source: "chain:..."` still works.

### New behaviour

- **Phase 2 is a real linter** — checks anchor presence, validates every `source:` resolves to a declared id or `style_ref`, lists all errors and exits before any API spend.
- **`cost_log.jsonl`** appended per NB-2 call with `{ts, phase, asset_id, model, cost, success}`. Per-level.
- **Review HTML (Phase 6)** now has:
  - One checkbox per asset card
  - Sticky toolbar with "Save approvals", Approve all, Unapprove all, live count
  - Visual rejected-state outline + dimming
  - JS to write `approved_ids.json` to user's Downloads
- **Phase 4 `tool_orientation_rule`** is now a top-level config field rather than per-tool prompt repetition — DRY.
- **Sub-part bg removal switched to `remove_bg_local`** (plain rembg, no safety net). The hybrid algorithms kept dust-coloured corner fringes because dust ≈ grey bg.
- **`backgrounds` category** auto-skips rembg in Phase 5 and goes through Phase 3b like other sprites.

### Tools added to manifest

A-tier (10): `hand_rubber_glove`, `hose_A`, `brush_A`, `spray_bottle_A`, `hairdryer_A`, `lint_roller_A`, `mini_blower_A`, `blower_A`, `plastic_bag_circuit_cover`, `plastic_bag_drip_catcher`

B-tier (4): `industrial_blower_B`, `foam_machine_B`, `pressure_washer_B`, `mini_scrubber_B`

### Examples included

- **Level 5 — Plushie** (10 production assets) — simple case, was already in V1
- **Level 6 — Air Conditioner** (30 production assets) — NEW canonical complex example

### Lessons codified

- `BEST_PRACTICES.md` rewritten with V2 patterns (source-field I2I, multi-anchor, algorithm selection)
- `docs/TOOL_RULES.md` updated with the diagonal side-profile orientation (replaces the misguided "handle toward camera 3D-foreshortening" rule from V1)
- `WORKFLOW.md` is new — explicit phase-by-phase guide with human gates

---

## V1 (initial)

- Pipeline with Phase 1–7
- Level 5 plushie canonical example
- 6 A-tier tools
- Basic README + SETUP + BEST_PRACTICES + TOOL_RULES (early version)
- T2I tool generation (deprecated in V2 — all I2I now)
