"""15m-grid WR/LR pullback entry mask for the QUICK_OPEN_GOOD producer."""
import json
import os
from bisect import bisect_right

import numpy as np

_ARCHIVE_CACHE = {"mtime": None, "rows": [], "times": []}


def historical_wr_membership_mask(symbol, timestamps, is_long, account=None):
    """Return account-specific archived membership; unknown data stays False."""
    ts = np.asarray(timestamps, dtype=float)
    result = np.zeros(ts.shape, dtype=bool)
    if ts.ndim != 1 or account not in ("ang", "inf"):
        return result
    for i, value in enumerate(ts):
        result[i] = _wr_in_list_at_account(symbol, bool(is_long), float(value), account) is True
    return result


def _archive_path():
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "wr_lists_history.jsonl")


def _wr_in_list_at_account(symbol, is_long, timestamp, account):
    path = _archive_path()
    try:
        mtime = os.path.getmtime(path)
        if _ARCHIVE_CACHE["mtime"] != mtime:
            rows = []
            with open(path) as stream:
                for line in stream:
                    try:
                        row = json.loads(line)
                        if isinstance(row, dict) and row.get("ts") is not None:
                            rows.append(row)
                    except (TypeError, ValueError):
                        continue
            rows.sort(key=lambda row: float(row["ts"]))
            _ARCHIVE_CACHE["mtime"] = mtime
            _ARCHIVE_CACHE["rows"] = rows
            _ARCHIVE_CACHE["times"] = [float(row["ts"]) for row in rows]
        index = bisect_right(_ARCHIVE_CACHE["times"], float(timestamp)) - 1
        if index < 0:
            return None
        key = f"{account}_{'long' if is_long else 'short'}"
        return str(symbol) in (_ARCHIVE_CACHE["rows"][index].get(key) or [])
    except (OSError, TypeError, ValueError):
        return None


def wr_lr_pullback_15m_mask(ha_4h, k_1h, k_15m, k_15m_prev, wt_cross_15m, in_list, is_long):
    """Mirror the live WR/LR branch when USE_1M_3M_SIGNALS_ENABLED is False.

    The live 3m and 1m slots both resolve to 15m under that mode. Membership is
    supplied by the caller from the historical WR/LR list archive, not assumed.
    """
    htf = np.char.lower(np.asarray(ha_4h, dtype=str))
    k1h = np.asarray(k_1h, dtype=float)
    k15 = np.asarray(k_15m, dtype=float)
    k15p = np.asarray(k_15m_prev, dtype=float)
    cross = np.asarray(wt_cross_15m, dtype=str)
    listed = np.asarray(in_list, dtype=bool)
    n = k15.shape[0]
    if not (htf.shape == k1h.shape == k15p.shape == cross.shape == listed.shape == (n,)):
        raise ValueError("WR/LR pullback inputs must be 1D arrays with matching lengths")
    if is_long:
        return listed & (htf != "red") & (k1h < 45.0) & (k15 < 45.0) & (k15 < 40.0) & ((cross == "BULL") | ((k15 > k15p) & (k15 < 30.0)))
    return listed & (htf != "green") & (k1h > 55.0) & (k15 > 55.0) & (k15 > 60.0) & ((cross == "BEAR") | ((k15 < k15p) & (k15 > 70.0)))


def wr_lr_good_score(score, fire_mask):
    """Mirror the live `score = max(score, 22)` performed on a WR/LR fire."""
    values = np.asarray(score, dtype=float)
    fires = np.asarray(fire_mask, dtype=bool)
    if values.shape != fires.shape:
        raise ValueError("score and fire_mask must have matching shapes")
    return np.where(fires, np.maximum(values, 22.0), values)


def wr_lr_quick_good_from_npz(npz, symbol, is_long, cfg, score=None, membership_lookup=None, account=None):
    """Return `(fire_mask, score_after)` for the live 15m WR/LR producer.

    Requires timestamps for historical list membership. Unknown membership is
    False. The native 1m/3m-enabled path returns None because these 15m inputs
    cannot stand in for its true lower-timeframe metrics.
    """
    n = _npz_len(npz)
    if n is None:
        return None
    if not bool(_cfg(cfg, "WR_PULLBACK_ENABLED", True)):
        mask = np.zeros(n, dtype=bool)
        base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
        if base.shape != (n,):
            raise ValueError("score must have shape (n,)")
        return mask, base.copy()
    if bool(_cfg(cfg, "USE_1M_3M_SIGNALS_ENABLED", False)):
        return None
    timestamps = _array(npz, "timestamps", n)
    if timestamps is None:
        return None
    account = account or _cfg(cfg, "VEC_ACCOUNT_KEY", None)
    if account not in ("ang", "inf"):
        return None
    membership = historical_wr_membership_mask(str(symbol), timestamps, is_long, account) if membership_lookup is None else np.zeros(n, dtype=bool)
    if membership_lookup is not None:
        try:
            for i, ts in enumerate(timestamps):
                membership[i] = membership_lookup(account, str(symbol), bool(is_long), float(ts)) is True
        except Exception:
            membership.fill(False)
    ha = _ha_strings(_array(npz, "ha_4h", n), n)
    k1h = _first_array(npz, ("stoch_k_1h", "k_1h"), n, 50.0)
    k15 = _first_array(npz, ("stoch_k_15m", "k_15m"), n, 50.0)
    k15_prev = _first_array(npz, ("stoch_k_15m_prev", "k_15m_prev"), n, 50.0)
    wt_cross = _wt_cross_labels(_first_array(npz, ("wt_cross_15m",), n, 0.0))
    fire = wr_lr_pullback_15m_mask(ha, k1h, k15, k15_prev, wt_cross, membership, is_long)
    base = np.zeros(n, dtype=float) if score is None else np.asarray(score, dtype=float)
    if base.shape != (n,):
        raise ValueError("score must have shape (n,)")
    return fire, wr_lr_good_score(base, fire)


def _cfg(cfg, key, default):
    if isinstance(cfg, dict):
        return cfg.get(key, default)
    return getattr(cfg, key, default)


def _npz_len(npz):
    try:
        if "timestamps" in npz:
            values = np.asarray(npz["timestamps"]).reshape(-1)
            return int(values.size) if values.size else None
        for value in npz.values():
            values = np.asarray(value)
            if values.ndim == 1 and values.size:
                return int(values.size)
    except Exception:
        return None
    return None


def _array(npz, key, n):
    try:
        if key not in npz:
            return None
        values = np.asarray(npz[key]).reshape(-1)
        return values if values.size == n else None
    except Exception:
        return None


def _first_array(npz, keys, n, default):
    for key in keys:
        values = _array(npz, key, n)
        if values is not None:
            try:
                result = values.astype(float)
                return np.where(np.isfinite(result), result, default)
            except (TypeError, ValueError):
                continue
    return np.full(n, default, dtype=float)


def _ha_strings(values, n):
    if values is None:
        return np.full(n, "", dtype=str)
    try:
        raw = np.asarray(values).reshape(-1)
        if raw.dtype.kind in "SUO":
            return np.asarray([str(v).lower() for v in raw], dtype=str)
        nums = raw.astype(float)
        return np.where(nums == -1, "red", np.where(nums == 1, "green", "neutral"))
    except (TypeError, ValueError):
        return np.full(n, "", dtype=str)


def _wt_cross_labels(values):
    raw = np.asarray(values).reshape(-1)
    if raw.dtype.kind in "SUO":
        return np.asarray([str(v).upper() for v in raw], dtype=str)
    try:
        nums = raw.astype(float)
        return np.where(nums == 1, "BULL", np.where(nums == -1, "BEAR", ""))
    except (TypeError, ValueError):
        return np.full(raw.shape, "", dtype=str)
