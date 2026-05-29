# My paint inventory

Source: photographed and extracted 2026-05-29.

The slash command reads this file when it starts so the optimizer
treats these as `already_owned` (free in the cover problem).

## Army Painter — Warpaints Fanatic

Solid acrylics:

- Phalanx Blue
- Deep Grey
- Matt White
- Ultramarine Blue
- Matt Black
- Greenskin
- Leather Brown
- Pure Red
- Daemonic Yellow

Metallics (excluded from blending — pigment is mica-coated, doesn't obey simple color mixing):

- Greedy Gold
- Plate Mail Metal

Effects / glow paints (not regular opaque acrylics):

- Plasma Coil Glow
- Radiation Glow

Washes:

- Strong Tone (Quickshade wash)

Primers / mediums:

- Brush-On Primer

## Status

Army Painter is NOT yet in `data/paints.csv`. The optimizer currently
only knows Citadel and Vallejo paints, so passing these names as
`already_owned` won't match anything and they'll be ignored. To make
this inventory actually usable by the optimizer, the Army Painter
Warpaints Fanatic line needs to be added to the paint DB with hex
values (see `scripts/build_paint_db.py` for the pattern; Army Painter's
official site publishes swatches per paint).

Until then, this file is reference-only.
