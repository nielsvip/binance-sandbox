"""formation_exit_live — vector twin of the live classic-formation EXIT (tradier_manage.py:18702-18735, ez_positions_quick.py:3668, FULL_RECIPE branch 18534).
ONE predicate: classic_formations.select_latest_formation(action='EXIT') (scalar, live) == classic_formations.formation_vector_mask(action='EXIT') (vector); this module only
derives the missing NPZ formation_* fields (ensure_npz_formation_fields, same detector the live indicator uses) and returns the mask. Position-level gates stay in the engine loop:
FORMATION_EXIT_MIN_GAIN_PCT (live: gain >= floor) and the structural exit veto (engine already applies STRUCTURAL_EXIT_GATE to exit_sig).
Replaces the old proxy in compute_exit_signals (1h lower-high/lower-low) which ignored the detector and the per-family switches."""
import numpy as np

_FAMILY_KEYS = ("FORMATION_HEAD_SHOULDERS_EXIT_ENABLED", "FORMATION_DOUBLE_TOP_BOTTOM_EXIT_ENABLED", "FORMATION_WEDGE_EXIT_ENABLED", "FORMATION_TRIANGLE_EXIT_ENABLED",
                "FORMATION_FLAG_PENNANT_EXIT_ENABLED", "FORMATION_CUP_HANDLE_EXIT_ENABLED", "FORMATION_TREND_STRUCTURE_EXIT_ENABLED")
_CACHE = {}


def formation_exit_mask(npz, n, cfg, is_long):
    if not any(bool(getattr(cfg, k, False)) for k in _FAMILY_KEYS):
        return np.zeros(n, dtype=bool)
    import classic_formations as CF
    key = (id(npz), n)
    fields = _CACHE.get(key)
    if fields is None:
        arrays = {k: npz[k] for k in npz.files} if hasattr(npz, "files") else dict(npz)
        fields = dict(arrays)
        fields.update(CF.ensure_npz_formation_fields(arrays, timeframes=CF._formation_timeframes(cfg)))
        _CACHE.clear(); _CACHE[key] = fields
    mask, _score, _fam = CF.formation_vector_mask(fields, is_long=is_long, action="EXIT", config=cfg, n=n)
    return np.asarray(mask, dtype=bool)
