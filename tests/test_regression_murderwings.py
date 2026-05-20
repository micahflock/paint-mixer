"""Regression test against the murderwings color reference.

The reference HTML contains ~26 hand-picked target colors with annotated
Citadel and Vallejo paint suggestions. We use those annotations as
loose ground truth: the optimizer's recommendations should largely
agree, and where they diverge it should be inspectable.

Assertions are intentionally loose because:
- The reference annotations include paints that may not be in our DB
  (different paint lines, discontinued paints, paint codes vs names).
- The reference often suggests multiple paints per target ("base + mid")
  while the optimizer picks one blend.
- The optimizer is allowed to find a better Lab-space match than the
  hand pick.

The test prints divergences so they can be inspected, and fails only
when the optimizer is *worse* than expected: too many missed targets,
or zero overlap with hand picks across the whole palette.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from palette_optimizer.cli import run_optimize
from palette_optimizer.db import load_paints


REFERENCE_HTML = Path(__file__).resolve().parent.parent / "murderwings_color_reference.html"

# JS arrays in the reference that contain annotated colors.
DATA_ARRAYS = ("BASE_COLORS", "RUDDER_COLORS", "TRIM_COLORS", "WEATHERING_COLORS")

OBJECT_RE = re.compile(r"\{[^{}]*\}", re.DOTALL)
FIELD_RES = {
    "id":      re.compile(r"id:\s*'([^']*)'"),
    "name":    re.compile(r"name:\s*'([^']*)'"),
    "hex":     re.compile(r"hex:\s*'(#[0-9A-Fa-f]{6})'"),
    "citadel": re.compile(r"citadel:\s*'([^']*)'"),
    "vallejo": re.compile(r"vallejo:\s*'([^']*)'"),
}


def _extract_array(text: str, name: str) -> str:
    start = text.index(f"const {name} = [")
    depth = 0
    i = text.index("[", start)
    for j in range(i, len(text)):
        c = text[j]
        if c == "[":
            depth += 1
        elif c == "]":
            depth -= 1
            if depth == 0:
                return text[i:j + 1]
    raise ValueError(f"no closing ] for {name}")


def _parse_objects(block: str) -> list[dict]:
    out = []
    for m in OBJECT_RE.finditer(block):
        body = m.group(0)
        rec = {}
        for k, regex in FIELD_RES.items():
            mm = regex.search(body)
            if mm:
                rec[k] = mm.group(1)
        if "hex" in rec and "name" in rec:
            out.append(rec)
    return out


def parse_reference(html_path: Path) -> list[dict]:
    text = html_path.read_text(encoding="utf-8")
    entries: list[dict] = []
    for arr in DATA_ARRAYS:
        block = _extract_array(text, arr)
        entries.extend(_parse_objects(block))
    return entries


def extract_paint_names(annotation: str, paint_names: set[str]) -> set[str]:
    """Return the subset of `paint_names` that appear as substrings of `annotation`.

    Tries longest-first to avoid e.g. matching "Red" inside "Mephiston Red".
    """
    if not annotation:
        return set()
    hits = set()
    text = annotation
    for name in sorted(paint_names, key=len, reverse=True):
        if name in text:
            hits.add(name)
    return hits


@pytest.fixture(scope="module")
def reference_entries():
    if not REFERENCE_HTML.exists():
        pytest.skip(f"reference not present at {REFERENCE_HTML}")
    return parse_reference(REFERENCE_HTML)


@pytest.fixture(scope="module")
def paint_names():
    return {p.name for p in load_paints()}


def test_reference_parses_with_substantial_entries(reference_entries):
    # Sanity: the reference should yield a meaningful number of targets.
    assert len(reference_entries) >= 20, f"only {len(reference_entries)} parsed"
    for e in reference_entries:
        assert e["hex"].startswith("#") and len(e["hex"]) == 7
        assert e["name"]


def test_reference_annotations_overlap_paint_db(reference_entries, paint_names):
    """Sanity: at least some annotated paint names should be in our DB.
    If this fails, the DB and the reference are out of sync."""
    matches_per_entry = []
    for e in reference_entries:
        cit = extract_paint_names(e.get("citadel", ""), paint_names)
        val = extract_paint_names(e.get("vallejo", ""), paint_names)
        matches_per_entry.append(len(cit) + len(val))
    matched_entries = sum(1 for n in matches_per_entry if n > 0)
    # At least half of the reference entries should reference at least one
    # paint that exists in our DB.
    assert matched_entries >= len(reference_entries) // 2, (
        f"only {matched_entries} of {len(reference_entries)} entries had any "
        f"DB-known paint in their annotations"
    )


def test_optimizer_largely_agrees_with_reference(reference_entries, paint_names, capsys):
    """End-to-end: run the optimizer on the reference targets, compare
    recommendations against hand picks. Loose assertions; prints diff."""
    targets = [
        {"name": e["name"], "hex": e["hex"]} for e in reference_entries
    ]
    out = run_optimize({
        "targets": targets,
        "brand_filter": ["citadel", "vallejo_model_color", "vallejo_model_air"],
        "max_paints": 16,
        "tolerance_delta_e": 5.0,
        "max_paints_per_recipe": 3,
    })

    total = out["summary"]["targets_total"]
    hit = out["summary"]["targets_hit"]
    # At least 75% of targets should be hit within tolerance ΔE2000 ≤ 5.
    assert hit / total >= 0.75, f"only {hit}/{total} hit within tolerance"

    # Build hand-picked sets per target.
    hand_per_target: dict[str, set[str]] = {}
    for e in reference_entries:
        names = (
            extract_paint_names(e.get("citadel", ""), paint_names)
            | extract_paint_names(e.get("vallejo", ""), paint_names)
        )
        hand_per_target[e["name"]] = names

    # Compare each recipe's recommended paints against the hand pick.
    overlap_counts = []
    divergences = []
    for r in out["recipes"]:
        tname = r["target"]["name"]
        recipe_paints = {c["paint"] for c in r["blend"]}
        hand = hand_per_target.get(tname, set())
        if not hand:
            continue  # nothing to compare against
        overlap = recipe_paints & hand
        overlap_counts.append(len(overlap))
        if not overlap:
            divergences.append((tname, recipe_paints, hand, r["delta_e"]))

    # Print diff for inspection (visible with `pytest -s`).
    if divergences:
        print(f"\n{len(divergences)} recipes diverge from hand pick:")
        for tname, opt, hand, de in divergences[:10]:
            print(f"  {tname}  ΔE={de}")
            print(f"    optimizer: {sorted(opt)}")
            print(f"    handpick : {sorted(hand)}")

    # Loose check: at least one recipe should agree with the hand pick.
    # (Across ~26 targets, hitting at least a few is a low bar — failure
    # here means something is badly wrong, like a coordinate-space bug.)
    assert sum(overlap_counts) >= 3, (
        f"optimizer overlapped hand picks on only {sum(overlap_counts)} paints "
        f"across the whole palette — something is likely miscalibrated"
    )

    # Capture the structured result for visibility.
    print(f"\nsummary: {hit}/{total} hit within tolerance ΔE ≤ 5.0; "
          f"{out['summary']['paints_recommended']} paints recommended; "
          f"{sum(overlap_counts)} recipe-component overlaps with hand picks")
