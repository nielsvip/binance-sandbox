"""TRADIER OVERDUE REENTRY — vector twin of the reentry_monitor OVERDUE BYPASS gate.

Live (tradier_manage.py:52506-52584, SAFETY SWITCH 3): a reentry candidate that
has waited 2h+ since exit bypasses the stoch+WT gates when EITHER leg holds —
(a) 15m Donchian breakout (LONG close >= dc_high_15m, SHORT mirrored), or
(b) GR HL/HH on >= REENTRY_GR_MIN_TFS of 1h/4h/D with WT agreement on each
counted TF (mode OR/HH/HL, default OR/2).

Vector semantics (stocks / MODE=tradier only, flat after a close): age in bars
since the last CLOSE bar; overdue at >= REENTRY_OVERDUE_MIN_BARS (default 8 =
2h of 15m bars, the live 2h). All inputs are NPZ 15m/1h/4h/D fields. Fail-closed
throughout: any missing/NaN input refuses the bar, never forces a trade.

ONE predicate for v12_quick_engine.simulate_one (single overdue.fire call site,
gated to MODE == "tradier"); default OFF so the sweep baseline is untouched
(BIBLE s14.1).
"""

from __future__ import annotations

import math

REASON = "REENTRY_OVERDUE"

_TFS = ("1h", "4h", "D")


def _num(v, d):
    try:
        f = float(v)
        return f if math.isfinite(f) else d
    except (TypeError, ValueError):
        return d


def _get(cfg, k, d):
    v = cfg(k, d) if callable(cfg) else getattr(cfg, k, d)
    return d if v is None else v


def enabled(cfg) -> bool:
    v = _get(cfg, "REENTRY_OVERDUE_MODEL_ENABLED", False)
    return (
        (str(v).strip().lower() in ("1", "true", "yes"))
        if isinstance(v, str)
        else bool(v)
    )


def fire(
    cfg, is_long, age_bars, px, dc_15m, highs, lows, highs_prev, lows_prev, wts
) -> bool:
    """True when the overdue bypass fires this bar.

    px: current price. dc_15m: dc_high_15m (LONG) or dc_low_15m (SHORT) level.
    highs/lows/highs_prev/lows_prev/wts: dicts keyed by TF in _TFS; wts holds
    (wt1, wt2) tuples per TF. Mirrors the live dc_ok/gr_ok disjunction exactly.
    """
    try:
        min_bars = int(_get(cfg, "REENTRY_OVERDUE_MIN_BARS", 8) or 8)
        if int(age_bars) < min_bars:
            return False
        px = _num(px, 0.0)
        if px <= 0:
            return False
        dc_lvl = _num(dc_15m, 0.0)
        if is_long:
            dc_ok = dc_lvl > 0 and px >= dc_lvl
        else:
            dc_ok = dc_lvl > 0 and px <= dc_lvl
        mode = str(_get(cfg, "REENTRY_GR_HLHH_MODE", "OR") or "OR").upper()
        try:
            min_tfs = int(_get(cfg, "REENTRY_GR_MIN_TFS", 2) or 2)
        except (TypeError, ValueError):
            return False
        count = 0
        for tf in _TFS:
            hi = _num((highs or {}).get(tf), 0.0)
            hip = _num((highs_prev or {}).get(tf), 0.0)
            lo = _num((lows or {}).get(tf), 0.0)
            lop = _num((lows_prev or {}).get(tf), 0.0)
            w = (wts or {}).get(tf) or (0.0, 0.0)
            w1, w2 = _num(w[0], 0.0), _num(w[1], 0.0)
            hh = hi > 0 and hip > 0 and hi > hip
            hl = lo > 0 and lop > 0 and lo > lop
            wt_ok = (w1 > w2) if is_long else (w1 < w2)
            if mode == "HH" and hh and wt_ok:
                count += 1
            elif mode == "HL" and hl and wt_ok:
                count += 1
            elif mode != "HH" and mode != "HL" and (hh or hl) and wt_ok:
                count += 1
        gr_ok = count >= min_tfs
        return bool(dc_ok or gr_ok)
    except Exception:
        return False
