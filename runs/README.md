# Saved optimizer runs

Each `palette-optimizer optimize` invocation writes a timestamped record
here: `optimize-<YYYYMMDD-HHMMSS>.json`. Every file is self-contained:

```json
{
  "generated_at": "2026-05-30T08:55:41",
  "input":  { ...the palette + constraints that were optimized... },
  "result": { ...the recommended paints + recipes... }
}
```

This directory is tracked in git so runs persist across web sessions —
commit and push a new file to keep it. Override the location with
`--output-dir` or skip saving with `--no-save`.
