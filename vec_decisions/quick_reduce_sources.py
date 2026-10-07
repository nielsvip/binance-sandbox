"""rate()-driven exit REDUCE sources (CRYPTO live: ez_positions_quick.AdvancedSignalRater.rate is_exit=True) — vec twins of
SIMPLE_TP_EXIT (ez_positions_quick.py:3686-3688, BACKTEST_CHANGE_139) and MACD_EXIT (4559-4567, BACKTEST_CHANGE_136). [N4]
LIVE: both return (100, 'REDUCE', reason) from the exit scorer; the caller (check_exit_candidates, 14893-14897) then SUPPRESSES any reduce whose reason carries no
sanctioned technical token when QUICK_REDUCE_TECHNICAL_ONLY is True (default True, USER MANDATE 2026-06-02: 'DISABLE the QUICK_REDUCE stochastic traps ... SIMPLE_TP ...').
=> these two switches ONLY act when QUICK_REDUCE_TECHNICAL_ONLY=False: that is their MASTER dependency (forced OFF in their tests: data/switch_dependencies.json 'peers_off').
A surviving reduce executes reduction_qty = positionAmt - pos_min_qty (ALL BUT DUST, ez_positions_quick.py:15001), not a fractional table; the sim keeps DUST_FRAC of the
position (min qty is ~0 on the sim's fractional crypto sizing: documented approximation).
Stocks (tradier_manage) have NO twin of either (rate() is the crypto scorer): crypto only."""
from __future__ import annotations

DUST_FRAC = 0.02


def _tech_only(cfg) -> bool:
    return bool(getattr(cfg, "QUICK_REDUCE_TECHNICAL_ONLY", True))


def simple_tp(cfg, gain_pct: float):
    if _tech_only(cfg) or not bool(getattr(cfg, "SIMPLE_TP_EXIT_ENABLED", False)):
        return False, ""
    tp = float(getattr(cfg, "SIMPLE_TP_PCT", 0.50))
    if gain_pct >= tp:
        return True, f"SIMPLE_TP_EXIT(gain={gain_pct:.2f}%>=tp={tp}%)_bc139"
    return False, ""


def macd_exit(cfg, is_long: bool, gain_pct: float, cross_under: bool, cross_over: bool):
    if _tech_only(cfg) or not bool(getattr(cfg, "MACD_EXIT_ENABLED", False)):
        return False, ""
    if gain_pct > float(getattr(cfg, "MACD_EXIT_MIN_GAIN", 0.3)):
        if is_long and cross_under:
            return True, f"MACD_EXIT_L(gain={gain_pct:.2f}%)_bc136"
        if (not is_long) and cross_over:
            return True, f"MACD_EXIT_S(gain={gain_pct:.2f}%)_bc136"
    return False, ""
