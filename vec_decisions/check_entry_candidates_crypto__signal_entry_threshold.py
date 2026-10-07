"""check_entry_candidates_crypto__signal_entry_threshold.py

SHARED scalar+vectorized predicate for the SIGNAL-BASED ENTRY threshold gate in
check_entry_candidates_for_account (ez_positions_quick.py:15462-15480).

This is the primary score-vs-threshold admission gate that runs when no
DC-breakout / reentry / delta path has already set should_trade. It opens when the
rate() score clears a per-symbol threshold (default 4) OR the recommendation string
contains BUY/SELL, AND the recommendation does NOT contain WAIT or HEDGE. The inner
STRICT_STOCH_GATE (k_1m vs d_1m) is already its own module
(check_entry_candidates_crypto__strict_stoch_gate.py); this core is the OUTER
score/rec admission predicate.

FAITHFUL EXTRACTION of ez_positions_quick.py:15463-15480:

    _sig_score_min = 4
    if PER_SYM_CONFIG_ENABLED:
        if 'ENTRY_SCORE_THRESHOLD' in per_sym_overrides:
            est = float(...)
            if est > _sig_score_min: _sig_score_min = est
    rec_str = str(rec) if rec is not None else ""
    if (score >= _sig_score_min or "BUY" in rec_str or "SELL" in rec_str) \
       and ('WAIT' not in rec_str and 'HEDGE' not in rec_str):
        # ... STRICT_STOCH_GATE inner check ...
        should_trade = True

PURITY: pure per-bar predicate on the per-bar score scalar (rate() output the
engine already has), the rec string, and the per-symbol threshold (an effective
threshold the engine resolves from per_sym overrides — passed in as
`sig_score_min`, exactly like the strict_stoch module isolates its inputs). No
runtime feed. Mirrors strategy_enhancements.py _pyramid_fires.
"""
from typing import Tuple
import numpy as np

_DEFAULT_SCORE_MIN = 4.0


def _effective_score_min(default_min, per_sym_threshold):
    """Replica of 15464-15470: per-sym threshold overrides only if STRICTLY greater."""
    sm = default_min
    if per_sym_threshold is not None and float(per_sym_threshold) > sm:
        sm = float(per_sym_threshold)
    return sm


def _signal_entry_fires(score, rec_str, sig_score_min):
    """PURE predicate. Mirrors ez_positions_quick.py:15471-15472. rec_str is the
    raw recommendation string (already coerced; None -> '')."""
    r = rec_str or ""
    if ("WAIT" in r) or ("HEDGE" in r):
        return False
    return (score >= sig_score_min) or ("BUY" in r) or ("SELL" in r)


def check_signal_entry_threshold(config, score, rec, per_sym_threshold=None,
                                 default_min=_DEFAULT_SCORE_MIN) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (passes_gate, reason). per_sym_threshold is the
    resolved per-symbol ENTRY_SCORE_THRESHOLD (None when no override)."""
    rec_str = str(rec) if rec is not None else ""
    sig_min = _effective_score_min(default_min, per_sym_threshold)
    if not _signal_entry_fires(float(score), rec_str, sig_min):
        return False, ""
    return True, f"SIGNAL_ENTRY_score={float(score):.1f}>=thr={sig_min:.1f}_rec={rec_str}"


def check_signal_entry_threshold_vec(config, score_arr, rec_arr, sig_score_min_arr,
                                     default_min=_DEFAULT_SCORE_MIN):
    """VECTORIZED per-bar gate mask. SAME logic as scalar. rec_arr = per-bar rec
    strings (object array); sig_score_min_arr = per-bar effective thresholds
    (engine resolves per-sym overrides; pass default_min-filled when none)."""
    sc = np.asarray(score_arr, dtype=float)
    smin = np.asarray(sig_score_min_arr, dtype=float)
    recs = np.asarray(rec_arr, dtype=object)
    has_wait = np.array(["WAIT" in (str(r) if r is not None else "") for r in recs], dtype=bool)
    has_hedge = np.array(["HEDGE" in (str(r) if r is not None else "") for r in recs], dtype=bool)
    has_buy = np.array(["BUY" in (str(r) if r is not None else "") for r in recs], dtype=bool)
    has_sell = np.array(["SELL" in (str(r) if r is not None else "") for r in recs], dtype=bool)
    blocked = has_wait | has_hedge
    passes = (sc >= smin) | has_buy | has_sell
    return passes & (~blocked)
