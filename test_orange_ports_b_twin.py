"""test_orange_ports_b_twin.py — pytest for vec_decisions/twin_orange_ports_b.

Per-filter coverage (48): inert-default behavior, boundary samples, and
formula agreement vs independent oracles (ez_satoshit for the SATOSHIT
entry family; hand-verified vectors + independent transcriptions elsewhere).
Self-contained: does NOT import v12_quick_engine (worktree lacks its
vec_decisions deps) — formulas were transcribed from the read sources.
"""
import random
from types import SimpleNamespace

import pytest

from vec_decisions import twin_orange_ports_b as T

get = T.get


def cfg(**kw):
    return dict(kw)


def test_all_48_covered():
    assert len(T.FILTERS) == 48 and len(set(T.FILTERS)) == 48
    assert set(T.DEFAULTS) == set(T.FILTERS)
    assert set(T.STATUS) == set(T.FILTERS)
    for n in T.FILTERS:
        assert callable(get(n)), n
    with pytest.raises(KeyError):
        get("NO_SUCH_FILTER")


def test_source_integrity_no_stubs():
    import pathlib
    import re
    src = pathlib.Path(T.__file__).read_text()
    code = re.sub(r'"""[\s\S]*?"""', "", src)
    code = "\n".join(line for line in code.splitlines() if not line.strip().startswith("#"))
    assert "and False" not in code
    assert "_ = getattr" not in code
    assert "_ =" not in code.replace("_GET", "").replace("_GET[name]", "")
    assert "getattr(cfg, " in src or "_raw(cfg" in src  # real reads exist
    for n in T.FILTERS:
        assert f'"{n}"' in src, n  # literal key read


# ── inert-default: parent-off / wrong-side / inert defaults ──

def test_inert_parent_off_returns_none():
    off_cases = [
        ("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER", True, {"rsi_1h": 99, "stoch_k": 99}),
        ("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER", False, {"rsi_1h": 0, "stoch_k": 0}),
        ("SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER", False, {"rsi_1h": 0, "stoch_k": 0}),
        ("SMA200_DIST_LONG_THRESHOLD", True, {"close": 1, "sma_200_1h": 100}),
        ("TF_ALIGNMENT_MIN_TOTAL", True, {"wt1_1h": 9, "wt2_1h": 0, "wt1_4h": 9, "wt2_4h": 0, "wt1_D": 9, "wt2_D": 0}),
        ("TF_FOCUS_ENTRY_HARD_GATE", True, {"wt1_1h": 9, "wt2_1h": 0, "wt1_4h": 9, "wt2_4h": 0}),
        ("TF_FOCUS_WEIGHT", True, {"wt1_1h": 9, "wt2_1h": 0, "wt1_4h": 9, "wt2_4h": 0}),
        ("TRADIER_DC_POSITION_ENTRY_THRESHOLD", True, {"dc_position_15m": 0.0}),
        ("TRADIER_ENTRY_SCORE_THRESHOLD", True, {"mfi_D": 99}),
        ("TRADIER_FH_MOMENTUM_DC_MAX_LONG", True, {"timestamps": 49500, "open_D": 100, "close": 102, "dc_position_15m": 0, "mfi_1h": 99}),
        ("TRADIER_FH_MOMENTUM_MFI_MIN", True, {"timestamps": 49500, "open_D": 100, "close": 102, "dc_position_15m": 0, "mfi_1h": 99}),
        ("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", True, {"timestamps": 49500, "open_D": 100, "close": 102, "dc_position_15m": 0, "mfi_1h": 99}),
        ("TRADIER_FH_MOMENTUM_WINDOW_MINUTES", True, {"timestamps": 49500, "open_D": 100, "close": 102, "dc_position_15m": 0, "mfi_1h": 99}),
        ("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", True, {"k_3m": 0}),
        ("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", False, {"k_3m": 100}),
        ("TRADIER_RSI_SHORT_REL_VOLUME_MIN", False, {"rsi_1h": 99, "relative_volume_1h": 9}),
        ("VWAP_FILTER_ENABLED", True, {"close": 1, "vwap_D": 100}),
        ("WIN_TRAIL_EROSION_PCT", True, {"peak_pnl_pct": 10, "live_pnl_pct": 0}),
        ("WT_15M_BOUNCE_FILTER_HH_ENABLED", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_15M_BOUNCE_FILTER_HL_ENABLED", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_15M_BOUNCE_FILTER_MODE", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_15M_BOUNCE_VOLUME_THRESHOLD", True, {"wt1_15m": 9, "wt2_15m": 0, "wt1_15m_prev": 0, "wt2_15m_prev": 9, "wt_cross_rising_1h": True}),
        ("WT_DC_DETAILED_ENTRY_THRESHOLD", True, {"wtdc_score": 100}),
        ("WT_DC_ENTRY_THRESHOLD", True, {"wtdc_score": 100}),
        ("WT_DC_K5M_MIN_SHORT_HARD", False, {"stoch_k_15m": 100}),
    ]
    for name, is_long, ind in off_cases:
        assert get(name)(ind, is_long, cfg()) is None, name


def test_inert_wrong_side_returns_none():
    side_cases = [
        ("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER", False, {"SATOSHIT_EXIT_ENABLED": True}),
        ("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER", True, {"SATOSHIT_EXIT_ENABLED": True}),
        ("SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER", True, {"SATOSHIT_EXIT_ENABLED": True}),
        ("SATOSHIT_LONG_MFI_MAX_TRADIER", False, {}),
        ("SATOSHIT_LONG_RSI_MAX_TRADIER", False, {}),
        ("SATOSHIT_LONG_STOCH_K_MAX_TRADIER", False, {}),
        ("SATOSHIT_SHORT_MFI_MIN_TRADIER", True, {}),
        ("SATOSHIT_SHORT_RSI_MIN_TRADIER", True, {}),
        ("SATOSHIT_SHORT_STOCH_K_MIN_TRADIER", True, {}),
        ("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", False, {"K_ZONE_ENTRY_ENABLED": True}),
        ("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", True, {"K_ZONE_ENTRY_ENABLED": True}),
        ("WT_DC_DC_POS_THRESHOLD_LONG", False, {}),
        ("WT_DC_DC_POS_THRESHOLD_SHORT", True, {}),
        ("WT_DC_STOCH_THRESHOLD_LONG", False, {}),
        ("WT_DC_STOCH_THRESHOLD_SHORT", True, {}),
    ]
    for name, is_long, c in side_cases:
        assert get(name)({}, is_long, cfg(**c)) is None, name


def test_inert_defaults_at_live_values():
    # Always-on-in-vec gates: defaults must reproduce the pass-through value.
    assert get("WT_DC_DIRECT_THRESHOLD")({}, True, cfg()) is True  # -1 = no block
    assert get("WT_DC_HTF_GATE_MODE")({}, True, cfg()) is True  # default TFs skip expanded block
    # Missing keys fail open exactly like vec stays-ones.
    assert get("WT_DC_DC_POS_THRESHOLD_LONG")({}, True, cfg()) is True
    assert get("WT_DC_STOCH_THRESHOLD_SHORT")({}, False, cfg()) is True
    assert get("VWAP_FILTER_ENABLED")({"close": 5}, True, cfg(VWAP_FILTER_ENABLED=True)) is True
    # TF label outside vec's list fails open.
    assert get("WT_DC_DC_POS_THRESHOLD_LONG")({"dc_position_1h": 0.99}, True, cfg(WT_DC_DC_TF="W")) is True
    assert get("WT_DC_HTF_GATE")({"wt1_4h": 0, "wt2_4h": 9, "wt1_D": 0, "wt2_D": 9}, True, cfg(WT_DC_HTF_GATE="bogus")) is True


# ── boundary samples: threshold ±ε flips ──

def test_boundary_satoshit_votes():
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")({"rsi_15m": 49.99}, True, cfg()) is True
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")({"rsi_15m": 50.0}, True, cfg()) is False
    assert get("SATOSHIT_LONG_STOCH_K_MAX_TRADIER")({"stoch_k_15m": 59.99}, True, cfg()) is True
    assert get("SATOSHIT_LONG_STOCH_K_MAX_TRADIER")({"stoch_k_15m": 60.0}, True, cfg()) is False
    assert get("SATOSHIT_LONG_MFI_MAX_TRADIER")({"mfi_15m": 59.99}, True, cfg()) is True
    assert get("SATOSHIT_LONG_MFI_MAX_TRADIER")({"mfi_15m": 60.0}, True, cfg()) is False
    assert get("SATOSHIT_SHORT_RSI_MIN_TRADIER")({"rsi_15m": 55.01}, False, cfg()) is True
    assert get("SATOSHIT_SHORT_RSI_MIN_TRADIER")({"rsi_15m": 55.0}, False, cfg()) is False
    assert get("SATOSHIT_SHORT_STOCH_K_MIN_TRADIER")({"stoch_k_15m": 50.01}, False, cfg()) is True
    assert get("SATOSHIT_SHORT_STOCH_K_MIN_TRADIER")({"stoch_k_15m": 50.0}, False, cfg()) is False
    assert get("SATOSHIT_SHORT_MFI_MIN_TRADIER")({"mfi_15m": 50.01}, False, cfg()) is True
    assert get("SATOSHIT_SHORT_MFI_MIN_TRADIER")({"mfi_15m": 50.0}, False, cfg()) is False
    assert get("SATOSHIT_HTF_MFI_D_MIN_TRADIER")({"mfi_D": 30.0}, True, cfg()) is True
    assert get("SATOSHIT_HTF_MFI_D_MIN_TRADIER")({"mfi_D": 29.99}, True, cfg()) is False
    assert get("SATOSHIT_HTF_MFI_D_MIN_TRADIER")({"mfi_D": 30.0}, False, cfg()) is True  # live: >= both sides
    assert get("SATOSHIT_HTF_RVOL_1H_MIN_TRADIER")({"relative_volume_1h": 0.3}, True, cfg()) is True
    assert get("SATOSHIT_HTF_RVOL_1H_MIN_TRADIER")({"relative_volume_1h": 0.299}, True, cfg()) is False
    # _TRADIER override wins over base (mirrors ez_satoshit._cfg chain)
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")({"rsi_15m": 55}, True, cfg(SATOSHIT_LONG_RSI_MAX_TRADIER=60)) is True
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")({"rsi_15m": 55}, True, cfg(SATOSHIT_LONG_RSI_MAX=60)) is True
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")({"rsi_15m": 55}, True, cfg(SATOSHIT_LONG_RSI_MAX_TRADIER=50, SATOSHIT_LONG_RSI_MAX=60)) is False


def test_boundary_satoshit_exit_and_quorum():
    c = cfg(SATOSHIT_EXIT_ENABLED=True)
    assert get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER")({"rsi_1h": 55, "stoch_k": 60}, True, c) is True
    assert get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER")({"rsi_1h": 55, "stoch_k": 59.99}, True, c) is False
    assert get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER")({"rsi_1h": 54.99, "stoch_k": 60}, True, c) is False
    c2 = cfg(SATOSHIT_EXIT_ENABLED=True, SATOSHIT_MIN_VOTES_TRADIER=2)  # OR mode
    assert get("SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER")({"rsi_1h": 0, "stoch_k": 60}, True, c2) is True
    assert get("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER")({"rsi_1h": 42, "stoch_k": 50}, False, c) is True
    assert get("SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER")({"rsi_1h": 42.01, "stoch_k": 50}, False, c) is False
    assert get("SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER")({"rsi_1h": 42, "stoch_k": 50.01}, False, c) is False
    # quorum: exactly 3 of 5 long votes (R,K,M + bb miss + ha miss)
    ind3 = {"rsi_15m": 10, "stoch_k_15m": 10, "mfi_15m": 10, "bb_pct_b_1h": 0.9, "ha_15m": "green"}
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind3, True, cfg()) is True
    ind2 = dict(ind3, mfi_15m=90)
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind2, True, cfg()) is False
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind2, True, cfg(SATOSHIT_MIN_VOTES_TRADIER=2)) is True


def test_boundary_blocks_and_gates():
    assert get("SMA200_DIST_LONG_THRESHOLD")({"close": 97, "sma_200_1h": 100}, True, cfg(SMA200_DIST_ENTRY_ENABLED=True)) is False  # -3.0 not < -3
    assert get("SMA200_DIST_LONG_THRESHOLD")({"close": 96.9, "sma_200_1h": 100}, True, cfg(SMA200_DIST_ENTRY_ENABLED=True)) is True
    assert get("SMA200_DIST_LONG_THRESHOLD")({"close": 103.1, "sma_200_1h": 100}, False, cfg(SMA200_DIST_ENTRY_ENABLED=True)) is True
    assert get("SMA200_DIST_LONG_THRESHOLD")({"close": 103, "sma_200_1h": 100}, False, cfg(SMA200_DIST_ENTRY_ENABLED=True)) is False
    assert get("STRENGTH_FILTER_ENABLED")({"wt1_1h": 4.0, "wt2_1h": 0}, True, cfg()) is True
    assert get("STRENGTH_FILTER_ENABLED")({"wt1_1h": 3.999, "wt2_1h": 0}, True, cfg()) is False
    assert get("STRENGTH_MIN_SCORE")({"wt1_1h": 4.0, "wt2_1h": 0}, True, cfg(STRENGTH_FILTER_ENABLED=False)) is None
    assert get("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER")({"k_3m": 34.99}, True, cfg(K_ZONE_ENTRY_ENABLED=True)) is True
    assert get("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER")({"k_3m": 35.0}, True, cfg(K_ZONE_ENTRY_ENABLED=True)) is False
    assert get("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER")({"k_3m": 65.01}, False, cfg(K_ZONE_ENTRY_ENABLED=True)) is True
    assert get("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER")({"k_3m": 65.0}, False, cfg(K_ZONE_ENTRY_ENABLED=True)) is False
    assert get("VWAP_FILTER_ENABLED")({"close": 100.01, "vwap_D": 100}, True, cfg(VWAP_FILTER_ENABLED=True)) is True
    assert get("VWAP_FILTER_ENABLED")({"close": 100.0, "vwap_D": 100}, True, cfg(VWAP_FILTER_ENABLED=True)) is False
    assert get("VWAP_FILTER_ENABLED")({"close": 99.99, "vwap_D": 100}, False, cfg(VWAP_FILTER_ENABLED=True)) is True
    assert get("WIN_TRAIL_EROSION_PCT")({"peak_pnl_pct": 10, "live_pnl_pct": 9}, True, cfg(WIN_TRAIL_EROSION_PCT=0.1)) is True
    assert get("WIN_TRAIL_EROSION_PCT")({"peak_pnl_pct": 10, "live_pnl_pct": 9.001}, True, cfg(WIN_TRAIL_EROSION_PCT=0.1)) is False
    assert get("WIN_TRAIL_EROSION_PCT")({"peak_pnl_pct": 10, "live_pnl_pct": 0}, True, cfg(WIN_TRAIL_EROSION_PCT=0.1)) is True
    assert get("WIN_TRAIL_EROSION_PCT")({"peak_pnl_pct": 0, "live_pnl_pct": -5}, True, cfg(WIN_TRAIL_EROSION_PCT=0.1)) is False
    assert get("TRADIER_DC_POSITION_ENTRY_THRESHOLD")({"dc_position_15m": 0.249}, True, cfg(DC_DAYTRADE_ENABLED=True)) is True
    assert get("TRADIER_DC_POSITION_ENTRY_THRESHOLD")({"dc_position_15m": 0.25}, True, cfg(DC_DAYTRADE_ENABLED=True)) is False
    assert get("TRADIER_DC_POSITION_ENTRY_THRESHOLD")({"dc_position_15m": 0.751}, False, cfg(DC_DAYTRADE_ENABLED=True)) is True
    assert get("TRADIER_DC_POSITION_ENTRY_THRESHOLD")({"dc_position_15m": 0.1, "dc_width_1h": 5, "dc_width_1h_prev": 6}, True, cfg(DC_DAYTRADE_ENABLED=True)) is False
    assert get("TRADIER_RSI_SHORT_REL_VOLUME_MIN")({"rsi_1h": 70.1, "relative_volume_1h": 2.4}, False, cfg(MODE="tradier")) is True
    assert get("TRADIER_RSI_SHORT_REL_VOLUME_MIN")({"rsi_1h": 70.0, "relative_volume_1h": 9}, False, cfg(MODE="tradier")) is False
    assert get("TRADIER_RSI_SHORT_REL_VOLUME_MIN")({"rsi_1h": 99, "relative_volume_1h": 9}, True, cfg(MODE="tradier")) is False
    assert get("TRADIER_ENTRY_SCORE_THRESHOLD")({"mfi_D": 40}, True, cfg(MODE="tradier")) is True
    assert get("TRADIER_ENTRY_SCORE_THRESHOLD")({"mfi_D": 39.99}, True, cfg(MODE="tradier")) is False
    assert get("TRADIER_ENTRY_SCORE_THRESHOLD")({"mfi_D": 99}, True, cfg(MODE="tradier", TRADIER_ENTRY_SCORE_THRESHOLD=23.9)) is None
    assert get("TF_ALIGNMENT_MIN_TOTAL")({"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 0, "wt2_4h": 1, "wt1_D": 0, "wt2_D": 1}, True, cfg(MODE="tradier", BACKTEST_VALIDATED_GATES_TRADIER=True)) is True  # cnt1>=need1
    assert get("TF_ALIGNMENT_MIN_TOTAL")({"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 0, "wt2_4h": 1, "wt1_D": 0, "wt2_D": 1}, True, cfg(MODE="tradier", BACKTEST_VALIDATED_GATES_TRADIER=True, TF_ALIGNMENT_MIN_TOTAL=8)) is False  # need2
    assert get("TF_FOCUS_ENTRY_HARD_GATE")({"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 1, "wt2_4h": 0}, True, cfg(TF_FOCUS_ENTRY_HARD_GATE=True)) is True
    assert get("TF_FOCUS_ENTRY_HARD_GATE")({"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 0, "wt2_4h": 1}, True, cfg(TF_FOCUS_ENTRY_HARD_GATE=True)) is False
    assert get("TF_FOCUS_WEIGHT")({"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 1, "wt2_4h": 0}, True, cfg(TF_FOCUS_ENTRY_HARD_GATE=True, TF_FOCUS_WEIGHT=0.0)) is False
    assert get("TF_HTF1")({"wt1_1h": 1, "wt2_1h": 0, "wt1_15m": 0, "wt2_15m": 1, "wt1_4h": 0, "wt2_4h": 1}, True, cfg(TF_HTF1="15m", TF_HTF3="4h")) is True  # cnt1>=1
    assert get("TF_HTF3")({"wt1_1h": 0, "wt2_1h": 1, "wt1_15m": 0, "wt2_15m": 1, "wt1_4h": 0, "wt2_4h": 1}, True, cfg(TF_HTF1="15m", TF_HTF3="4h")) is False
    assert get("TF_HTF1")({"wt1_1h": 1, "wt2_1h": 0}, True, cfg(HTF_ALIGNMENT_ENABLED=False)) is None


def test_boundary_fh_momentum():
    base = {"open_D": 100.0, "close": 100.6, "dc_position_15m": 0.33, "mfi_1h": 55.0}
    c = cfg(FH_MOMENTUM_ENABLED=True)
    assert get("TRADIER_FH_MOMENTUM_WINDOW_MINUTES")(dict(base, timestamps=49500), True, c) is True  # 13:45 in window
    assert get("TRADIER_FH_MOMENTUM_WINDOW_MINUTES")(dict(base, timestamps=43200), True, c) is False  # 12:00 out
    assert get("TRADIER_FH_MOMENTUM_WINDOW_MINUTES")(dict(base, timestamps=49500 + 61 * 60), True, c) is False  # 14:46 out
    assert get("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT")(dict(base, timestamps=49500, close=100.49), True, c) is False  # 0.49<0.5
    assert get("TRADIER_FH_MOMENTUM_DC_MAX_LONG")(dict(base, timestamps=49500, dc_position_15m=0.331), True, c) is False
    assert get("TRADIER_FH_MOMENTUM_MFI_MIN")(dict(base, timestamps=49500, mfi_1h=54.99), True, c) is False
    sbase = {"timestamps": 49500, "open_D": 100.0, "close": 99.5, "dc_position_15m": 0.67, "mfi_1h": 45.0}
    assert get("TRADIER_FH_MOMENTUM_MIN_MOVE_PCT")(sbase, False, c) is True  # -0.5<=-0.5, 0.67>=0.67, 45<=45
    assert get("TRADIER_FH_MOMENTUM_MFI_MIN")(dict(sbase, mfi_1h=45.01), False, c) is False
    assert get("TRADIER_FH_MOMENTUM_DC_MAX_LONG")(dict(base, timestamps=49500), True, cfg()) is None


def test_boundary_wtdc_pack():
    assert get("WT_DC_DC_POS_THRESHOLD_LONG")({"dc_position_1h": 0.499}, True, cfg()) is True
    assert get("WT_DC_DC_POS_THRESHOLD_LONG")({"dc_position_1h": 0.5}, True, cfg()) is False
    assert get("WT_DC_DC_POS_THRESHOLD_SHORT")({"dc_position_1h": 0.501}, False, cfg()) is True
    assert get("WT_DC_DC_POS_THRESHOLD_SHORT")({"dc_position_1h": 0.5}, False, cfg()) is False
    assert get("WT_DC_DC_POS_THRESHOLD_LONG")({"dc_position_4h": 0.1}, True, cfg(WT_DC_DC_TF="4h")) is True
    assert get("WT_DC_STOCH_THRESHOLD_LONG")({"stoch_k_15m": 39.99}, True, cfg()) is True  # 5m→15m floor
    assert get("WT_DC_STOCH_THRESHOLD_LONG")({"stoch_k_15m": 40.0}, True, cfg()) is False
    assert get("WT_DC_STOCH_THRESHOLD_SHORT")({"stoch_k_15m": 60.01}, False, cfg()) is True
    assert get("WT_DC_STOCH_THRESHOLD_SHORT")({"stoch_k_15m": 60.0}, False, cfg()) is False
    assert get("WT_DC_K5M_MIN_SHORT_HARD")({"stoch_k_15m": 20.0}, False, cfg(WT_DC_K5M_HARD_ENABLED=True)) is True
    assert get("WT_DC_K5M_MIN_SHORT_HARD")({"stoch_k_15m": 19.99}, False, cfg(WT_DC_K5M_HARD_ENABLED=True)) is False
    assert get("WT_DC_K5M_MIN_SHORT_HARD")({"stoch_k_5m": 10.0, "stoch_k_15m": 99.0}, False, cfg(WT_DC_K5M_HARD_ENABLED=True)) is False  # 5m wins
    assert get("WT_DC_K5M_MIN_SHORT_HARD")({"stoch_k_15m": 0.0}, True, cfg(WT_DC_K5M_HARD_ENABLED=True)) is True  # long passes
    # TF-adjusted entry bars: 15m→thr-10 floor20 / 4h→+10 cap85 / D→+15 cap85
    cd = cfg(WT_DC_ENABLED=True, WT_DC_DETAILED_SCORER_ENABLED=True)
    assert get("WT_DC_DETAILED_ENTRY_THRESHOLD")({"wtdc_score": 43}, True, cd) is True
    assert get("WT_DC_DETAILED_ENTRY_THRESHOLD")({"wtdc_score": 42.99}, True, cd) is False
    assert get("WT_DC_DETAILED_ENTRY_THRESHOLD")({"wtdc_score": 33}, True, cfg(**{**cd, "WT_DC_TF_ENTRY": "15m"})) is True
    assert get("WT_DC_DETAILED_ENTRY_THRESHOLD")({"wtdc_score": 32.99}, True, cfg(**{**cd, "WT_DC_TF_ENTRY": "15m"})) is False
    cs = cfg(WT_DC_ENABLED=True)
    assert get("WT_DC_ENTRY_THRESHOLD")({"wtdc_score": 55}, True, cfg(**{**cs, "WT_DC_TF_ENTRY": "4h"})) is True
    assert get("WT_DC_ENTRY_THRESHOLD")({"wtdc_score": 54.99}, True, cfg(**{**cs, "WT_DC_TF_ENTRY": "4h"})) is False
    assert get("WT_DC_ENTRY_THRESHOLD")({"wtdc_score": 60}, True, cfg(**{**cs, "WT_DC_TF_ENTRY": "D"})) is True
    assert get("WT_DC_ENTRY_THRESHOLD")({"wtdc_score": 100}, True, cfg(**{**cs, "WT_DC_DETAILED_SCORER_ENABLED": True})) is None
    # HTF gate: 4h_d default; short forced; unknown fails open
    alg = {"wt1_1h": 1, "wt2_1h": 0, "wt1_4h": 1, "wt2_4h": 0, "wt1_D": 1, "wt2_D": 0}
    assert get("WT_DC_HTF_GATE")(alg, True, cfg()) is True
    assert get("WT_DC_HTF_GATE")(dict(alg, wt1_D=0, wt2_D=1), True, cfg()) is False
    assert get("WT_DC_HTF_GATE")(dict(alg, wt1_1h=0, wt2_1h=1), True, cfg(WT_DC_HTF_GATE="1h")) is False
    assert get("WT_DC_HTF_GATE")(dict(alg, wt1_1h=0, wt2_1h=1), True, cfg()) is True  # 1h ignored under 4h_d
    assert get("WT_DC_HTF_GATE")(alg, False, cfg(WT_DC_HTF_GATE="1h")) is False  # short forced 4h_d; 4h bull = against short
    salg = {"wt1_1h": 0, "wt2_1h": 1, "wt1_4h": 0, "wt2_4h": 1, "wt1_D": 0, "wt2_D": 1}
    assert get("WT_DC_HTF_GATE")(salg, False, cfg()) is True
    # expanded HTF1/HTF2 gate + AND/OR + stocks-stack inversion
    ce = cfg(WT_DC_TF_HTF="1h", WT_DC_TF_HTF2="4h")
    assert get("WT_DC_HTF_GATE_MODE")(alg, True, ce) is True
    assert get("WT_DC_HTF_GATE_MODE")(dict(alg, wt1_1h=0, wt2_1h=1), True, ce) is False  # AND needs both
    assert get("WT_DC_HTF_GATE_MODE")(dict(alg, wt1_1h=0, wt2_1h=1), True, cfg(**{**ce, "WT_DC_HTF_GATE_MODE": "OR"})) is True
    stack = cfg(**{**ce, "MODE": "tradier", "STOCKS_LIVE_ENTRY_STACK_ENABLED": True})
    assert get("WT_DC_HTF_GATE_MODE")(dict(alg, wt1_1h=0, wt2_1h=1), True, stack) is True  # inverted: AND→OR
    assert get("WT_DC_HTF_GATE_MODE")(dict(alg, wt1_D=0, wt2_D=1), True, cfg(WT_DC_TF_HTF="none", WT_DC_TF_HTF2="D")) is False


def test_boundary_vel_decay():
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.1, "wt_velocity_3m": 1.0}, True, cfg()) is True
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 1.0, "wt_velocity_1h_prev": 2.1, "wt_velocity_3m": 1.0}, True, cfg()) is False
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.0, "wt_velocity_3m": 1.0}, True, cfg()) is False
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.1, "wt_velocity_3m": 1.06}, True, cfg()) is False
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": -0.9, "wt_velocity_1h_prev": -2.1, "wt_velocity_3m": -1.0}, False, cfg()) is True
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.1, "wt_velocity": 1.0}, True, cfg()) is True  # alias
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 0.9, "wt_velocity_1h_prev": 2.1, "wt_velocity_3m": 1.0}, True, cfg(WT_VEL_DECAY_EXIT_ENABLED=False)) is None
    assert get("WT_VEL_DECAY_THRESHOLD")({"wt_velocity_1h": 1.9, "wt_velocity_1h_prev": 4.1, "wt_velocity_3m": 2.0}, True, cfg(WT_VEL_DECAY_THRESHOLD=2.0)) is True


# ── formula agreement ──

def test_agreement_satoshit_vs_live_oracle():
    import ez_satoshit as EZ
    rng = random.Random(20261004)
    cfg_ns = SimpleNamespace()  # bare: ez _cfg falls back to base defaults
    for i in range(600):
        is_long = (i % 2 == 0)
        ind = {
            "rsi_15m": rng.uniform(0, 100),
            "stoch_k_15m": rng.uniform(0, 100),
            "mfi_15m": rng.uniform(0, 100),
            "bb_pct_b_1h": rng.uniform(0, 1),
            "ha_15m": rng.choice(["red", "green", "neutral"]),
            "mfi_D": rng.uniform(0, 100),
            "relative_volume_1h": rng.uniform(0, 3),
        }
        should, votes, _ = EZ.satoshit_entry_signal(ind, is_long, cfg_ns)
        quorum = get("SATOSHIT_MIN_VOTES_TRADIER")(ind, is_long, {})
        assert quorum == (votes >= 3), (i, ind, votes, quorum)
        htf = get("SATOSHIT_HTF_MFI_D_MIN_TRADIER")(ind, is_long, {}) and get("SATOSHIT_HTF_RVOL_1H_MIN_TRADIER")(ind, is_long, {})
        assert (quorum and htf) == should, (i, ind, should)
    # override agreement: _TRADIER keys honored by both
    ind = {"rsi_15m": 55, "stoch_k_15m": 10, "mfi_15m": 10, "bb_pct_b_1h": 0.1, "ha_15m": "red", "mfi_D": 50, "relative_volume_1h": 1.0}
    cfg_o = SimpleNamespace(SATOSHIT_LONG_RSI_MAX_TRADIER=60)
    should, votes, _ = EZ.satoshit_entry_signal(ind, True, cfg_o)
    assert votes == 5 and should is True
    assert get("SATOSHIT_LONG_RSI_MAX_TRADIER")(ind, True, {"SATOSHIT_LONG_RSI_MAX_TRADIER": 60}) is True
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind, True, {"SATOSHIT_LONG_RSI_MAX_TRADIER": 60}) is True


def _b15_oracle(ind, is_long, c):
    """Independent transcription of vec v12:9736-9808 + stocks tradier:1297-1358."""
    if not c.get("WT_15M_BOUNCE_OPEN_ENABLED", False):
        return None
    w1 = float(ind.get("wt1_15m", 0) or 0)
    w2 = float(ind.get("wt2_15m", 0) or 0)
    w1p = float(ind.get("wt1_15m_prev", w1) or w1)
    w2p = float(ind.get("wt2_15m_prev", w2) or w2)
    cross = ((w1p <= w2p) and (w1 > w2)) if is_long else ((w1p >= w2p) and (w1 < w2))
    still = (w1 > w2) if is_long else (w1 < w2)
    bb = float(ind.get("bb_pct_b_15m", 0.5) or 0.5)
    bb_ok = (float(c.get("WT_15M_BOUNCE_BB_MIN", 0.05) or 0.05) <= bb <= float(c.get("WT_15M_BOUNCE_BB_MAX", 0.95) or 0.95))
    r1 = bool(ind.get("wt_cross_rising_1h", False))
    r4 = bool(ind.get("wt_cross_rising_4h", False))
    req = bool(c.get("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False))
    h1 = r1 if is_long else (not r1)
    h4 = r4 if is_long else (not r4)
    htf_ok = (h1 and h4) if req else (h1 or h4)
    hl = bool(c.get("WT_15M_BOUNCE_FILTER_HL_ENABLED", False) or c.get("WT_15M_BOUNCE_LOW_1H_GT_PREV", False))
    hh = bool(c.get("WT_15M_BOUNCE_FILTER_HH_ENABLED", False) or c.get("WT_15M_BOUNCE_HIGH_1H_GT_PREV", False))
    if hl or hh:
        dl = float(ind.get("dc_low_1h", 0) or 0)
        dlp = float(ind.get("dc_low_1h_prev", dl) or dl)
        dh = float(ind.get("dc_high_1h", 0) or 0)
        dhp = float(ind.get("dc_high_1h_prev", dh) or dh)
        hl_ok = (dl > dlp) if hl else True
        hh_ok = (dh > dhp) if hh else True
        mode = str(c.get("WT_15M_BOUNCE_FILTER_MODE", "AND") or "AND").upper()
        if mode == "OR":
            hlhh = hl_ok if (hl and not hh) else (hh_ok if (hh and not hl) else (hl_ok or hh_ok))
        else:
            hlhh = hl_ok and hh_ok
    else:
        hlhh = True
    trig = still if (hl or hh) else cross
    von = bool(c.get("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False) or c.get("WT_15M_BOUNCE_REL_VOL_GT_1", False))
    if von:
        vmode = str(c.get("WT_15M_BOUNCE_VOLUME_MODE", "relvol") or "relvol").lower()
        vthr = float(c.get("WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0) or 1.0)
        if vmode == "relvol":
            rel = ind.get("relative_volume_15m", None)
            rel = float(ind.get("relative_volume_1h", 1.0)) if rel is None else float(rel)
            vol_ok = rel > vthr
        else:
            v = float(ind.get("volume_15m", 0) or 0)
            s = float(ind.get("volume_sma_15m", 0) or 0)
            if v == 0 or s == 0:
                v = float(ind.get("volume_1h", 0) or 0)
                s = float(ind.get("volume_sma_1h", 0) or 0)
            vol_ok = v > ((s if s != 0 else 1.0) * vthr)
    else:
        vol_ok = True
    return bool(trig and bb_ok and htf_ok and hlhh and vol_ok)


def test_agreement_bounce_randomized():
    rng = random.Random(77)
    names = ["WT_15M_BOUNCE_FILTER_HH_ENABLED", "WT_15M_BOUNCE_FILTER_HL_ENABLED", "WT_15M_BOUNCE_FILTER_MODE",
             "WT_15M_BOUNCE_REQUIRE_BOTH_HTF", "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", "WT_15M_BOUNCE_VOLUME_THRESHOLD"]
    for i in range(400):
        is_long = rng.random() < 0.5
        w1, w2 = rng.uniform(-100, 100), rng.uniform(-100, 100)
        ind = {
            "wt1_15m": w1, "wt2_15m": w2,
            "wt1_15m_prev": rng.uniform(-100, 100), "wt2_15m_prev": rng.uniform(-100, 100),
            "bb_pct_b_15m": rng.uniform(-0.2, 1.2),
            "wt_cross_rising_1h": rng.random() < 0.5, "wt_cross_rising_4h": rng.random() < 0.5,
            "dc_low_1h": rng.uniform(90, 110), "dc_low_1h_prev": rng.uniform(90, 110),
            "dc_high_1h": rng.uniform(90, 110), "dc_high_1h_prev": rng.uniform(90, 110),
            "relative_volume_15m": rng.uniform(0, 3), "relative_volume_1h": rng.uniform(0, 3),
            "volume_15m": rng.uniform(0, 5000), "volume_sma_15m": rng.uniform(1, 5000),
        }
        c = {"WT_15M_BOUNCE_OPEN_ENABLED": True,
             "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": rng.random() < 0.5,
             "WT_15M_BOUNCE_FILTER_HL_ENABLED": rng.random() < 0.5,
             "WT_15M_BOUNCE_FILTER_HH_ENABLED": rng.random() < 0.5,
             "WT_15M_BOUNCE_FILTER_MODE": rng.choice(["AND", "OR"]),
             "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": rng.random() < 0.5,
             "WT_15M_BOUNCE_VOLUME_MODE": rng.choice(["relvol", "ema"]),
             "WT_15M_BOUNCE_VOLUME_THRESHOLD": rng.choice([0.5, 1.0, 1.5])}
        exp = _b15_oracle(ind, is_long, c)
        for n in names:
            assert get(n)(ind, is_long, dict(c)) == exp, (i, n, is_long)
    # still-mode: HL on turns trigger from cross to still
    ind = {"wt1_15m": 5, "wt2_15m": 0, "wt1_15m_prev": 4, "wt2_15m_prev": 0, "bb_pct_b_15m": 0.5,
           "wt_cross_rising_1h": True, "wt_cross_rising_4h": False, "dc_low_1h": 101, "dc_low_1h_prev": 100}
    c = cfg(WT_15M_BOUNCE_OPEN_ENABLED=True)
    assert get("WT_15M_BOUNCE_FILTER_HL_ENABLED")(ind, True, c) is False  # no fresh cross
    assert get("WT_15M_BOUNCE_FILTER_HL_ENABLED")(ind, True, cfg(**{**c, "WT_15M_BOUNCE_FILTER_HL_ENABLED": True})) is True  # still+HL


def test_agreement_wtdc_direct_vectors():
    full_long = {"wt1_D": 1, "wt2_D": 0, "wt1_4h": 1, "wt2_4h": 0, "wt_cross_1h": "BULL",
                 "dc_position_1h": 0.3, "stoch_k_5m": 20}  # 25+25+30+10+10 = 100
    assert get("WT_DC_DIRECT_THRESHOLD")(full_long, True, cfg(WT_DC_DIRECT_THRESHOLD=100)) is True
    assert get("WT_DC_DIRECT_THRESHOLD")(full_long, True, cfg(WT_DC_DIRECT_THRESHOLD=101)) is False
    none_long = {"wt1_D": 0, "wt2_D": 1, "wt1_4h": 0, "wt2_4h": 1, "wt_cross_1h": "BEAR",
                 "dc_position_1h": 0.7, "stoch_k_5m": 80}  # 0
    assert get("WT_DC_DIRECT_THRESHOLD")(none_long, True, cfg(WT_DC_DIRECT_THRESHOLD=20)) is False
    part = dict(full_long, wt_cross_1h="NONE", stoch_k_5m=50)  # 25+25+0+10+0 = 60
    assert get("WT_DC_DIRECT_THRESHOLD")(part, True, cfg(WT_DC_DIRECT_THRESHOLD=60)) is True
    assert get("WT_DC_DIRECT_THRESHOLD")(part, True, cfg(WT_DC_DIRECT_THRESHOLD=61)) is False
    assert get("WT_DC_DIRECT_THRESHOLD")(part, True, cfg(WT_DC_DIRECT_THRESHOLD=60, WT_DC_DIRECT_HTF_GATE="4h_d")) is True
    assert get("WT_DC_DIRECT_THRESHOLD")(dict(part, wt1_D=0, wt2_D=1), True, cfg(WT_DC_DIRECT_THRESHOLD=35, WT_DC_DIRECT_HTF_GATE="4h_d")) is False
    assert get("WT_DC_DIRECT_THRESHOLD")(dict(full_long, wt_cross_1h=1), True, cfg(WT_DC_DIRECT_THRESHOLD=100)) is True  # numeric cross
    full_short = {"wt1_D": 0, "wt2_D": 1, "wt1_4h": 0, "wt2_4h": 1, "wt_cross_1h": "BEAR",
                  "dc_position_1h": 0.7, "stoch_k_5m": 80}
    assert get("WT_DC_DIRECT_THRESHOLD")(full_short, False, cfg(WT_DC_DIRECT_THRESHOLD=100)) is True
    # stoch gate short: gate_k > 100-sg → 80 > 20 True → eligible stays True; flip with low k:
    assert get("WT_DC_DIRECT_THRESHOLD")(full_short, False, cfg(WT_DC_DIRECT_THRESHOLD=100, WT_DC_DIRECT_COMBINED_STOCH_GATE=80)) is True
    assert get("WT_DC_DIRECT_THRESHOLD")(dict(full_short, stoch_k_5m=10), False, cfg(WT_DC_DIRECT_THRESHOLD=60, WT_DC_DIRECT_COMBINED_STOCH_GATE=80)) is False


def test_agreement_misc_randomized():
    rng = random.Random(4242)
    for i in range(300):
        is_long = rng.random() < 0.5
        # vel decay exact formula
        v1, v1p, v3, thr = rng.uniform(-5, 5), rng.uniform(-5, 5), rng.uniform(-5, 5), rng.choice([0.5, 1.0, 2.0])
        ind = {"wt_velocity_1h": v1, "wt_velocity_1h_prev": v1p, "wt_velocity_3m": v3}
        c = {"WT_VEL_DECAY_THRESHOLD": thr}
        if is_long:
            exp = (v1p > thr * 2) and (v1 < thr) and (v3 < v1p * 0.5)
        else:
            exp = (v1p < -thr * 2) and (v1 > -thr) and (v3 > v1p * 0.5)
        assert get("WT_VEL_DECAY_THRESHOLD")(ind, is_long, c) == exp
        # k-zone / strength / htf exact formulas
        k = rng.uniform(0, 100)
        kc = {"K_ZONE_ENTRY_ENABLED": True}
        assert get("TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER")({"k_3m": k}, True, kc) == (k < 35)
        assert get("TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER")({"k_3m": k}, False, kc) == (k > 65)
        a, b = rng.uniform(-50, 50), rng.uniform(-50, 50)
        assert get("STRENGTH_MIN_SCORE")({"wt1_1h": a, "wt2_1h": b}, is_long, {}) == (abs(a - b) >= 4.0)
        # win trail exact formula
        peak, live, e = rng.uniform(0, 20), rng.uniform(-5, 20), rng.choice([0.05, 0.1, 0.25])
        assert get("WIN_TRAIL_EROSION_PCT")({"peak_pnl_pct": peak, "live_pnl_pct": live}, is_long, {"WIN_TRAIL_EROSION_PCT": e}) == ((peak - live) >= peak * e if peak > 0 else False)
        # vwap exact formula
        cl, vw = rng.uniform(50, 150), rng.uniform(50, 150)
        vc = {"VWAP_FILTER_ENABLED": True}
        assert get("VWAP_FILTER_ENABLED")({"close": cl, "vwap_D": vw}, True, vc) == (cl > vw)
        assert get("VWAP_FILTER_ENABLED")({"close": cl, "vwap_D": vw}, False, vc) == (cl < vw)
        # sma dist exact formula
        sma = rng.uniform(50, 150)
        close = rng.uniform(50, 150)
        dist = (close - sma) / sma * 100.0
        sc = {"SMA200_DIST_ENTRY_ENABLED": True}
        assert get("SMA200_DIST_LONG_THRESHOLD")({"close": close, "sma_200_1h": sma}, True, sc) == (dist < -3.0)
        assert get("SMA200_DIST_LONG_THRESHOLD")({"close": close, "sma_200_1h": sma}, False, sc) == (dist > 3.0)


def test_cfg_object_and_mapping_both_work():
    ind = {"rsi_15m": 10, "stoch_k_15m": 10, "mfi_15m": 10, "bb_pct_b_1h": 0.1, "ha_15m": "red"}
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind, True, SimpleNamespace()) is True
    assert get("SATOSHIT_MIN_VOTES_TRADIER")(ind, True, SimpleNamespace(SATOSHIT_MIN_VOTES_TRADIER=6)) is False
    assert get("WT_DC_DIRECT_THRESHOLD")({}, True, SimpleNamespace()) is True
    # determinism: same inputs → same outputs twice
    ind2 = {"wt1_D": 1, "wt2_D": 0, "wt1_4h": 1, "wt2_4h": 0, "wt_cross_1h": "BULL", "dc_position_1h": 0.3, "stoch_k_5m": 20}
    c2 = cfg(WT_DC_DIRECT_THRESHOLD=60)
    assert get("WT_DC_DIRECT_THRESHOLD")(ind2, True, c2) == get("WT_DC_DIRECT_THRESHOLD")(dict(ind2), True, dict(c2))
