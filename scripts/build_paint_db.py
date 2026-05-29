"""Build data/paints.csv from upstream Arcturus5404/miniature-paints markdown.

Run as:
    uv run python scripts/build_paint_db.py /tmp/citadel.md /tmp/vallejo.md

Schema produced (see src/palette_optimizer/db.py):
    name,brand,line,sku,hex,category,notes

Category rules:
- "solid"     — paint that can sensibly enter a Lab-weighted blend
- "wash"      — Citadel Shade, Vallejo washes (excluded from optimizer)
- "contrast"  — Citadel Contrast, Vallejo Xpress (excluded)
- "technical" — Citadel Technical/Glaze/Dry/Spray (excluded)
- "metallic"  — known metallics by line or name keyword (excluded)
"""

from __future__ import annotations

import csv
import re
import sys
from pathlib import Path


HEX_RE = re.compile(r"`(#[0-9A-Fa-f]{6})`")


# Citadel "Set" -> (line, category)
CITADEL_SET_MAP = {
    "Base": ("citadel_base", "solid"),
    "Layer": ("citadel_layer", "solid"),
    "Foundation": ("citadel_foundation", "solid"),
    "Foundation (discontinued)": ("citadel_foundation_discontinued", "exclude"),
    "Foundation Wash (discontinued)": ("citadel_shade", "wash"),
    "Foundation Primer (discontinued)": ("citadel_spray", "exclude"),
    "Air": ("citadel_air", "solid"),
    "Shade": ("citadel_shade", "wash"),
    "Contrast": ("citadel_contrast", "contrast"),
    "Technical": ("citadel_technical", "technical"),
    "Glaze": ("citadel_glaze", "technical"),
    "Dry": ("citadel_dry", "technical"),
    "Spray": ("citadel_spray", "exclude"),
}

# Vallejo "Set" -> (line, category)
VALLEJO_SET_MAP = {
    "Model Color": ("vallejo_model_color", "solid"),
    "Model Air": ("vallejo_model_air", "solid"),
    "Game Color": ("vallejo_game_color", "solid"),
    "Game Air": ("vallejo_game_air", "solid"),
    "Panzer Aces": ("vallejo_panzer_aces", "solid"),
    "Metal Color": ("vallejo_metal_color", "metallic"),
    "Premium Air": ("vallejo_premium_air", "solid"),
    "Premium Airbrush Color": ("vallejo_premium_air", "solid"),
    "Game Wash": ("vallejo_game_wash", "wash"),
    "Game Color Wash": ("vallejo_game_wash", "wash"),
    "Game Color Special FX": ("vallejo_game_fx", "exclude"),
    "Mecha Color": ("vallejo_mecha_color", "solid"),
    "Mecha Wash": ("vallejo_mecha_wash", "wash"),
    "Xpress Color": ("vallejo_xpress", "contrast"),
    "Xpress Color Intense": ("vallejo_xpress", "contrast"),
    "Surface Primer": ("vallejo_primer", "exclude"),
    "Wash FX": ("vallejo_wash_fx", "wash"),
    "Weathering FX": ("vallejo_weathering", "exclude"),
    "Liquid Gold": ("vallejo_liquid_gold", "metallic"),
    "Hobby Paint": ("vallejo_hobby", "solid"),
    "Nocturna Models": ("vallejo_nocturna", "solid"),
    "Arte Deco": ("vallejo_arte_deco", "exclude"),
    "Arte Deco Colores Fluoresecents": ("vallejo_arte_deco", "exclude"),
}

# Citadel metallic paints by exact name. The "Set" alone can't distinguish
# these (e.g. Leadbelcher is in the Base line). List is non-exhaustive but
# catches the common offenders.
CITADEL_METALLICS = {
    "Auric Armour Gold",
    "Balthasar Gold",
    "Brass Scorpion",
    "Canoptek Alloy",
    "Gehenna's Gold",
    "Grey Knights Steel",
    "Hashut Copper",
    "Ironbreaker",
    "Iron Hands Steel",
    "Leadbelcher",
    "Liberator Gold",
    "Necron Compound",
    "Ratling Grenadier",
    "Retributor Armour",
    "Runefang Steel",
    "Runelord Brass",
    "Skullcrusher Brass",
    "Stormhost Silver",
    "Sycorax Bronze",
    "Warpfiend Grey",  # actually purple; remove if it sneaks in
    "Warplock Bronze",
}
# Drop the false positive above to be safe:
CITADEL_METALLICS.discard("Warpfiend Grey")

# Name keywords that indicate "not a regular opaque paint" regardless of line.
# Applies to all brands.
NON_PAINT_KEYWORDS = (
    "thinner",
    "medium",
    "varnish",
    "clear",         # Citadel Air "Clears" are translucent
    "transparent",   # Vallejo Transparent line is translucent
    "flow improver",
    "retarder",
    "additive",
    "primer",        # gets caught by Set too, but belt-and-braces
)
METAL_NAME_KEYWORDS = (
    "metallic ",
    " metal ",        # e.g. "Plate Mail Metal", "Old Iron Metal"
    " silver",
    " gold",
    " bronze",
    " copper",
    " brass",
    " chrome",
    " steel",
    " gunmetal",
    " pewter",
    " tin",
    "silver ",
    "gold ",
    "bronze ",
    "copper ",
    "brass ",
    "steel ",
)

# Vallejo metallic keywords (Model Color line has metallics by name).
VALLEJO_METAL_KEYWORDS = (
    "metallic ",
    "silver",
    "gold",
    "bronze",
    "copper",
    "brass",
    "chrome",
    "steel",
    "gunmetal",
    "pewter",
    "tin",
)
# Vallejo pearlescents / fluorescents we want to exclude
VALLEJO_EXCLUDE_KEYWORDS = (
    "pearl",
    "fluorescent",
    "uv ",
    "phosphor",
)

# Army Painter "Set" -> (line, category). The Warpaints Fanatic line is the
# current main range; older "Warpaints" was rebranded into Fanatic but the
# upstream still lists both.
ARMY_PAINTER_SET_MAP = {
    "Warpaints Fanatic":                ("army_painter_fanatic", "solid"),
    "Warpaints":                        ("army_painter_warpaints", "solid"),
    "Warpaints Air":                    ("army_painter_air", "solid"),
    "Warpaints Wash":                   ("army_painter_wash", "wash"),
    "Warpaints Tone":                   ("army_painter_tone", "wash"),
    "Warpaints Fanatic Wash":           ("army_painter_fanatic_wash", "wash"),
    "Warpaints Primer":                 ("army_painter_primer", "exclude"),
    "Speedpaint Set":                   ("army_painter_speedpaint", "contrast"),
    "Speedpaint Set 2.0":               ("army_painter_speedpaint", "contrast"),
    "Quickshade Washes Set":            ("army_painter_quickshade", "wash"),
    "Metallic Colours Paint Set":       ("army_painter_metallic", "metallic"),
    "Skin Tones Paint Set":             ("army_painter_skintones", "solid"),
    "Skin Tones Paint Set - Washes":    ("army_painter_skintones_wash", "wash"),
    "D&D Nolzur's Marvelous Pigments":          ("army_painter_dnd", "solid"),
    "D&D Nolzur's Marvelous Pigments Primer":   ("army_painter_dnd_primer", "exclude"),
    "D&D Nolzur's Marvelous Pigments Wash":     ("army_painter_dnd_wash", "wash"),
    "D&D Undead Set":                   ("army_painter_dnd", "solid"),
    "D&D Underdark Set":                ("army_painter_dnd", "solid"),
}

# Effect/glow keywords (translucent fluorescents). Applies across brands now
# that Army Painter has fluorescent "glow" paints in the regular range.
EFFECT_KEYWORDS = (
    "glow",
    "fluor",
    "phosphor",
    "uv ",
)


def parse_md(path: Path, brand: str, set_map: dict[str, tuple[str, str]]) -> list[dict]:
    rows: list[dict] = []
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    header_idx = None
    for i, line in enumerate(lines):
        if line.startswith("|Name|"):
            header_idx = i
            break
    if header_idx is None:
        raise ValueError(f"no table header in {path}")
    header = [h.strip() for h in lines[header_idx].strip("|").split("|")]
    name_idx = header.index("Name")
    set_idx = header.index("Set")
    hex_idx = header.index("Hex")
    code_idx = header.index("Code") if "Code" in header else None

    # Skip header + separator
    for raw in lines[header_idx + 2:]:
        if not raw.startswith("|"):
            continue
        cells = [c.strip() for c in raw.strip("|").split("|")]
        if len(cells) < len(header):
            continue
        name = cells[name_idx]
        set_name = cells[set_idx]
        hex_cell = cells[hex_idx]
        m = HEX_RE.search(hex_cell)
        if not m:
            continue
        hex_v = m.group(1).upper()
        sku = cells[code_idx] if code_idx is not None else ""

        mapping = set_map.get(set_name)
        if mapping is None:
            line_id, category = f"{brand}_unknown", "unknown"
        else:
            line_id, category = mapping

        lname = name.lower()
        # Hard excludes by name (mediums, varnishes, translucent "clears")
        if any(kw in lname for kw in NON_PAINT_KEYWORDS):
            category = "exclude"
        # Metallic / effect overrides
        if category == "solid":
            if brand == "citadel" and name in CITADEL_METALLICS:
                category = "metallic"
            elif any(kw in (" " + lname + " ") for kw in METAL_NAME_KEYWORDS):
                category = "metallic"
            elif any(kw in lname for kw in EFFECT_KEYWORDS):
                category = "exclude"  # fluorescent / glow paints are translucent
            elif brand == "vallejo" and any(kw in lname for kw in VALLEJO_EXCLUDE_KEYWORDS):
                category = "exclude"

        rows.append({
            "name": name,
            "brand": brand,
            "line": line_id,
            "sku": sku,
            "hex": hex_v,
            "category": category,
            "notes": "",
        })
    return rows


def dedupe(rows: list[dict]) -> list[dict]:
    """Same paint name appears in multiple lines (e.g. Citadel Abaddon Black
    in Base and Air). Keep the highest-priority line per name.

    Priority is: any solid Base/Layer/Model Color > Model Air > Game Color
    > others. Within a tie, keep the first encountered.
    """
    line_priority = {
        "citadel_base": 0,
        "citadel_layer": 0,
        "citadel_foundation": 1,
        "vallejo_model_color": 0,
        "vallejo_model_air": 2,
        "vallejo_game_color": 1,
        "vallejo_game_air": 3,
        "vallejo_panzer_aces": 2,
        "vallejo_premium_air": 3,
        "citadel_air": 4,
        # Army Painter: Warpaints Fanatic is the current main range; the
        # older "Warpaints" line has different hex for some shared names.
        "army_painter_fanatic": 0,
        "army_painter_warpaints": 1,
        "army_painter_air": 2,
        "army_painter_dnd": 3,
        "army_painter_skintones": 3,
        "army_painter_speedpaint": 4,
    }
    best: dict[tuple[str, str], dict] = {}
    for row in rows:
        key = (row["brand"], row["name"])
        prev = best.get(key)
        if prev is None:
            best[key] = row
            continue
        if line_priority.get(row["line"], 99) < line_priority.get(prev["line"], 99):
            best[key] = row
    return list(best.values())


def main(argv: list[str]) -> int:
    if len(argv) < 3:
        print(
            "usage: build_paint_db.py <citadel.md> <vallejo.md> [army_painter.md]",
            file=sys.stderr,
        )
        return 2
    citadel = parse_md(Path(argv[1]), "citadel", CITADEL_SET_MAP)
    vallejo = parse_md(Path(argv[2]), "vallejo", VALLEJO_SET_MAP)
    all_rows = citadel + vallejo
    if len(argv) >= 4:
        army_painter = parse_md(Path(argv[3]), "army_painter", ARMY_PAINTER_SET_MAP)
        all_rows += army_painter
    rows = dedupe(all_rows)
    rows.sort(key=lambda r: (r["brand"], r["line"], r["name"]))

    out = Path(__file__).resolve().parent.parent / "data" / "paints.csv"
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", newline="", encoding="utf-8") as f:
        f.write("# Source: Arcturus5404/miniature-paints (MIT, 2022 Rick Fleuren)\n")
        f.write("# https://github.com/Arcturus5404/miniature-paints\n")
        f.write("# Normalized via scripts/build_paint_db.py.\n")
        f.write("# Hex values are community-derived from vendor swatches; spot-check\n")
        f.write("# before trusting individual rows. Edit this file directly to correct.\n")
        f.write("# category=solid is the only category included in the optimizer.\n")
        writer = csv.DictWriter(
            f, fieldnames=["name", "brand", "line", "sku", "hex", "category", "notes"]
        )
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    print(f"wrote {out} ({len(rows)} rows)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
