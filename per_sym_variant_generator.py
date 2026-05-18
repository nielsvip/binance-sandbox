#!/usr/bin/env python3
"""per_sym_variant_generator — knob-combination producer for per_sym_vec_engine_*.

Produces 100k–10M variant rows for the variant-axis vectorized backtester. Each variant
is a flat dict of knob_name → value. The generator supports:

  1. Cartesian product over a "core" set of discrete knobs (every combination).
  2. Random / Latin-Hypercube / Sobol sampling for high-dim "extension" knobs.
  3. Hybrid: Cartesian core × sampled extensions = cartesian-many "base configs",
     each replicated n_samples times with different extension draws.

Per CLAUDE.md (Phase 1 = cross-symbol, Phase 2 = per-symbol): this is the per-symbol
candidate generator. The 7d agent picks the top-K by time-weighted Sharpe.

Sobol uses numpy.random (deterministic seed) — scipy.stats.qmc.Sobol if available.

NO LIVE CODE. NO SIDE EFFECTS. Pure generator.
"""
from __future__ import annotations

import itertools
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

import numpy as np

# ───────────────────────── knob spec ──────────────────────────────────────

# DISCRETE knobs: every value enumerated.
# Format: knob_name -> list of values.
DISCRETE_KNOBS_CORE_CRYPTO: Dict[str, List[Any]] = {
    'MIN_TFS_AGREE_ENTRY': [1, 2, 3, 4],
    'MIN_TFS_AGREE_EXIT': [1, 2, 3, 4],
    'ENTRY_MODE': ['dc_break', 'wt_cross', 'bb_extreme', 'or'],
    'MIN_HOLD_BARS_15m': [1, 3, 5, 8, 13],
    'COOLDOWN_BARS_15m': [0, 1, 3, 5],
}
# 4 × 4 × 4 × 5 × 4 = 1280 core combos (crypto)

DISCRETE_KNOBS_CORE_STOCKS: Dict[str, List[Any]] = {
    'MIN_TFS_AGREE_ENTRY': [2, 3, 4],            # stocks tighter
    'MIN_TFS_AGREE_EXIT': [1, 2, 3],
    'ENTRY_MODE': ['dc_break', 'wt_cross', 'bb_extreme', 'or'],
    'MIN_HOLD_BARS_15m': [2, 5, 8, 13, 21],      # stocks slower
    'COOLDOWN_BARS_15m': [0, 1, 3, 5],
}
# 3 × 3 × 4 × 5 × 4 = 720 core combos (stocks)

DISCRETE_KNOBS_EXT: Dict[str, List[Any]] = {
    'REVERSE_ON_EXIT_ENABLED': [False, True],
    'REENTRY_MEAN_REV_ENABLED': [False, True],
    'NOLOSS_ENABLED': [False, True],
    'HARD_LOSS_PCT_ENABLED': [False, True],
    'PEAK_PROTECT_ENABLED': [False, True],
    'HEDGE_ENABLED': [False, True],
    'HEDGE_WT_TF': ['3m', '15m', '1h'],
    'AUGMENT_ENABLED': [False, True],
    'REQUIRE_D_TREND': [False, True],
    'REQUIRE_W_TREND': [False, True],
    'BT_DC_BB_D_BREAK_REVERSE_ENABLED': [False, True],
    'BT_WT15M_AGAINST_FORCE_HEDGE_ENABLED': [False, True],
    'BT_ALL_TF_AGAINST_CLOSE_ENABLED': [False, True],
    'BT_RIDICULOUS_HOLD_GUARD_ENABLED': [False, True],
    'BT_UNDERWATER_HEDGE_OR_CLOSE_ENABLED': [False, True],
    'BB_AUTO_TUNE_ENABLED': [False, True],
    'PEAK_GIVEBACK_FIXED_PCT_ENABLED': [False, True],
    'FOLLOW_THROUGH_REENTRY_ENABLED': [False, True],
}
# 18 toggles + 1 (3-value) → too many for cartesian, sampled.

CONTINUOUS_KNOBS: Dict[str, Tuple[float, float]] = {
    'BB_LONG_ENTRY_MAX': (0.05, 0.35),
    'BB_SHORT_ENTRY_MIN': (0.65, 0.95),
    'HARD_LOSS_PCT': (0.2, 2.0),
    'PEAK_GIVEBACK_FIXED_DROP_PCT': (0.2, 1.5),
    'HEDGE_SIZE_FRAC': (0.25, 1.5),
    'REENTRY_MEAN_REV_TOLERANCE_PCT': (0.10, 1.0),
    'FOLLOW_THROUGH_MIN_MOVE_PCT': (0.02, 0.30),
    'BT_RIDICULOUS_LOSS_PCT': (-25.0, -5.0),
    'BT_RIDICULOUS_HOLD_HOURS': (12.0, 96.0),
    'ENTRY_BB_EXTREME_THRESHOLD': (0.05, 0.20),
    'ENTRY_BB_SQUEEZE_RATIO': (1.2, 2.5),
    'ENTRY_VOL_SPIKE_RATIO': (1.5, 4.0),
}


# ───────────────────────── samplers ───────────────────────────────────────

def _sobol_unit(n: int, dim: int, seed: int = 0) -> np.ndarray:
    """Sobol sample in (0,1)^dim. Falls back to numpy.random if scipy unavailable.

    Suppresses scipy's "n not power-of-2" warning (we accept non-balanced Sobol — variant
    counts rarely line up to powers of 2, and the small loss of balance is irrelevant when
    cartesian product over CORE knobs is already deterministic).
    """
    try:
        import warnings
        from scipy.stats import qmc
        sampler = qmc.Sobol(d=dim, scramble=True, seed=seed)
        with warnings.catch_warnings():
            warnings.simplefilter('ignore', UserWarning)
            return sampler.random(n)
    except Exception:
        rng = np.random.default_rng(seed)
        return rng.random((n, dim))


def _lhs_unit(n: int, dim: int, seed: int = 0) -> np.ndarray:
    """Latin Hypercube sample in (0,1)^dim. Falls back to numpy.random."""
    try:
        from scipy.stats import qmc
        sampler = qmc.LatinHypercube(d=dim, seed=seed)
        return sampler.random(n)
    except Exception:
        rng = np.random.default_rng(seed)
        out = np.zeros((n, dim))
        for d in range(dim):
            u = (np.arange(n) + rng.random(n)) / n
            rng.shuffle(u)
            out[:, d] = u
        return out


# ───────────────────────── public API ─────────────────────────────────────

def generate_variants(
    n_total: int = 1_000_000,
    mode: str = 'crypto',
    method: str = 'sobol',
    core_cartesian: bool = True,
    seed: int = 0,
    overrides: Optional[Dict[str, Any]] = None,
) -> List[Dict[str, Any]]:
    """Generate `n_total` knob-dicts.

    method:
      - 'sobol'  : Sobol over ext + continuous
      - 'lhs'    : Latin Hypercube
      - 'random' : iid uniform
    core_cartesian:
      - True (default): cartesian product of CORE discrete knobs, samples ext × cont
        per core combo. Returns cartesian * n_samples_per_core.
      - False: sample everything (core + ext + cont) — pure Sobol.
    overrides: dict of knob -> single value to FIX across all variants.

    Returns list of dicts. Memory: ~30 bytes/knob/variant. 1M × 30 knobs × 30B = 1 GB.
    For >1M variants, consider streaming generator (TODO).
    """
    overrides = overrides or {}
    discrete_core = DISCRETE_KNOBS_CORE_CRYPTO if mode == 'crypto' else DISCRETE_KNOBS_CORE_STOCKS
    ext = DISCRETE_KNOBS_EXT
    cont = CONTINUOUS_KNOBS

    # Filter out any knob fixed by override
    discrete_core = {k: v for k, v in discrete_core.items() if k not in overrides}
    ext = {k: v for k, v in ext.items() if k not in overrides}
    cont = {k: v for k, v in cont.items() if k not in overrides}

    if core_cartesian:
        core_keys = list(discrete_core.keys())
        core_vals = [discrete_core[k] for k in core_keys]
        n_core_combos = int(np.prod([len(v) for v in core_vals])) if core_vals else 1
        if n_core_combos == 0:
            n_core_combos = 1
        n_samples_per_core = max(1, n_total // n_core_combos)
        ext_keys = list(ext.keys())
        cont_keys = list(cont.keys())
        dim = len(ext_keys) + len(cont_keys)
        if dim > 0:
            if method == 'sobol':
                u = _sobol_unit(n_samples_per_core, dim, seed=seed)
            elif method == 'lhs':
                u = _lhs_unit(n_samples_per_core, dim, seed=seed)
            else:
                rng = np.random.default_rng(seed)
                u = rng.random((n_samples_per_core, dim))
        else:
            u = np.zeros((n_samples_per_core, 0))
        # Decode ext (toggle / categorical) from uniform [0,1) — pick index = floor(u * len)
        ext_vals_per_sample = {}
        for i, k in enumerate(ext_keys):
            vs = ext[k]
            col = (u[:, i] * len(vs)).astype(np.int64).clip(0, len(vs) - 1)
            ext_vals_per_sample[k] = [vs[c] for c in col]
        # Decode continuous — linear map to [lo, hi]
        cont_vals_per_sample = {}
        for j, k in enumerate(cont_keys):
            lo, hi = cont[k]
            col_u = u[:, len(ext_keys) + j]
            cont_vals_per_sample[k] = (lo + col_u * (hi - lo)).tolist()

        out: List[Dict[str, Any]] = []
        for core_combo in itertools.product(*core_vals):
            core_dict = dict(zip(core_keys, core_combo))
            for s in range(n_samples_per_core):
                row = dict(core_dict)
                for k in ext_keys:
                    row[k] = ext_vals_per_sample[k][s]
                for k in cont_keys:
                    row[k] = cont_vals_per_sample[k][s]
                row.update(overrides)
                out.append(row)
                if len(out) >= n_total:
                    return out
        return out
    else:
        # Pure sample across everything
        ext_keys = list(ext.keys())
        cont_keys = list(cont.keys())
        disc_core_keys = list(discrete_core.keys())
        dim = len(disc_core_keys) + len(ext_keys) + len(cont_keys)
        if method == 'sobol':
            u = _sobol_unit(n_total, dim, seed=seed)
        elif method == 'lhs':
            u = _lhs_unit(n_total, dim, seed=seed)
        else:
            rng = np.random.default_rng(seed)
            u = rng.random((n_total, dim))
        out = []
        for s in range(n_total):
            row: Dict[str, Any] = {}
            i = 0
            for k in disc_core_keys:
                vs = discrete_core[k]
                row[k] = vs[int(u[s, i] * len(vs)) % len(vs)]
                i += 1
            for k in ext_keys:
                vs = ext[k]
                row[k] = vs[int(u[s, i] * len(vs)) % len(vs)]
                i += 1
            for k in cont_keys:
                lo, hi = cont[k]
                row[k] = float(lo + u[s, i] * (hi - lo))
                i += 1
            row.update(overrides)
            out.append(row)
        return out


def generate_variants_array(
    n_total: int = 1_000_000,
    mode: str = 'crypto',
    method: str = 'sobol',
    core_cartesian: bool = True,
    seed: int = 0,
    overrides: Optional[Dict[str, Any]] = None,
) -> Tuple[List[str], np.ndarray, Dict[str, List[Any]]]:
    """Memory-efficient alternative: return (knob_names, variants_array, categorical_maps).

    `variants_array` has shape (n_total, n_knobs).
    For categorical knobs (str, bool), values are int indices into categorical_maps[knob].
    For numeric knobs, values are the actual float/int.

    This is ~10× more memory-efficient than list-of-dict (200MB for 1M × 30 knobs vs 2GB).
    """
    variants = generate_variants(n_total=n_total, mode=mode, method=method,
                                  core_cartesian=core_cartesian, seed=seed,
                                  overrides=overrides)
    if not variants:
        return [], np.zeros((0, 0), dtype=np.float32), {}
    knob_names = sorted(variants[0].keys())
    # Detect type per knob from first row
    cat_maps: Dict[str, List[Any]] = {}
    for k in knob_names:
        v0 = variants[0][k]
        if isinstance(v0, (str, bool)):
            uniq = sorted({v[k] for v in variants}, key=lambda x: (str(type(x)), str(x)))
            cat_maps[k] = list(uniq)
    arr = np.zeros((len(variants), len(knob_names)), dtype=np.float32)
    for i, v in enumerate(variants):
        for j, k in enumerate(knob_names):
            val = v[k]
            if k in cat_maps:
                arr[i, j] = cat_maps[k].index(val)
            else:
                arr[i, j] = float(val)
    return knob_names, arr, cat_maps


def variants_array_to_dicts(
    knob_names: List[str], arr: np.ndarray, cat_maps: Dict[str, List[Any]]
) -> List[Dict[str, Any]]:
    """Decode (knob_names, arr, cat_maps) back to list-of-dicts."""
    out = []
    for i in range(arr.shape[0]):
        d: Dict[str, Any] = {}
        for j, k in enumerate(knob_names):
            v = arr[i, j]
            if k in cat_maps:
                d[k] = cat_maps[k][int(v)]
            else:
                d[k] = float(v)
        out.append(d)
    return out


# ───────────────────────── smoke ─────────────────────────────────────────

if __name__ == '__main__':
    import time
    t0 = time.time()
    vs = generate_variants(n_total=100_000, mode='crypto', method='sobol')
    dt = time.time() - t0
    print(f"[gen] 100k variants in {dt:.2f}s | first: {vs[0]}")
    print(f"[gen] sample variants[50000]: {vs[50000]}")
    t0 = time.time()
    knobs, arr, maps = generate_variants_array(n_total=100_000, mode='crypto')
    dt = time.time() - t0
    print(f"[gen-array] 100k × {len(knobs)} knobs in {dt:.2f}s | shape={arr.shape} mem={arr.nbytes/1e6:.1f}MB")
