# Foam — Realistic Flat Suds

## Use case
Object covered in soap foam during a cleaning step, where:
- foam should be CLEARLY VISIBLE (white, opaque, dominant)
- but stay FLAT / LOW PROFILE on the surface (no cartoony whipped-cream pile)
- and MUST NOT drip off the edges (no foam blobs hanging beyond the silhouette)

Use this for sub-part foam states (`cover_foamed`, `shell_foamed`, `filter_X_foamed`),
i.e. detached parts shown isolated on a flat background where dripping foam would look wrong.

For chain states where the whole AC is buried in foam, prefer `foam/full_body_coverage.md`
instead (more dramatic, full-coverage variant).

## Snippet

> Take the stained [OBJECT] in the reference image. PRESERVE the EXACT SAME shape, size,
> position and framing. The [OBJECT] surface is now COMPLETELY COVERED in a CLEARLY VISIBLE
> layer of PURE WHITE soap foam — foam is THE DOMINANT VISUAL across the [OBJECT], completely
> OBSCURING the stains underneath. White foam covers 100% of the visible [OBJECT] face. Foam
> is REALISTIC SOAP SUDS texture (fine bubbly small-bubble texture, like dish soap foam) —
> clearly bright white with subtle bubble dots, NOT yellowed, NOT cartoonish whipped cream.
> Foam keeps a MODERATE FLAT-ish profile — visible thickness but not piled into a tall mound,
> sitting close to the surface like a generous coating of bath foam. ABSOLUTELY NO FOAM
> DRIPPING OFF EDGES — foam edges stay neatly within the [OBJECT] silhouette boundary,
> NO foam blobs hanging off the sides, NO drips off the bottom edges, NO sagging foam.
> [OBJECT] position, framing, lighting and isolated grey background remain identical to the reference.

## Companion negatives

- NO foam dripping off edges
- NO foam blobs hanging beyond the silhouette
- NO sagging foam under its own weight
- NO whipped-cream / cartoon puffy foam
- NO yellowed or grey foam (must read as bright clean white)
- NO foam covering less than 100% of the surface (must fully obscure stains)

## Compatible with

- `shape_rules/preserve_silhouette.md` — for subpart consistency across states
- `shape_rules/front_on_view.md` — when the part is shown head-on (covers, filters)
- `scrubbed/wipe_arcs_deflated.md` — the next state in the chain (foam after scrubbing)
- `lighting/soft_warm_top_left.md` — default lighting

## Tested on

- `level_06_aircon` — `shell_foamed`, `filter_1_foamed` (round 3, May 25 2026)
- (extensible: any part-level foam state in any level)

## Why this prompt works (gotchas)

- "Take the stained X in the reference image" anchors the SHAPE. Without this, NB2 can
  reinterpret the silhouette and introduce drift.
- "COMPLETELY COVERED ... DOMINANT VISUAL" is needed because earlier weaker phrasings
  ("a layer of foam") were under-applied and the stains stayed visible. The model
  conservatively keeps existing detail unless you push hard.
- "Flat-ish profile" + "NOT piled high" prevents the cartoony whipped-cream mound.
- "NO FOAM DRIPPING OFF EDGES" is explicit because, without it, NB2 routinely adds long
  white drips beyond the rectangle. The drips look ridiculous on isolated subparts
  (the foam appears to defy gravity since there's no AC body below it).
- `[OBJECT]` placeholder: replace with `cover panel`, `shell housing`, `filter screen`, etc.

## Failure modes seen

1. **Foam too thin / invisible** — happens if you over-emphasize "thin" or "flat".
   Counter by adding "CLEARLY VISIBLE, DOMINANT VISUAL, completely covers".
2. **Foam covers only top half** — happens when the model interprets foam as gravity-driven.
   Counter by adding "100% of the visible face" and explicit area coverage.
3. **Stains still showing through foam** — model preserved too much of source.
   Counter with "completely OBSCURING the stains underneath".

## Cost to derive

~$0.18 (round 3 needed 2 retries on shell + 1 on filter before locking in the wording).
