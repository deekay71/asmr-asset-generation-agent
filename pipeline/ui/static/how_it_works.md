# How Shine It V6 Works

Shine It is an AI asset-generation pipeline for single-item ASMR cleaning games. It turns a game-design doc into a full chain of dirty→clean image states, plus tool sprites and trash overlays, then learns from your feedback so each round of regeneration is better than the last.

---

## The big picture

```
items_config.json (your GDD)
       │
       ▼
prompt_agent.compose()   ──── reads step_patterns.json (the brain)
       │                          ▲
       ▼                          │  feedback distilled here
i2i_backend (Vertex Flash / Fal NB-2 / Pro)
       │
       ▼
projects/level_NN/staging/*.png
       │
       ▼
phase_5 postprocess (bg removal, crop)
       │
       ▼
review (this UI) → approved_ids.json + regen_queue.json
       │                              │
       ▼                              ▼
phase_7 promote               --learn → step_patterns.json
```

---

## Phases

| Phase | Purpose |
|---|---|
| **1 Anchor** | Generate the "perfect clean" reference image for the level. |
| **1b Sub-flow** | Composite anchors for items that open up (AC filters, keyboard keycaps). |
| **2 Validate** | Free static check of items_config.json. Always run before phase 3. |
| **3 Chain** | The main event — dirty→clean state chain via I2I, following `source_state`. |
| **3b Sprites/Trash** | Detached pieces: trash overlays, subparts, backgrounds, style variants. |
| **4 Tools** | Tool sprites (brushes, blowers, scrapers) used during cleaning. |
| **5 Postprocess** | Background removal + tight alpha crop. |
| **6 Review** | This UI's Review tab. Tri-state per asset: approve / reject / regen+comment. |
| **7 Promote** | Copy approved staging assets to `final/`. |

---

## The prompt agent (V6's headline feature)

Instead of a single 3,500-character envelope prompt, V6 builds a tight ~600-character prompt by filling **slot-based templates** chosen by each asset's `step_type` (`dirty_base`, `foam_application`, `tool_sprite`, …). The templates live in `pipeline/prompt_agent/templates/`.

Each composed prompt also gets:
- the matching pattern's permanent **`best_practices`** rules
- its **`forbid`** anti-patterns
- and (if any) the user's **regen comment** for the asset

You can inspect any composed prompt before generating — **Generate → 🔍 Prompt preview** tab.

---

## The learn loop

1. You mark assets in the **Review** tab and click **Save verdicts**.
2. Click **Run --learn**. Each regen comment is sent to Gemini Flash text-only, which distils it into a structured rule (polarity + clause).
3. The rule becomes a **candidate** under the asset's `step_type` in `step_patterns.json`.
4. When the same rule appears a **second time** in a future review pass, it is **promoted** into permanent `best_practices` or `forbid`.
5. Future `compose()` calls automatically include it. The agent gets better, permanently.

Curate the brain manually on the **Memory** tab.

---

## Where files live

```
projects/level_NN_name/
├── items_config.json          ← your GDD
├── staging/*.png              ← generated outputs, pre-postprocess
├── final/*.png                ← postprocessed (bg removed, cropped)
├── approved/*.png             ← promoted (phase 7)
├── approved_ids.json          ← Review save target
├── regen_queue.json           ← Review save target
├── cost_log.jsonl             ← per-call cost record
└── agent_log.jsonl            ← compose() decisions
pipeline/step_patterns.json    ← the agent's persistent memory
```

---

## Backends

- **`google_flash`** (default) — `gemini-2.5-flash-image` via Vertex. Fast, good instruction-following, cheap.
- **`fal_nb2`** — Fal Nano-Banana 2 Edit. Quality baseline.
- **`fal_nb_pro`** — Fal Nano-Banana Pro. Best for hero anchors, slower & pricier.

Pick per-phase from the dropdown in the Generate tab.

---

## Backward compatibility

V6 is opt-in per asset. Any state that declares `step_type` uses the new prompt agent; any state without it falls back to the V5 envelope. Levels can also force V5 with `"pipeline_version": 5` at the top of items_config.json.
