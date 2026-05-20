import pytest

from palette_optimizer.db import filter_paints, load_paints, validate_db


def test_default_db_loads_solids():
    paints = load_paints()
    assert len(paints) > 100, "expected a sizable solid pool from upstream community DB"
    # Hex values must parse and be uppercased.
    for p in paints[:10]:
        assert p.hex.startswith("#") and len(p.hex) == 7


def test_validate_default_db_has_no_errors():
    report = validate_db()
    assert report["errors"] == [], report["errors"][:5]


def test_brand_filter_matches_brand_or_line():
    paints = load_paints()
    citadel_only, _ = filter_paints(paints, brand_filter=["citadel"])
    assert citadel_only and all(p.brand == "citadel" for p in citadel_only)

    model_air_only, _ = filter_paints(paints, brand_filter=["vallejo_model_air"])
    assert model_air_only and all(p.line == "vallejo_model_air" for p in model_air_only)


def test_owned_paints_included_even_when_brand_filter_excludes_them():
    paints = load_paints()
    # Pick an owned vallejo paint while filtering to citadel only — should still appear.
    vallejo = next(p for p in paints if p.brand == "vallejo")
    candidates, owned = filter_paints(
        paints, brand_filter=["citadel"], already_owned=[vallejo.name]
    )
    assert any(p.name == vallejo.name for p in candidates)
    assert any(p.name == vallejo.name for p in owned)


def test_metallics_and_washes_excluded_from_solids():
    paints = load_paints()
    names = {p.name for p in paints}
    # A few well-known metallics that should NOT be in the solid pool.
    for excluded in ("Leadbelcher", "Retributor Armour", "Auric Armour Gold"):
        assert excluded not in names, f"{excluded} should be excluded as metallic"
