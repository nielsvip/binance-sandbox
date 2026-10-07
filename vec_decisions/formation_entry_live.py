"""formation_entry_live — vector twin of the live classic-formation ENTRY (ez_positions_quick.py:15762-15797).

ONE predicate: classic_formations.select_latest_formation(action='ENTRY') (scalar, live) == classic_formations.formation_vector_mask(action='ENTRY') (vector); this module only
derives the missing NPZ formation_* fields (ensure_npz_formation_fields, same detector the live indicator uses) and returns the mask. Mirrors formation_exit_live.py.
Live fires standalone at score 29 (STRONG_BUY/SELL) for flat positions; the v12 caller gives it standalone weight. Per-family ENTRY switches are previously tested with CORRECT IMMUTABLE False
defaults — per_sym values change ONLY via daily v15_avg_delta per cat_side, NEVER by an agent. Zeros while defaults hold; the twin exists so vec==live IF v15 ever enables a family.
"""
import numpy as np

_FAMILY_KEYS = ("FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED", "FORMATION_DOUBLE_TOP_BOTTOM_ENTRY_ENABLED", "FORMATION_WEDGE_ENTRY_ENABLED", "FORMATION_TRIANGLE_ENTRY_ENABLED",
                "FORMATION_FLAG_PENNANT_ENTRY_ENABLED", "FORMATION_CUP_HANDLE_ENTRY_ENABLED", "FORMATION_TREND_STRUCTURE_ENTRY_ENABLED")
_CACHE = {}


def formation_entry_mask(npz, n, cfg, is_long):
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
    mask, _score, _fam = CF.formation_vector_mask(fields, is_long=is_long, action="ENTRY", config=cfg, n=n)
    return np.asarray(mask, dtype=bool)
