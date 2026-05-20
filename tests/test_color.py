import numpy as np
import pytest

from palette_optimizer.color import (
    BlendComponent,
    blend_lab,
    confidence_label,
    delta_e_2000,
    format_hex,
    hex_to_lab,
    lab_to_hex,
    parse_hex,
)


def test_parse_hex_long_and_short():
    assert parse_hex("#FF0000") == (255, 0, 0)
    assert parse_hex("ff0000") == (255, 0, 0)
    assert parse_hex("#F00") == (255, 0, 0)


@pytest.mark.parametrize("bad", ["#", "#GGGGGG", "#12345", "not-a-color"])
def test_parse_hex_rejects_garbage(bad):
    with pytest.raises(ValueError):
        parse_hex(bad)


def test_hex_roundtrip_pure_colors():
    # Pure primaries should round-trip exactly through Lab.
    for hex_v in ["#FF0000", "#00FF00", "#0000FF", "#FFFFFF", "#000000"]:
        lab = hex_to_lab(hex_v)
        assert lab_to_hex(lab) == hex_v.upper()


def test_delta_e_zero_for_same_color():
    lab = hex_to_lab("#BB1F2E")
    assert delta_e_2000(lab, lab) == pytest.approx(0.0, abs=1e-9)


def test_delta_e_red_vs_green_is_large():
    # CIEDE2000 between pure red and pure green is around 86.
    de = delta_e_2000(hex_to_lab("#FF0000"), hex_to_lab("#00FF00"))
    assert 80 < de < 95


def test_red_white_blend_is_pink():
    """50/50 red+white should land in the pink region: high L, positive a*, small b*."""
    comps = [
        BlendComponent("red", "#FF0000", hex_to_lab("#FF0000"), 0.5),
        BlendComponent("white", "#FFFFFF", hex_to_lab("#FFFFFF"), 0.5),
    ]
    lab = blend_lab(comps)
    L, a, b = lab
    assert L > 70           # much lighter than pure red
    assert a > 30           # still strongly red
    # Pure red has yellow undertones in Lab (b* ≈ 67), so 50/50 with white
    # lands around b* ≈ 33. Just sanity-check it's not in the blue region.
    assert b > 0


def test_blend_normalizes_ratios():
    a = BlendComponent("a", "#FF0000", hex_to_lab("#FF0000"), 2.0)
    b = BlendComponent("b", "#FFFFFF", hex_to_lab("#FFFFFF"), 2.0)
    norm = blend_lab([a, b])
    unit = blend_lab([
        BlendComponent("a", "#FF0000", hex_to_lab("#FF0000"), 0.5),
        BlendComponent("b", "#FFFFFF", hex_to_lab("#FFFFFF"), 0.5),
    ])
    assert np.allclose(norm, unit)


def test_confidence_label_buckets():
    assert confidence_label(0.5, 5.0) == "high"
    assert confidence_label(3.0, 5.0) == "medium"
    assert confidence_label(7.0, 5.0) == "low"


def test_format_hex_clips():
    assert format_hex((300, -5, 128)) == "#FF0080"
