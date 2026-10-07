"""check_entry_candidates_crypto__wr_lr_pullback.py

SHARED scalar+vectorized predicate for the WR/LR PULLBACK entry in
check_entry_candidates_for_account (ez_positions_quick.py:15552-15588).

Fires a pullback-into-uptrend entry (LONG, "WR") or pullback-into-downtrend entry
(SHORT, "LR"): HTF trend confirmed (symbol in the account's WR/LR ranking list +
ha_4h not against), all of k_1h/k_15m/k_3m low (LONG) / high (SHORT), and the 1m
turning back the trend direction.

FAITHFUL EXTRACTION of ez_positions_quick.py:15569-15584:

  LONG (symbol in _wr_long):
    _htf_ok      = ha_4h != "red"
    _all_low     = k_1h<45 and k_15m<45 and k_3m<40
    _1m_turning  = wt_cross_1m=="BULL"  OR (k_1m>k_1m_prev and k_1m<30)
    fires        = _htf_ok and _all_low and _1m_turning   → score=max(.,22), rec=GOOD_BUY
  SHORT (symbol in _wr_short):
    _htf_ok      = ha_4h != "green"
    _all_high    = k_1h>55 and k_15m>55 and k_3m>60
    _1m_turning  = wt_cross_1m=="BEAR"  OR (k_1m<k_1m_prev and k_1m>70)
    fires        = _htf_ok and _all_high and _1m_turning  → score=max(.,22), rec=GOOD_SELL

PURITY: the FIRE PREDICATE is pure per-bar on NPZ-derivable fields:
  ha_4h (heikin-ashi color string), stoch_k_1h, stoch_k_15m, stoch_k_3m,
  stoch_k_1m, k_1m_prev, wt_cross_1m (WT cross label string).
The two non-numeric inputs (ha_4h color, wt_cross_1m label) are present in the
indicator dict / derivable in NPZ precompute, so this is vectorizable.

CALLER-SUPPLIED FLAG (not part of the per-bar predicate): `in_list` mirrors the
live `symbol in _wr_long` / `symbol in _wr_short` membership — exactly like the
pyramid template passes `is_long`. The 300s cooldown (_wr_pullback_last) is
live-only state (state seam), applied by the caller, NOT in the predicate.

Mirrors strategy_enhancements.py _pyramid_fires: one pure core shared by scalar +
vec so the two paths cannot drift.

USER 2026-10-06 full-parity notes:
- NO-1m/3m (NOTE_3M_REENABLE): while USE_1M_3M_SIGNALS_ENABLED is off, the k_3m leg reads
  k_15m (<40 long / >60 short), the 1m-turning leg reads k_15m vs k_15m_prev (<30 / >70),
  and wt_cross_1m reads wt_cross_15m — identical to the live fallback in ez_positions_quick.
- in_list (WR/LR ranking membership) is caller-supplied. Live reads the account's
  symbols_{acct}_{long,short}.json. Helpers: wr_in_list_live (current files, for VEC_EXACT
  in-process) and wr_in_list_at (data/wr_lists_history.jsonl archive, for backtests).
  Sweeps over pre-archive history fail closed (False) — documented seam.
"""
import json
import os
import sys
import time
from typing import Tuple
import numpy as np

_WR_FILE_CACHE = {"ts": 0.0, "lists": {}}
_WR_ARCHIVE_CACHE = {"mtime": 0.0, "rows": []}


def _no3m(config) -> bool:
    if isinstance(config, dict):
        return not bool(config.get("USE_1M_3M_SIGNALS_ENABLED", False))
    return not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED", False))


def wr_in_list_live(sym: str, is_long: bool) -> bool:
    """Current WR/LR membership for the live process account (VEC_EXACT in-process).

    Parses sys.argv for `--account <acct>` (ez_manage fleet processes) and reads
    symbols_<acct>_<long|short>.json (same files live reads). Anything else
    (sweeps, backtests, stocks) -> False (fail-closed). 60s file cache.
    """
    try:
        acct = None
        argv = list(sys.argv or [])
        for _i, _a in enumerate(argv):
            if _a == "--account" and _i + 1 < len(argv):
                acct = str(argv[_i + 1]).strip().lower()
                break
        if not acct:
            return False
        now = time.time()
        if now - float(_WR_FILE_CACHE.get("ts", 0.0)) > 60.0 or acct not in _WR_FILE_CACHE.get("lists", {}):
            base = os.path.dirname(os.path.abspath(__file__))
            root = os.path.dirname(base)
            long_p = os.path.join(root, f"symbols_{acct}_long.json")
            short_p = os.path.join(root, f"symbols_{acct}_short.json")
            longs, shorts = set(), set()
            try:
                if os.path.exists(long_p):
                    with open(long_p) as _f:
                        _d = json.load(_f)
                    longs = {_s for _s in (_d if isinstance(_d, list) else []) if _s}
            except Exception:
                pass
            try:
                if os.path.exists(short_p):
                    with open(short_p) as _f:
                        _d = json.load(_f)
                    shorts = {_s for _s in (_d if isinstance(_d, list) else []) if _s}
            except Exception:
                pass
            _WR_FILE_CACHE["ts"] = now
            _WR_FILE_CACHE.setdefault("lists", {})[acct] = (longs, shorts)
        longs, shorts = _WR_FILE_CACHE["lists"][acct]
        return (str(sym) in longs) if is_long else (str(sym) in shorts)
    except Exception:
        return False


def wr_in_list_at(sym: str, is_long: bool, ts: float):
    """Historical WR/LR membership from data/wr_lists_history.jsonl (None if unknown).

    Archive rows: {"ts": epoch, "ang_long": [...], "ang_short": [...], "inf_long": [...], "inf_short": [...]}.
    Returns the membership from the latest row with row.ts <= ts (any account — a sym listed for
    either account counts, matching live tradeable-universe semantics for backtests).
    """
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        p = os.path.join(os.path.dirname(base), "data", "wr_lists_history.jsonl")
        if not os.path.exists(p):
            return None
        mt = os.path.getmtime(p)
        if mt != _WR_ARCHIVE_CACHE.get("mtime", 0.0):
            rows = []
            with open(p) as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line:
                        continue
                    try:
                        _r = json.loads(_line)
                    except Exception:
                        continue
                    if isinstance(_r, dict) and _r.get("ts"):
                        rows.append(_r)
            rows.sort(key=lambda _r: float(_r.get("ts", 0) or 0))
            _WR_ARCHIVE_CACHE["mtime"] = mt
            _WR_ARCHIVE_CACHE["rows"] = rows
        best = None
        for _r in _WR_ARCHIVE_CACHE["rows"]:
            try:
                if float(_r.get("ts", 0) or 0) <= float(ts):
                    best = _r
                else:
                    break
            except Exception:
                continue
        if best is None:
            return None
        key = "_long" if is_long else "_short"
        for _acct in ("ang", "inf"):
            try:
                if str(sym) in (best.get(f"{_acct}{key}") or []):
                    return True
            except Exception:
                continue
        return False
    except Exception:
        return None


def _wr_lr_pullback_fires(ha_4h: str, k_1h: float, k_15m: float, k_3m: float,
                          k_1m: float, k_1m_prev: float, wt_cross_1m: str,
                          in_list: bool, is_long: bool) -> bool:
    """PURE per-bar fire test. Mirrors ez_positions_quick.py:15569-15584. ha_4h and
    wt_cross_1m are compared case-insensitively / exactly as the live code does
    (live lowercases ha_4h; wt_cross_1m compared to literal 'BULL'/'BEAR')."""
    if not in_list:
        return False
    ha = str(ha_4h).lower()
    if is_long:
        htf_ok = ha != "red"
        all_low = k_1h < 45 and k_15m < 45 and k_3m < 40
        turning = (wt_cross_1m == "BULL") or (k_1m > k_1m_prev and k_1m < 30)
        return bool(htf_ok and all_low and turning)
    htf_ok = ha != "green"
    all_high = k_1h > 55 and k_15m > 55 and k_3m > 60
    turning = (wt_cross_1m == "BEAR") or (k_1m < k_1m_prev and k_1m > 70)
    return bool(htf_ok and all_high and turning)


def check_wr_lr_pullback(config, indicators: dict, metrics: dict, in_list: bool,
                         is_long: bool) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason). k_1m / k_1m_prev / wt_cross_1m
    come from metrics (live), the rest from indicators — same sources as live
    (ez_positions_quick.py:15558-15567)."""
    def gi(k, d=50.0):
        v = (indicators or {}).get(k, d)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    def gm(k, d=50.0):
        v = (metrics or {}).get(k, d)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    ha_4h = str((indicators or {}).get("ha_4h", ""))
    k_1h = gi("stoch_k_1h")
    k_15m = gi("stoch_k_15m")
    if _no3m(config):
        k_3m = gi("stoch_k_15m")
        k_1m = gi("stoch_k_15m")
        k_1m_prev = gi("stoch_k_15m_prev", gi("k_15m_prev", 50.0))
        wt_cross_1m = str((indicators or {}).get("wt_cross_15m", ""))
    else:
        k_3m = gi("stoch_k_3m")
        k_1m = gm("stoch_k_1m")
        k_1m_prev = gm("k_1m_prev")
        wt_cross_1m = str((metrics or {}).get("wt_cross_1m", ""))
    if not _wr_lr_pullback_fires(ha_4h, k_1h, k_15m, k_3m, k_1m, k_1m_prev,
                                 wt_cross_1m, in_list, is_long):
        return False, ""
    k_4h = gi("stoch_k_4h")
    if is_long:
        return True, f"WR_PULLBACK_k4={k_4h:.0f}_k1h={k_1h:.0f}_k15={k_15m:.0f}_k3={k_3m:.0f}_k1m={k_1m:.0f}"
    return True, f"LR_SHORTTOP_k4={k_4h:.0f}_k1h={k_1h:.0f}_k15={k_15m:.0f}_k3={k_3m:.0f}_k1m={k_1m:.0f}"


def check_wr_lr_pullback_vec(config, ha_4h_arr, k_1h_arr, k_15m_arr, k_3m_arr,
                             k_1m_arr, k_1m_prev_arr, wt_cross_1m_arr, in_list,
                             is_long, k_15m_prev_arr=None, wt_cross_15m_arr=None):
    """VECTORIZED per-bar fire mask. SAME logic as scalar. `in_list` is a scalar
    bool (the symbol's membership in the account WR/LR list, same for all bars of
    that symbol). ha_4h_arr / wt_cross_1m_arr are object/str ndarrays. Caller takes
    first-True per position + applies the 300s cooldown (state seam).
    NO-1m/3m: while the switch is off, k_3m<-k_15m, k_1m/k_1m_prev<-k_15m/k_15m_prev
    (shift when k_15m_prev_arr is None), wt_cross_1m<-wt_cross_15m (== live)."""
    n = np.asarray(k_1h_arr).shape[0]
    if not in_list:
        return np.zeros(n, dtype=bool)
    ha = np.char.lower(np.asarray(ha_4h_arr, dtype=str))
    k1h = np.asarray(k_1h_arr, dtype=float)
    k15 = np.asarray(k_15m_arr, dtype=float)
    if _no3m(config):
        k3 = k15
        k1 = k15
        if k_15m_prev_arr is not None:
            k1p = np.asarray(k_15m_prev_arr, dtype=float)
        else:
            k1p = np.roll(k15, 1)
            if n:
                k1p[0] = k15[0]
        wtc = np.asarray(wt_cross_15m_arr if wt_cross_15m_arr is not None else [], dtype=str)
        if wtc.shape[0] != n:
            wtc = np.full(n, "", dtype=str)
    else:
        k3 = np.asarray(k_3m_arr, dtype=float)
        k1 = np.asarray(k_1m_arr, dtype=float)
        k1p = np.asarray(k_1m_prev_arr, dtype=float)
        wtc = np.asarray(wt_cross_1m_arr, dtype=str)
    if is_long:
        htf_ok = ha != "red"
        all_low = (k1h < 45) & (k15 < 45) & (k3 < 40)
        turning = (wtc == "BULL") | ((k1 > k1p) & (k1 < 30))
        return htf_ok & all_low & turning
    htf_ok = ha != "green"
    all_high = (k1h > 55) & (k15 > 55) & (k3 > 60)
    turning = (wtc == "BEAR") | ((k1 < k1p) & (k1 > 70))
    return htf_ok & all_high & turning
