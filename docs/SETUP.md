# Setup

## 1. Python

Python 3.10+ required (tested on 3.13). Confirm:

```bash
python3 --version
```

## 2. Install dependencies

From the package root:

```bash
pip install -r pipeline/requirements.txt
```

Or manually:

```bash
pip install fal-client Pillow "rembg[cpu]" python-dotenv requests numpy
```

`fal_helper.py` auto-installs missing packages on first run, but installing
manually is faster and lets you see any errors clearly.

## 3. Get a Fal.ai API key

1. Sign up at https://fal.ai
2. Visit https://fal.ai/dashboard/keys and create a key
3. Top up at https://fal.ai/dashboard/billing — **$5 is enough for a full new level run with retry buffer**

## 4. Configure `.env`

From the package root:

```bash
cp .env.example .env
```

Edit `.env` and set:

```
FAL_KEY=your-actual-fal-key-here
```

The `.env` lives **at the package root** (next to `pipeline/`, not inside it).

## 5. Verify environment

```bash
# Tests fal_client import + key + rembg local model + cropping
python pipeline/fal_helper.py --test --env .env
```

This generates a tiny test image and runs it through the full local pipeline.
Confirms auth + generation + rembg + crop all work end-to-end.

## 6. Verify the package

```bash
# Schema validation — free, no API calls
python pipeline/shine_it_pipeline.py --level 5 --phase 2
python pipeline/shine_it_pipeline.py --level 6 --phase 2

# Should print:
# [OK] items_config.json valid — states=7, ..., All sources resolve.
```

## 7. Try a dry-run

```bash
# See the cost of regenerating a single chain state — costs nothing:
python pipeline/shine_it_pipeline.py --level 6 --phase 3 --state 04_coil_foamed --dry-run

# Should print:
#   Filter   : '04_coil_foamed' → ['04_coil_foamed']
#   Will gen : 1 state(s)
#   Cost est : 1 × $0.030 = $0.03
#   [DRY-RUN] No API calls will be made.
```

If that works, the package is fully wired and you're ready. Now read
**`WORKFLOW.md`** for the phase-by-phase guide.

## Cost Expectations

| Action | Cost |
|---|---|
| Anchor (Phase 1) | $0.03–0.05 per model |
| Chain state (Phase 3) | $0.03 |
| Sprite/sub-part (Phase 3b) | $0.03 |
| Tool (Phase 4) | $0.03 (cached tools auto-skip) |
| Post-process (Phase 5) | free (local rembg) |
| Review HTML (Phase 6) | free |
| Promote (Phase 7) | free |

A full level run (Phase 1 → 7, ~16–30 assets) typically costs **$0.50–$2.00** depending on complexity.

## Troubleshooting

### `FAL_KEY not found in .env file`
- Make sure `.env` is at the **package root**, not in `pipeline/`
- Check the format: `FAL_KEY=...` with `=`, not `:` or whitespace
- Try `cat .env` to confirm the variable name spelling

### `User is locked. Reason: Exhausted balance.`
- Your Fal.ai account has no credit. Top up at https://fal.ai/dashboard/billing.
- $5 buys ~150 NB-2 generations. A full new level is ~$1, with retries ~$2.

### `rembg` install fails on macOS
```bash
pip install rembg --break-system-packages
```
Or use a virtualenv:
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r pipeline/requirements.txt
```

### Generations look off-style
- Confirm `references/style_anchor_compact.png` exists
- Confirm `items_config.json` `style_reference` path resolves
- Run `--phase 2` validation

### Identity drift across chain (e.g., brown bear → grey bear)
- Tighten `color_constant` in `items_config.json`
- Make sure the pipeline is using **multi-anchor I2I** for chain states (this is the default in Phase 3 — passes `[anchor_url, source_state_url]`)

### Sub-part bg removal leaves fringes
- Default V2 uses `remove_bg_local` (plain rembg) for sub-parts — this is what works for dust-coloured fringes
- If you have a special case, edit `phase_5_postprocess` in `shine_it_pipeline.py` and swap the algorithm

### Phase 3b complains about missing source
- Run `--phase 2` to validate. It now lists exactly which `source:` references don't resolve.
- Common cause: `source: "subpart:X"` declared before `subpart X` itself. Reorder the `subparts` array so dependencies come first.

### "I want to undo a generation"
- Files in `staging/` are the most recent generation. Just regen the failed asset with `--phase N --state/--sprite/--tool ID --force --yes`.
- `approved/` is the production-locked copy. Phase 7 over-writes it on the next promote.

### Lost track of what I've spent
```bash
# Per-level total:
python3 -c "import json; print(sum(json.loads(l)['cost'] for l in open('projects/level_06_aircon/cost_log.jsonl')))"
```
