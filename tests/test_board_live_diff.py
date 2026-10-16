"""ONE comparator decides "live matches its board": live_twins.vec_exact.board_live_diff.

The live ENTRY gate (latest_board_ok) and the go-live applier's already-live skip
(tools/v15_persym_golive.py) used to answer that question differently — the applier compared the live
`overrides` COLUMN against its own diff-only key set and called 139 sym_sides already-live while the gate
read them as LIVE_SET_NE_BOARD, so a freshly published board was never applied AND every crypto ENTRY for
the sym_side was refused (measured 2026-10-16 on persym_golive_20261016_refresh_1238).

A board key that exists on NO live surface is not divergence either: 104 of the 434 published boards were
refused purely over four deleted switches (REENTRY_TIER2_MAX_MINUTES_TRADIER on 111 boards,
REENTRY_TIER2_MAX_MINUTES, DC_BREAKOUT_OVERBOUGHT_K15M_CAP, WT_DC_DETAILED_TF) that live can neither store
nor read — the same treatment MAX_AUGMENTS_PER_POSITION already gets from _LB_SKIP.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from live_twins import vec_exact as vx


def test_dead_board_keys_are_not_divergence():
    for key in ("REENTRY_TIER2_MAX_MINUTES_TRADIER", "REENTRY_TIER2_MAX_MINUTES", "DC_BREAKOUT_OVERBOUGHT_K15M_CAP", "WT_DC_DETAILED_TF"):
        assert vx.board_key_is_live(key, "STOCKS_LONG") is False, key
        assert vx.board_key_is_live(key, "CRYPTO_LONG") is False, key


def test_wired_switches_are_live_keys():
    for key in ("DAYTRADE_DC_TARGET_TF", "EXIT_R1_R2_FILTER_TF", "MOMENTUM_BREAKOUT_FILTER_TF", "PARTIAL_PROFIT_LOCK_FRAC"):
        assert vx.board_key_is_live(key, "CRYPTO_LONG") is True, key
        assert vx.board_key_is_live(key, "STOCKS_SHORT") is True, key


def test_board_live_diff_reports_only_real_divergence():
    board = {
        "DAYTRADE_DC_TARGET_TF": "4h",                  # differs -> reported
        "PARTIAL_PROFIT_LOCK_FRAC": 1.0,                # equal -> not reported
        "REENTRY_TIER2_MAX_MINUTES_TRADIER": 120,       # dead key, live cannot carry it -> not reported
        "MAX_AUGMENTS_PER_POSITION": 3,                 # _LB_SKIP -> not reported
        "ABLATION_SOMETHING": True,                     # engine artefact -> not reported
    }
    live = {"DAYTRADE_DC_TARGET_TF": "OFF", "PARTIAL_PROFIT_LOCK_FRAC": 1.0}
    assert vx.board_live_diff("XRPUSDC", "LONG", board, live=live) == ["DAYTRADE_DC_TARGET_TF"]


def test_board_live_diff_normalises_bools_and_numbers():
    board = {"EXIT_VELOCITY_WT_ENABLED": True, "GOLDEN_RULE_BASE_USD": 10}
    live = {"EXIT_VELOCITY_WT_ENABLED": "true", "GOLDEN_RULE_BASE_USD": 10.0}
    assert vx.board_live_diff("AAPL", "LONG", board, live=live) == []


def test_missing_live_key_is_divergence():
    board = {"GOLDEN_RULE_BASE_USD": 10.0}
    assert vx.board_live_diff("AAPL", "LONG", board, live={}) == ["GOLDEN_RULE_BASE_USD"]


def test_dead_key_skip_is_reversible(monkeypatch):
    """LIVE_BOARD_DEAD_KEY_SKIP=0 restores the pre-2026-10-16 all-keys comparison."""
    vx._LB_LIVEKEY.clear()
    monkeypatch.setenv("LIVE_BOARD_DEAD_KEY_SKIP", "0")
    board = {"REENTRY_TIER2_MAX_MINUTES_TRADIER": 120}
    assert vx.board_live_diff("AAPL", "LONG", board, live={}) == ["REENTRY_TIER2_MAX_MINUTES_TRADIER"]
    monkeypatch.delenv("LIVE_BOARD_DEAD_KEY_SKIP")
    vx._LB_LIVEKEY.clear()
    assert vx.board_live_diff("AAPL", "LONG", board, live={}) == []


def test_cat_side_is_derived_from_the_symbol():
    assert vx._lb_cat_side("XRPUSDC", "LONG") == "CRYPTO_LONG"
    assert vx._lb_cat_side("AAPL", "SHORT") == "STOCKS_SHORT"


def test_applier_and_gate_share_the_comparator():
    """The go-live no-op skip must call board_live_diff, or the two can drift apart again."""
    src = (ROOT / "tools" / "v15_persym_golive.py").read_text()
    assert "board_live_diff" in src
    assert "V15_GOLIVE_NOOP_BOARD_CHECK" in src
