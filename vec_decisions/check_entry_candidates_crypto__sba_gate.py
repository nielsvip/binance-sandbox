"""check_entry_candidates_crypto__sba_gate.py

SHARED scalar+vectorized STATEFUL-SEAM predicate for Strategic Bounce Averaging
(SBA) admission in check_entry_candidates_for_account
(ez_positions_quick.py:15483-15510, BACKTEST_CHANGE_145).

SBA averages-down an UNDERWATER position at a confirmed bounce. The bounce-quality
score `_sba_bounce_score` delegates to `_market_quality_score` (a separate scorer
module — passed in here as input `sba_score`, like the BB detector seam). What this
core vectorizes is the SBA ADMISSION GATE: the loss-band window plus the per-position
add-count cap, per-position cooldown, global cooldown, concurrency cap, notional cap,
and the min-score gate. These are pure GIVEN per-position STATE ARRAYS the engine's
state-walk supplies (gain, add_count, last_sba_time, global_last_time, active_count,
pos_val) — exactly the stateful_seam contract.

FAITHFUL EXTRACTION of ez_positions_quick.py:15485-15510:

    if not should_trade and SBA_ENABLED and pos_amt>0:
        sba_min = SBA_MIN_LOSS_PCT (-2.0); sba_max = SBA_MAX_LOSS_PCT (-15.0)
        if gain <= sba_min and gain >= sba_max:                      # loss band
            if cnt < SBA_MAX_ADDS (2)
               and (now-last) > SBA_COOLDOWN_POSITION_S (3600)
               and (now-global_last) > SBA_COOLDOWN_GLOBAL_S (300)
               and active_count < SBA_MAX_CONCURRENT (3)
               and pos_val < start_size*SBA_MAX_TOTAL_MULT (2.5):    # notional cap
                if sba_score >= SBA_MIN_SCORE (4):
                    should_trade = True                              # FIRES

PURITY: gate is pure given the 6 state inputs + sba_score + start_size + now. No
runtime feed (the bounce SCORER is the only non-NPZ piece, isolated as `sba_score`).
Mirrors strategy_enhancements.py _pyramid_fires.
"""
from typing import Tuple
import numpy as np

_MIN_LOSS_PCT = -2.0
_MAX_LOSS_PCT = -15.0
_MAX_ADDS = 2
_COOLDOWN_POS_S = 3600.0
_COOLDOWN_GLOBAL_S = 300.0
_MAX_CONCURRENT = 3
_MAX_TOTAL_MULT = 2.5
_MIN_SCORE = 4.0


def _sba_gate_fires(gain, add_count, last_sba_time, global_last_time, active_count,
                    pos_val, sba_score, start_size, now,
                    min_loss=_MIN_LOSS_PCT, max_loss=_MAX_LOSS_PCT, max_adds=_MAX_ADDS,
                    cd_pos=_COOLDOWN_POS_S, cd_global=_COOLDOWN_GLOBAL_S,
                    max_concurrent=_MAX_CONCURRENT, max_total_mult=_MAX_TOTAL_MULT,
                    min_score=_MIN_SCORE):
    """PURE given per-position state. Mirrors ez_positions_quick.py:15487-15502.
    Returns bool (admission gate passes)."""
    if not (gain <= min_loss and gain >= max_loss):
        return False
    max_val = start_size * max_total_mult
    gates = (add_count < max_adds and (now - last_sba_time) > cd_pos
             and (now - global_last_time) > cd_global
             and active_count < max_concurrent and pos_val < max_val)
    if not gates:
        return False
    return sba_score >= min_score


def check_sba_gate(config, gain, add_count, last_sba_time, global_last_time,
                   active_count, pos_val, sba_score, start_size, now) -> Tuple[bool, str]:
    """LIVE/scalar path. State + sba_score supplied by the caller (sim/live position
    state + the bounce scorer). Returns (fires, reason)."""
    min_loss = float(getattr(config, "SBA_MIN_LOSS_PCT", _MIN_LOSS_PCT))
    max_loss = float(getattr(config, "SBA_MAX_LOSS_PCT", _MAX_LOSS_PCT))
    max_adds = int(getattr(config, "SBA_MAX_ADDS", _MAX_ADDS))
    cd_pos = float(getattr(config, "SBA_COOLDOWN_POSITION_S", _COOLDOWN_POS_S))
    cd_global = float(getattr(config, "SBA_COOLDOWN_GLOBAL_S", _COOLDOWN_GLOBAL_S))
    max_conc = int(getattr(config, "SBA_MAX_CONCURRENT", _MAX_CONCURRENT))
    max_mult = float(getattr(config, "SBA_MAX_TOTAL_MULT", _MAX_TOTAL_MULT))
    min_score = float(getattr(config, "SBA_MIN_SCORE", _MIN_SCORE))
    fires = _sba_gate_fires(float(gain), int(add_count), float(last_sba_time),
                            float(global_last_time), int(active_count), float(pos_val),
                            float(sba_score), float(start_size), float(now), min_loss,
                            max_loss, max_adds, cd_pos, cd_global, max_conc, max_mult,
                            min_score)
    if not fires:
        return False, ""
    return True, f"SBA_BOUNCE_s{float(sba_score):.1f}_g{float(gain):.1f}pct_cnt{int(add_count)+1}/{max_adds}"


def check_sba_gate_vec(config, gain_arr, add_count_arr, last_sba_time_arr,
                       global_last_time_arr, active_count_arr, pos_val_arr,
                       sba_score_arr, start_size, now):
    """VECTORIZED per-bar gate mask. SAME thresholds + SAME math as scalar. All six
    state arrays are per-position state the engine state-walk supplies; sba_score_arr
    is the bounce-scorer output per bar."""
    g = np.asarray(gain_arr, dtype=float)
    cnt = np.asarray(add_count_arr, dtype=float)
    last = np.asarray(last_sba_time_arr, dtype=float)
    glast = np.asarray(global_last_time_arr, dtype=float)
    active = np.asarray(active_count_arr, dtype=float)
    pval = np.asarray(pos_val_arr, dtype=float)
    sscore = np.asarray(sba_score_arr, dtype=float)
    min_loss = float(getattr(config, "SBA_MIN_LOSS_PCT", _MIN_LOSS_PCT))
    max_loss = float(getattr(config, "SBA_MAX_LOSS_PCT", _MAX_LOSS_PCT))
    max_adds = int(getattr(config, "SBA_MAX_ADDS", _MAX_ADDS))
    cd_pos = float(getattr(config, "SBA_COOLDOWN_POSITION_S", _COOLDOWN_POS_S))
    cd_global = float(getattr(config, "SBA_COOLDOWN_GLOBAL_S", _COOLDOWN_GLOBAL_S))
    max_conc = int(getattr(config, "SBA_MAX_CONCURRENT", _MAX_CONCURRENT))
    max_mult = float(getattr(config, "SBA_MAX_TOTAL_MULT", _MAX_TOTAL_MULT))
    min_score = float(getattr(config, "SBA_MIN_SCORE", _MIN_SCORE))
    max_val = start_size * max_mult
    in_band = (g <= min_loss) & (g >= max_loss)
    gates = ((cnt < max_adds) & ((now - last) > cd_pos) & ((now - glast) > cd_global)
             & (active < max_conc) & (pval < max_val))
    return in_band & gates & (sscore >= min_score)
