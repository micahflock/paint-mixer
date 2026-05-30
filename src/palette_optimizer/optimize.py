"""Optimization: per-target blend search + set cover over the paint pool.

Two-stage problem:

1. For each target, enumerate candidate blends (1/2/3 paints) and score
   each by CIEDE2000 against the target. Per-target candidate pruning
   keeps this tractable: we only consider the K paints nearest the
   target in Lab space (plus user-owned paints). Top-N blends per
   target are retained so the cover step can pick the best blend whose
   components are all in the chosen pool.

2. Choose a minimum subset of paints (≤ max_paints) such that every
   target has at least one retained blend within tolerance whose
   components all come from the subset. Greedy.

Blend enumeration is vectorized: all candidate blend Lab values are
stacked into one (N, 3) array and ΔE2000 is computed against the target
in a single batched call. This makes the enumeration tractable for
realistic paint pools (~700 candidates) and palette sizes.

Targets where no retained blend hits tolerance go to `unreachable`.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field
from itertools import combinations

import numpy as np

from .color import (
    blend_lab,  # re-exported for tests
    confidence_label,
    hex_to_lab,
    lab_to_hex,
)
from .db import Paint

with warnings.catch_warnings():
    warnings.filterwarnings("ignore", message=".*SciPy.*")
    warnings.filterwarnings("ignore", message=".*Matplotlib.*")
    import colour


# Per-target pruning: how many of the nearest paints (by ΔE2000 to target)
# to consider as blend components. Larger = more thorough, slower.
DEFAULT_NEIGHBORHOOD = 24

# How many top-scoring blends to retain per target. The cover step picks
# among these; larger means the cover has more flexibility but more work.
DEFAULT_TOP_K = 60


def _ratios_2(step: int = 10) -> np.ndarray:
    """Interior 2-paint ratios on a step-spaced simplex. Shape (R, 2)."""
    return np.array([[i / step, 1 - i / step] for i in range(1, step)], dtype=float)


def _ratios_3(step: int = 10) -> np.ndarray:
    """Interior 3-paint ratios on a step-spaced simplex. Shape (R, 3)."""
    out = []
    for i in range(1, step):
        for j in range(1, step - i):
            k = step - i - j
            if k >= 1:
                out.append((i / step, j / step, k / step))
    return np.array(out, dtype=float)


RATIOS_2 = _ratios_2(10)
RATIOS_3 = _ratios_3(10)


@dataclass(frozen=True)
class Blend:
    # paint_ids are globally-unique Paint.id values (brand:name), not bare
    # names. This is internal to the optimizer; the CLI maps them back to
    # human-facing name/brand/line for JSON output.
    paint_ids: tuple[str, ...]
    ratios: tuple[float, ...]
    predicted_hex: str
    delta_e: float

    @property
    def support(self) -> frozenset[str]:
        return frozenset(self.paint_ids)


@dataclass
class TargetResult:
    target_name: str
    target_hex: str
    blends: list[Blend] = field(default_factory=list)  # sorted ascending by delta_e

    @property
    def best_blend(self) -> Blend | None:
        return self.blends[0] if self.blends else None


def _batch_delta_e(target_lab: np.ndarray, blend_labs: np.ndarray) -> np.ndarray:
    """ΔE2000 between target (shape (3,)) and a batch of blends (shape (N, 3))."""
    # colour.delta_E broadcasts; result shape (N,)
    return np.asarray(colour.delta_E(target_lab, blend_labs, method="CIE 2000"))


def search_blends_for_target(
    target_name: str,
    target_hex: str,
    candidates: list[Paint],
    *,
    max_paints_per_recipe: int = 3,
    neighborhood: int = DEFAULT_NEIGHBORHOOD,
    top_k: int = DEFAULT_TOP_K,
    forced_include: set[str] | None = None,
) -> TargetResult:
    if not candidates:
        return TargetResult(target_name, target_hex, [])

    target_lab = hex_to_lab(target_hex)
    cand_lab = np.stack([p.lab for p in candidates])  # (N, 3)

    # Distance from target to every candidate, for neighborhood selection.
    dists = _batch_delta_e(target_lab, cand_lab)
    order = np.argsort(dists)

    take = min(neighborhood, len(candidates))
    neigh_idx_list = list(int(i) for i in order[:take])
    forced = forced_include or set()
    by_id = {p.id: i for i, p in enumerate(candidates)}
    for pid in forced:
        idx = by_id.get(pid)
        if idx is not None and idx not in neigh_idx_list:
            neigh_idx_list.append(idx)

    neigh_idx = np.array(neigh_idx_list, dtype=int)
    neigh = [candidates[i] for i in neigh_idx]
    neigh_lab = cand_lab[neigh_idx]  # (K, 3)
    neigh_dists = _batch_delta_e(target_lab, neigh_lab)  # (K,)

    n_keep = max(top_k, 1)
    blends: list[Blend] = []

    # 1-paint
    for i, p in enumerate(neigh):
        de = float(neigh_dists[i])
        blends.append(Blend((p.id,), (1.0,), p.hex, de))

    # 2-paint: vectorize across all pairs × ratios
    if max_paints_per_recipe >= 2 and len(neigh) >= 2:
        pair_idx = np.array(list(combinations(range(len(neigh)), 2)), dtype=int)  # (P, 2)
        a_lab = neigh_lab[pair_idx[:, 0]]  # (P, 3)
        b_lab = neigh_lab[pair_idx[:, 1]]
        # Broadcast: (P, 1, 3) * (R, 1) + (P, 1, 3) * (R, 1) -> (P, R, 3)
        r = RATIOS_2  # (R, 2)
        labs = (
            a_lab[:, None, :] * r[None, :, 0:1]
            + b_lab[:, None, :] * r[None, :, 1:2]
        )  # (P, R, 3)
        labs_flat = labs.reshape(-1, 3)
        des = _batch_delta_e(target_lab, labs_flat)  # (P*R,)
        # Keep only the most promising n_keep entries here to limit memory.
        top_n = min(n_keep * 4, des.shape[0])
        cand_order = np.argpartition(des, top_n - 1)[:top_n]
        cand_order = cand_order[np.argsort(des[cand_order])]
        for flat_idx in cand_order:
            pi = flat_idx // r.shape[0]
            ri = flat_idx % r.shape[0]
            i = pair_idx[pi, 0]
            j = pair_idx[pi, 1]
            ra, rb = float(r[ri, 0]), float(r[ri, 1])
            lab = labs[pi, ri]
            blends.append(Blend(
                (neigh[i].id, neigh[j].id),
                (ra, rb),
                lab_to_hex(lab),
                float(des[flat_idx]),
            ))

    # 3-paint: same pattern. Memory: K=24 -> C(24,3)=2024 triples × 36 ratios
    # × 3 floats = ~218k floats per target, fine.
    if max_paints_per_recipe >= 3 and len(neigh) >= 3:
        tri_idx = np.array(list(combinations(range(len(neigh)), 3)), dtype=int)
        a_lab = neigh_lab[tri_idx[:, 0]]
        b_lab = neigh_lab[tri_idx[:, 1]]
        c_lab = neigh_lab[tri_idx[:, 2]]
        r = RATIOS_3  # (R, 3)
        labs = (
            a_lab[:, None, :] * r[None, :, 0:1]
            + b_lab[:, None, :] * r[None, :, 1:2]
            + c_lab[:, None, :] * r[None, :, 2:3]
        )  # (T, R, 3)
        labs_flat = labs.reshape(-1, 3)
        des = _batch_delta_e(target_lab, labs_flat)
        top_n = min(n_keep * 4, des.shape[0])
        cand_order = np.argpartition(des, top_n - 1)[:top_n]
        cand_order = cand_order[np.argsort(des[cand_order])]
        for flat_idx in cand_order:
            ti = flat_idx // r.shape[0]
            ri = flat_idx % r.shape[0]
            i, j, k = tri_idx[ti]
            ra, rb, rc = float(r[ri, 0]), float(r[ri, 1]), float(r[ri, 2])
            lab = labs[ti, ri]
            blends.append(Blend(
                (neigh[i].id, neigh[j].id, neigh[k].id),
                (ra, rb, rc),
                lab_to_hex(lab),
                float(des[flat_idx]),
            ))

    # Retention strategy: greedy set cover needs options at every blend size.
    # If we kept only the top_k blends by ΔE, 3-paint blends would dominate
    # and a single paint pick could never cover any target. So we stratify:
    # keep all 1-paint blends, plus the best diverse 2- and 3-paint blends.
    blends.sort(key=lambda b: b.delta_e)
    seen_supports: set[frozenset[str]] = set()
    by_size: dict[int, list[Blend]] = {1: [], 2: [], 3: []}
    quotas = {1: len(neigh), 2: top_k, 3: top_k}
    for b in blends:
        if b.support in seen_supports:
            continue
        size = len(b.support)
        if len(by_size[size]) >= quotas.get(size, top_k):
            continue
        seen_supports.add(b.support)
        by_size[size].append(b)
    retained = by_size[1] + by_size[2] + by_size[3]
    retained.sort(key=lambda b: b.delta_e)
    return TargetResult(target_name, target_hex, retained)


def _covers(target: TargetResult, pool: frozenset[str], tolerance: float) -> Blend | None:
    for blend in target.blends:
        if blend.delta_e > tolerance:
            break  # sorted: nothing later is within tolerance
        if blend.support.issubset(pool):
            return blend
    return None


def greedy_set_cover(
    targets: list[TargetResult],
    candidate_ids: list[str],
    *,
    max_paints: int,
    tolerance: float,
    owned: set[str],
) -> set[str]:
    # `candidate_ids` and `owned` are globally-unique Paint.id values.
    pool: set[str] = set(owned)
    covered: set[int] = set()

    def refresh_covered() -> None:
        covered.clear()
        frozen = frozenset(pool)
        for i, t in enumerate(targets):
            if _covers(t, frozen, tolerance) is not None:
                covered.add(i)

    refresh_covered()
    slots = max_paints - len(pool)

    while slots > 0 and len(covered) < len(targets):
        best_id: str | None = None
        best_new_count = 0
        best_delta_sum = float("inf")
        for pid in candidate_ids:
            if pid in pool:
                continue
            trial = frozenset(pool | {pid})
            new_count = 0
            delta_sum = 0.0
            for i, t in enumerate(targets):
                if i in covered:
                    continue
                blend = _covers(t, trial, tolerance)
                if blend is not None:
                    new_count += 1
                    delta_sum += blend.delta_e
            if new_count == 0:
                continue
            if (new_count > best_new_count) or (
                new_count == best_new_count and delta_sum < best_delta_sum
            ):
                best_id = pid
                best_new_count = new_count
                best_delta_sum = delta_sum
        if best_id is None:
            break
        pool.add(best_id)
        slots -= 1
        refresh_covered()

    return pool


def assign_recipes(
    targets: list[TargetResult],
    pool: frozenset[str],
    tolerance: float,
) -> tuple[list[tuple[TargetResult, Blend]], list[tuple[TargetResult, Blend | None]]]:
    recipes: list[tuple[TargetResult, Blend]] = []
    unreachable: list[tuple[TargetResult, Blend | None]] = []
    for t in targets:
        blend = _covers(t, pool, tolerance)
        if blend is not None:
            recipes.append((t, blend))
            continue
        closest_in_pool: Blend | None = None
        for b in t.blends:
            if b.support.issubset(pool):
                if closest_in_pool is None or b.delta_e < closest_in_pool.delta_e:
                    closest_in_pool = b
        unreachable.append((t, closest_in_pool or t.best_blend))
    return recipes, unreachable


def confidence_for(delta_e: float, tolerance: float) -> str:
    return confidence_label(delta_e, tolerance)


__all__ = [
    "DEFAULT_NEIGHBORHOOD",
    "DEFAULT_TOP_K",
    "Blend",
    "TargetResult",
    "assign_recipes",
    "blend_lab",
    "confidence_for",
    "greedy_set_cover",
    "search_blends_for_target",
]
