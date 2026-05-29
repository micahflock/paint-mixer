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

**Known limitation: name collisions across brands.** A few Army Painter
paint names collide with paints in other brands at different hex
values — e.g. Army Painter "Ultramarine Blue" (#284D8E) vs Vallejo
"Ultramarine Blue" (#383967). The current `already_owned` input
accepts bare strings and resolves names with last-write-wins, so the
wrong paint can win silently. A regression test
(`tests/test_db.py::test_filter_paints_colliding_name_should_disambiguate`,
marked `xfail`) captures the failure mode. Until the refactor lands,
either:

- Restrict the run with `brand_filter: ["army_painter"]` so collisions
  can't happen, or
- Manually filter `already_owned` to only paints with names unique
  across the DB.

The planned fix: globally-unique paint identity (`brand:name`) used as
the key throughout the optimizer and CLI, with `already_owned` items
accepting either a bare string (when unambiguous) or a
`{"name": "...", "brand": "..."}` object.
