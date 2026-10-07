"""Stocks entry-vetting stack — faithful port of the tradier queue entry
vetting every live OPEN passes (tradier_manage.py:24893-24944).

LIVE GATES (unconditional in live — no enable switch; V8 parity env bypass):
  ZONE (24893-24898): fresh OPEN/QUICK_OPEN only (reentry / rotation /
      gap_fill / rz exempt). k = k_15m in the fast window else k_1h; fast =
      09:30-10:00 or 14:00-16:00 ET (24875-24877). LONG blocked when k >
      ENTRY_ZONE_LONG (live 80.0); SHORT blocked when k < ENTRY_ZONE_SHORT
      (live 20.0). Predicate shared with live: vec_decisions.shared_zone.
  ALIGNMENT (24899-24930): 17 direction-agreement conditions (4 stoch K>D
      pairs, 4 HA colors, 3 px-vs-DC-basis, 3 WT crosses, 2 RSI vs 50, 1
      px-vs-SMA200); fresh entries need >= ENTRY_MIN_ALIGNMENT (live 5;
      6 for proven-strategy reasons — unobservable in vec, uses 5).
      Reentries are boost-only, never blocked; RATIO_RECOVERY exempt.
  DC4 (24931-24944): LONG blocked below dc_low4_5m (MTF_ARROW / LR_BAND
      reasons exempt); SHORT blocked above dc_high4_5m (no exemption).
      Applies to ALL entries incl. reentries.

VEC GAP: vec has no alignment gate (live code says so: "vector has no
alignment gate", 24926), no zone gate, no DC4-bottom gate at any level.

PLACEMENT (staged): zone+alignment constrain the FINAL fresh-entry signal
only (live exempts reentries — entry_sig-only placement is exactly
live-faithful); DC4 is a post-reason fire veto like GR (covers every fire;
band-reason exemption enforced at the veto site). TRC_* knob variants are
out of scope (vec has no account dimension on this path; trb-effective).

5m TERMS: NPZ has no 5m arrays. The two 5m alignment terms (WT 5m pair,
HA 5m) are IGNORED (max countable 15, live min kept verbatim at 5 —
documented, conservative direction). DC4 floors dc_low4/high4_5m to the
15m 4-bar channel when the 5m key is absent (wider channel -> fewer blocks
than live 5m; closer to live than inert).

ENTRY_VET_RELAX_MODE finding: that knob family is crypto-only — the sole
live consumer is ez_manage.check_entry_vetting (crypto; staged crypto twin
already exists in lane entry-crypto). No tradier consumer exists. The
operative stocks entry-vetting strictness is this queue stack.
"""
from __future__ import annotations

import os

import numpy as np


def _v8_bypass():
    return os.environ.get("V8_BACKTEST_BYPASS_DRAWDOWN") == "1" or bool(os.environ.get("V8_LADDER_ONLY_SIDE"))


def _fast_window_mask(ts, n):
    """Live fast window (24875): 09:30-10:00 or 14:00-16:00 ET, from bar ts."""
    try:
        t = np.asarray(ts, dtype=float)
        if t.size != n:
            return np.zeros(n, dtype=bool)
        # ET = UTC-4 (Apr-Oct sessions; live uses wall-clock ET).
        et = (t - 4 * 3600) % 86400
        mins = et // 60
        return np.asarray((((mins >= 570) & (mins < 600)) | ((mins >= 840) & (mins < 960))), dtype=bool)
    except Exception:
        return np.zeros(n, dtype=bool)


def fresh_block_mask(npz, n, is_long, cfg, close, safe, ts):
    """Bool[n], True = live zone/alignment would refuse a FRESH entry.
    None when the V8 parity bypass env is set (live bypasses too)."""
    if _v8_bypass():
        return None
    px = np.asarray(close, dtype=float)
    # --- zone (live 24893-24898 + shared_zone.is_zone_blocked) ---
    try:
        ez = float(getattr(cfg, "ENTRY_ZONE_LONG", 80.0))
    except Exception:
        ez = 80.0
    try:
        esz = float(getattr(cfg, "ENTRY_ZONE_SHORT", 20.0))
    except Exception:
        esz = 20.0
    k15 = np.asarray(safe(npz, "stoch_k_15m", n, 50.0), dtype=float)
    k1h = np.asarray(safe(npz, "stoch_k_1h", n, 50.0), dtype=float)
    fast = _fast_window_mask(ts, n)
    zk = np.where(fast, k15, k1h)
    if is_long:
        zone_blk = zk > ez
    else:
        zone_blk = zk < esz
    # --- alignment count (live 24899-24912; 5m terms ignored, see docstring) ---
    al = np.zeros(n, dtype=np.int32)
    for tf in ("15m", "1h", "4h", "D"):
        kk = np.asarray(safe(npz, "stoch_k_%s" % tf, n, 50.0), dtype=float)
        dd = np.asarray(safe(npz, "stoch_d_%s" % tf, n, 50.0), dtype=float)
        al += ((kk > dd) if is_long else (kk < dd)).astype(np.int32)
    for tf in ("15m", "1h", "4h"):
        hk = "ha_%s" % tf if ("ha_%s" % tf) in npz else "ha_color_%s" % tf
        ha = np.asarray(safe(npz, hk, n, 0.0), dtype=float)
        al += ((ha > 0) if is_long else (ha < 0)).astype(np.int32)
    for tf in ("15m", "1h", "4h"):
        b = np.asarray(safe(npz, "dc_basis_%s" % tf, n, 0.0), dtype=float)
        good = b > 0
        al += np.where(good, ((px > b) if is_long else (px < b)).astype(np.int32), 0)
    for tf in ("15m", "1h"):
        w1 = np.asarray(safe(npz, "wt1_%s" % tf, n, 0.0), dtype=float)
        w2 = np.asarray(safe(npz, "wt2_%s" % tf, n, 0.0), dtype=float)
        al += ((w1 > w2) if is_long else (w1 < w2)).astype(np.int32)
    r1 = np.asarray(safe(npz, "rsi_1h", n, 50.0), dtype=float)
    r4 = np.asarray(safe(npz, "rsi_4h", n, 50.0), dtype=float)
    al += ((r1 > 50) if is_long else (r1 < 50)).astype(np.int32)
    al += ((r4 > 50) if is_long else (r4 < 50)).astype(np.int32)
    sma = np.asarray(safe(npz, "sma_200_1h", n, 0.0), dtype=float)
    good = sma > 0
    al += np.where(good, ((px > sma) if is_long else (px < sma)).astype(np.int32), 0)
    try:
        min_al = int(float(getattr(cfg, "ENTRY_MIN_ALIGNMENT", 5)))
    except Exception:
        min_al = 5
    align_blk = al < min_al
    return np.asarray(zone_blk | align_blk, dtype=bool)


def dc4_block_mask(npz, n, is_long, cfg, close, safe):
    """Bool[n], True = live DC4 top/bottom veto would refuse (any fire incl.
    reentry; LONG band-reason exemption enforced at the veto site)."""
    if _v8_bypass():
        return None
    px = np.asarray(close, dtype=float)
    if "dc_low4_5m" in npz:
        lo = np.asarray(safe(npz, "dc_low4_5m", n, 0.0), dtype=float)
    else:
        lo = np.asarray(safe(npz, "dc_low4_15m", n, 0.0), dtype=float)
    if "dc_high4_5m" in npz:
        hi = np.asarray(safe(npz, "dc_high4_5m", n, 0.0), dtype=float)
    else:
        hi = np.asarray(safe(npz, "dc_high4_15m", n, 0.0), dtype=float)
    if is_long:
        blk = (lo > 0) & (px < lo)
    else:
        blk = (hi > 0) & (px > hi)
    return np.asarray(blk, dtype=bool)
