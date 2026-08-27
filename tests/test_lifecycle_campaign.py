from __future__ import annotations

import json

from tools.opt import lifecycle_campaign as C


def test_torn_symbol_file_recovers_quoted_order(tmp_path, monkeypatch):
    (tmp_path / "symbols_flz.json").write_text('["BTCUSDC" "ETHUSDC", "BTCUSDC"]')
    monkeypatch.setattr(C, "ROOT", tmp_path)
    assert C._symbols("symbols_flz.json") == ["BTCUSDC", "ETHUSDC"]


def test_campaign_starts_flz_pairs_then_recent_trb(tmp_path, monkeypatch):
    files = {
        "symbols_flz.json": ["BTCUSDC"],
        "symbols_trb_long.json": ["MU"],
        "symbols_trb_short.json": ["NVDA"],
        "symbols_men_long.json": [], "symbols_men_short.json": [],
        "symbols_fin.json": [], "symbols_ang_long.json": [], "symbols_ang_short.json": [],
        "symbols_tradier.json": ["MU", "NVDA"],
    }
    for name, values in files.items():
        (tmp_path / name).write_text(json.dumps(values))
    monkeypatch.setattr(C, "ROOT", tmp_path)
    monkeypatch.setattr(C.pilot, "load_live_recipes", lambda: {
        "BTCUSDC_LONG": {}, "BTCUSDC_SHORT": {}, "MU_LONG": {}, "NVDA_SHORT": {}})
    monkeypatch.setattr(C, "_performance", lambda: ({
        "MU": {"trade_count": 5, "accounts": ["trb"]},
        "NVDA": {"trade_count": 4, "accounts": ["trb"]},
    }, "now"))
    monkeypatch.setattr(C, "_recent_trb_sides", lambda: {"NVDA_SHORT": 99.0})
    result = C.campaign_order()
    assert [row["symside"] for row in result["queue"]] == [
        "BTCUSDC_LONG", "BTCUSDC_SHORT", "NVDA_SHORT", "MU_LONG"]
    assert result["queue"][2]["phase"] == "02_TRB_ACTIVE_30D"
