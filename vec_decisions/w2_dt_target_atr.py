"""w2-exits staged twin: DT_TARGET_ATR (ATR-driven daytrade target).

Live source: tradier_manage.py:32936-32951 (ATR target) + :32976-32978 (fire).
  atr = atr_5m primary, atr_15m fallback (live :32947-32949)
  atr_target_pct = 2*atr/entry ; eff = max(atr_target, target_pct, noloss_min)
  fire when gain_pct >= eff. Reason DT_TARGET_ATR when ATR drove the target.

Vec delta (disclosed): NPZs carry atr_15m but NO atr_5m (verified S1
MSFT.npz 1155 keys; LG-13 USER ORDER forbids 5m approximation), so the twin
models the live 15m-fallback path only. When live would use 5m, vec uses 15m.

User-order boundary: the fixed-% DT_TARGET branch (gain >= target_pct) was
DELETED from vec by 2026-09-28 USER SPEC ("NEVER a fix % profit exit",
v12 walk). This twin fires ONLY when the ATR target drives
(atr_target > target_pct); the fixed branch stays deleted. Scalar walk
predicate + ONE call site (after the DC daytrade branch, mirroring live
elif order stop-DC -> target-DC -> ATR/fixed target).
"""
from __future__ import annotations


def dt_target_atr_threshold(entry_px: float, atr15: float, cfg,
                             is_tradier: bool):
    """Return (fires_only_if_atr_drives_threshold, atr_target_pct, fixed_pct).

    Pure predicate core; the walk compares live gain against the returned
    ATR threshold. Returns (None, atr_pct, fixed_pct) when ATR cannot drive
    (switch OFF, no ATR, or ATR target below the fixed target).
    """
    try:
        if not bool(getattr(cfg, "DT_TARGET_ATR_ENABLED", False)):
            return None, 0.0, 0.0
        if is_tradier:
            fixed_pct = float(getattr(cfg, "TRADIER_DC_DAYTRADE_TARGET_PCT", 0.005))
        else:
            fixed_pct = float(getattr(cfg, "DC_DAYTRADE_TARGET_PCT", 0.01))
        noloss_min = float(getattr(cfg, "NOLOSS_MIN_PROFIT_PCT_TRADIER", 1.0)) / 100.0
        fixed_pct = max(fixed_pct, noloss_min)  # live :32943
        if not (entry_px and entry_px > 0 and atr15 and atr15 > 0):
            return None, 0.0, fixed_pct
        atr_pct = 2.0 * float(atr15) / float(entry_px)  # live :32950
        if atr_pct <= fixed_pct:
            return None, atr_pct, fixed_pct  # fixed would drive -> stays deleted
        return atr_pct, atr_pct, fixed_pct
    except Exception:
        return None, 0.0, 0.0


def dt_target_atr_fires(gain_pct_frac: float, entry_px: float, atr15: float,
                         cfg, is_tradier: bool):
    """Walk predicate: (fire: bool, reason: str). gain as fraction (0.01=1%)."""
    try:
        thr, _atr, _fx = dt_target_atr_threshold(entry_px, atr15, cfg, is_tradier)
        if thr is None:
            return False, ""
        if float(gain_pct_frac) >= float(thr):
            return True, f"DT_TARGET_ATR {float(gain_pct_frac):.2%}"
        return False, ""
    except Exception:
        return False, ""
