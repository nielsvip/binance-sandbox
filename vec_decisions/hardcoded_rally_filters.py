"""hardcoded_rally_filters — optional entry filters for HARDCODED_RALLY_REENTRY (Agent RE, queue RE/001, 2026-10-01).

HARDCODED_RALLY_REENTRY (live: tradier_manage.py:21582 stocks / ez_manage.py:3055 check_reentry_eligible crypto; vector: simulate_one 3 sites) fires on
close past the last exit alone (REQUIRE_WT False). These filters REDUCE its firings; ALL default to OFF/0 = identical to today. Swept per sym_side to find
the filters each symbol needs. 15m+ arrays only (no 3m/5m). Same predicate must be staged live (data/live_parity/staged/RE/001).
  HARDCODED_RALLY_MIN_MOVE_PCT   (0.0)   close must be >= this % beyond the exit price (long: above, short: below)
  HARDCODED_RALLY_MIN_AGE_MIN    (0)     minutes since the last exit must be >= this
  HARDCODED_RALLY_HTF_TREND_TF   ("OFF") "1h" | "4h" | "1h,4h" (AND): wt1_<tf> must side with the trade (long >= 0, short <= 0)
  HARDCODED_RALLY_DC_POS_MAX     (0.0)   0=OFF; long: dc_position_15m <= this, short: dc_position_15m >= 1-this (no chasing an extended range edge)
  HARDCODED_RALLY_SMA200_SIDE_ENABLED (False) long needs close > sma_200_15m, short close < sma_200_15m
"""
import numpy as np

KEYS = ("HARDCODED_RALLY_MIN_MOVE_PCT", "HARDCODED_RALLY_MIN_AGE_MIN", "HARDCODED_RALLY_HTF_TREND_TF", "HARDCODED_RALLY_DC_POS_MAX", "HARDCODED_RALLY_SMA200_SIDE_ENABLED")


def _arr(npz, key, n):
    if key in npz:
        a = np.asarray(npz[key], dtype=float)
        if a.size < n:
            t = np.zeros(n); t[:a.size] = a; a = t
        return np.nan_to_num(a[:n], nan=0.0, posinf=0.0, neginf=0.0)
    return None


class RallyFilters:
    def __init__(self, npz, n, cfg, is_long):
        self.is_long = bool(is_long)
        self.min_move = float(getattr(cfg, "HARDCODED_RALLY_MIN_MOVE_PCT", 0.0) or 0.0)
        self.min_age = float(getattr(cfg, "HARDCODED_RALLY_MIN_AGE_MIN", 0.0) or 0.0)
        tf = str(getattr(cfg, "HARDCODED_RALLY_HTF_TREND_TF", "OFF") or "OFF").strip()
        self.tfs = [] if tf.upper() in ("OFF", "") else [t.strip() for t in tf.split(",") if t.strip()]
        self.dc_max = float(getattr(cfg, "HARDCODED_RALLY_DC_POS_MAX", 0.0) or 0.0)
        self.sma = bool(getattr(cfg, "HARDCODED_RALLY_SMA200_SIDE_ENABLED", False))
        self.active = bool(self.min_move > 0 or self.min_age > 0 or self.tfs or self.dc_max > 0 or self.sma)
        self.wt = {t: _arr(npz, "wt1_" + t, n) for t in self.tfs} if self.active else {}
        self.dc = _arr(npz, "dc_position_15m", n) if self.dc_max > 0 else None
        self.sma_a = _arr(npz, "sma_200_15m", n) if self.sma else None

    def ok(self, i, px, last_exit, age_min):
        """True = the rally reentry may fire on bar i. Missing data on an ENABLED filter = blocked (no silent pass)."""
        if not self.active:
            return True
        L = self.is_long
        if self.min_move > 0 and last_exit > 0:
            mv = (px - last_exit) / last_exit * 100.0
            if (mv if L else -mv) < self.min_move:
                return False
        if self.min_age > 0 and age_min < self.min_age:
            return False
        for t, a in self.wt.items():
            if a is None or i >= a.size or (a[i] < 0 if L else a[i] > 0):
                return False
        if self.dc_max > 0:
            if self.dc is None or i >= self.dc.size:
                return False
            d = self.dc[i]
            if (d > self.dc_max) if L else (d < 1.0 - self.dc_max):
                return False
        if self.sma:
            if self.sma_a is None or i >= self.sma_a.size or self.sma_a[i] <= 0:
                return False
            if (px <= self.sma_a[i]) if L else (px >= self.sma_a[i]):
                return False
        return True
