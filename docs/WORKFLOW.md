# Workflow — Phase-by-Phase Guide

Read this once before running anything. It tells you **what each phase does, what costs money, and where you (the human) need to make a decision** before continuing.

---

## Phase Map at a Glance

| # | Phase | What it does | Spends $ | Human gate? |
|---|---|---|---|---|
| **1** | **Anchor** | Generate the final clean state of the item (the "pristine" reference image). FLUX + NB-2 side-by-side. | ✅ $0.03–0.08 | **YES — pick winner** |
| **2** | **Validate** | Lint `items_config.json`: schema, anchor present, all sources resolve. | ❌ free | No |
| **3** | **Chain** | Walk backwards from the anchor, generating dirtier versions of the item. | ✅ N × $0.03 | **YES after** — review chain |
| **3b** | **Independent sprites** | Generate trash overlays, sub-parts, style variants, backgrounds. Each can I2I from the chain state where it naturally appears. | ✅ M × $0.03 | **YES after** — review |
| **4** | **Tools** | Generate cleaning tool sprites. Dedups against `tools_manifest.json` across levels. | ✅ K × $0.03 | **YES after** — orientation/identity check |
| **5** | **Post-process** | rembg + alpha threshold + tight crop, runs locally. | ❌ free | No |
| **6** | **Build review HTML** | Generate `review_chain.html` with checkboxes for approval. | ❌ free | **YES — open in browser, tick approvals, click Save** |
| **7** | **Promote** | Copy `final/*.png` → `approved/*.png`. Either all or only the IDs you approved in Phase 6. | ❌ free | No |

**Total spend for a typical new level: ~$1 across all generation phases.**

---

## How Money Is Spent — The Three Levers

Every generation phase supports:

```bash
--dry-run     # Free preview: shows what would be generated + total cost. NO API calls.
--yes         # Skip the interactive "Continue? [y/N]" cost confirmation.
--state X     # Phase 3: regen only one chain state (or glob: --state 'foam_*')
--sprite X    # Phase 3b: regen only one sprite or category (--sprite 'cover_*')
--tool X      # Phase 4: regen only one tool (--tool spray_bottle_A)
--force       # Phase 4: regen even if cached in tools_manifest.json
```

**Always start with `--dry-run`** for any phase you haven't run before. It costs nothing and tells you exactly what you'll spend.

---

## Walkthrough — Generating a New Level From Scratch

Assume you're adding **Level 7** (some new item — let's say a coffee machine).

### Step 0 · Prepare the config

Create `projects/level_07_coffee_machine/items_config.json` modelled on `level_06_aircon/items_config.json`. Sections you'll usually need:

- `states` — chain states from dirty (`00_…`) to clean (`NN_pristine`). The cleanest state is the **anchor**.
- `tools_required` — list of tools. Reuse existing ones from `projects/tools/tools_manifest.json` (mark `"cached": true`).
- Optional: `subparts`, `trash_overlays`, `overlay_effects`, `style_variants`, `backgrounds`.

Then validate before spending a cent:

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 2
```

Should print `[OK] items_config.json valid — ... All sources resolve.` If it complains, fix the config and re-run.

### Step 1 · Anchor (Phase 1) → 🔴 HUMAN DECISION

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 1                  # FLUX + NB-2 (~$0.08)
python pipeline/shine_it_pipeline.py --level 7 --phase 1 --models nb2     # NB-2 only (~$0.03)
```

This produces `staging/anchor_flux.png` and/or `staging/anchor_nb2.png`, plus `style_comparison.html`.

**🔴 You MUST:**
1. Open `level_07_coffee_machine/style_comparison.html` in a browser
2. Look at both anchors
3. Decide: does the style match? Pose/framing correct? Is the item recognisable?
4. If yes: copy the chosen file to `staging/{name}_{anchor_id}.png` (the canonical anchor for the chain). E.g. `cp staging/anchor_nb2.png staging/coffee_machine_07_pristine.png`
5. If no: rewrite the anchor prompt in `items_config.json` and re-run Phase 1

**Do not move on until you're happy with the anchor.** Every subsequent state will inherit its style. Fixing it later means regenerating the whole chain.

### Step 2 · Chain (Phase 3) → 🔴 HUMAN DECISION

```bash
# Always dry-run first to see the cost:
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --dry-run
# Then spend:
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --yes
```

This walks **backwards from the anchor** through `source_state` pointers, generating progressively dirtier images. Each step uses **multi-anchor I2I** (anchor + previous state) to lock identity.

**🔴 After it completes:**
1. Look at each `staging/{name}_{state_id}.png`
2. Check: is each state visually distinct? Is the dirt escalation believable? Did the model preserve the item's color/identity?
3. If one state is bad: regen it alone (`--state 04_foam`) instead of the whole chain.
4. If the chain drifted (anchor brown → state 00 grey): tighten the `color_constant` and `style_lock_prefix` strings in `items_config.json`, then re-run targeted regens.

### Step 3 · Sub-parts / Overlays / Style Variants / Backgrounds (Phase 3b)

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --dry-run
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --yes
```

If a sprite has `source: "chain:STATE_ID"` or `source: "subpart:OTHER_ID"`, the pipeline I2Is from THAT image (not the style ref). This is how sub-parts inherit the dust look from the chain.

**Order matters in `subparts` array:** if `cover_stained.source = subpart:cover_dusty`, then `cover_dusty` must be earlier in the array. The pipeline does NOT topologically sort — it runs in declared order.

**🔴 Regen any that look off:**

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --sprite 'filter_*' --yes
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --sprite cover_stained --yes
```

### Step 4 · Tools (Phase 4)

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 4 --dry-run
python pipeline/shine_it_pipeline.py --level 7 --phase 4 --yes
```

**Tools already in `projects/tools/tools_manifest.json` are skipped.** Only new tools generate.

**🔴 Visual check after:**
- Open each new tool in `projects/tools/{tool_id}.png`
- Verify the orientation rule (see `docs/TOOL_RULES.md`):
  - Handle in lower-right
  - Business-end (bristles/nozzle/tip) pointing upper-left
  - No water/foam/steam/motion lines — just the dry tool
- If one is bad: `--tool TOOL_ID --force --yes` (force regen even if cached)

### Step 5 · Post-process (Phase 5)

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 5
```

No API spend. Runs rembg locally on every PNG in `staging/`, applies alpha threshold 128, tight-crops to content + 2px padding, writes to `final/`.

Sub-parts use the `local` (plain rembg) algorithm because dust often colour-matches the grey background — the hybrid safety nets would keep dust-coloured fringes.

### Step 6 · Review HTML (Phase 6) → 🔴 HUMAN DECISION

```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 6
open level_07_coffee_machine/review_chain.html
```

The HTML now shows:
- Every asset organised by category
- A checkbox under each asset (defaults to ✓ approved)
- A toolbar: **Save approvals**, Approve all, Unapprove all, live count
- Unchecked cards highlighted with a red outline + dimmed

**🔴 You do this:**
1. Click through every asset. Anything you don't like → uncheck it.
2. Click **Save approvals** → downloads `approved_ids.json` to your Downloads folder.
3. Move it into the level folder: `mv ~/Downloads/approved_ids.json level_07_coffee_machine/`

**OR** if everything looks great, skip the JSON dance — just run Phase 7 without `--only-approved`.

### Step 7 · Promote (Phase 7)

```bash
# Promote everything in final/ (default):
python pipeline/shine_it_pipeline.py --level 7 --phase 7

# Promote ONLY the assets you ticked in the review HTML:
python pipeline/shine_it_pipeline.py --level 7 --phase 7 --only-approved
```

Copies files from `final/` → `approved/`. The `approved/` directory is the **production source of truth** for engine integration.

---

## Common Operations Cheat Sheet

### "I want to redo just one chain state"
```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --state 04_foam --yes
```

### "I want to redo all subparts whose id starts with `filter_`"
```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --sprite 'filter_*' --yes
```

### "I want to regen a tool even though it's already in the manifest"
```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 4 --tool spray_bottle_A --force --yes
```

### "How much will phase X cost me right now?"
```bash
python pipeline/shine_it_pipeline.py --level 7 --phase 3 --dry-run
python pipeline/shine_it_pipeline.py --level 7 --phase 3b --dry-run
python pipeline/shine_it_pipeline.py --level 7 --phase 4 --dry-run
```

### "Where did all my money go?"
Look at `projects/level_NN_xxx/cost_log.jsonl`. Every NB-2 call gets one line: timestamp, phase, asset id, cost, success.

```bash
# Total spent on a level:
python3 -c "import json; print(sum(json.loads(l)['cost'] for l in open('projects/level_07_coffee_machine/cost_log.jsonl')))"
```

---

## What NOT to Do

- ❌ **Don't run Phase 1 → 3 → 3b → 4 in one go without reviewing.** Style drift compounds. Each phase has a gate for a reason.
- ❌ **Don't run any phase without `--dry-run` first** the first time you try it on a new level. Even an experienced operator can mis-configure `items_config.json` and spend $5 on a typo.
- ❌ **Don't edit assets in `approved/`.** That's the production-locked copy. Edit in `staging/` (or regen), then re-promote.
- ❌ **Don't add B-tier tools without locking A-tier first.** B-tier visual identity should clearly relate to A-tier (more rugged version of the same kind of tool).
- ❌ **Don't forget the wall/scene background.** Every level should have one — engine composites it behind everything.

---

## Help / Reference

- **`README.md`** — what this package is, quick start
- **`SETUP.md`** — Python install, Fal.ai key, environment
- **`BEST_PRACTICES.md`** — chain design, prompt patterns, what's been learned
- **`docs/TOOL_RULES.md`** — the diagonal-profile tool orientation rule
- **`CHANGELOG.md`** — V1 → V2 changes
- **Canonical example configs:**
  - `projects/level_05_plushie/items_config.json` — single object, 7 states + 3 trash + 0 sub-parts
  - `projects/level_06_aircon/items_config.json` — complex object, 7 states + 20 sub-parts + 2 plastic bag tools + 3 style variants + 1 wall background
