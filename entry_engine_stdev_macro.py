"""STDEV_MACRO entry engine — pure-function additive entry signal.

Wraps `stdev_macro.compute_stdev_macro_state` (scalar, live-friendly) +
`vec_paths/stdev_macro_vec.py` (precompute) into the entry_engine_* interface
used by tradier_manage.py / ez_manage.py LIVE_ENTRY_ENGINE pipeline.

Signature mirrors entry_engine_wt / entry_engine_dc / entry_engine_stoch:
    should_fire_stdev_macro_entry(symbol, indicators, side) -> (fire, reason, score)

Edge rationale (function audit 2026-05-18 06:00 UTC):

    STDEV_D200_HIGH + WT_D_BEAR composite: 68.46% WR, +41.86 bps fwd60.
    The strongest single signal in the codebase; until 2026-05-19 it was wired
    only into the backtest engine (vec_paths/stdev_macro_vec.py + stdev_macro.py
    `r4_exit`) — never an entry signal in live trading.

    LONG case: STRONG_BOT macro state (auto-tuned bb_pct_b_D < 0.03 AND
    bb_pct_b_4h < 0.10) + LTF wt_3m flipping bullish = bottom-pick mean
    reversion. Conservative — fires rarely.

    SHORT case: STRONG_TOP (bb_pct_b_D > 0.97 AND bb_pct_b_4h > 0.90) +
    wt_D bearish (wt1_D < wt2_D) = top-pick. This is the +41 bps signal.

Score band:
    1.0  STRONG_TOP / STRONG_BOT + LTF confirm        -> FIRE
    0.8  STRONG_TOP / STRONG_BOT (no LTF confirm)     -> FIRE
    0.5  TOP / BOT + LTF confirm                      -> FIRE
    0.0  no extreme                                   -> NO FIRE

Default behavior:
    Engine is gated by `LIVE_ENTRY_ENGINE_ENABLED` (master) AND
    `LIVE_ENTRY_ENGINE_STDEV_MACRO_ENABLED` (per-engine sub-toggle), both
    DEFAULT FALSE in config.py / config_tradier.py until user flips.

CAVEAT (per CLAUDE.md NEW STRATEGY PROHIBITION):
    The signal-fire / fwd-bps stats above come from a 6-symbol × 1-yr forward-
    return measurement (not a full sample-floor backtest). It satisfies neither
    the publishable Sharpe floor (≥48 crypto OR ≥100 stocks × >1yr × ≥30
    trades/sym) nor the pool_sharpe>1.0 promotion gate. The signal-quality
    evidence is STRONG but not sufficient to flip the flag live without a
    Tier-2 multi-symbol backtest first.

Pure: no I/O, no Redis, no logger, no config reads except what callers pass.
"""
from __future__ import annotations
from typing import Tuple

try:
    from stdev_macro import compute_stdev_macro_state
except Exception:
    compute_stdev_macro_state = None


_FIRE_THRESHOLD = 0.5


def _f(d: dict, key: str, default: float = 0.0) -> float:
    try:
        v = d.get(key, default)
        if v is None:
            return float(default)
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def should_fire_stdev_macro_entry(symbol: str, indicators: dict, side: str) -> Tuple[bool, str, float]:
    """Pure-function STDEV_MACRO macro-extreme entry detector.

    Args:
        symbol: ticker (logging only, no side-effects).
        indicators: dict with bb_pct_b_D, bb_pct_b_4h (auto-tuned BB %B fields
                    precomputed in NPZ via ez_indicators.bb_auto_tune), optionally
                    wt1_3m / wt2_3m / wt1_D / wt2_D for LTF/HTF confirmation.
        side: "LONG" or "SHORT".

    Returns:
        (fire: bool, reason: str, score: float in [0.0, 1.0])

    Fails closed on missing module / bad inputs / missing data — never raises.
    """
    try:
        if compute_stdev_macro_state is None:
            return False, "STDEV_MACRO_MODULE_MISSING", 0.0
        if not isinstance(indicators, dict):
            return False, "NO_INDICATORS", 0.0
        side_u = (side or "").upper()
        if side_u not in ("LONG", "SHORT"):
            return False, f"BAD_SIDE({side})", 0.0
        # compute_stdev_macro_state is fail-open: missing fields → MID → score 0
        state_dict = compute_stdev_macro_state(indicators, config_obj=None)
        if not state_dict.get("data_present"):
            return False, "STDEV_MACRO_NO_DATA", 0.0
        macro_state = state_dict.get("macro_state", "MID")
        pctb_d = state_dict.get("bb_pct_b_D", 0.5)
        pctb_4h = state_dict.get("bb_pct_b_4h", 0.5)
        # LTF confirmation: wt1_3m vs wt2_3m flipping with mean-revert side
        wt1_3m = _f(indicators, "wt1_3m")
        wt2_3m = _f(indicators, "wt2_3m")
        # HTF confirmation: Daily WT agreeing with mean-revert side
        wt1_D = _f(indicators, "wt1_D")
        wt2_D = _f(indicators, "wt2_D")
        if side_u == "LONG":
            # LONG enters when price extended DOWN (mean-revert up). State must be BOT/STRONG_BOT.
            if macro_state == "STRONG_BOT":
                ltf_confirm = wt1_3m > wt2_3m  # 3m flipping bullish
                htf_confirm = wt1_D > wt2_D    # Daily WT bullish
                if ltf_confirm and htf_confirm:
                    return True, f"STDEV_MACRO_LONG_STRONG_BOT(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},LTF+HTF)", 1.0
                if ltf_confirm or htf_confirm:
                    return True, f"STDEV_MACRO_LONG_STRONG_BOT(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},partial)", 0.8
                return True, f"STDEV_MACRO_LONG_STRONG_BOT(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},no_confirm)", 0.5
            if macro_state == "BOT":
                ltf_confirm = wt1_3m > wt2_3m
                if ltf_confirm:
                    return True, f"STDEV_MACRO_LONG_BOT(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},LTF)", 0.5
                return False, f"STDEV_MACRO_LONG_BOT_NO_LTF(bbD={pctb_d:.3f})", 0.3
            return False, f"STDEV_MACRO_LONG_STATE={macro_state}", 0.0
        # SHORT mirror — STRONG_TOP / TOP. This is the +41bps composite path.
        if macro_state == "STRONG_TOP":
            ltf_confirm = wt1_3m < wt2_3m  # 3m flipping bearish
            htf_confirm = wt1_D < wt2_D    # Daily WT bearish — THE STDEV_D200_HIGH+WT_D_BEAR composite
            if ltf_confirm and htf_confirm:
                return True, f"STDEV_MACRO_SHORT_STRONG_TOP(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},LTF+HTF)", 1.0
            if ltf_confirm or htf_confirm:
                return True, f"STDEV_MACRO_SHORT_STRONG_TOP(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},partial)", 0.8
            return True, f"STDEV_MACRO_SHORT_STRONG_TOP(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},no_confirm)", 0.5
        if macro_state == "TOP":
            ltf_confirm = wt1_3m < wt2_3m
            if ltf_confirm:
                return True, f"STDEV_MACRO_SHORT_TOP(bbD={pctb_d:.3f},bb4h={pctb_4h:.3f},LTF)", 0.5
            return False, f"STDEV_MACRO_SHORT_TOP_NO_LTF(bbD={pctb_d:.3f})", 0.3
        return False, f"STDEV_MACRO_SHORT_STATE={macro_state}", 0.0
    except Exception as exc:
        return False, f"STDEV_MACRO_EXC({type(exc).__name__})", 0.0


def _test():
    """Inline tests — run via `python entry_engine_stdev_macro.py`."""
    # T1: clean SHORT STRONG_TOP composite (bbD=0.99, bb4h=0.95, wt_D bear, wt_3m bear)
    ind1 = {
        "bb_pct_b_D": 0.99, "bb_pct_b_4h": 0.95, "bb_pct_b_1h": 0.85,
        "wt1_3m": 5.0, "wt2_3m": 10.0,
        "wt1_D": -3.0, "wt2_D": 5.0,
    }
    fire, reason, score = should_fire_stdev_macro_entry("BTCUSDC", ind1, "SHORT")
    assert fire is True, f"T1 fire={fire}"
    assert score == 1.0, f"T1 score={score}"
    assert "STRONG_TOP" in reason and "LTF+HTF" in reason
    print(f"T1 SHORT STRONG_TOP composite: fire={fire} score={score} reason={reason}")
    # T2: LONG STRONG_BOT composite
    ind2 = {
        "bb_pct_b_D": 0.01, "bb_pct_b_4h": 0.05, "bb_pct_b_1h": 0.10,
        "wt1_3m": 10.0, "wt2_3m": 5.0,
        "wt1_D": 5.0, "wt2_D": -3.0,
    }
    fire2, reason2, score2 = should_fire_stdev_macro_entry("ETHUSDC", ind2, "LONG")
    assert fire2 is True, f"T2 fire={fire2}"
    assert score2 == 1.0, f"T2 score={score2}"
    print(f"T2 LONG STRONG_BOT composite: fire={fire2} score={score2} reason={reason2}")
    # T3: MID state — no fire
    ind3 = {"bb_pct_b_D": 0.50, "bb_pct_b_4h": 0.50}
    fire3, reason3, score3 = should_fire_stdev_macro_entry("SOLUSDC", ind3, "LONG")
    assert fire3 is False
    print(f"T3 MID no-fire: fire={fire3} score={score3} reason={reason3}")
    # T4: missing data → fail-open NO_DATA
    ind4 = {}
    fire4, reason4, score4 = should_fire_stdev_macro_entry("XRPUSDC", ind4, "LONG")
    assert fire4 is False
    print(f"T4 missing-data no-fire: fire={fire4} score={score4} reason={reason4}")
    # T5: bad side
    fire5, reason5, score5 = should_fire_stdev_macro_entry("BTC", ind1, "BAD")
    assert fire5 is False
    print(f"T5 bad-side no-fire: fire={fire5} score={score5} reason={reason5}")
    # T6: SHORT STRONG_TOP no LTF/HTF confirm → 0.5 fire
    ind6 = {"bb_pct_b_D": 0.99, "bb_pct_b_4h": 0.95}
    fire6, reason6, score6 = should_fire_stdev_macro_entry("AAPL", ind6, "SHORT")
    assert fire6 is True and score6 == 0.5
    print(f"T6 SHORT STRONG_TOP no-confirm: fire={fire6} score={score6} reason={reason6}")
    print("ALL TESTS PASSED")


if __name__ == "__main__":
    _test()
