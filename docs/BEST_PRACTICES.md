# Best Practices

Hard-won lessons from production runs across two levels and 130+ generations. Read this before designing a new level.

## Chain Design

- **Each state = clean snapshot, not a transition.** If state 04 shows "half-rinsed, half-foam," gameplay reads as broken. Make state 04 fully-rinsed (the clean wet result of step 5). The gameplay animation handles the in-between.

- **Each state visually distinct from neighbours.** If 02 (foam blanket) and 03 (scrubbed foam) look 90% identical, players don't feel progress. Push the prompts toward dramatic differences. Use the `escalation_rule` field to remind yourself.

- **Dirty state should be DRAMATICALLY dirty.** Mud, leaves, grass clumps, dark stains, dust drips, cobwebs — exaggerate. Mildly-dirty reads as "what am I supposed to clean?" In `items_config.json` lean on phrases like "DRAMATICALLY DIRTY", "abandoned 20 years in a warehouse", "OVERWHELMING, FULLY COVERING".

- **Trash items separate from the base.** Don't bake trash into state 00. Use `trash_overlays` for independent transparent sprites. The engine layers them on top for the tap-to-remove mechanic, and the BASE stays constant during step 1.

## I2I — The Source Field Is Your Friend

V2 introduces the `source` / `sources` field. **Always use it** when an asset has a natural visual context elsewhere in the level.

```json
// Sub-part dusty inherits the dirt look from the closed AC chain state:
{"id": "cover_dusty", "source": "chain:00_closed_dusty", "prompt_t2i": "..."}

// Each subsequent cleaning state inherits from the previous in its mini-chain:
{"id": "cover_stained", "source": "subpart:cover_dusty", "prompt_t2i": "..."}

// Tools that appear in chain states should I2I from there to match exactly:
{"id": "plastic_bag_circuit_cover", "source": "chain:03_coil_double_protected", ...}
```

When `source` is set, the **prompt is interpreted as a transform instruction** ("Take this image. Change only: ..."). When `source` is missing, the prompt is auto-prefixed with the legacy style-ref subject-replace template.

### Multi-anchor: best of both worlds

```json
{"id": "filter_1_dusty", "sources": ["style_ref", "chain:00_closed_dusty"]}
```

First image locks the **render style + quality**, second supplies the **content/dirt visual**. The model fuses both. Most useful for sub-parts where neither alone gives a good result.

## Prompt Engineering

- **"Keep the EXACT same camera framing, zoom, pose and proportions"** in every chain transformation prompt. NB-2 respects this.
- **Persistent constants:** name the unchanged elements. Example: `"The bear's fur base color is STILL chocolate brown (#5C3A20)"`. Without this, color drifts 5–10% per I2I step and compounds across the chain.
- **One change per step.** Stacking changes (`now wet AND foamy AND dirty`) produces hybrid garbage. One transformation per state.
- **"NO X, NO Y, NO Z"** — explicit exclusions matter. The model will add trash to a clean bear unless you say "NO trash, NO gum, NO wrappers".
- **For sub-part extraction from a chain state:** start the prompt with `"Look at the [item] in the reference image. EXTRACT JUST the [part]..."`. Then describe target appearance.
- **For sub-part state transitions:** start with `"Take this [part] from the reference image. Change ONLY: ..."`. Reinforce that position/framing/lighting must stay the same.

## Tool Sprites

- **Player-POV orientation.** Diagonal side-profile: handle/grip toward lower-right of frame, business-end (nozzle/bristles/barrel) pointing upper-left. Full length visible. See `docs/TOOL_RULES.md`.
- **NO embedded effects.** No water from hose, no foam from bottle, no air from dryer, no lint on roller. These are runtime VFX. Bake-in breaks reuse and conflicts with engine particle systems.
- **Single coherent silhouette.** Watch for "merged into two tools" — repeat "ONE SINGLE [tool]" in the prompt and explicitly forbid duplication.
- **Reuse via manifest.** Tools generated once go into `projects/tools/tools_manifest.json`. Phase 4 dedups — only new tools generate per level. Mark reused tools `"cached": true` in your config.
- **B-tier tools = visually similar but more powerful.** Industrial blower not just bigger but obviously rugged (DeWalt-style yellow/black). Electric foam dispenser not just spray bottle scaled up — different form factor entirely.

## Post-Processing

- **rembg, not BiRefNet.** BiRefNet destroys light surfaces (white/cream/peach).
- **Algorithm selection:**
  - `remove_bg_local` (plain rembg) for sub-parts with dust-coloured fringes (V2 default for subparts)
  - `remove_bg_hybrid` for high-contrast objects on grey #808080 backgrounds (chain states)
  - `remove_bg_smart_hybrid` for assets with both dark and light content
  - `remove_bg_fal_pixelcut` for tricky cases (paid, ~$0.01 each)
- **Alpha threshold 128 + 2px crop padding** = production edges
- **Backgrounds skip rembg entirely** — they're opaque scene plates

## Scene Backgrounds

- One per level: wall (wall-mounted items) / table (tabletop) / floor (large items)
- I2I from the **clean anchor** with `"Remove the [item] entirely. Show only the [wall/table]..."` — inherits camera/lighting/style
- Push for visible texture: "stucco bumps", "paint stipple grain", "wood grain micro-variation"
- Soft-focus / low detail so foreground sprites read clearly

## Workflow Discipline

- **Hard approval gates** between Phase 1 (anchor) and Phase 7 (promote). Never auto-cascade. The review HTML exists specifically so you catch style drift before it propagates.
- **Always `--dry-run` first** on any phase you haven't run before on a new level. Free preview, catches schema typos before $5 of generation.
- **Targeted regens beat full regens.** `--state 04_foam` is one $0.03 call. Re-running phase 3 wastes ~$0.15.
- **Cost log is real money** — check `cost_log.jsonl` after each batch.
- **Single CLI orchestrator** — don't write inline scripts, use the flags.

## When Things Go Wrong

| Symptom | Fix |
|---|---|
| Color identity drifts across chain | Strengthen `color_constant`; ensure multi-anchor I2I is being used (chain state should use legacy `source_state` + auto-anchor multi-anchor) |
| Style drifts on a sub-part | Add `sources: ["style_ref", "chain:..."]` instead of single source |
| Tool sprites look "front-on" / pointing-at-camera | Check `tool_orientation_rule` in items_config; confirm phase_4 prepends it for non-source tools |
| Spray bottle / mini blower etc. merges into "two tools" | Prefix prompt with "ONE SINGLE [tool]" and explicitly forbid duplicates |
| Trash baked into state 00 | Add `"NO trash items, NO gum, NO wrappers"` to state 00 prompt |
| State 04 shows half-state | Rewrite as a CLEAN snapshot of the AFTER-action state |
| Sub-part dusty looks warped (filter etc.) | Strengthen prompt: "PERFECTLY FLAT RECTANGULAR PANEL, no perspective, no warping" |
| rembg eats part of asset | Use `remove_bg_hybrid` for that category in phase_5 |
| Half a tool gets cropped | Increase padding in `clean_and_crop(padding=N)` |
| Sub-part bg removal leaves dust-coloured fringe | Already V2 default (`remove_bg_local`). If still bad, try `remove_bg_fal_pixelcut` (paid) |
| Phase 3b complains "source not staged" | Order your `subparts` so dependencies come first; or run Phase 3 first to stage chain states |
| Fal 403 "User is locked" | Top up Fal balance |

## What I'd Do Differently Next Level

- **Generate anchor + state 00 as a Phase 1 pair** to validate the "very dirty" extreme before locking the anchor. Cheap validation, catches bad anchor early.
- **Shared `style_locks.json`** at package root for color constants and style-prefix snippets. New levels just import them.
- **Lock the chain BEFORE generating sub-parts.** If you regen state 00 later, every sub-part with `source: "chain:00"` would need regen too.
- **Generate 1 tool first as a sanity check** before batching all of them — easier to spot orientation issues before spending $0.30 on a tier.
- **Run `phase 2` after every config edit.** Two-second sanity check that saves hours.
