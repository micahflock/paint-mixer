# Paint Palette Optimizer

Given a target color palette, recommend the minimum set of hobby paints
(Citadel and/or Vallejo) needed to reproduce that palette via blending.
Output is a purchase list plus per-color blend recipes.

The intended interface is the `/palette` slash command in Claude Code:
clone this repo, open it in Claude Code, run `/palette`, and walk
through palette construction conversationally. The CLI is the engine;
Claude is the UX.

## Quick start

```sh
uv sync --extra dev
uv run palette-optimizer optimize --input palette.json
```

Or use the slash command (inside Claude Code, with this repo open):

```
/palette
```

## CLI

JSON in, JSON out. See `src/palette_optimizer/cli.py` for the full
schema. Minimal example:

```json
{
  "targets": [{"name": "Insignia Red", "hex": "#BB1F2E"}],
  "brand_filter": ["citadel", "vallejo_model_color"],
  "tolerance_delta_e": 5.0,
  "max_paints_per_recipe": 3
}
```

Subcommands:
- `optimize` — recommend paints + recipes for a palette
- `validate-db` — sanity-check the paint database
- `explain-recipe` — re-render a single recipe in detail

## Model limits

- Mixing is approximated by a ratio-weighted mean in CIELab. The
  physically correct model (Kubelka-Munk) needs K/S coefficients we
  don't have. Predicted hex underestimates chroma loss when mixing
  complements.
- Coverage/opacity is not modeled; paints are assumed opaque.
- Metallics, pearlescents, contrast paints, and washes are excluded
  from the database — they don't obey simple color mixing.
- Vallejo Model Air is brushable but handles differently from Model
  Color. The optimizer surfaces this distinction; it doesn't collapse
  them.
- Color appearance varies under different lighting and with varnish.
  Treat ΔE as guidance, not truth.

## Extending the paint database

`data/paints.csv` is the source of record. The header comment in the
file names the upstream community dataset(s) it was seeded from. Add
rows directly; run `uv run palette-optimizer validate-db` to sanity
check.
