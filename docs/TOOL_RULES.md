# Shine It Items — Tool Sprite Generation Rules

These rules apply to **every level**, every tool sprite, every tier (A/B/C).
Tools are overlay sprites the engine drags onto dirty surfaces. They must read
as premium-product hero icons — fully visible, isolated, no embedded effects.

## Orientation (mandatory)

**Diagonal side-profile, in-plane rotation.** Not 3D perspective foreshortening.

- The tool is shown in **full side profile** — entire length clearly visible
- Rotated **diagonally inside the 2D picture plane**
- **Handle / grip → lower-right of frame**
- **Business-end → upper-left of frame**
  - Brush bristles, scrubber pad → side of the cluster visible, pointing up-left
  - Spray nozzle, hose tip, hairdryer barrel → opening pointing up-left
  - Tweezers, lint roller, blower nozzle → tip pointing up-left
- No extreme foreshortening, no perspective distortion, no "coming out of the screen"

Reference examples (in `references/tool_orientation/`):
- `co_trang_diem 1.png` — powder brush (bristles upper-left, handle lower-right)
- `nhip 1.png` — tweezers (tips upper-left, body lower-right)
- (user-provided trio): purple grinder, pink powder brush, rose-gold tweezers

## Visibility (mandatory)

- **Business-end is clearly visible** but oriented up-left
- For brushes/scrubbers: you see the SIDE of the bristle cluster, fanning toward
  upper-left. The bristles are not pointing AT the camera and not pointing
  STRAIGHT AWAY — they're shown from the side so you see the fluffy texture
  while reading as "pointing away from the handle"
- For nozzles/tips: same — side profile, opening visible, oriented up-left

## Isolation (mandatory)

The sprite must contain ONLY the tool itself. **Never include:**

- Water, mist, spray, droplets
- Foam, bubbles, suds, soap
- Air streams, heat lines, steam
- Lint, fluff, debris attached
- Sparks, motion lines, effect VFX
- Hands gripping the tool (unless the tool IS a hand/glove)
- Surfaces, ground, environments, props
- Cast shadows on objects (only a soft drop shadow directly under the tool)

The tool is dry, clean, idle, and isolated on a transparent / flat white
background.

## Lighting + Render

- Premium product-photography render style
- Soft warm top-left lighting with gentle highlight rim
- Soft drop shadow directly under the tool
- Semi-realistic stylized 3D — not photoreal, not flat cartoon
- Smooth gradients, subtle material detail (chrome, plastic, bristles, etc.)
- Same render style as the canonical compact reference

## Framing

- Tool fills the frame with **~10–15% padding** on all sides
- The diagonal axis runs roughly **45° from lower-right corner toward
  upper-left corner**
- Center of mass roughly center of frame

## Prompt Template

```
A [TOOL DESCRIPTION], shown in full side-profile view as a premium product
photograph. The tool is rotated diagonally across the frame — the handle
positioned in the LOWER-RIGHT of the image, and the body extending diagonally
toward the UPPER-LEFT where the business-end (bristles / nozzle / tip) is
located. The full length of the tool is clearly visible in profile, with the
business-end visible from the side (not pointing at the camera, not pointing
straight into the background, but oriented diagonally up-left so the side of
the [bristles / nozzle / tip] reads clearly).

NO water, NO foam, NO mist, NO air stream, NO motion lines, NO sparks, NO
attached debris, NO hands holding it — just the dry isolated tool itself.

Isolated on flat white background. Semi-realistic stylized 3D mobile-game
prop render in the same premium product-photography style as the reference.
Soft warm top-left lighting, gentle highlight rim, soft drop shadow directly
under the tool. Centered, single object, 15% padding around object.
```

## Generation Method (locked)

- **Always I2I** from the canonical style reference
  (`references/style_anchor_compact.png`)
- Model: Nano-Banana 2 Edit (`fal-ai/nano-banana-2/edit`)
- The compact reference locks the render style; the prompt swaps the subject
  and dictates the diagonal-profile orientation

## Library Persistence

Tools generated once go into `projects/tools/final/` and are referenced in
`projects/tools/tools_manifest.json`. Future levels reuse them — Phase 4
dedups against the manifest and skips already-generated tools.

## Quick Rejection Checklist

- ❌ Nozzle/tip pointing AT the camera (coming out of screen)
- ❌ Foreshortening / 3D perspective rather than flat 2D rotation
- ❌ Water spray / foam / motion lines / hand in frame
- ❌ Tool only partially visible (cropped business end)
- ❌ Tool on a surface / ground / context background
- ✅ Full side profile, diagonal rotation, handle lower-right, tip upper-left,
  business end visible from the side, isolated on white/transparent
