"""kg_entry_gate (FLT2, 2026-10-01) — ONE predicate for the crypto EMA_9_21 / KINDERGARTEN entry gates, called identically by
live (ez_manage.execute_now fresh-OPEN chokepoint, via `ema921_pass`) and by the vector (v12_quick_engine, `ema921_pass_vec`).
Semantics = the stocks cumulative gate (tradier_manage.should_enter_long/short) so both venues mean the same thing:
  checks = 9/21 per TF in EMA_9_21_FILTER_TFS (fallback EMA_9_21_TIMEFRAME, default 1h): LONG ok iff ema_9_above_21_tf != 0, SHORT ok iff != 1; TF without data = no check.
  KINDERGARTEN_STRICT_TFS (comma list): every listed-TF check must pass. Need = min(effective_min_tfs, len(checks)); effective_min_tfs = KINDERGARTEN_CUMULATIVE_MIN_TFS if >0
  else EMA_9_21_FILTER_MIN_TFS else 1 (same chain as live_entry_gates.effective_kg_min_tfs). No checks available -> pass.
Gate only active when EMA_9_21_FILTER_ENABLED. `get(key, default)` is the live config resolver (per-sym aware)."""
import numpy as np


def _tfs(s):
    return [t.strip() for t in str(s or "").split(",") if t.strip()]


def effective_min_tfs(get):
    try:
        kc = int(float(get("KINDERGARTEN_CUMULATIVE_MIN_TFS", 0) or 0))
        if kc > 0:
            return kc
        return max(1, int(float(get("EMA_9_21_FILTER_MIN_TFS", 1) or 1)))
    except (TypeError, ValueError):
        return 1


def _filter_tfs(get):
    return _tfs(get("EMA_9_21_FILTER_TFS", get("EMA_9_21_TIMEFRAME", "1h"))) or ["1h"]


def ema921_pass(get, indicators, is_long):
    """Scalar live predicate. Returns (passed, passing, n_checks)."""
    if not bool(get("EMA_9_21_FILTER_ENABLED", False)):
        return True, 0, 0
    checks = []
    for tf in _filter_tfs(get):
        raw = (indicators or {}).get(f"ema_9_above_21_{tf}")
        if raw is None:
            continue
        v = float(raw)
        checks.append((tf, (v != 0.0) if is_long else (v != 1.0)))
    if not checks:
        return True, 0, 0
    strict = _tfs(get("KINDERGARTEN_STRICT_TFS", ""))
    if strict and not all(ok for tf, ok in checks if tf in strict):
        return False, sum(1 for _, ok in checks if ok), len(checks)
    passing = sum(1 for _, ok in checks if ok)
    return passing >= min(effective_min_tfs(get), len(checks)), passing, len(checks)


def ema921_pass_vec(npz, n, is_long, cfg, safe):
    """Vector twin. Returns bool[n] pass mask, or None when the gate is off / no data."""
    def get(k, d=None):
        return getattr(cfg, k, d)
    if not bool(get("EMA_9_21_FILTER_ENABLED", False)):
        return None
    checks = []
    for tf in _filter_tfs(get):
        k = f"ema_9_above_21_{tf}"
        if k in npz:
            a = np.asarray(safe(npz, k, n, np.nan), dtype=float)
            have = ~np.isnan(a)
            ok = (a != 0.0) if is_long else (a != 1.0)
            checks.append((tf, have, ok))
    if not checks:
        return None
    strict = _tfs(get("KINDERGARTEN_STRICT_TFS", ""))
    ok_all = np.ones(n, dtype=bool)
    passing = np.zeros(n, dtype=np.int16)
    found = np.zeros(n, dtype=np.int16)
    for tf, have, ok in checks:
        found += have.astype(np.int16)
        passing += (have & ok).astype(np.int16)
        if strict and tf in strict:
            ok_all &= ~have | ok
    need = np.minimum(effective_min_tfs(get), found)
    res = ok_all & (passing >= need)
    return np.where(found == 0, True, res)
