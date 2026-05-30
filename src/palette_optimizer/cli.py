"""CLI entry point: JSON in, JSON out, no interactive prompts.

Subcommands:
    optimize        — recommend paints + recipes for a palette
    validate-db     — sanity-check the paint database
    explain-recipe  — re-render a single recipe in detail

Input schema (optimize):
    {
        "targets": [{"name": "Insignia Red", "hex": "#BB1F2E"}, ...],
        "brand_filter": ["citadel", "vallejo_model_color"],
        "max_paints": 12,
        "already_owned": ["Mephiston Red", {"name": "Ultramarine Blue", "brand": "army_painter"}, ...],
        "tolerance_delta_e": 5.0,
        "max_paints_per_recipe": 3
    }

`max_paints` is the budget of *new* paints to buy. Already-owned paints are
free and do NOT count against it, so the total recommended pool may exceed
max_paints (it can be up to len(already_owned) + max_paints). The output
summary reports `paints_to_buy` (capped by max_paints) separately from
`paints_recommended` (the full pool including owned).

Each `already_owned` item is either a bare name string (resolved only when
that name is globally unique across the paint DB) or a structured
{"name": "...", "brand": "..."} object. The structured form disambiguates
cross-brand name collisions (e.g. "Ultramarine Blue" ships in both vallejo
and army_painter at different hex); a bare ambiguous name is an error.

Airbrush-line paints (vallejo_model_air, citadel_air, etc.) are excluded
from recommendations by default — they handle differently from brush
paints. Set "include_air": true to allow them, or name a specific air line
in brand_filter to opt that one line back in. Owned air paints stay usable.

Output schema (optimize): see README and docstring of `run_optimize`.

Errors exit nonzero with a structured JSON object on stderr:
    {"error": {"code": "...", "message": "..."}}
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .color import hex_to_lab, parse_hex
from .db import _index_paints, filter_paints, load_paints, resolve_paint_ref, validate_db
from .optimize import (
    DEFAULT_NEIGHBORHOOD,
    DEFAULT_TOP_K,
    Blend,
    TargetResult,
    assign_recipes,
    confidence_for,
    greedy_set_cover,
    search_blends_for_target,
)


DEFAULTS = {
    "max_paints": 12,
    "tolerance_delta_e": 5.0,
    "max_paints_per_recipe": 3,
}


def _emit_error(code: str, message: str, exit_code: int = 1) -> int:
    json.dump({"error": {"code": code, "message": message}}, sys.stderr)
    sys.stderr.write("\n")
    return exit_code


def _load_input(args: argparse.Namespace) -> dict:
    if args.input:
        text = Path(args.input).read_text(encoding="utf-8")
    else:
        text = sys.stdin.read()
    if not text.strip():
        raise ValueError("no input received (expected JSON on stdin or --input)")
    return json.loads(text)


def run_optimize(payload: dict, paint_db_path: Path | None = None) -> dict:
    targets_in = payload.get("targets") or []
    if not targets_in:
        raise ValueError("'targets' must be a non-empty list")
    for t in targets_in:
        if "hex" not in t:
            raise ValueError(f"target missing 'hex': {t!r}")
        parse_hex(t["hex"])  # validate

    brand_filter = payload.get("brand_filter") or []
    already_owned = payload.get("already_owned") or []
    max_paints = int(payload.get("max_paints", DEFAULTS["max_paints"]))
    tolerance = float(payload.get("tolerance_delta_e", DEFAULTS["tolerance_delta_e"]))
    max_per_recipe = int(payload.get("max_paints_per_recipe", DEFAULTS["max_paints_per_recipe"]))
    neighborhood = int(payload.get("neighborhood", DEFAULT_NEIGHBORHOOD))
    top_k = int(payload.get("top_k", DEFAULT_TOP_K))
    include_air = bool(payload.get("include_air", False))

    if max_per_recipe not in (1, 2, 3):
        raise ValueError("max_paints_per_recipe must be 1, 2, or 3")

    paints = load_paints(paint_db_path)
    candidates, owned = filter_paints(
        paints,
        brand_filter=brand_filter or None,
        already_owned=already_owned or None,
        exclude_air=not include_air,
    )
    if not candidates:
        raise ValueError("no candidate paints after brand_filter")

    owned_ids = {p.id for p in owned}

    # Per-target blend search.
    target_results: list[TargetResult] = []
    for t in targets_in:
        tr = search_blends_for_target(
            t.get("name") or t["hex"],
            t["hex"],
            candidates,
            max_paints_per_recipe=max_per_recipe,
            neighborhood=neighborhood,
            top_k=top_k,
            forced_include=owned_ids,
        )
        target_results.append(tr)

    # Set cover (keyed on globally-unique Paint.id).
    cand_ids = [p.id for p in candidates]
    pool = greedy_set_cover(
        target_results,
        cand_ids,
        max_paints=max_paints,
        tolerance=tolerance,
        owned=owned_ids,
    )
    recipes, unreachable = assign_recipes(target_results, frozenset(pool), tolerance)

    # Resolve paint metadata for the purchase list.
    paint_by_id = {p.id: p for p in candidates}
    purchase_list = []
    for pid in sorted(pool):
        if pid in owned_ids:
            continue
        p = paint_by_id.get(pid)
        if p is None:
            continue
        purchase_list.append({
            "name": p.name,
            "brand": p.brand,
            "line": p.line,
            "sku": p.sku,
            "hex": p.hex,
        })

    out_recipes = [
        _recipe_dict(tr, blend, tolerance, paint_by_id) for tr, blend in recipes
    ]
    out_unreachable = [
        _unreachable_dict(tr, blend, tolerance, paint_by_id) for tr, blend in unreachable
    ]

    return {
        "purchase_list": purchase_list,
        "recipes": out_recipes,
        "unreachable": out_unreachable,
        "summary": {
            "paints_recommended": len(pool),
            "paints_to_buy": len(purchase_list),
            "paints_already_owned": len(owned_ids & pool),
            "targets_total": len(target_results),
            "targets_hit": len(recipes),
            "targets_missed": len(unreachable),
            "tolerance_delta_e": tolerance,
        },
        "constraints_echo": {
            "brand_filter": brand_filter,
            "max_paints": max_paints,
            "max_paints_per_recipe": max_per_recipe,
            "already_owned": list(already_owned),
        },
    }


def _recipe_dict(tr: TargetResult, blend: Blend, tolerance: float, by_id: dict) -> dict:
    components = []
    for pid, ratio in zip(blend.paint_ids, blend.ratios, strict=True):
        p = by_id.get(pid)
        components.append({
            # JSON reports the human-facing bare name, not the brand:name id.
            "paint": p.name if p else pid,
            "ratio": round(ratio, 3),
            "brand": p.brand if p else None,
            "line": p.line if p else None,
            "hex": p.hex if p else None,
        })
    return {
        "target": {"name": tr.target_name, "hex": tr.target_hex},
        "blend": components,
        "predicted_hex": blend.predicted_hex,
        "delta_e": round(blend.delta_e, 2),
        "confidence": confidence_for(blend.delta_e, tolerance),
    }


def _unreachable_dict(
    tr: TargetResult, blend: Blend | None, tolerance: float, by_id: dict
) -> dict:
    reason = "no blend within tolerance using the chosen paint pool"
    return {
        "target": {"name": tr.target_name, "hex": tr.target_hex},
        "closest_delta_e": round(blend.delta_e, 2) if blend else None,
        "closest_blend": (
            [
                {"paint": by_id[pid].name if pid in by_id else pid, "ratio": round(r, 3)}
                for pid, r in zip(blend.paint_ids, blend.ratios, strict=True)
            ]
            if blend else None
        ),
        "closest_predicted_hex": blend.predicted_hex if blend else None,
        "reason": reason,
    }


def cmd_optimize(args: argparse.Namespace) -> int:
    payload = _load_input(args)
    db_path = Path(args.paint_db) if args.paint_db else None
    result = run_optimize(payload, db_path)
    json.dump(result, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def cmd_validate_db(args: argparse.Namespace) -> int:
    db_path = Path(args.paint_db) if args.paint_db else None
    report = validate_db(db_path)
    json.dump(report, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0 if not report["errors"] else 1


def cmd_explain_recipe(args: argparse.Namespace) -> int:
    """Re-score a specific recipe.

    Input JSON:
        {"target_hex": "#...", "blend": [{"paint": "...", "ratio": 0.7}, ...]}

    Each blend component's "paint" is either a bare name string (resolved
    only when globally unique across the DB) or a structured
    {"name": "...", "brand": "..."} object that disambiguates cross-brand
    name collisions.
    """
    payload = _load_input(args)
    target_hex = payload["target_hex"]
    blend_in = payload["blend"]
    tolerance = float(payload.get("tolerance_delta_e", DEFAULTS["tolerance_delta_e"]))

    db_path = Path(args.paint_db) if args.paint_db else None
    by_id, by_name = _index_paints(load_paints(db_path))

    from .color import BlendComponent, blend_lab, delta_e_2000, lab_to_hex

    comps = []
    resolved = []
    for c in blend_in:
        p = resolve_paint_ref(c["paint"], by_id, by_name)
        if p is None:
            raise ValueError(f"unknown paint: {c['paint']!r}")
        resolved.append(p)
        comps.append(BlendComponent(p.name, p.hex, p.lab, float(c["ratio"])))

    lab = blend_lab(comps)
    target_lab = hex_to_lab(target_hex)
    de = float(delta_e_2000(target_lab, lab))

    out = {
        "target_hex": target_hex,
        "predicted_hex": lab_to_hex(lab),
        "delta_e": round(de, 2),
        "confidence": confidence_for(de, tolerance),
        "components": [
            {
                "paint": p.name,
                "hex": p.hex,
                "ratio": round(c.ratio, 3),
                "brand": p.brand,
                "line": p.line,
            }
            for c, p in zip(comps, resolved, strict=True)
        ],
    }
    json.dump(out, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="palette-optimizer",
        description="Recommend the minimum paint set to reproduce a target palette.",
    )
    p.add_argument("--paint-db", help="Path to paints.csv (default: bundled data/paints.csv)")
    sub = p.add_subparsers(dest="command", required=True)

    p_opt = sub.add_parser("optimize", help="Recommend paints and recipes")
    p_opt.add_argument("--input", help="Path to input JSON (default: stdin)")
    p_opt.set_defaults(func=cmd_optimize)

    p_val = sub.add_parser("validate-db", help="Sanity-check the paint database")
    p_val.set_defaults(func=cmd_validate_db)

    p_exp = sub.add_parser("explain-recipe", help="Re-score a specific recipe")
    p_exp.add_argument("--input", help="Path to input JSON (default: stdin)")
    p_exp.set_defaults(func=cmd_explain_recipe)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except json.JSONDecodeError as e:
        return _emit_error("InvalidJSON", str(e))
    except (ValueError, FileNotFoundError) as e:
        return _emit_error(type(e).__name__, str(e))


if __name__ == "__main__":
    sys.exit(main())
