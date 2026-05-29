"""Paint database loader and validator.

The CSV at data/paints.csv is the source of record. Schema:

    name,brand,line,sku,hex,category,notes

- name:     human-readable paint name (e.g. "Mephiston Red")
- brand:    "citadel" | "vallejo"
- line:     specific product line (e.g. "citadel_base", "vallejo_model_color",
            "vallejo_model_air"). Model Air is brushable but handles
            differently from Model Color and is kept distinct.
- sku:      vendor SKU or stock code; may be empty
- hex:      "#RRGGBB"
- category: "solid" | "metallic" | "wash" | "contrast" | "technical" | ...
            Only "solid" is included in the candidate pool. Others are
            kept in the CSV so we can audit.
- notes:    free text
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np

from .color import hex_to_lab, parse_hex


@lru_cache(maxsize=4096)
def _cached_hex_to_lab(hex_value: str) -> np.ndarray:
    return hex_to_lab(hex_value)


SOLID_CATEGORY = "solid"
DEFAULT_CSV = Path(__file__).resolve().parent.parent.parent / "data" / "paints.csv"


@dataclass(frozen=True)
class Paint:
    name: str
    brand: str
    line: str
    sku: str
    hex: str
    category: str
    notes: str

    @property
    def lab(self) -> np.ndarray:
        return _cached_hex_to_lab(self.hex)


def load_paints(path: Path | None = None, *, only_solid: bool = True) -> list[Paint]:
    csv_path = path or DEFAULT_CSV
    if not csv_path.exists():
        raise FileNotFoundError(f"paint database not found: {csv_path}")
    paints: list[Paint] = []
    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(_strip_comments(f))
        required = {"name", "brand", "line", "hex", "category"}
        missing = required - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"paint CSV missing columns: {sorted(missing)}")
        for row in reader:
            category = (row.get("category") or "").strip().lower()
            if only_solid and category != SOLID_CATEGORY:
                continue
            paints.append(
                Paint(
                    name=row["name"].strip(),
                    brand=row["brand"].strip().lower(),
                    line=row["line"].strip().lower(),
                    sku=(row.get("sku") or "").strip(),
                    hex=row["hex"].strip(),
                    category=category,
                    notes=(row.get("notes") or "").strip(),
                )
            )
    return paints


def _strip_comments(lines):
    """Skip header comment lines starting with '#'."""
    for line in lines:
        if line.lstrip().startswith("#"):
            continue
        yield line


def filter_paints(
    paints: list[Paint],
    *,
    brand_filter: list[str] | None = None,
    already_owned: list[str] | None = None,
) -> tuple[list[Paint], list[Paint]]:
    """Return (candidate pool, owned pool) given user constraints.

    Owned paints are always candidates (free in the cover problem) regardless
    of brand_filter. Candidate pool is brand-filtered, deduplicated by name.

    FIXME: paint identity here (and throughout the optimizer + CLI) is the
    bare `name` string. Adding Army Painter introduced cross-brand name
    collisions (e.g. "Ultramarine Blue" exists in both vallejo and
    army_painter at different hex), and last-write-wins on the by-name
    dict can silently pick the wrong Paint. The planned refactor moves
    to a globally-unique identity (brand:name) and accepts a structured
    {"name", "brand"} form in already_owned. See the xfail test
    tests/test_db.py::test_filter_paints_colliding_name_should_disambiguate.
    """
    by_name = {p.name: p for p in paints}
    owned: list[Paint] = []
    if already_owned:
        for name in already_owned:
            p = by_name.get(name)
            if p is not None:
                owned.append(p)
    if brand_filter:
        wanted = {b.lower() for b in brand_filter}
        candidates = [
            p for p in paints
            if p.brand in wanted or p.line in wanted
        ]
    else:
        candidates = list(paints)
    # Ensure owned are in the candidate list.
    cand_names = {p.name for p in candidates}
    for p in owned:
        if p.name not in cand_names:
            candidates.append(p)
            cand_names.add(p.name)
    return candidates, owned


def validate_db(path: Path | None = None) -> dict:
    """Sanity-check the CSV. Returns a report dict."""
    csv_path = path or DEFAULT_CSV
    errors: list[str] = []
    warnings: list[str] = []
    seen_names: dict[tuple[str, str], int] = {}
    rows = 0
    by_brand: dict[str, int] = {}
    by_line: dict[str, int] = {}
    by_category: dict[str, int] = {}

    with csv_path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(_strip_comments(f))
        for i, row in enumerate(reader, start=1):
            rows += 1
            name = (row.get("name") or "").strip()
            if not name:
                errors.append(f"row {i}: empty name")
                continue
            brand_key = (row.get("brand") or "").strip().lower()
            seen_names[(brand_key, name)] = seen_names.get((brand_key, name), 0) + 1
            hex_v = (row.get("hex") or "").strip()
            try:
                parse_hex(hex_v)
            except ValueError as e:
                errors.append(f"row {i} ({name}): {e}")
            brand = (row.get("brand") or "").strip().lower()
            line = (row.get("line") or "").strip().lower()
            category = (row.get("category") or "").strip().lower()
            if not brand:
                warnings.append(f"row {i} ({name}): missing brand")
            if not line:
                warnings.append(f"row {i} ({name}): missing line")
            by_brand[brand] = by_brand.get(brand, 0) + 1
            by_line[line] = by_line.get(line, 0) + 1
            by_category[category] = by_category.get(category, 0) + 1

    for (brand_key, name), count in seen_names.items():
        if count > 1:
            errors.append(f"duplicate name within brand: {brand_key} / {name!r} (x{count})")

    return {
        "path": str(csv_path),
        "rows": rows,
        "errors": errors,
        "warnings": warnings,
        "counts": {
            "by_brand": by_brand,
            "by_line": by_line,
            "by_category": by_category,
        },
    }
