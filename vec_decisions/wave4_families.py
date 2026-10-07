"""Wave 4 — OI_CONFIRM entry gate, MI_EXIT voter, EMA_BLANKET entry gate (2026-09-28).

LIVE SOURCES (mirrored exactly):
- OI_CONFIRM (ez_positions_quick.py:12678-12716): when |oi_change_1h_pct| >=
  OI_CONFIRM_MIN_CHANGE_PCT AND |price_chg_1h_pct| >= OI_CONFIRM_MIN_PRICE_PCT:
  LONG blocked on (px_up & !oi_up) squeeze-rally and (!px_up & oi_up) new-shorts;
  SHORT blocked on (!px_up & !oi_up) long-liquidation and (px_up & oi_up) new-longs.
  Crypto data (oi_* arrays); stocks NPZs lack OI -> fail-open (gate passes), same as
  live where the gate lives in the crypto manager only.
- MI_EXIT momentum-interception voter (ez_positions_quick.py:3723-3760, tradier VARFIX
  twin tradier_manage.py:19350-19372): sub-signals per side — STRUCT (peak LH 1h/4h long,
  trough HL short), EXHAUST (velocity/accel reconstruction per the quick_reduce_strong
  faithfulness note: NPZ momentum_state int8 is NOT the live EXHAUST string), DIV
  (wt_divergence int8 -1 BEAR/+1 BULL), VEL_DECEL (>=2 of scalp/1h/4h velocity
  thresholds -1.0/-1.0/-0.5 side-adjusted), WAVE (wt_wave_phase_1h CONTRACTING — NPZ
  int8: check codes; 'CONTRACTING' maps to the negative/contracting code). Fire when
  votes >= MI_TF_AGREE_MIN and gain >= MI_MIN_GAIN_EXIT (loop applies the gain gate).
- EMA_BLANKET (census class NEITHER — new BOTH sides, live diff in
  PROPOSAL_FILTER_WAVE1_20260928.md wave-4 appendix): entry requires
  ema_9_above_21_{tf} to agree with the side on >= EMA_BLANKET_FILTER_MIN_TFS of
  (15m, 1h, 4h, D). Default ENABLED False = neutral.
"""
from __future__ import annotations

import numpy as np

_MI_TFS = ("15m", "1h", "4h", "D")


def oi_confirm_entry_gate(npz, n, is_long, config, close, safe):
    if not bool(getattr(config, "OI_CONFIRM_ENABLED", False)):
        return None
    # b7: live nests the OI gate INSIDE `if HTF_DIRECTION_GATE_ENABLED` (ez_positions_quick 12658-12700) -> dead unless that master is on
    if not bool(getattr(config, "HTF_DIRECTION_GATE_ENABLED", False)):
        return None
    oi = safe(npz, "oi_change_1h_pct", n, np.nan)
    p1h = safe(npz, "close_1h_prev", n, 0.0)
    if not np.any(np.isfinite(oi)) or not np.any(p1h > 0):
        return None  # no OI data on this venue — fail-open, matches live (crypto-only gate)
    min_oi = float(getattr(config, "OI_CONFIRM_MIN_CHANGE_PCT", 0.5))
    min_px = float(getattr(config, "OI_CONFIRM_MIN_PRICE_PCT", 0.3))
    with np.errstate(divide="ignore", invalid="ignore"):
        pxc = np.where(p1h > 0, (close - p1h) / np.where(p1h > 0, p1h, 1.0) * 100.0, np.nan)
    act = np.isfinite(oi) & np.isfinite(pxc) & (np.abs(oi) >= min_oi) & (np.abs(pxc) >= min_px)
    px_up = pxc > 0
    oi_up = oi > 0
    if is_long:
        blocked = (px_up & ~oi_up) | (~px_up & oi_up)
    else:
        blocked = (~px_up & ~oi_up) | (px_up & oi_up)
    return ~(act & blocked)


def _exhaust(npz, n, tf, is_long, safe):
    v = safe(npz, f"wt_velocity_{tf}", n, 0.0)
    a = safe(npz, f"wt_acceleration_{tf}", n, 0.0)
    if is_long:  # EXHAUST_UP = rising but decelerating (ez_indicators.py:1638 convention)
        return (v > 0) & (a <= 0)
    return (~(v > 0)) & (a >= 0)  # EXHAUST_DOWN (else-branch)


def mi_exit_signal(npz, n, is_long, config, safe):
    """Bool[n] MI voter fire mask (>= MI_TF_AGREE_MIN votes); gain gate applied in loop."""
    # [PAR/002] stocks live reads MI_EXIT_ENABLED_TRADIER (config_tradier False); the crypto key MI_EXIT_ENABLED (ez_positions_quick) must not drive stock exits
    _mi_key = "MI_EXIT_ENABLED_TRADIER" if str(getattr(config, "MODE", "crypto")) == "tradier" else "MI_EXIT_ENABLED"
    if not bool(getattr(config, _mi_key, False)):
        return None
    votes = np.zeros(n, dtype=np.int8)
    if bool(getattr(config, "MI_STRUCT_EXIT_ENABLED", True)):
        for tf in ("1h", "4h"):
            if is_long:
                s = safe(npz, f"wt_peak_structure_{tf}", n, 0)
                votes += (s == -1).astype(np.int8)  # LH
            else:
                s = safe(npz, f"wt_trough_structure_{tf}", n, 0)
                votes += (s == 1).astype(np.int8)  # HL
    if bool(getattr(config, "MI_EXHAUST_EXIT_ENABLED", True)):
        for tf in ("1h", "4h"):
            votes += _exhaust(npz, n, tf, is_long, safe).astype(np.int8)
    if bool(getattr(config, "MI_DIV_EXIT_ENABLED", True)):
        want = -1 if is_long else 1  # BEAR / BULL
        for tf in ("1h", "4h"):
            d = safe(npz, f"wt_divergence_{tf}", n, 0)
            votes += (d == want).astype(np.int8)
    if bool(getattr(config, "MI_VELOCITY_EXIT_ENABLED", True)):
        vs = safe(npz, "wt_velocity_15m", n, 0.0)  # scalp proxy: lowest common TF in NPZ
        v1 = safe(npz, "wt_velocity_1h", n, 0.0)
        v4 = safe(npz, "wt_velocity_4h", n, 0.0)
        if is_long:
            ag = (vs < -1.0).astype(np.int8) + (v1 < -1.0).astype(np.int8) + (v4 < -0.5).astype(np.int8)
        else:
            ag = (vs > 1.0).astype(np.int8) + (v1 > 1.0).astype(np.int8) + (v4 > 0.5).astype(np.int8)
        votes += (ag >= 2).astype(np.int8)
    if bool(getattr(config, "MI_WAVE_EXIT_ENABLED", True)):
        w = safe(npz, "wt_wave_phase_1h", n, 0)
        votes += (w < 0).astype(np.int8)  # CONTRACTING encoded negative (precompute int8)
    mi_min = int(float(getattr(config, "MI_TF_AGREE_MIN", 3) or 3))
    return votes >= mi_min


def ema_blanket_entry_gate(npz, n, is_long, config, close, safe):
    if not bool(getattr(config, "EMA_BLANKET_FILTER_ENABLED", False)):
        return None
    min_tfs = int(float(getattr(config, "EMA_BLANKET_FILTER_MIN_TFS", 2) or 2))
    agree = np.zeros(n, dtype=np.int8)
    found = 0
    for tf in _MI_TFS:
        key = f"ema_9_above_21_{tf}"
        if key not in npz:
            continue
        found += 1
        a = safe(npz, key, n, 0)
        above = a.astype(bool) if a.dtype == bool else (a > 0.5)
        agree += (above if is_long else ~above).astype(np.int8)
    if found == 0:
        return None
    return agree >= min_tfs


def ema_blanket_live_pass(indicators, is_long, config):
    """Scalar LIVE twin of ema_blanket_entry_gate (same TFs, same MIN_TFS, same missing-TF rule) for
    tradier_manage.queue_trade_action / ez_manage.execute_now OPEN gates. Returns (passed, agree, found)."""
    if not bool(getattr(config, "EMA_BLANKET_FILTER_ENABLED", False)):
        return True, 0, 0
    min_tfs = int(float(getattr(config, "EMA_BLANKET_FILTER_MIN_TFS", 2) or 2))
    agree = found = 0
    for tf in _MI_TFS:
        raw = (indicators or {}).get(f"ema_9_above_21_{tf}")
        if raw is None:
            continue
        found += 1
        above = bool(raw) if isinstance(raw, bool) else float(raw) > 0.5
        agree += int(above if is_long else not above)
    if found == 0:
        return True, 0, 0
    return agree >= min_tfs, agree, found


def htf_direction_gate(npz, n, is_long, config, close, safe):
    """Live-parity HTF direction gate (ez_positions_quick.py:12092-12140, applied at the
    open gate 12658 with default ENABLED=True): counts wt-direction agreement on D/4h/1h
    (+ price vs sma_200_D when HTF_GATE_SIGNALS_SMA200D), requires
    HTF_GATE_MIN_CONFIRMATIONS and the D signal when HTF_GATE_D_MANDATORY.
    NOTE: live exempts specific entry families (SCALP_V3, RZ bypass, hedges); the vec
    entry stream is family-agnostic so the gate applies to all vec entries — slight
    over-blocking vs live for those families, disclosed."""
    if not bool(getattr(config, "HTF_DIRECTION_GATE_ENABLED", True)):
        return None
    if not bool(getattr(config, "HTF_GATE_APPLY_TO_OPEN", True)):   # b7: live _should_gate for opens
        return None
    return htf_direction_pass_raw(npz, n, is_long, config, close, safe)


def htf_direction_pass_raw(npz, n, is_long, config, close, safe):
    """HTF direction core (no ENABLED/APPLY flags) — ez_positions_quick.check_htf_direction_gate."""
    sigs = []
    for tf in ("D", "4h", "1h"):
        w1 = safe(npz, f"wt1_{tf}", n, 0.0)
        w2 = safe(npz, f"wt2_{tf}", n, 0.0)
        ok = ((w1 > w2) if is_long else (w1 < w2)) & (w1 != 0) & (w2 != 0)
        sigs.append(ok)
    met = sigs[0].astype(np.int8) + sigs[1].astype(np.int8) + sigs[2].astype(np.int8)
    if bool(getattr(config, "HTF_GATE_SIGNALS_SMA200D", True)):
        sma = safe(npz, "sma_200_D", n, 0.0)
        sma_ok = ((close > sma) if is_long else (close < sma)) & (sma > 0)
        met = met + np.where(sma > 0, sma_ok.astype(np.int8), 0)
    min_conf = int(float(getattr(config, "HTF_GATE_MIN_CONFIRMATIONS", 3) or 3))
    allow = met >= min_conf
    if bool(getattr(config, "HTF_GATE_D_MANDATORY", True)):
        allow = allow & sigs[0]
    return allow


def bb_squeeze_entry_gate(npz, n, is_long, config, close, safe):
    """REAL BB/TTM squeeze entry gate (2026-09-29, replaces the dead-alignment path —
    [[bb-squeeze-alignment-dead]]): NPZ carries genuine squeeze arrays from precompute
    (squeeze_on_{tf}, squeeze_fire_{tf}; 15m/1h). When BB_SQUEEZE_ENTRY_ENABLED, entry is
    allowed only on squeeze-release bars (squeeze_fire — the TTM breakout trigger)."""
    if not bool(getattr(config, "BB_SQUEEZE_ENTRY_ENABLED", False)):
        return None
    for tf in ("15m", "1h"):
        key = f"squeeze_fire_{tf}"
        if key in npz:
            fire = safe(npz, key, n, 0)
            return fire > 0
    return None  # no squeeze arrays on this NPZ — fail-open


def bb_squeeze_exit_mask(npz, n, is_long, config, safe):
    """TTM compression exit: squeeze turning ON while in a trade = volatility died ->
    exit trigger (ORed into exit_sig). Default OFF; standard TTM semantics documented
    (no FILTERS_EXPLAINED row exists; arrays are real precompute outputs)."""
    if not bool(getattr(config, "BB_SQUEEZE_EXIT_ENABLED", False)):
        return None
    for tf in ("15m", "1h"):
        key = f"squeeze_on_{tf}"
        if key in npz:
            on = safe(npz, key, n, 0) > 0
            prev = np.roll(on, 1); prev[0] = on[0]
            return on & ~prev  # transition into squeeze
    return None
