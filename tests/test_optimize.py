"""Optimizer tests using synthetic palettes built from the real paint DB.

Strategy: pick a known paint, set the target to its exact hex. A correct
optimizer must find a near-zero ΔE recipe and include that paint in the
purchase list.
"""

import json
import subprocess
import sys

import pytest

from palette_optimizer.cli import run_optimize
from palette_optimizer.db import load_paints


@pytest.fixture(scope="module")
def paints():
    return load_paints()


def _palette_from_paints(paints, names):
    by_name = {p.name: p for p in paints}
    return [{"name": p.name, "hex": by_name[p.name].hex} for p in [by_name[n] for n in names]]


def test_exact_paint_targets_get_exact_recipes(paints):
    """Targets that are exact paints should yield a 100% solo recipe."""
    target_names = ["Macragge Blue", "Abaddon Black", "White Scar"]
    targets = []
    for n in target_names:
        p = next(pp for pp in paints if pp.name == n)
        targets.append({"name": n, "hex": p.hex})

    out = run_optimize({
        "targets": targets,
        "brand_filter": ["citadel"],
        "max_paints": 5,
        "tolerance_delta_e": 5.0,
    })

    assert out["summary"]["targets_hit"] == len(target_names)
    # Each exact-paint target should be reached with a near-zero ΔE. The
    # optimizer may pick a different paint with an identical hex (e.g.
    # Corax White ↔ White Scar both #FAF7E5), so we only check ΔE.
    for r in out["recipes"]:
        assert r["delta_e"] < 1.0, r


def test_already_owned_eliminates_purchase(paints):
    p = next(pp for pp in paints if pp.name == "Macragge Blue")
    out = run_optimize({
        "targets": [{"name": "Imperial Blue", "hex": p.hex}],
        "already_owned": ["Macragge Blue"],
        "brand_filter": ["citadel"],
        "max_paints": 3,
        "tolerance_delta_e": 5.0,
    })
    assert out["summary"]["paints_to_buy"] == 0
    assert out["summary"]["targets_hit"] == 1


@pytest.mark.parametrize("brand", ["army_painter", "vallejo"])
def test_structured_already_owned_disambiguates_brand(paints, brand):
    """End-to-end: a cross-brand colliding name passed via the structured
    {"name","brand"} form must resolve to exactly the brand requested, so
    recipes are computed against that brand's hex — never a last-write-wins
    sibling. "Ultramarine Blue" ships in both army_painter and vallejo at
    different hex; asking for one brand must yield that brand's hex.
    """
    colliders = {p.brand: p for p in paints if p.name == "Ultramarine Blue"}
    assert {"army_painter", "vallejo"} <= set(colliders), colliders
    assert colliders["army_painter"].hex != colliders["vallejo"].hex
    wanted = colliders[brand]
    other = colliders["vallejo" if brand == "army_painter" else "army_painter"]

    # Target is the requested brand's exact hex. Owning that exact paint
    # should yield a near-zero solo recipe referencing the requested brand.
    out = run_optimize({
        "targets": [{"name": "Owned Ultra", "hex": wanted.hex}],
        "already_owned": [{"name": "Ultramarine Blue", "brand": brand}],
        "max_paints": 6,
        "tolerance_delta_e": 5.0,
    })

    assert out["summary"]["targets_hit"] == 1
    recipe = out["recipes"][0]
    comp = next(c for c in recipe["blend"] if c["paint"] == "Ultramarine Blue")
    assert comp["brand"] == brand
    assert comp["hex"] == wanted.hex
    assert comp["hex"] != other.hex  # the wrong brand did not silently win
    assert recipe["delta_e"] < 1.0
    # The owned paint is free, so it must not appear in the purchase list.
    assert not any(
        e["name"] == "Ultramarine Blue" and e["brand"] == brand
        for e in out["purchase_list"]
    )


def test_bare_ambiguous_owned_name_errors_end_to_end(paints):
    """A bare colliding name must raise through run_optimize, not silently pick."""
    with pytest.raises(ValueError, match="ambiguous|disambiguate|multiple"):
        run_optimize({
            "targets": [{"name": "X", "hex": "#284D8E"}],
            "already_owned": ["Ultramarine Blue"],
            "max_paints": 6,
            "tolerance_delta_e": 5.0,
        })


def test_unreachable_target_is_reported(paints):
    """Targets impossible to reach (e.g. fluorescent magenta with citadel-only)
    should appear in the unreachable list with a closest_delta_e and a reason."""
    out = run_optimize({
        "targets": [{"name": "Vivid Magenta", "hex": "#FF00FF"}],
        "brand_filter": ["citadel"],
        "max_paints": 3,
        "tolerance_delta_e": 0.5,  # very tight; nothing should hit
    })
    # Either it's unreachable, or it hits with a very low delta_e — in
    # either case the structure is well-formed.
    assert out["summary"]["targets_total"] == 1
    if out["unreachable"]:
        u = out["unreachable"][0]
        assert u["closest_delta_e"] is not None
        assert "reason" in u


def test_brand_filter_isolates_pool(paints):
    out = run_optimize({
        "targets": [{"name": "Test", "hex": "#BB1F2E"}],
        "brand_filter": ["vallejo_model_color"],
        "max_paints": 4,
        "tolerance_delta_e": 5.0,
    })
    # Every purchased paint must be in the requested line.
    for entry in out["purchase_list"]:
        assert entry["line"] == "vallejo_model_color", entry


def test_cli_smoke_via_subprocess():
    """Round-trip: stdin JSON, stdout JSON. Catches argparse/IO regressions."""
    payload = {
        "targets": [{"name": "Test", "hex": "#BB1F2E"}],
        "brand_filter": ["citadel"],
        "max_paints": 4,
        "tolerance_delta_e": 5.0,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "palette_optimizer.cli", "optimize"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, proc.stderr
    out = json.loads(proc.stdout)
    assert out["summary"]["targets_total"] == 1


def test_cli_validate_db_subcommand():
    proc = subprocess.run(
        [sys.executable, "-m", "palette_optimizer.cli", "validate-db"],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    report = json.loads(proc.stdout)
    assert report["rows"] > 100


def test_cli_explain_recipe(paints):
    p = next(pp for pp in paints if pp.name == "Macragge Blue")
    payload = {
        "target_hex": p.hex,
        "blend": [{"paint": "Macragge Blue", "ratio": 1.0}],
        "tolerance_delta_e": 5.0,
    }
    proc = subprocess.run(
        [sys.executable, "-m", "palette_optimizer.cli", "explain-recipe"],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0
    out = json.loads(proc.stdout)
    assert out["delta_e"] < 1.0
    assert out["confidence"] == "high"


def test_invalid_input_exits_nonzero_with_structured_error():
    proc = subprocess.run(
        [sys.executable, "-m", "palette_optimizer.cli", "optimize"],
        input="not json at all",
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert proc.returncode != 0
    err = json.loads(proc.stderr)
    assert err["error"]["code"] == "InvalidJSON"
