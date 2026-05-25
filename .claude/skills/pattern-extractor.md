---
name: pattern-extractor
description: Extract reusable prompt patterns from a finished level into prompts_library/. Use when user says "save patterns", "tổng hợp pattern", "extract patterns", "lưu pattern lại", "harvest prompts", or similar — i.e. after they've finished iterating a level's prompts and want to reuse them in future levels.
---

# Pattern Extractor skill

This skill is the **natural-language trigger** for the same procedure as the
`/synthesize-patterns` slash command. They share one implementation.

## When to activate

Activate when the user message contains any of (case-insensitive, Vietnamese or English):

- "save (the )?pattern(s)?"
- "lưu (cái )?pattern"
- "tổng hợp (lại )?pattern"
- "extract pattern(s)?"
- "harvest (the )?prompt(s)?"
- "rút (gọn|ra) pattern"
- "(synthesize|synthesise) pattern(s)?"
- (or the user explicitly mentions `prompts_library/` and asks to add to it)

If the user just said "review the session" without mentioning patterns, **don't activate** —
they probably mean a general recap, not pattern extraction.

## What to do

Run the procedure in `.claude/commands/synthesize-patterns.md` (steps 1–7).

Key reminders specific to chat-triggered invocation:

1. **Confirm scope conversationally.** Don't dump tables before confirming which level.
   Example: "Mình thấy bạn vừa xong level 6 aircon. Bạn muốn extract patterns từ level đó,
   hay cả các level khác (`level_05_plushie` …)?"

2. **Show the proposal as a short list first**, not a full dump. Wait for "approve all" /
   "approve 1,3" before writing files.

3. **After writing**, recap the new pattern IDs and remind the user that they can now
   reuse them via `composer.py apply --pattern <id>`.

## Edge cases

- **No converged prompts found.** If git history is shallow (< 2 commits on the config)
  or every prompt is still volatile, say so explicitly:
  > "Chưa đủ data để extract — config mới có 1 commit, prompts chưa hội tụ.
  >  Sau ≥2 rounds feedback hãy gọi lại."

- **All candidates already in library.** Say: "Tất cả prompts đã hội tụ đều khớp với
  patterns có sẵn (`foam/realistic_flat_suds`, ...). Không có pattern mới để add."

- **Library doesn't exist yet.** Bootstrap by creating `prompts_library/` first using
  the structure described in the existing `prompts_library/README.md` (or create that
  README too if missing). Then continue extraction.

## Provenance

When writing patterns, always fill the `Tested on` block with the actual level + sprite IDs
from the source `items_config.json`. This is the audit trail that prevents pattern rot
("does this still work on new levels?"). Future runs of this skill will read `Tested on`
to know which patterns have field validation.
