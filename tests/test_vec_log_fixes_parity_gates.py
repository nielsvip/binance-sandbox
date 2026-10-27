"""VEC_DICTATE_ONLY_BLOCK fixes 2026-10-27 (vec_log_fixes): E_1 / E_3 board switches,
and the no-board NON_TRADEABLE exit.

Reasons are copied from data/vector_disparity_queue.jsonl records of 2026-10-26/27.
"""
import pytest

import per_sym_store
from live_twins import board_authority
from vec_decisions import vec_parity_gates as g


@pytest.fixture
def per_sym(monkeypatch):
    table = {}
    monkeypatch.setattr(per_sym_store, "get_overrides", lambda ss: table.get(ss, {}))
    return table


E3 = "E_3_STRUCTURE_EXIT_2TF_15m=LH_1h=LH_g=-0.42%"
E1 = "E_1_WT_DELTA_EXIT_delta=-66_thr=50_g=0.31%"


@pytest.mark.parametrize("mode,expected", [(2, True), (2.0, True), ("2", True), (1, False), (0, False), (0.0, False)])
def test_e3_passes_only_at_live_mode_2(per_sym, mode, expected):
    per_sym["GRASSUSDT_LONG"] = {"E_3_USE_WT_STRUCTURE_EXIT_MODE": mode}
    assert g.dictate_only_exempt("QUICK_CLOSE", E3, sym_side="GRASSUSDT_LONG") is expected


def test_e3_without_sym_side_stays_blocked(per_sym):
    assert g.dictate_only_exempt("QUICK_CLOSE", E3) is False


@pytest.mark.parametrize("on,expected", [(True, True), (False, False)])
def test_e1_follows_its_board_switch(per_sym, on, expected):
    per_sym["STXUSDT_SHORT"] = {"E_1_WT_EXIT_USE_DELTA_ENABLED": on}
    assert g.dictate_only_exempt("QUICK_CLOSE", E1, sym_side="STXUSDT_SHORT") is expected


def test_ratio_close_side_reduce_stays_under_law():
    # Left as-is on purpose: tests/test_vec_parity_switches.py pins USER 2026-10-25
    # "RATIO trims lost their exemption" for RATIO_CLOSE_<SIDE>; operator ruling pending.
    assert g.dictate_only_exempt("REDUCE", "RATIO_CLOSE_LONG_L52_S48_tgt30/70_gain0.1%") is False


NT = "NON_TRADEABLE_WT15M_EXIT_SHORT_wt6.9_g1.96_qty_999999"


@pytest.mark.parametrize("state,expected", [("UNKNOWN", True), ("HOLDS", False), ("FLAT", False)])
def test_non_tradeable_exit_released_only_without_board(per_sym, monkeypatch, state, expected):
    monkeypatch.setattr(board_authority, "board_state", lambda pk, *a, **k: state)
    assert g.dictate_only_exempt("CLOSE", NT, sym_side="BABA_SHORT") is expected


def test_non_tradeable_never_releases_an_open(per_sym, monkeypatch):
    monkeypatch.setattr(board_authority, "board_state", lambda pk, *a, **k: "UNKNOWN")
    assert g.dictate_only_exempt("OPEN", NT, sym_side="BABA_SHORT") is False


def test_unrelated_native_exit_still_blocked(per_sym, monkeypatch):
    monkeypatch.setattr(board_authority, "board_state", lambda pk, *a, **k: "UNKNOWN")
    per_sym["GRASSUSDT_LONG"] = {"E_3_USE_WT_STRUCTURE_EXIT_MODE": 2, "E_1_WT_EXIT_USE_DELTA_ENABLED": True}
    assert g.dictate_only_exempt("CLOSE", "IN_GAIN_TREND_EXIT", sym_side="GRASSUSDT_LONG") is False
    assert g.dictate_only_exempt("CLOSE", "DELTA_EXIT_DC15M_FLOOR_BREAK", sym_side="GRASSUSDT_LONG") is False
