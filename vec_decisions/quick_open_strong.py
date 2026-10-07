"""SHARED scalar+vectorized predicate for the LIVE decision QUICK_OPEN_STRONG.

LIVE SOURCE OF TRUTH: ez_positions_quick.py
    PositionFieldCalculator.calculate_trading_signals() — lines 9534-9545.

The live decision builds a `composite_signal_score` starting at 50 and applies
three additive adjustments, clamps to [0, 100], and emits action='STRONG_BUY'
(LONG) / 'STRONG_SELL' (SHORT) when score >= 80. ez_manage.py then decorates the
emitted action with the QUICK_OPEN_ prefix at the augment/open site (the
"AUGMENT" 2413-event path).

FAITHFUL EXTRACTION of ez_positions_quick.py:9534-9545:

    composite_score = 50
    if 'momentum' in signals:            composite_score += momentum * 0.5
    if 'risk_level' in signals:          composite_score += (10 - risk_level) * 2
    if high_volatility_warning:          composite_score -= 10
    composite_score = clamp(composite_score, 0, 100)
    STRONG  iff composite_score >= 80

Where the upstream inputs (same function, lines 9491-9520, 9503-9508) are:
  momentum               = mean(gain_list[-3:])          (per-position runtime state)
  risk_level             from drawdown = max_gain - current_gain:
        drawdown>2 -> 5 ; drawdown>5 -> 8 ; drawdown>10 -> 10 ; else 1
        (NOTE: live default base is risk_level=1 and the block only runs when
         positionAmt>0 and avg_entry_price>0; if absent the risk term is omitted.)
  high_volatility_warning = (volatility_1h_% / avg_volatility_1h_%) > 1.5

The PURE per-bar predicate below replicates the score math EXACTLY. It takes the
already-derived `momentum`, `risk_level`, `high_vol` inputs plus presence flags
(have_momentum / have_risk) that mirror the `'x' in signals` guards in live code,
so the scalar and vectorized paths CANNOT drift.

NPZ / FIELD CAVEAT (read npz_fields_needed below): the live inputs `momentum`
(rolling gain_list) and `risk_level` (max_gain/drawdown tracking) are
PER-POSITION RUNTIME STATE, not stored in the backtest NPZ. The backtest engine
must synthesize them from its own simulated position state (gain history,
max_gain) exactly like the live PositionFieldCalculator does. `volatility_1h_%`
and `avg_volatility_1h_%` are also runtime rolling values; a backtest must
recompute the same ratio. This module is the SHARED math; the CALLER supplies the
inputs from whichever source (live market_data vs simulated state), identical to
the pyramid template (strategy_enhancements.py:_pyramid_fires).
"""
from typing import Tuple
import numpy as np


# ── SHARED per-bar QUICK_OPEN_STRONG predicate (single source of truth) ──────
# Live scalar (check_quick_open_strong) AND vectorized backtest
# (check_quick_open_strong_vec) BOTH derive their fire decision from the math
# below, so the two paths cannot drift. Pure: no config, no state.
def _composite_score(momentum: float, risk_level: float, high_vol: bool,
                     have_momentum: bool, have_risk: bool) -> float:
    """Exact replica of ez_positions_quick.py:9534-9542 composite_signal_score."""
    score = 50.0
    if have_momentum:
        score += momentum * 0.5
    if have_risk:
        score += (10.0 - risk_level) * 2.0
    if high_vol:
        score -= 10.0
    if score < 0.0:
        score = 0.0
    elif score > 100.0:
        score = 100.0
    return score


def _quick_open_strong_fires(momentum: float, risk_level: float, high_vol: bool,
                             have_momentum: bool, have_risk: bool,
                             strong_threshold: float) -> bool:
    """Pure per-bar fire test: composite_signal_score >= strong_threshold (live 80).
    Side-agnostic — live emits STRONG_BUY for LONG and STRONG_SELL for SHORT off
    the SAME score; the BUY/SELL label is applied by the caller from is_long."""
    return _composite_score(momentum, risk_level, high_vol, have_momentum, have_risk) >= strong_threshold


def _risk_level_from_drawdown(drawdown: float) -> float:
    """Exact replica of ez_positions_quick.py:9516-9520 risk_level ladder."""
    risk_level = 1.0
    if drawdown > 2.0:
        risk_level = 5.0
    if drawdown > 5.0:
        risk_level = 8.0
    if drawdown > 10.0:
        risk_level = 10.0
    return risk_level


def _strong_threshold(config) -> float:
    # Live hardcodes 80 (ez_positions_quick.py:9544). Exposed as a knob but
    # defaults to the exact live value so behavior is identical out of the box.
    return float(getattr(config, "QUICK_OPEN_STRONG_SCORE_THRESHOLD", 80.0))


def check_quick_open_strong(config, indicators: dict, is_long: bool,
                            momentum: float = None, risk_level: float = None,
                            high_vol: bool = None) -> Tuple[bool, str]:
    """LIVE/scalar path. Returns (fires, reason).

    Mirrors ez_positions_quick.py:9534-9545. Inputs (momentum / risk_level /
    high_vol) may be passed explicitly (preferred — caller derives them exactly
    as the live PositionFieldCalculator does) or read from `indicators` for
    convenience. Presence flags reproduce the live `'x' in signals` guards: if
    momentum/risk_level is None and absent from `indicators`, that additive term
    is OMITTED, exactly like live."""
    have_momentum = momentum is not None or ("momentum" in (indicators or {}))
    have_risk = risk_level is not None or ("risk_level" in (indicators or {}))
    if momentum is None:
        momentum = float((indicators or {}).get("momentum", 0.0) or 0.0)
    if risk_level is None:
        risk_level = float((indicators or {}).get("risk_level", 1.0) or 1.0)
    if high_vol is None:
        high_vol = bool((indicators or {}).get("high_volatility_warning", False))
    thr = _strong_threshold(config)
    if not _quick_open_strong_fires(float(momentum), float(risk_level), bool(high_vol),
                                    have_momentum, have_risk, thr):
        return False, ""
    reason = "QUICK_OPEN_STRONG_BUY" if is_long else "QUICK_OPEN_STRONG_SELL"
    score = _composite_score(float(momentum), float(risk_level), bool(high_vol), have_momentum, have_risk)
    return True, f"{reason} score={score:.1f}>=80"


def check_quick_open_strong_vec(config, momentum_arr, risk_level_arr, high_vol_arr,
                                have_momentum: bool = True, have_risk: bool = True):
    """VECTORIZED per-bar fire mask — backtest path. SAME threshold + SAME score
    math as the live scalar check_quick_open_strong (vectorized via numpy).
    Returns a bool ndarray.

    Arrays are per-bar (synthesized by the backtest engine from simulated
    position state, NOT raw NPZ — see FIELD CAVEAT in module docstring):
      momentum_arr   : mean(gain_list[-3:]) per bar
      risk_level_arr : risk_level (1/5/8/10) per bar — use _risk_level_from_drawdown
      high_vol_arr   : bool, (volatility_1h_% / avg_volatility_1h_%) > 1.5 per bar
    have_momentum / have_risk reproduce the live presence guards (scalar booleans;
    in backtest both are typically True once a position has gain history)."""
    m = np.asarray(momentum_arr, dtype=float)
    r = np.asarray(risk_level_arr, dtype=float)
    hv = np.asarray(high_vol_arr, dtype=bool)
    n = m.shape[0]
    score = np.full(n, 50.0, dtype=float)
    if have_momentum:
        score = score + m * 0.5
    if have_risk:
        score = score + (10.0 - r) * 2.0
    score = score - np.where(hv, 10.0, 0.0)
    score = np.clip(score, 0.0, 100.0)
    thr = _strong_threshold(config)
    return score >= thr
