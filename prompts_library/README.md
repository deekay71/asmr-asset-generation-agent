# Shine It Prompt Library

Reusable prompt patterns refined through iterative feedback on real levels (plushie, aircon, …).
Each pattern is a small, paste-ready snippet you can drop into your `items_config.json` prompts —
plus the negatives/companions/tested-on metadata that make it survive future levels.

---

## Structure

```
prompts_library/
├── README.md                ← this file
├── index.json               ← machine-readable index (composer.py reads this)
├── composer.py              ← CLI to assemble prompts from patterns
└── patterns/
    ├── foam/
    │   ├── realistic_flat_suds.md
    │   ├── thick_voluminous.md            (todo)
    │   └── full_body_coverage.md          (todo)
    ├── dust/                              (todo)
    ├── stains/                            (todo)
    ├── scrubbed/                          (todo)
    ├── clean/                             (todo)
    ├── bags/                              (todo)
    ├── cobwebs/                           (todo)
    ├── shape_rules/                       (todo)
    ├── style_anchors/                     (todo)
    └── lighting/                          (todo)
```

---

## Pattern file format

Every pattern `.md` follows this template:

```
# Pattern Name

## Use case
One line: when to apply.

## Snippet
> The actual prompt text to paste. Wrapped in a blockquote
> so it's visually distinct.

## Companion negatives
- NO X
- NO Y

## Compatible with
- patterns/foo/bar.md
- patterns/baz/qux.md

## Tested on
- level NN <name> — state IDs / sprite IDs

## Notes
Free-form tips, gotchas, why this prompt works.
```

The same fields are mirrored in `index.json` (id, file, snippet, negatives, compatible_with,
tested_on, tags) so `composer.py` can assemble final prompts programmatically.

---

## Usage

### Manual (copy-paste)

Find the pattern in `patterns/<category>/<name>.md`, copy the `Snippet` block, paste into the
relevant prompt field in `items_config.json`. Add the companion negatives at the end.

### Programmatic (composer.py)

```bash
# Assemble a prompt from multiple patterns
python3 prompts_library/composer.py \
    --base "Take the stained shell housing." \
    --apply foam/realistic_flat_suds \
    --apply shape_rules/preserve_silhouette \
    --object "AC shell housing"
```

Composer substitutes `[OBJECT]` placeholders in snippets, joins negatives, dedupes,
and prints the final prompt to stdout (or `--out` file).

### Listing / searching

```bash
python3 prompts_library/composer.py list
python3 prompts_library/composer.py list --tag dust
python3 prompts_library/composer.py show foam/realistic_flat_suds
```

---

## Adding a new pattern

1. Write the `.md` file under the right category folder.
2. Add an entry to `index.json` with the same metadata.
3. Test it — preferably re-derive a sprite from a level that originally used a free-form prompt
   and check the result still passes review.
4. Update `Tested on` in the `.md` after each successful use.

---

## Provenance — patterns derived so far

| Pattern | Source level | Rounds to dial in | Cost ($) |
|---|---|---|---|
| `foam/realistic_flat_suds` | level_06_aircon (shell + filter foam states) | 2 (round 3 fix) | ~$0.18 |
| _(more to be added as we extract from level 6 history)_ | | | |
