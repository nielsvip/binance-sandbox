"""Data-freshness takeover kinds (USER 2026-10-19: S1 takes over when the MacBook is
not trading OR has old indicators; positions information included).

2026-10-19 proved the gap: 160/178 stock NPZs Friday-frozen all morning -> 285
VEC_EXACT_STALE_FALLBACK, one vec open all day, while every existing kind stayed
quiet (engines alive, broker reachable, orders flowing out). These kinds are the
Mac-measured, S1-decided tripwire for exactly that class.

Offline: tmp BASE + tiny NPZs, no network, no agents.
"""
import json
import os
import sys

import pytest

np = pytest.importorskip("numpy")
sys.path.insert(0, "/Users/niels/Documents/binance/tools")
import mac_failover_agent as mfa

T0 = 1_800_000_000.0
SYMS = [f"TST{i:02d}" for i in range(12)]


def _mkbase(tmp_path, npz_age_s, state_age_s):
    base = tmp_path / "b"
    (base / "backtest_v8" / "indicators").mkdir(parents=True, exist_ok=True)
    with open(base / "symbols_trb_long.json", "w") as fh:
        json.dump({"symbols": SYMS}, fh)
    for s in SYMS:
        p = base / "backtest_v8" / "indicators" / f"{s}.npz"
        np.savez(str(p), timestamps=np.array([T0 - 90000.0, T0 - npz_age_s]))
        os.utime(p, (T0, T0))
    for a in ("tra", "trb", "trc"):
        (base / a).mkdir(exist_ok=True)
        p = base / a / "state.json"
        p.write_text("{}")
        os.utime(p, (T0, T0 - state_age_s))
    for a in ("ang", "inf", "flz", "men", "fin"):
        (base / a).mkdir(exist_ok=True)
        p = base / a / "long_positions.json"
        p.write_text("{}")
        os.utime(p, (T0, T0 - state_age_s))
    return base


def _agent(monkeypatch, base):
    monkeypatch.setattr(mfa, "BASE", base)
    ag = object.__new__(mfa.Agent)
    ag._npz_bar = {}
    return ag


def _health(applicable=True):
    return {"stocks": {"applicable": applicable, "kinds": [], "reasons": []},
            "crypto": {"applicable": True, "kinds": [], "reasons": []}}


def test_fresh_everything_no_kinds(tmp_path, monkeypatch):
    ag = _agent(monkeypatch, _mkbase(tmp_path, 900.0, 30.0))
    h = _health()
    ag.augment_data_freshness(h, T0, ())
    assert h["stocks"]["kinds"] == [], h["stocks"]
    assert h["crypto"]["kinds"] == [], h["crypto"]
    assert h["stocks"]["indicators_universe_n"] == 12


def test_stale_npz_majority_fires(tmp_path, monkeypatch):
    ag = _agent(monkeypatch, _mkbase(tmp_path, 20000.0, 30.0))
    h = _health()
    ag.augment_data_freshness(h, T0, ())
    assert "indicators_stale" in h["stocks"]["kinds"], h["stocks"]
    assert any("twin falls back" in r for r in h["stocks"]["reasons"])
    assert h["stocks"]["not_trading"] is True


def test_stale_minority_quiet(tmp_path, monkeypatch):
    base = _mkbase(tmp_path, 900.0, 30.0)
    for s in SYMS[:3]:
        p = base / "backtest_v8" / "indicators" / f"{s}.npz"
        np.savez(str(p), timestamps=np.array([T0 - 90000.0, T0 - 20000.0]))
        os.utime(p, (T0, T0))
    ag = _agent(monkeypatch, base)
    h = _health()
    ag.augment_data_freshness(h, T0, ())
    assert "indicators_stale" not in h["stocks"]["kinds"], h["stocks"]


def test_wedged_position_loop_fires(tmp_path, monkeypatch):
    ag = _agent(monkeypatch, _mkbase(tmp_path, 900.0, 3600.0))
    h = _health()
    ag.augment_data_freshness(h, T0, ())
    assert "positions_stale" in h["stocks"]["kinds"], h["stocks"]
    assert "positions_stale" in h["crypto"]["kinds"], h["crypto"]


def test_fenced_and_closed_silent(tmp_path, monkeypatch):
    ag = _agent(monkeypatch, _mkbase(tmp_path, 20000.0, 3600.0))
    h = _health()
    ag.augment_data_freshness(h, T0, ("stocks", "crypto"))
    assert h["stocks"]["kinds"] == [] and h["crypto"]["kinds"] == []
    h = _health(applicable=False)
    ag2 = _agent(monkeypatch, _mkbase(tmp_path, 20000.0, 30.0))
    ag2.augment_data_freshness(h, T0, ())
    assert "indicators_stale" not in h["stocks"]["kinds"]


def test_s1_honours_new_kinds():
    import live_failover_s1 as s1
    assert s1.NT_NEED["indicators_stale"] == float(os.environ.get("LF_NT_INDICATORS_S", "0"))
    assert s1.NT_NEED["positions_stale"] == float(os.environ.get("LF_NT_POSITIONS_S", "0"))
    assert set(s1.NT_NEED) >= {"broker_unreachable", "order_path_dead", "traders_dead",
                               "account_dead", "indicators_stale", "positions_stale"}


def _sacct(alive=1, dead=False, bad=False):
    return {"trader_alive": alive, "order_path_dead": dead, "broker_unreachable": bad,
            "orders_submitted_window": 0, "last_order_submitted_age_s": None}


def test_single_stock_account_order_dead_takes_venue():
    import live_failover_s1 as s1
    acc = {"trb": _sacct(dead=True), "trc": _sacct(), "tra": _sacct()}
    kinds, reasons, named = s1.stock_single_dead(acc, {}, T0)
    assert kinds == ["stock_account_dead"] and named == ["trb"], (kinds, named)
    assert "trb" in reasons[0] and "trc" in reasons[0]


def test_all_stock_accounts_dead_is_not_single():
    import live_failover_s1 as s1
    acc = {"trb": _sacct(dead=True), "trc": _sacct(dead=True), "tra": _sacct(dead=True)}
    kinds, _, named = s1.stock_single_dead(acc, {}, T0)
    assert kinds == [] and named == [], (kinds, named)


def test_vanished_engine_needs_dwell_then_fires():
    import live_failover_s1 as s1
    acc = {"trb": _sacct(alive=0), "trc": _sacct(), "tra": _sacct()}
    ms = {}
    kinds, _, _ = s1.stock_single_dead(acc, ms, T0)
    assert kinds == [], kinds
    kinds, reasons, named = s1.stock_single_dead(acc, ms, T0 + 241)
    assert kinds == ["stock_account_dead"] and named == ["trb"], (kinds, named)


def test_vanished_engine_recovers_and_restarts_timer():
    import live_failover_s1 as s1
    acc = {"trb": _sacct(alive=0), "trc": _sacct(), "tra": _sacct()}
    ms = {}
    s1.stock_single_dead(acc, ms, T0)
    whole = {"trb": _sacct(), "trc": _sacct(), "tra": _sacct()}
    kinds, _, _ = s1.stock_single_dead(whole, ms, T0 + 100)
    assert kinds == [] and ms == {}, (kinds, ms)
    kinds, _, _ = s1.stock_single_dead(acc, ms, T0 + 200)
    assert kinds == [], kinds


def test_all_vanished_is_not_single():
    import live_failover_s1 as s1
    acc = {a: _sacct(alive=0) for a in ("trb", "trc", "tra")}
    kinds, _, _ = s1.stock_single_dead(acc, {}, T0 + 9999)
    assert kinds == [], kinds
