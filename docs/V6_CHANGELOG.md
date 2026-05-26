# V6 — Prompt Agent + Learning Loop

V6 is a structural rewrite of how the pipeline composes prompts and learns from your feedback. The asset generation backends, scheduling, and review HTML stay the same — what changes is the prompt-writing layer in the middle.

## The Two Problems V6 Solves

### Problem 1: V5 prompts were 3,500+ chars of mostly-repeating boilerplate

Every chain state, sub-part, and tool got wrapped in:
```
CONSISTENCY RULES:    (300 chars, same every asset)
GAME CONTEXT:         (400 chars, same every asset)
STYLE:                (300 chars, same every asset)
CONTAINMENT:          (250 chars, same every asset)
STEP-TYPE PATTERN:    (500 chars)
TASK:                 (800-1200 chars, the actual diff)
IDENTITY LOCK:        (400 chars, same every asset)
```

5 of those 7 blocks were near-duplicates across every prompt in the level. The model's attention was diluted across boilerplate it had already seen.

### Problem 2: Feedback didn't compound

When you said "foam needs to be more even" on Level 6, that fix got applied to *that one regen* via a `REGEN FEEDBACK:` block. The next level that generated foam started fresh — no memory of the lesson.

## V6 Solution

### 1. Slot-filled templates per step_type

Each pattern (foam_application, scrub_pattern, dirty_base, …) has a dedicated template in `pipeline/prompt_agent/templates/`. The agent fills the slots from the asset's `spec` block + level defaults + step_patterns library, prunes empty slots, and emits 600-1,500 char prompts.

### 2. Learning library that promotes recurring lessons

`step_patterns.json` now has:

```jsonc
"foam_application": {
  "best_practices": [...],     // permanent rules — auto-injected on every foam prompt
  "forbid": [...],             // anti-patterns — auto-injected as "DO NOT" clauses
  "candidates": [              // promoted to best_practices/forbid after 2nd occurrence
    {"polarity": "forbid", "text": "...", "seen_count": 1, ...}
  ],
  "history": [...]             // append-only audit log
}
```

When you save feedback through `review_chain.html` and run `--learn`, the agent:

1. Reads `regen_queue.json`
2. For each `{asset_id, comment}` entry, distils via Gemini Flash text (~$0.0005/call) into a structured rule
3. Detects polarity (best_practice vs forbid) — heuristically or via LLM
4. Computes Jaccard similarity against existing candidates
5. If similar candidate exists → increment `seen_count`. If `seen_count` hits 2 → promote to permanent.
6. If new → add as candidate, watch for recurrence.

Result: the second time someone says "foam isn't even," the rule becomes permanent and applies to every future foam_application across every level.

## New CLI

```bash
# Process the review session into permanent rules:
python pipeline/shine_it_pipeline.py --level 7 --learn
```

Output:
```
V6 Learn — Level 7
  Entries processed   : 2
  Candidates added    : 1
  Candidates +1       : 1
  Rules PROMOTED      : 1
    • [dirty_base.forbid] Leaves must not extend beyond the keyboard footprint.
```

## New Review HTML Banner

When the agent has learned new rules since you last opened the review HTML, a green banner appears at the top of the page. Tracked via `agent_banner_seen.json` per level.

## Backwards Compatibility

V5 envelope wrapper still runs for any asset that:

- Has no `step_type` field, AND
- Has no `spec` block

So your existing L5/L6/L8 configs keep generating bit-for-bit identical output. New levels (or migrated old ones) opt in by adding `step_type` per asset.

To force V5 behaviour on a level that has step_types declared, set `"pipeline_version": 5` at the top of items_config.json.

## Schema Additions (all optional)

```jsonc
{
  // V6 NEW — concept defaults at the level scope
  "concept_defaults": {
    "lighting": "soft warm top-left, gentle directional",
    "camera": "strictly front-on, no tilt",
    "containment": "all effects within asset silhouette"
  },

  "states": [
    {
      "id": "00_filthy",
      "step_type": "dirty_base",   // V6 — picks template + best_practices + forbid
      "spec": {                     // V6 NEW — structured slots (optional)
        "diff_from_prev": "...",
        "object_count": "...",
        "preserve_from_prev": "...",
        "notes": "..."             // free-text escape hatch, ≤200 chars
      },
      // V2 free-form `prompt` field still supported as fallback
      "prompt": "..."
    }
  ]
}
```

## Templates Bundled

13 step_type templates in `pipeline/prompt_agent/templates/`:

- `_base.txt` (fallback)
- `dirty_base.txt`
- `dust_layer.txt`
- `stain_reveal.txt`
- `foam_application.txt`
- `scrub_pattern.txt`
- `rinse_complete.txt`
- `polish_complete.txt`
- `trash_overlay.txt`
- `tool_sprite.txt`
- `subpart_dusty.txt`
- `brick_repair.txt`
- `mortar_apply.txt`
- `background_scene.txt`

Each is a short text file with `{{slot}}` placeholders. Edit them as you learn what your style needs.

## Costs

V6 adds two optional Gemini Flash text calls per pipeline run:

| Call | When | Cost |
|---|---|---|
| Slot inference (compose) | Per asset that has `step_type` but no full `spec.diff_from_prev` | ~$0.0005 |
| Feedback distillation (learn) | Per regen comment | ~$0.0005 |

Net cost per level: ~$0.01 added across all calls. Negligible.

## Roadmap

V6 ships these features. The bigger asks queued for V7:

- **Local web UI** wrapping pipeline + review + learn in a browser flow (FastAPI + single-page frontend)
- **Pre-gen critique**: Gemini text reviews the assembled prompt before sending to the I2I model; flags issues
- **Post-gen quality gate**: Gemini text scores generated images against the spec; auto-flags for regen if score < threshold
- **AI Studio API key path** for `gemini-2.5-pro-image-preview` (true Nano-Banana Pro direct)
- **Reference image library** with multi-anchor I2I for difficult step types (subflow composites, brick repair)
