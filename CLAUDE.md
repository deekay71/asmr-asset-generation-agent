# Shine It Asset Agent V2 — Agent Instructions

You are running inside the user's local copy of **Shine It Asset Agent V2**, an
AI pipeline that generates game art assets (cleaning-game items + their dirty
states + tools + backgrounds) using fal.ai (FLUX Pro + Nano-Banana 2 Edit).

The user has just unzipped this project and opened Claude Code. **They want
you to do everything for them.** Treat them as a non-technical user.

---

## 🎯 First-message behavior (CRITICAL)

When the user sends their first message — regardless of what they say
("setup", "chạy đi", "tạo asset", "hi", "?", etc.) — **silently run the
AUTO-SETUP CHECKLIST below** before responding to their actual request.

Only ask the user a question if a step truly requires user input
(e.g. their `FAL_KEY`). Never ask "Should I check Python?" — just check it.

### AUTO-SETUP CHECKLIST

Run each step. Skip if already done.

```
[1] Check Python ≥ 3.10
[2] Check .env exists with FAL_KEY
[3] Check pip deps installed (fal_client, rembg, PIL)
[4] Report status, then handle user's actual request
```

#### Step 1 — Python

```bash
python3 --version 2>/dev/null || python --version 2>/dev/null
```

If missing OR < 3.10:
- **macOS**: `brew --version >/dev/null 2>&1 && brew install python@3.12` —
  if no brew, instruct user to install brew from https://brew.sh **before**
  doing anything else.
- **Linux**: `sudo apt-get install -y python3 python3-pip` (or yum/dnf)
- **Windows**: tell user to install from https://www.python.org/downloads/
  with "Add to PATH" checked, then restart terminal.

#### Step 2 — `.env`

```bash
test -f .env && grep -q "^FAL_KEY=" .env && grep -v "your-fal-key-here" .env > /dev/null
```

If `.env` missing OR `FAL_KEY` placeholder:
1. Ask user **once**: "Bạn cần FAL_KEY từ https://fal.ai/dashboard/keys.
   Đã có chưa? Nếu có, paste vào đây."
2. Write `.env` with `FAL_KEY=<their key>`
3. Tell them they need credit on fal.ai (~$1 per level).

#### Step 3 — Dependencies

```bash
python3 -c "import fal_client, rembg, PIL" 2>&1
```

If ImportError, install silently:
```bash
python3 -m pip install -q -r pipeline/requirements.txt
```

#### Step 4 — Report status & proceed

After setup, give the user a short status line, then handle whatever they
asked for. If they didn't ask anything specific, show:

```
✓ Setup OK. Bạn muốn:
  • Tạo asset cho level có sẵn (5=plushie hoặc 6=aircon)
  • Tạo level mới
  • Mở review page xem assets có sẵn
  • Khác
```

---

## 🛠 How to handle common user requests

### "Tạo asset cho level X" / "Generate assets for level X"

1. Run Phase 0 wizard interactively (helps user pick style ref, confirm config):
   ```bash
   python3 pipeline/shine_it_pipeline.py --level X --phase 0
   ```
2. Run Phase 1 (anchor candidates):
   ```bash
   python3 pipeline/shine_it_pipeline.py --level X --phase 1
   ```
3. **Show the user both candidates** (FLUX vs NB2) by reading both PNGs.
   Ask which one they prefer. Copy the chosen one to the anchor filename:
   ```bash
   cp projects/level_XX_name/staging/anchor_flux.png \
      projects/level_XX_name/staging/{name}_{anchor_id}.png
   ```
4. Run Phase 3 → 3b → 5 sequentially.  **For first-run / new levels**, use
   `--mode smart` so the pipeline pauses at complex states and lets the user
   approve each before continuing.  For stable production batches, use `--yes`
   (implies `--mode batch --model flash`).  Use `--model pro` for higher
   quality at 4.5× cost.  When neither `--mode` nor `--model` specified,
   pipeline prompts interactively:
   ```bash
   # First run / new level — pause at risky states only
   python3 pipeline/shine_it_pipeline.py --level X --phase 3 --mode smart
   # Or full waterfall (pause every state)
   python3 pipeline/shine_it_pipeline.py --level X --phase 3 --mode waterfall
   # Stable production — gen all, review at end
   python3 pipeline/shine_it_pipeline.py --level X --phase 3 --yes
   python3 pipeline/shine_it_pipeline.py --level X --phase 3b --yes
   python3 pipeline/shine_it_pipeline.py --level X --phase 5
   ```
5. Open review HTML + start feedback server (foreground/background):
   ```bash
   python3 pipeline/shine_it_pipeline.py --level X --phase 6
   # then start feedback server in background:
   cd projects/level_XX_name && python3 feedback_server.py &
   # then open review:
   open projects/level_XX_name/review_chain.html
   ```
6. Tell user: "Review xong, gõ 'check feedback' để tôi đọc và apply."

### "Check feedback" (after user clicked Send in review HTML)

1. Read `projects/level_XX_name/feedback.json`
2. For each item with `regen: true`, update the prompt in
   `items_config.json` based on the `text` feedback.
3. For items with `ref_image_path`, save the image path note in prompt as
   "use this ref: <path>".
4. Re-run affected phases (Phase 3 for chain items, Phase 3b for subparts).
5. Run Phase 5 + Phase 6 again to refresh review.

### "Promote" / "Approve" / "Done"

```bash
python3 pipeline/shine_it_pipeline.py --level X --phase 7
```

Output lands in `projects/level_XX_name/approved/`.

### "Create new level" / "Tạo level mới"

1. Ask the user for: level number, item name (English), brief description, art
   style, list of states (clean → dirtiest), tools needed.
2. Copy template:
   ```bash
   cp -r projects/level_05_plushie projects/level_NN_<name>
   rm -rf projects/level_NN_<name>/{staging,final,approved}
   ```
3. Edit `items_config.json` based on the user's spec. Get every prompt right
   the first time — don't generate placeholder prompts.
4. Run Phase 0 wizard.

### "Review" / "Mở review"

```bash
python3 pipeline/shine_it_pipeline.py --level X --phase 6
cd projects/level_XX_name && python3 feedback_server.py &
open projects/level_XX_name/review_chain.html
```

---

## 📁 Key files

```
pipeline/shine_it_pipeline.py      # main CLI orchestrator
pipeline/fal_helper.py             # fal.ai wrappers
projects/level_NN_<name>/
    items_config.json              # THE RECIPE — every prompt lives here
    staging/                       # raw outputs from Phase 1/3/3b
    final/                         # background-removed outputs from Phase 5
    approved/                      # promoted outputs from Phase 7
    feedback.json                  # written by review_chain.html
    feedback_refs/                 # ref images dropped by user
projects/tools/
    tools_manifest.json            # shared tool sprite manifest
    final/                         # all tool sprites (cached, reused across levels)
references/
    style_anchor_compact.png       # default style reference
```

## 💰 Costs

- Phase 1 (anchor):  ~$0.08  (1× FLUX $0.050 + 1× NB2 $0.030)
- Phase 3 (6 chain states): ~$0.18
- Phase 3b (~23 sprites): ~$0.69
- Phase 4 (tools, only if not cached): ~$0.36 (12× NB2)
- **Total new level: ~$0.95-1.30**

Tell the user the cost estimate *before* spending. Never start Phase 3/3b/4
without confirming.

---

## 🚫 Don'ts

- **Don't** ask the user technical setup questions ("do you have Python?",
  "should I install rembg?"). Just check and act.
- **Don't** require the user to manually run `start.py` or any phase. You
  orchestrate everything via Bash tool.
- **Don't** modify `items_config.json` without confirming the change in
  natural language first.
- **Don't** silently spend credit. Always confirm cost before Phase 1/3/3b/4.
- **Don't** leave the feedback server running across sessions — kill on exit.

---

## 🗣 Tone

- Vietnamese by default (user is Vietnamese).
- Short, action-oriented messages. State results, not process.
- When showing generated images, use the Read tool with the PNG path so the
  user sees them inline.
