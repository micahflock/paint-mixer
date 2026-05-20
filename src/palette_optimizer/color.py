"""Color science primitives.

All distance comparisons use CIELab + CIEDE2000. RGB and HSL distances
are inadequate for perceptual matching and are not used.

Mixing model: ratio-weighted mean of CIELab coordinates. This is an
approximation. The physically correct model for opaque pigment mixing
is Kubelka-Munk, which requires per-pigment K/S coefficients that are
not available for hobby paints. The Lab-weighted mean reproduces value
and hue averaging acceptably but underestimates chroma loss when mixing
near-complementary colors. Treat predicted_hex as guidance, not truth.
"""

from __future__ import annotations

from dataclasses import dataclass

import warnings

import numpy as np

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*SciPy.*")
    warnings.filterwarnings("ignore", message=".*Matplotlib.*")
    import colour


HEX_PREFIX = "#"


def parse_hex(value: str) -> tuple[int, int, int]:
    s = value.strip().lstrip(HEX_PREFIX)
    if len(s) == 3:
        s = "".join(c * 2 for c in s)
    if len(s) != 6:
        raise ValueError(f"invalid hex color: {value!r}")
    try:
        r = int(s[0:2], 16)
        g = int(s[2:4], 16)
        b = int(s[4:6], 16)
    except ValueError as e:
        raise ValueError(f"invalid hex color: {value!r}") from e
    return r, g, b


def format_hex(rgb: tuple[int, int, int]) -> str:
    r, g, b = (max(0, min(255, int(round(c)))) for c in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def hex_to_lab(value: str) -> np.ndarray:
    r, g, b = parse_hex(value)
    srgb = np.array([r, g, b], dtype=float) / 255.0
    xyz = colour.sRGB_to_XYZ(srgb)
    lab = colour.XYZ_to_Lab(xyz)
    return lab


def lab_to_hex(lab: np.ndarray) -> str:
    xyz = colour.Lab_to_XYZ(lab)
    srgb = colour.XYZ_to_sRGB(xyz)
    srgb = np.clip(srgb, 0.0, 1.0) * 255.0
    return format_hex((srgb[0], srgb[1], srgb[2]))


def delta_e_2000(lab_a: np.ndarray, lab_b: np.ndarray) -> float:
    return float(colour.delta_E(lab_a, lab_b, method="CIE 2000"))


@dataclass(frozen=True)
class BlendComponent:
    """A single paint contribution to a blend.

    `lab` is cached so the optimizer doesn't reconvert from hex on every blend.
    """

    name: str
    hex: str
    lab: np.ndarray
    ratio: float


def blend_lab(components: list[BlendComponent]) -> np.ndarray:
    """Ratio-weighted mean of Lab coordinates. Ratios are normalized."""
    if not components:
        raise ValueError("blend requires at least one component")
    ratios = np.array([c.ratio for c in components], dtype=float)
    total = ratios.sum()
    if total <= 0:
        raise ValueError("blend ratios must sum to a positive value")
    weights = ratios / total
    labs = np.stack([c.lab for c in components])
    return (labs * weights[:, None]).sum(axis=0)


def confidence_label(delta_e: float, tolerance: float) -> str:
    if delta_e < tolerance / 2:
        return "high"
    if delta_e < tolerance:
        return "medium"
    return "low"
