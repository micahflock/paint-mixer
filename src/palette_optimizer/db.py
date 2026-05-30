"""Paint database loader and validator.

The CSV at data/paints.csv is the source of record. Schema:

    name,brand,line,sku,hex,category,notes

- name:     human-readable paint name (e.g. "Mephiston Red"). Not unique
            across brands; pair with `brand` (see Paint.id) for identity.
- brand:    "citadel" | "vallejo" | "army_painter"
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

    @property
    def id(self) -> str:
        """Globally-unique paint identity.

        Bare `name` is not unique: the same name can ship in multiple
        brands at different hex values (e.g. "Ultramarine Blue" exists in
        both vallejo and army_painter). The optimizer, CLI, and JSON I/O
        key on this `brand:name` identity so the correct Paint object is
        used. The brand-prefixed form is internal — JSON output still
        reports the bare `name` (plus brand/line) for humans.
        """
        return f"{self.brand}:{self.name}"


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


def _index_paints(
    paints: list[Paint],
) -> tuple[dict[str, Paint], dict[str, list[Paint]]]:
    """Build (by-id, by-name) lookup maps over a paint list.

    `by_id` keys on the globally-unique `Paint.id` (brand:name). `by_name`
    groups paints sharing a bare name so callers can detect cross-brand
    collisions rather than silently picking a last-write-wins entry.
    """
    by_id: dict[str, Paint] = {}
    by_name: dict[str, list[Paint]] = {}
    for p in paints:
        by_id[p.id] = p
        by_name.setdefault(p.name, []).append(p)
    return by_id, by_name


def resolve_paint_ref(
    item: str | dict,
    by_id: dict[str, Paint],
    by_name: dict[str, list[Paint]],
) -> Paint | None:
    """Resolve a paint reference to a concrete Paint.

    A reference is either:
      - a bare name string, resolved only when the name is globally unique
        across the DB; an ambiguous bare name raises ValueError pointing at
        the structured form, and
      - a {"name": "...", "brand": "..."} dict, which always disambiguates.

    Returns None when the reference matches no paint (e.g. a name from a
    brand that isn't in the DB), leaving it to the caller to ignore or error.
    """
    if isinstance(item, dict):
        name = (item.get("name") or "").strip()
        brand = (item.get("brand") or "").strip().lower()
        if not name or not brand:
            raise ValueError(
                "structured paint reference requires both 'name' and 'brand': "
                f"{item!r}"
            )
        return by_id.get(f"{brand}:{name}")

    name = str(item).strip()
    matches = by_name.get(name, [])
    if len(matches) > 1:
        brands = sorted(p.brand for p in matches)
        raise ValueError(
            f"paint name {name!r} is ambiguous across brands {brands}; "
            f"disambiguate with the structured form "
            f'{{"name": "{name}", "brand": "<one of {brands}>"}}'
        )
    return matches[0] if matches else None


def filter_paints(
    paints: list[Paint],
    *,
    brand_filter: list[str] | None = None,
    already_owned: list[str | dict] | None = None,
) -> tuple[list[Paint], list[Paint]]:
    """Return (candidate pool, owned pool) given user constraints.

    Owned paints are always candidates (free in the cover problem) regardless
    of brand_filter. Candidate pool is brand-filtered.

    Paint identity here (and throughout the optimizer + CLI) is the
    globally-unique `Paint.id` (brand:name), not the bare `name`. Adding
    Army Painter introduced cross-brand name collisions (e.g. "Ultramarine
    Blue" exists in both vallejo and army_painter at different hex), so
    `already_owned` items may be either a bare string (resolved only when
    the name is unique across the DB) or a {"name", "brand"} dict that
    always disambiguates. An ambiguous bare name raises ValueError. See
    resolve_paint_ref.
    """
    by_id, by_name = _index_paints(paints)
    owned: list[Paint] = []
    if already_owned:
        for item in already_owned:
            p = resolve_paint_ref(item, by_id, by_name)
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
    # Ensure owned are in the candidate list (keyed on identity, not name).
    cand_ids = {p.id for p in candidates}
    for p in owned:
        if p.id not in cand_ids:
            candidates.append(p)
            cand_ids.add(p.id)
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
