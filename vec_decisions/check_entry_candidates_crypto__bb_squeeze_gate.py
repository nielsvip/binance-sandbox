"""check_entry_candidates_crypto__bb_squeeze_gate.py

SHARED scalar+vectorized predicate for the BB_SQUEEZE_BREAKOUT confirmation GATE in
check_entry_candidates_for_account (ez_positions_quick.py:15589-15609).

The detector detect_bb_squeeze_breakout (11537-11578) holds a rolling 100-bar
width_history deque + a squeeze state machine, so the DETECTOR itself is NOT a pure
per-bar predicate (rolling-buffer manual class — left to its own future extraction
or precompute). What IS pure is the CONFIRMATION GATE that runs once the detector
emits a 'BUY'/'SELL' signal: an alignment floor and a stoch_k_3m not-overbought /
not-oversold check. That gate is vectorized here, with the detector signal passed
in as input (exactly the seam pattern used by other modules that isolate a
non-pure dependency). The per-symbol cooldown is a state seam handled by the caller.

FAITHFUL EXTRACTION of ez_positions_quick.py:15595-15607:

    if _bbs_signal:                       # detector output 'BUY'/'SELL'  (INPUT here)
        min_align = BB_SQUEEZE_MIN_ALIGNMENT (10)
        alignment = ind.get('alignment', 0)
        k3m       = ind.get('stoch_k_3m', 50)
        stoch_ok  = (is_long and k3m<75) or (short and k3m>25)
        if alignment >= min_align and stoch_ok:
            should_trade = True; score = max(score, 18)

PURITY: pure per-bar predicate on NPZ fields (alignment, stoch_k_3m) given the
detector's discrete signal. No runtime feed. Mirrors strategy_enhancements.py
_pyramid_fires (one shared core for scalar + vec).
"""
from collections import deque
from typing import Tuple
import numpy as np

_MIN_ALIGNMENT = 10.0
_K3M_LONG_MAX = 75.0
_K3M_SHORT_MIN = 25.0


def _bb_squeeze_stoch_ok(k3m, is_long):
    """PURE: not-overbought (LONG) / not-oversold (SHORT). Mirrors 15599."""
    if is_long:
        return k3m < _K3M_LONG_MAX
    return k3m > _K3M_SHORT_MIN


def _bb_squeeze_gate_fires(signal_fired, alignment, k3m, is_long,
                           min_alignment=_MIN_ALIGNMENT):
    """Full gate = detector signal AND alignment floor AND stoch_ok.
    Mirrors ez_positions_quick.py:15595-15600. signal_fired = bool (detector emitted
    a 'BUY'/'SELL' for this side)."""
    if not signal_fired:
        return False
    return (alignment >= min_alignment) and _bb_squeeze_stoch_ok(k3m, is_long)


def check_bb_squeeze_gate(config, indicators: dict, is_long: bool,
                          signal: str = None) -> Tuple[bool, float, str]:
    """LIVE/scalar path. `signal` is the detect_bb_squeeze_breakout output
    ('BUY'/'SELL'/None). Returns (fires, score_floor, reason). score_floor mirrors
    the live `score = max(score, 18)`."""
    def g(k, d):
        v = (indicators or {}).get(k, d)
        try:
            return float(v) if v is not None else d
        except (TypeError, ValueError):
            return d
    # 2026-10-06 NO-3M PARITY (NOTE_3M_REENABLE): k leg reads 15m while the switch is off (== live).
    _no3m = not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED", False))
    alignment = g("alignment", 0.0)
    k3m = g("stoch_k_15m", 50.0) if _no3m else g("stoch_k_3m", 50.0)
    min_align = float(getattr(config, "BB_SQUEEZE_MIN_ALIGNMENT", _MIN_ALIGNMENT))
    signal_fired = signal in ("BUY", "SELL")
    if not _bb_squeeze_gate_fires(signal_fired, alignment, k3m, is_long, min_align):
        return False, 0.0, ""
    reason = f"BB_SQUEEZE_BREAKOUT_{signal}_align={alignment:.0f}_k3={k3m:.0f}"
    return True, 18.0, reason


def detect_bb_squeeze_breakout_vec(bb_upper, bb_lower, bb_pctb, is_long, n,
                                   width_percentile=0.25):
    """FAITHFUL sequential port of the live stateful detector
    ez_positions_quick.detect_bb_squeeze_breakout (11812-11853): rolling 100-bar
    bb_width history (bb_width=(upper-lower)/lower*100), squeeze entered when width
    <= percentile threshold, breakout BUY/SELL emitted when width>thr*1.5 and
    bb_pct_b_1h>1.0 (long) / <0.0 (short). Returns a per-bar bool of same-side
    detector fires. State machine is inherently sequential, so this is a bar loop
    (n~2881 crypto / ~3200 stock -> sub-ms). NO synthetic injection."""
    up = np.asarray(bb_upper, dtype=float)
    lo = np.asarray(bb_lower, dtype=float)
    pb = np.asarray(bb_pctb, dtype=float)
    fired = np.zeros(int(n), dtype=bool)
    hist = deque(maxlen=100)
    in_squeeze = False
    squeeze_bars = 0
    for i in range(int(n)):
        u = up[i] if i < len(up) else 0.0
        l = lo[i] if i < len(lo) else 0.0
        if not (l > 0 and u > 0):
            continue  # live returns None BEFORE touching width_history
        width = (u - l) / l * 100.0
        hist.append(width)
        if len(hist) < 20:
            continue
        sw = sorted(hist)
        idx = max(0, int(len(sw) * width_percentile) - 1)
        thr = sw[idx]
        if not in_squeeze:
            if width <= thr:
                in_squeeze = True
                squeeze_bars = 1
            continue
        squeeze_bars += 1
        if width > thr * 1.5:
            pctb = pb[i] if i < len(pb) else 0.5
            if pctb > 1.0 and is_long:
                in_squeeze = False
                squeeze_bars = 0
                fired[i] = True
                continue
            if pctb < 0.0 and not is_long:
                in_squeeze = False
                squeeze_bars = 0
                fired[i] = True
                continue
            in_squeeze = False
            squeeze_bars = 0
        if squeeze_bars > 800:
            in_squeeze = False
            squeeze_bars = 0
    return fired


def compute_alignment_vec(npz, n, is_long):
    """Shared alignment score — delegates to vec_decisions.alignment (same formula live uses)."""
    from vec_decisions.alignment import compute_alignment_vec as _shared
    return _shared(npz, n, is_long)


_BB_MEMO: dict = {}


def bb_squeeze_entry_mask_vec(npz, n, config, is_long):
    """Full live BB squeeze breakout ENTRY mask = faithful TF-parametric detector AND
    confirmation gate (alignment>=BB_SQUEEZE_MIN_ALIGNMENT AND stoch_ok on k_3m), mirroring
    ez_positions_quick.py 16141-16155. TF comes from BB_SQUEEZE_ENTRY_TF (default 1h) so the
    per-TF sweep switches are distinct. `alignment` is computed by compute_alignment_vec from
    real NPZ fields (user-approved wiring); k_3m read from NPZ (default 50)."""
    def _f(key, default):
        v = npz.get(key) if hasattr(npz, "get") else None
        if v is None:
            return np.full(int(n), float(default), dtype=float)
        a = np.asarray(v, dtype=float)
        if len(a) != int(n):
            a = (a[:int(n)] if len(a) > int(n)
                 else np.concatenate([a, np.full(int(n) - len(a), float(default))]))
        return a
    # 2026-10-06 full-parity: BB_SQUEEZE_ENABLED ANDed (live honors both switches).
    _en = config.get("BB_SQUEEZE_ENABLED", True) if isinstance(config, dict) else getattr(config, "BB_SQUEEZE_ENABLED", True)
    if not bool(_en):
        return np.zeros(int(n), dtype=bool)
    tf = str(getattr(config, "BB_SQUEEZE_ENTRY_TF", "1h") or "1h").lower()
    if tf in ("off", ""):
        return np.zeros(int(n), dtype=bool)
    up_k = f"bb_upper_{tf}" if hasattr(npz, "get") and npz.get(f"bb_upper_{tf}") is not None else "bb_upper_1h"
    lo_k = f"bb_lower_{tf}" if hasattr(npz, "get") and npz.get(f"bb_lower_{tf}") is not None else "bb_lower_1h"
    pb_k = f"bb_pct_b_{tf}" if hasattr(npz, "get") and npz.get(f"bb_pct_b_{tf}") is not None else "bb_pct_b_1h"
    width_pct = float(getattr(config, "BB_SQUEEZE_WIDTH_PERCENTILE", 0.25) or 0.25)
    # detector + alignment depend only on the (immutable, in-RAM) NPZ slice and these params — not on the config
    # under test — so memoize: the sequential detector was ~50% of every v12 eval in the v15 sweep.
    # keyed on the ARRAY objects, not the dict: evaluate_prepared hands simulate_one a fresh dict(npz) copy per eval,
    # so id(npz) never repeated and the memo never hit. The arrays themselves are shared; hit[0] pins them (no id reuse).
    # 2026-10-06 NO-3M PARITY (NOTE_3M_REENABLE): k leg reads 15m while the switch is off (== live).
    _no3m = not bool(getattr(config, "USE_1M_3M_SIGNALS_ENABLED", False)) if not isinstance(config, dict) else not bool(config.get("USE_1M_3M_SIGNALS_ENABLED", False))
    _kk = "k_15m" if _no3m else "k_3m"
    src = tuple(npz.get(k) for k in (up_k, lo_k, pb_k, "close", _kk)) if hasattr(npz, "get") else (npz,)
    key = (tuple(id(a) for a in src), int(n), bool(is_long), up_k, lo_k, pb_k, width_pct, _kk)
    hit = _BB_MEMO.get(key)
    if hit is None or any(a is not b for a, b in zip(hit[0], src)):
        fired = detect_bb_squeeze_breakout_vec(
            _f(up_k, 0.0), _f(lo_k, 0.0), _f(pb_k, 0.5), is_long, int(n), width_pct)
        hit = (src, fired, compute_alignment_vec(npz, n, is_long), _f(_kk, 50.0))
        if len(_BB_MEMO) > 64:
            _BB_MEMO.clear()
        _BB_MEMO[key] = hit
    _, fired, alignment, k3m = hit
    return check_bb_squeeze_gate_vec(config, fired.copy(), alignment.copy(), k3m.copy(), is_long)


def check_bb_squeeze_gate_vec(config, signal_fired_arr, alignment_arr, k3m_arr,
                              is_long):
    """VECTORIZED per-bar gate mask. SAME thresholds + SAME math as scalar.
    signal_fired_arr = per-bar bool of whether the detector emitted a same-side
    signal (the rolling detector is supplied by the engine/caller)."""
    sig = np.asarray(signal_fired_arr, dtype=bool)
    al = np.asarray(alignment_arr, dtype=float)
    k3 = np.asarray(k3m_arr, dtype=float)
    min_align = float(getattr(config, "BB_SQUEEZE_MIN_ALIGNMENT", _MIN_ALIGNMENT))
    if is_long:
        stoch_ok = k3 < _K3M_LONG_MAX
    else:
        stoch_ok = k3 > _K3M_SHORT_MIN
    return sig & (al >= min_align) & stoch_ok
