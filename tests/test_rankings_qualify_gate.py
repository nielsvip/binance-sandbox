"""The qualification gate and the scheduler priority it feeds (USER 2026-10-17).

Pins the four properties the gate exists to guarantee, all of which were violated
in production on 2026-10-17: side exclusivity, a cap that injections cannot outrun,
tradeability as a hard filter, and plots/scheduler reading the same qualified list.
"""

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import rankings_qualify as Q  # noqa: E402
import v15_ranking_priority as RP  # noqa: E402


def test_symbol_never_lands_on_both_sides():
    result = Q.qualify("crypto", ["AXSUSDT", "NEARUSDC"], ["AXSUSDT", "MINAUSDT"], scores={"AXSUSDT": -40.0, "NEARUSDC": 80.0, "MINAUSDT": -90.0})
    assert Q.symbols(result, "long") == ["NEARUSDC"]
    assert Q.symbols(result, "short") == ["AXSUSDT", "MINAUSDT"]
    assert result["dropped"]["conflict"] == ["AXSUSDT_LONG"]


def test_conflict_goes_to_the_stronger_score():
    result = Q.qualify("stocks", ["NVDA"], ["NVDA"], scores={"NVDA": 55.0})
    assert Q.symbols(result, "long") == ["NVDA"]
    assert Q.symbols(result, "short") == []


def test_dead_heat_drops_from_both():
    result = Q.qualify("stocks", ["TIE"], ["TIE"], scores={"TIE": 0.0})
    assert Q.symbols(result, "long") == []
    assert Q.symbols(result, "short") == []


def test_cap_holds_against_late_injections():
    injected = [f"S{i}USDT" for i in range(40)]
    result = Q.qualify("crypto", injected, ["X1USDT"], scores={}, cap=25)
    assert len(Q.symbols(result, "long")) == 25
    assert len(result["dropped"]["over_cap"]) == 15


def test_tradeability_is_a_hard_filter():
    result = Q.qualify("crypto", ["GOODUSDT", "DEADUSDT"], [], tradeable=lambda s, side: s != "DEADUSDT")
    assert Q.symbols(result, "long") == ["GOODUSDT"]
    assert result["dropped"]["not_tradeable"] == ["DEADUSDT_LONG"]


def test_plot_queue_is_the_qualified_order():
    result = Q.qualify("crypto", ["A", "B", "C"], ["X", "Y"], plot_n=2)
    assert Q.plot_queue(result) == [("W01", "A"), ("W02", "B"), ("L01", "X"), ("L02", "Y")]


def test_sym_sides_round_trip():
    result = Q.qualify("stocks", ["NVDA"], ["TSLA"])
    assert sorted(Q.sym_sides(result)) == ["NVDA_LONG", "TSLA_SHORT"]


def _write_tree(root, crypto_artifact=None, trb_long=None, trb_short=None, ang_long=None):
    (root / "data").mkdir(parents=True, exist_ok=True)
    if crypto_artifact is not None:
        (root / "data" / "rankings_qualified_crypto.json").write_text(json.dumps(crypto_artifact))
    for name, payload in (("symbols_trb_long.json", trb_long), ("symbols_trb_short.json", trb_short), ("symbols_ang_long.json", ang_long)):
        if payload is not None:
            (root / name).write_text(json.dumps(payload))


def test_each_venue_resolves_its_own_source(tmp_path):
    # A fresh crypto artifact must not stop stocks from falling back to symbols_trb_*.json.
    _write_tree(
        tmp_path,
        crypto_artifact={"long": [{"symbol": "AAAUSDT", "rank": 1}], "short": [{"symbol": "BBBUSDT", "rank": 1}]},
        trb_long=["NVDA"],
        trb_short=["TSLA"],
        ang_long=["IGNOREDUSDT"],
    )
    sets = RP.load_ranking_sets(root=tmp_path)
    assert sets["source"] == "crypto:qualified+stocks:symbol_lists"
    assert sets["long"] == frozenset({"AAAUSDT_LONG", "NVDA_LONG"})
    assert sets["short"] == frozenset({"BBBUSDT_SHORT", "TSLA_SHORT"})


def test_priority_fails_open_when_every_source_is_stale(tmp_path, monkeypatch):
    _write_tree(tmp_path, trb_long=["NVDA"], trb_short=["TSLA"])
    import os

    for path in (tmp_path / "symbols_trb_long.json", tmp_path / "symbols_trb_short.json"):
        os.utime(path, (0, 0))
    sets = RP.load_ranking_sets(root=tmp_path)
    assert sets["long"] == frozenset()
    assert sets["short"] == frozenset()
    assert sets["source"] == "none"


def test_kill_switch_disables_everything(monkeypatch):
    monkeypatch.setenv("V15_RANKING_PRIORITY", "0")
    RP.reset_cache()
    sets = RP.ranking_sets()
    assert sets["source"] == "disabled"
    assert not sets["pairs"]
    assert RP.ranking_boost("NVDA_LONG") == 1.0
    RP.reset_cache()


def test_refresh_window_is_daily_by_default(monkeypatch):
    monkeypatch.delenv("V15_RANKING_REFRESH_H", raising=False)
    assert RP.refresh_window_s() == 24 * 3600.0
    monkeypatch.setenv("V15_RANKING_REFRESH_H", "6")
    assert RP.refresh_window_s() == 6 * 3600.0


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
