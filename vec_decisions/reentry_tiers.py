"""REENTRY_TIERS — vector twin of the live crypto TIER1 / TIER1_PARTIAL / TIER2 reentry.
2026-10-07 USER KILL: TIER2_FORCED (time-based reopen) DELETED everywhere — reentry is ALWAYS technical and ONLY on trend continuation.

LIVE SOURCE: ez_positions_quick check_entry_candidates_for_account (TIER block ~15891-15934) + sizing (~16501-16511).
USER 2026-10-06 full-parity: complete mirror (was TIER1-cross + FORCED only; TIER2 chase, PARTIAL split,
rally-cap guard added; stoch legs are 15m while USE_1M_3M_SIGNALS_ENABLED is off — == live NO-1m/3m fallback).

Live chain (if/elif — order matters, mirrored):
  guards: reentry_px>0, age<20h (or never-exited edge), age>=REENTRY_MIN_GAP_MINUTES, not SYMGATE-blocked
          (REENTRY_SYMGATE_ENABLED=False both sides -> skipped), not RALLY_K15M_MAX-blocked.
  TIER1_PARTIAL: crossed exit by >0.1% AND exhausted (k>95 long / k<5 short) AND PARTIAL_ENABLED (score 12).
  TIER1        : crossed exit by >0.1% AND not exhausted (score 20, size floor SPS*TIER1_SIZE_MULT).
  TIER2 chase  : >TIER2_PRICE_PCT past exit AND momentum (k>k_prev or k>d side-aware) AND not exhausted
                 AND age>=TIER2_MIN_MINUTES (score 18, SPS floor).
  crossed+exhausted with PARTIAL disabled -> NO tier (falls through the whole chain, == live).
Scores feed live sizing; v12 uses size_mult floors (TIER1 1.5x SPS floor; others SPS floor).
"""
from __future__ import annotations


def rally_k15_blocked(k15: float, is_long: bool, cfg) -> bool:
    """Live RALLY_K15M_MAX guard mirror (live default 30.0)."""
    try:
        cap = float(getattr(cfg, "REENTRY_RALLY_K15M_MAX", 100.0) or 100.0)
    except Exception:
        cap = 100.0
    if cap >= 100.0:
        return False
    try:
        k = float(k15)
    except Exception:
        k = 50.0
    if is_long:
        return k >= cap
    return k <= (100.0 - cap)


def tier_fire(is_long: bool, px: float, last_exit_px: float, mins_since_exit: float, cfg,
              k15: float = 50.0, k15_prev: float = 50.0, d15: float = 50.0):
    """-> None | 'TIER1' | 'TIER1_PARTIAL' | 'TIER2'. Mirrors the live if/elif chain."""
    try:
        if not (px and px > 0 and last_exit_px and last_exit_px > 0):
            return None
        if mins_since_exit >= 72000.0 / 60.0:
            return None
        if mins_since_exit < float(getattr(cfg, "REENTRY_MIN_GAP_MINUTES", 0.0) or 0.0):
            return None
        try:
            k, kp, d = float(k15), float(k15_prev), float(d15)
        except Exception:
            k, kp, d = 50.0, 50.0, 50.0
        if rally_k15_blocked(k, is_long, cfg):
            return None
        if is_long:
            exhausted = k > 95.0
            momentum = (k > kp) or (k > d)
        else:
            exhausted = k < 5.0
            momentum = (k < kp) or (k < d)
        cross = 0.001
        crossed = px > last_exit_px * (1.0 + cross) if is_long else px < last_exit_px * (1.0 - cross)
        if crossed:
            if exhausted and bool(getattr(cfg, "REENTRY_EXHAUSTED_PARTIAL_ENABLED", True)):
                return "TIER1_PARTIAL"
            if not exhausted:
                return "TIER1"
            return None
        t2_pct = float(getattr(cfg, "REENTRY_TIER2_PRICE_PCT", 0.003) or 0.003)
        t2_min = float(getattr(cfg, "REENTRY_TIER2_MIN_MINUTES", 10.0) or 10.0)
        trend = px > last_exit_px * (1.0 + t2_pct) if is_long else px < last_exit_px * (1.0 - t2_pct)
        if trend and momentum and not exhausted and mins_since_exit >= t2_min:
            return "TIER2"
        return None
    except Exception:
        return None


def tier_score(tier) -> float:
    """Live score per tier (sizing input)."""
    return {"TIER1": 20.0, "TIER1_PARTIAL": 12.0, "TIER2": 18.0}.get(tier, 0.0)


def tier_reason(tier, is_long: bool, last_exit_px: float, px: float, k: float, mins_since_exit: float) -> str:
    """Live-exact reason strings (k label kept `k3m` — value is 15m under NO-1m/3m, both sides)."""
    try:
        kk = float(k)
    except Exception:
        kk = 50.0
    if tier == "TIER1_PARTIAL":
        return f"TIER1_PARTIAL_EXHAUSTED_exit{last_exit_px:.4f}_cur{px:.4f}_k3m{kk:.0f}"
    if tier == "TIER1":
        return f"TIER1_PRICE_CROSS_REENTRY_exit{last_exit_px:.4f}_cur{px:.4f}_k3m{kk:.0f}"
    if tier == "TIER2":
        return f"TIER2_CHASE_REENTRY_exit{last_exit_px:.4f}_cur{px:.4f}_k3m{kk:.0f}_min{mins_since_exit:.0f}"
    return ""


def size_mult(tier, cfg, base_mult: float = 1.0) -> float:
    """live: TIER1 qty = max(qty, SPS*TIER1_SIZE_MULT/price); TIER2/FORCED/PARTIAL = SPS floor (mult 1.0).

    NOTE live quirk mirrored: REENTRY_TIER2_SIZE_MULT (0.8)/0.5 are computed but the TIER2 floor
    uses plain SPS/price (mult unused) — so the twin applies no extra mult for TIER2 either.
    """
    if tier == "TIER1":
        return max(float(base_mult), float(getattr(cfg, "REENTRY_TIER1_SIZE_MULT", 1.5) or 1.5))
    return float(base_mult)
