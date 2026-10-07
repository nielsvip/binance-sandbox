"""PARITY LANE C 2026-10-06 — _cfg_auto side-key bug + director ruling "promotions only".

Before: execute_trade_action / execute_now locals side='BUY'|'SELL' -> per-sym rows missed and cat_side_of('BUY') = STOCKS_SHORT
for LONG positions. After: first LONG/SHORT among side/position_side/pos_side/positionSide, else parsed position_key, else None.
Frames whose legacy side was already LONG/SHORT (or absent / unrepairable) keep the historical _cfg call (byte-identical). Frames
REPAIRED from BUY/SELL resolve "cat" mode (cat_side of the true side > global = today's values; STOCKS_LONG==STOCKS_SHORT for all 83
affected keys) unless config CFG_AUTO_REPAIRED_SIDE_PERSYM_ENABLED=True -> per-sym PROMOTIONS > cat_side > global. Never the snapshot.
"""
import os
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


@pytest.fixture(scope="module")
def T():
    import tradier_manage
    return tradier_manage


def _capture(T, monkeypatch):
    seen = {}
    def fake(p, d=None, a=None, s=None, sd=None, _promotions_only=False):
        seen["args"] = (p, a, s, sd, _promotions_only)
        return d
    monkeypatch.setattr(T, "_cfg", fake)
    return seen


def test_buy_sell_uses_position_side_promotions_only(T, monkeypatch):
    seen = _capture(T, monkeypatch)
    def execute_now(position_key, account_key, symbol, side, position_side):
        return T._cfg_auto("PROBE", 1)
    execute_now("trb:AAPL_LONG", "trb", "AAPL", "SELL", "LONG")
    assert seen["args"] == ("PROBE", "trb", "AAPL", "LONG", "cat")
    monkeypatch.setattr(T.config, "CFG_AUTO_REPAIRED_SIDE_PERSYM_ENABLED", True, raising=False)
    execute_now("trb:AAPL_LONG", "trb", "AAPL", "SELL", "LONG")
    assert seen["args"] == ("PROBE", "trb", "AAPL", "LONG", True)


def test_position_key_fallback(T, monkeypatch):
    seen = _capture(T, monkeypatch)
    def handle_order(position_key, account_key, symbol, side):
        return T._cfg_auto("PROBE", 1)
    handle_order("trb:MSFT_SHORT", "trb", "MSFT", "BUY")
    assert seen["args"][3:] == ("SHORT", "cat")


def test_legacy_long_short_byte_identical(T, monkeypatch):
    seen = _capture(T, monkeypatch)
    def f(account_key, symbol, side):
        return T._cfg_auto("PROBE", 1)
    f("trb", "NVDA", "LONG")
    assert seen["args"][3:] == ("LONG", False)


def test_unresolvable_keeps_legacy_call(T, monkeypatch):
    seen = _capture(T, monkeypatch)
    def place_order(symbol, side):
        return T._cfg_auto("PROBE", 1)
    place_order("AAPL", "BUY")
    assert seen["args"][3:] == ("BUY", False)


def test_cat_mode_skips_all_per_sym(T, monkeypatch):
    import per_sym_store as pss
    monkeypatch.setattr(pss, "get_full_config", lambda ss: {"KX": "snap"})
    monkeypatch.setattr(pss, "get_overrides", lambda ss: {"KX": "promo"})
    monkeypatch.setattr(T, "_v8_sweep_override", lambda p: (False, None))
    monkeypatch.setattr(T, "_load_tradier_per_sym_cfgs", lambda path: {"ZZZTEST_LONG": {"KX": "ac"}})
    assert T._cfg("KX", "dflt", "trb", "ZZZTEST", "LONG", _promotions_only="cat") == "dflt"


def test_promotions_only_skips_snapshot(T, monkeypatch):
    import per_sym_store as pss
    monkeypatch.setattr(pss, "get_full_config", lambda ss: {"UNIVERSAL_NOLOSS_GATE": True, "PROMO_X": "snap"})
    monkeypatch.setattr(pss, "get_overrides", lambda ss: {"PROMO_X": "promo"})
    monkeypatch.setattr(T, "_v8_sweep_override", lambda p: (False, None))
    monkeypatch.setattr(T, "_load_full_recipe_live_cfgs", lambda: {})
    monkeypatch.setattr(T, "_tradier_final_book_get", lambda k, p: None)
    monkeypatch.setattr(T, "_load_tradier_per_sym_cfgs", lambda path: {})
    monkeypatch.setattr(T, "_load_global_per_sym_cfgs", lambda: {})
    assert T._cfg("UNIVERSAL_NOLOSS_GATE", "dflt", "trb", "ZZZTEST", "LONG") is True          # legacy precedence: snapshot wins
    assert T._cfg_ps("UNIVERSAL_NOLOSS_GATE", "dflt", "trb", "ZZZTEST", "LONG") != True        # promotions-only: snapshot ignored
    assert T._cfg_ps("PROMO_X", "dflt", "trb", "ZZZTEST", "LONG") == "promo"
