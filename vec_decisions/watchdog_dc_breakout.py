"""Shared vectorized predicate for the LIVE entry MOMENTUM_WATCHDOG_DC_{TF}_BREAKOUT.

Lane w2-watchdog (2026-10-04). Faithful vec twin of the live multi-TF Donchian
force-open (REQ3) in ez_manage.py momentum_sma_watchdog_loop (~36471-36487),
emitting reason f"MOMENTUM_WATCHDOG_DC_{tf}_BREAKOUT_{side}_..." via
queue_trade_action OPEN. Replaces the killed inline raw twin in
v12_quick_engine simulate_one (WATCHDOG_DC_VEC_ENABLED, killed 2026-10-03:
raw prev-level DC-touch with no confirm fired 4866/5145 COTI_SHORT 365D
trades -> DD100; master stays default OFF, this module only changes what the
master computes when explicitly enabled).

LIVE source of truth (crypto only; tradier_manage has no watchdog path):
    for _tf in WATCHDOG_DC_TFS:                       # ["15m","1h","4h","D"], largest wins
        LONG:  (_lvl > 0 and _px >= _lvl) or dc_high_crossover_{tf}
        SHORT: (_lvl > 0 and _px <= _lvl) or dc_low_crossunder_{tf}
    where _lvl = ind dc_high/dc_low_{tf} (forming-bar level, NO wt filter).

VEC MAPPING (per bar i, causal):
    - price      = close[i] (live tick px; bar granularity is the floor).
    - level      = dc_high/dc_low_{tf}_prev[i] (completed-bar channel; live
                   compares against the forming-bar channel, which on a
                   completed bar INCLUDES bar i and would only fire when
                   close == period extreme -- _prev reproduces live intent:
                   "broke the channel established before this bar").
    - event term = dc_high_crossover_{tf}[i] / dc_low_crossunder_{tf}[i]
                   NPZ flags (live-verbatim OR term).
    - TF loop order 15m -> 1h -> 4h -> D with overwrite = largest-TF-wins,
      tag = winning TF (engine formats the WATCHDOG reason from it).

PARENT-ORDERED DEVIATION (documented KNOWN-GAP, not a live gate):
    WATCHDOG_DC_VEC_WT_CONFIRM_TF (default "15m", "OFF" disables):
    AND-gate requiring WaveTrend favor at the confirm TF
    (LONG wt1>wt2 / SHORT wt1<wt2). Live has NO wt gate on the DC path
    (proven: live ang/AVGOUSDT_SHORT fired DC_4h_BREAKOUT_SHORT at
    wt3m -12.7/-13.0, i.e. AGAINST-short, and the live comment says
    "NO wt filter"); the confirm exists to fix the killed twin's overfire.
    NPZ has no 3m arrays, so the confirm uses the 15m proxy until lane
    w2-data3m lands real 3m sidecars (no sidecars as of 2026-10-04:
    data/wiring/staged/w2-data3m/ absent). Zero-filled (missing) WT bars
    PASS (live non-zero-guard idiom) so absent arrays never fake a veto.

CALLER-CONCERN gates NOT in this predicate (live parity, enforced by the
simulate_one call site / existing engine blocks, unchanged by this lane):
    - crypto-only (live loop is ez_manage): call site gates MODE != tradier.
    - LONG MTF+GR gate (FORCE_OPEN_REQUIRE_MTF_GR): existing engine block.
    - flat-key-only + 60s cooldown: flat-only is implicit in the sim loop
      (opens fire only when flat); 60s cooldown is sub-bar, not modelled.
    - EMA50 15m entry filter (live, default ON): engine applies it to
      entry_sig only, NOT to _wd_open -- flagged for parent, not wired here
      (choke-gates lane overlap; see KNOWN_GAPS.md GAP-5).
    - TF-ladder USD sizing (25 x 1/4/8/16, cap 600): vec uses its own
      sizing; notionals are not modelled (same as the killed twin).

CONFIG READ:
    MOMENTUM_SMA_WATCHDOG_ENABLED (default True), WATCHDOG_DC_FORCE_OPEN_ENABLED
    (default True), WATCHDOG_DC_VEC_ENABLED (default False -- USER kill, kept),
    WATCHDOG_DC_TFS (default ["15m","1h","4h","D"]),
    WATCHDOG_DC_VEC_WT_CONFIRM_TF (default "15m", "OFF" = live-faithful raw).

RETURNS (mask, tag) or (None, None) when master-gated OFF (integrity: default
OFF -> None -> zero behavior change; §39.5).
"""
from __future__ import annotations

from typing import Any

import numpy as np


_TF_ORDER = ("15m", "1h", "4h", "D")


def dc_breakout_open_mask(npz, n: int, is_long: bool, cfg: Any, close, _safe):
    """(mask, tag): per-bar DC-breakout force-open + winning-TF tag.

    mask[i] True  => live would force-open a flat key at bar i (REQ3).
    tag[i]        => winning TF ("15m"/"1h"/"4h"/"D", "" where mask False).
    """
    if not bool(getattr(cfg, "MOMENTUM_SMA_WATCHDOG_ENABLED", True)):
        return None, None
    if not bool(getattr(cfg, "WATCHDOG_DC_FORCE_OPEN_ENABLED", True)):
        return None, None
    if not bool(getattr(cfg, "WATCHDOG_DC_VEC_ENABLED", False)):
        return None, None
    try:
        _tfs = list(getattr(cfg, "WATCHDOG_DC_TFS", ["15m", "1h", "4h", "D"]))
    except Exception:
        _tfs = ["15m", "1h", "4h", "D"]

    px = np.asarray(close, dtype=float)
    if px.shape[0] != n:
        px = np.resize(px, n)

    mask = np.zeros(n, dtype=bool)
    tag = np.full(n, "", dtype=object)
    for _tf in _TF_ORDER:  # live loop order; later (larger) TF overwrites
        if _tf not in _tfs:
            continue
        if is_long:
            _lvl = np.asarray(_safe(npz, "dc_high_%s_prev" % _tf, n, 0.0), dtype=float)
            _hit = (_lvl > 0) & (px >= _lvl)
            try:
                _xo = np.asarray(_safe(npz, "dc_high_crossover_%s" % _tf, n, 0.0), dtype=float) != 0
                _hit = _hit | _xo
            except Exception:
                pass
        else:
            _lvl = np.asarray(_safe(npz, "dc_low_%s_prev" % _tf, n, 0.0), dtype=float)
            _hit = (_lvl > 0) & (px <= _lvl)
            try:
                _xu = np.asarray(_safe(npz, "dc_low_crossunder_%s" % _tf, n, 0.0), dtype=float) != 0
                _hit = _hit | _xu
            except Exception:
                pass
        mask = mask | np.asarray(_hit, dtype=bool)
        tag = np.where(np.asarray(_hit, dtype=bool), _tf, tag)

    # Parent-ordered 15m-proxy WT favor confirm (NOT a live gate -- KNOWN-GAP 1).
    _ctf = str(getattr(cfg, "WATCHDOG_DC_VEC_WT_CONFIRM_TF", "15m") or "15m").strip()
    if _ctf.upper() != "OFF" and bool(np.any(mask)):
        try:
            _w1 = np.asarray(_safe(npz, "wt1_%s" % _ctf, n, 0.0), dtype=float)
            _w2 = np.asarray(_safe(npz, "wt2_%s" % _ctf, n, 0.0), dtype=float)
            _fav = (_w1 > _w2) if is_long else (_w1 < _w2)
            _missing = (_w1 == 0) & (_w2 == 0)  # absent arrays pass (non-zero-guard idiom)
            _ok = np.asarray(_fav | _missing, dtype=bool)
            mask = mask & _ok
            tag = np.where(mask, tag, "")
        except Exception:
            pass
    return mask, tag
