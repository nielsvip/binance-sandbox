"""USER 2026-10-13: vector must never fire crypto augment pathways (PULLBACK_AUG / UAG gain-ladder)
on a STOCKS (tradier) position — tradier live has none of those sources (PULLBACK_AUG exists only in
ez_manage crypto). Proof case SNDK_LONG: MAXAUG=999999 with crypto augments = -0.50%; same set with
STOCK_LIVE_AUGMENT_ONLY = +40.49%, 0 augments, identical to MAXAUG=0. The evaluation wrapper forces
the live-sources flag for tradier so sheets, golive evidence and repairs all measure the live truth."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
from tools.opt import v12_pilot as VP


def test_tradier_prepared_forces_live_augment_sources(monkeypatch):
    monkeypatch.delenv("V12_STOCK_LIVE_AUGMENT_GUARD", raising=False)
    out = VP._venue_augment_guard({"mode": "tradier", "symside": "SNDK_LONG"}, {"MAX_AUGMENTS_PER_POSITION": 0})
    assert out["STOCK_LIVE_AUGMENT_ONLY"] is True
    out2 = VP._venue_augment_guard({"mode": "tradier"}, {"STOCK_LIVE_AUGMENT_ONLY": False})
    assert out2["STOCK_LIVE_AUGMENT_ONLY"] is True  # a swept False can never re-enable crypto augments on stocks


def test_crypto_untouched_and_kill_switch(monkeypatch):
    monkeypatch.delenv("V12_STOCK_LIVE_AUGMENT_GUARD", raising=False)
    out = VP._venue_augment_guard({"mode": "crypto", "symside": "BTCUSDT_LONG"}, {})
    assert "STOCK_LIVE_AUGMENT_ONLY" not in out
    monkeypatch.setenv("V12_STOCK_LIVE_AUGMENT_GUARD", "0")
    out = VP._venue_augment_guard({"mode": "tradier"}, {})
    assert "STOCK_LIVE_AUGMENT_ONLY" not in out
