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

Army Painter Warpaints Fanatic is in `data/paints.csv` as of the
"Add Army Painter to paint DB" commit. The optimizer recognizes all
solid paints listed above.

How this gets passed to the optimizer: the slash command sends each
owned paint to `already_owned` in the structured
`{"name": "...", "brand": "army_painter"}` form, so cross-brand name
collisions (e.g. Army Painter "Ultramarine Blue" #284D8E vs Vallejo
"Ultramarine Blue" #383967) resolve to the brand listed here.
