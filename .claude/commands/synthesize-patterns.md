---
description: Review session + git history to extract reusable prompt patterns into prompts_library/
---

# /synthesize-patterns

User has finished iterating on a level's prompts and wants to harvest the converged
prompts into reusable `prompts_library/` patterns for future levels.

## Procedure

Run these steps **in order**. Stop and ask the user only at step 5 (review/approve).

### 1. Detect scope

Determine which level(s) to mine:
- If user passed `$ARGUMENTS` like `level 6` or `6`, mine only that level.
- Otherwise list all `projects/level_*/items_config.json` and ask which one(s).

Report scope back to user before continuing.

### 2. Build the "candidate" prompt list

For each level in scope:

a. Run `git log --follow --oneline -- projects/level_NN_<name>/items_config.json` to see
   how many commits touched the config.

b. For each prompt field in the **current** `items_config.json`
   (`states[].prompt`, `subparts[].prompt_t2i`, `style_variants[].prompt_t2i`,
   `backgrounds[].prompt_t2i`, `tools_required[].prompt_t2i`):
   - **Stable** = same text for the last ≥2 commits → strong candidate.
   - **Iterated** = text changed in last commit but stabilized after ≥3 prior changes →
     also strong candidate (means user explicitly converged on it).
   - **Volatile** = changed every commit, never stable → skip (not converged yet).

c. Cross-reference each candidate against `prompts_library/index.json`:
   - If a candidate's snippet is ≥70% similar to an existing pattern's snippet
     → mark as **already-covered** (skip).
   - Else → mark as **new candidate**.

### 3. Classify each new candidate

For each new-candidate prompt, infer a category by looking at keywords in the snippet:

| Keywords found | Category | Example pattern id |
|---|---|---|
| `foam`, `soap`, `suds`, `bubbles`, `whipped` | `foam/` | `foam/<descriptor>` |
| `dust`, `dust mat`, `dusty`, `cobweb` | `dust/` or `cobwebs/` | `dust/<descriptor>` |
| `stain`, `mould`, `mildew`, `coffee`, `grime` | `stains/` | `stains/<descriptor>` |
| `wipe arc`, `scrubbed`, `scrub`, `cloth-wipe` | `scrubbed/` | `scrubbed/<descriptor>` |
| `pristine`, `clean`, `fresh-out-of-box` | `clean/` | `clean/<descriptor>` |
| `plastic bag`, `bag`, `pouch`, `cover` (protection) | `bags/` | `bags/<descriptor>` |
| `front-on`, `head-on`, `no perspective`, `flat slab` | `shape_rules/` | `shape_rules/<descriptor>` |
| `style`, `aesthetic`, `render quality`, `lighting` | `style_anchors/` or `lighting/` | … |

Descriptor = 2-4 words capturing the variant (e.g. `flat_no_drip`, `heavy_felt_mat`,
`thick_voluminous`). Snake-case.

### 4. Draft patterns

For each new candidate, draft:

a. The **pattern .md file**, following the template in `prompts_library/README.md`:
   - `# Pattern Name` — Title Case
   - `## Use case` — one line
   - `## Snippet` — blockquote of the prompt, with object-specific nouns replaced by `[OBJECT]`
   - `## Companion negatives` — every "NO X" / "NOT Y" / "ABSOLUTELY NO Z" line extracted
   - `## Compatible with` — guess from related categories in same config
   - `## Tested on` — list level + sprite/state IDs where this prompt is used
   - `## Notes` — short observation on why it converged (if obvious from git diff)
   - `## Cost to derive` — `git log` count × $0.03 estimate

b. The **`index.json` entry** with all the same fields.

### 5. Present proposal to user

Print a table:

```
PROPOSED NEW PATTERNS
─────────────────────────────────────────────────────
#  ID                              FROM                  ROUNDS  EST $
1  foam/realistic_flat_suds        L6 shell_foamed       2       $0.18
2  dust/heavy_felt_no_drips        L6 state_00, cover_*  3       $0.27
3  shape_rules/front_on_view       L6 cover_stained..    2       $0.06
…

For each pattern I'll show the snippet preview.
Reply: "approve all" / "approve 1,3" / "reject 2" / "edit 1"
```

Then for each pattern, print:
- ID
- First 200 chars of snippet
- Negatives count
- Tested-on summary

**Stop and wait for user decision.**

### 6. Apply approved patterns

For each approved pattern:
- Write `prompts_library/patterns/<category>/<descriptor>.md`
- Append entry to `prompts_library/index.json` (preserving valid JSON)
- Update `prompts_library/README.md`'s provenance table (add a row)

After writing all approved patterns, run:
```bash
python3 prompts_library/composer.py list
```
to confirm the index loads and the new patterns appear.

### 7. Commit (optional)

Ask the user: "Commit the new patterns to git?" — if yes, run:
```bash
git add prompts_library/
git commit -m "patterns: add <N> patterns from level <X> session"
```

## Constraints

- **Never silently overwrite** existing patterns. If a pattern id collides, suffix `_v2`.
- **Never invent prompts** — only extract verbatim from existing `items_config.json`.
- **Never include level-specific names** in the snippet (no "AC", "plushie", "Samsung"
  unless the pattern is intentionally object-specific — note this in `Use case`).
- **Always keep negatives verbatim** — they are the most valuable, hardest-won bits.

## Examples of good vs bad pattern names

✅ `foam/realistic_flat_suds` — clear category + 3-word descriptor
✅ `dust/heavy_felt_no_drips` — explains the key property
✅ `shape_rules/front_on_no_perspective` — rule-style name
❌ `foam/aircon_shell` — too object-specific
❌ `dust/v3` — uninformative
❌ `misc/things` — wrong category
