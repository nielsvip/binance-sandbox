import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("vdt", ROOT / "tools" / "vector_direct_trader.py")
V = importlib.util.module_from_spec(spec)
sys.modules["vdt"] = V
spec.loader.exec_module(V)

BAR = 1793132100
ALLOW = lambda acct, ss: True
DENY = lambda acct, ss: False


def st(open_, acts=(), eq=0.0, oq=0.0, tail=BAR, ok=True):
    return {"ok": ok, "open": open_, "npz_tail": tail, "acts_bar_ts": tail, "acts_last_bar": list(acts),
            "engine_qty": eq, "engine_open_qty": oq, "engine_open_bar_ts": tail - 900 * 3, "ts": "2026-10-27T20:31:00Z"}


def test_board_flat_broker_holds_closes_all():
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(False)}, {"XUSDT_LONG": {"qty": 12.0}}, BAR, set(), ALLOW)
    assert [(o["act"], o["qty"]) for o in orders] == [("CLOSE", 12.0)]


def test_board_holds_broker_flat_opens_scaled_and_respects_books():
    s = {"XUSDT_LONG": st(True, eq=75.0, oq=100.0)}
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], s, {}, BAR, set(), ALLOW)
    assert orders[0]["act"] == "OPEN" and abs(orders[0]["notional"] - V.START_POSITION_SIZE * 0.75) < 1e-9
    orders, notes = V.plan_account("ang", "crypto", ["XUSDT_LONG"], s, {}, BAR, set(), DENY)
    assert orders == [] and notes == [("XUSDT_LONG", "SKIP_OPEN_NOT_IN_BOOKS")]


def test_close_allowed_even_when_not_in_books_and_adopts_unlisted_position():
    orders, _ = V.plan_account("ang", "crypto", [], {"YUSDT_SHORT": st(False)}, {"YUSDT_SHORT": {"qty": 3.0}}, BAR, set(), DENY)
    assert [o["act"] for o in orders] == ["CLOSE"]


def test_augment_and_reduce_scaled_to_broker_qty():
    acts = [{"type": "REDUCE", "qty": 25.0, "qty_before": 100.0, "qty_frac": 0.25}, {"type": "AUGMENT", "qty": 50.0, "qty_before": 75.0}]
    orders, _ = V.plan_account("fin", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True, acts, 125.0, 100.0)}, {"XUSDT_LONG": {"qty": 8.0}}, BAR, set(), ALLOW)
    assert [(o["act"], round(o["qty"], 6)) for o in orders] == [("REDUCE", 2.0), ("AUGMENT", round(8.0 * 50 / 75, 6))]


def test_augment_already_done_by_mac_is_not_repeated():
    acts = [{"type": "AUGMENT", "qty": 50.0, "qty_before": 100.0}]
    orders, notes = V.plan_account("fin", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True, acts, 150.0, 100.0)}, {"XUSDT_LONG": {"qty": 12.0}}, BAR, set(), ALLOW, prev_qty={"XUSDT_LONG": 8.0})
    assert orders == [] and notes[0][1].startswith("AUGMENT_ALREADY_ON_BROKER")


def test_idempotent_and_stale_state_holds():
    s = {"XUSDT_LONG": st(False)}
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], s, {"XUSDT_LONG": {"qty": 1.0}}, BAR, {f"ang|XUSDT_LONG|{BAR}|CLOSE"}, ALLOW)
    assert orders == []
    stale = {"XUSDT_LONG": st(False, tail=BAR - 900 * 4)}
    orders, notes = V.plan_account("ang", "crypto", ["XUSDT_LONG"], stale, {"XUSDT_LONG": {"qty": 1.0}}, BAR, set(), ALLOW)
    assert orders == [] and notes == [("XUSDT_LONG", "HOLD_NO_FRESH_STATE")]
    notok = {"XUSDT_LONG": st(False, ok=False)}
    assert V.plan_account("ang", "crypto", ["XUSDT_LONG"], notok, {"XUSDT_LONG": {"qty": 1.0}}, BAR, set(), ALLOW)[0] == []


def test_expectations_only_for_acts_at_the_bar():
    s = {"A_LONG": st(True, [{"type": "OPEN", "qty": 1}]), "B_LONG": st(False, [{"type": "CLOSE", "qty": 1}]), "C_LONG": st(True)}
    exp = V.expectations_from_states(["tra"], {"tra": ["A_LONG", "B_LONG", "C_LONG"]}, s, BAR, ALLOW)
    assert sorted(exp) == [("tra", "A_LONG", "HOLD"), ("tra", "B_LONG", "FLAT")]


def hb(now, **sec):
    return {"epoch": now - 10, "trading_health": {"crypto": {"applicable": True, "kinds": [], **sec}}}


def test_activation_triggers_and_hysteresis(tmp_path):
    a = V.Activation(tmp_path / "act.json", V.CRYPTO_ACCOUNTS)
    now = 1_000_000.0
    trig, age = a.triggers(now, None, "crypto", {})
    assert set(trig) == set(V.CRYPTO_ACCOUNTS) and "absent" in trig["ang"]
    trig, _ = a.triggers(now, {"epoch": now - 500}, "crypto", {})
    assert "stale_500s" in trig["fin"]
    trig, _ = a.triggers(now, hb(now, account_dead_accounts=["inf"], accounts={"men": {"trader_alive": 1, "engine_dead_s": 400}, "ang": {"trader_alive": 1, "engine_dead_s": 0}}), "crypto", {"flz": "mac_missed_trade X"})
    assert set(trig) == {"inf", "men", "flz"}
    trig, _ = a.triggers(now, hb(now, kinds=["account_dead"]), "crypto", {})
    assert trig == {}
    ch = a.update(now, {"inf": "dead"})
    assert ch == [("inf", "TAKEOVER", "dead")] and a.active() == ["inf"]
    assert a.update(now + 100, {}) == [] and a.active() == ["inf"]
    a.update(now + V.HOLD_MIN_S - 10, {"inf": "still dead"})
    assert a.update(now + V.HOLD_MIN_S + 1, {}) == [] and a.active() == ["inf"]
    assert a.update(now + V.HOLD_MIN_S + V.RELEASE_HEALTHY_S, {}) == [] and a.active() == ["inf"]
    assert a.update(now + V.HOLD_MIN_S + 1 + V.RELEASE_HEALTHY_S, {})[0][1] == "HANDBACK" and a.active() == []
    t2 = now + V.HOLD_MIN_S + V.RELEASE_HEALTHY_S + 60
    a.update(t2, {"inf": "dead again"})
    assert a.st["inf"]["takeovers"] == 2 and a.st["inf"]["hold_s"] == 2 * V.HOLD_MIN_S
    a.save()
    b = V.Activation(tmp_path / "act.json", V.CRYPTO_ACCOUNTS)
    assert b.active() == ["inf"]


def test_named_kinds_scope_and_engine_down_persistence(tmp_path):
    a = V.Activation(tmp_path / "k.json", V.CRYPTO_ACCOUNTS)
    now = 3_000_000.0
    h = hb(now, kinds=["positions_stale", "not_trading_account"], positions_stale_accounts=["men"], not_trading_account_accounts=["flz", "inf"])
    assert set(a.triggers(now, h, "crypto", {})[0]) == {"men", "flz", "inf"}
    assert set(a.triggers(now, hb(now, kinds=["indicators_unproducible"]), "crypto", {})[0]) == set(V.CRYPTO_ACCOUNTS)
    down = hb(now, accounts={"flz": {"trader_alive": 0, "engine_dead_s": 0}})
    assert a.triggers(now, down, "crypto", {})[0] == {}
    down["epoch"] = now + V.ENGINE_DOWN_S
    assert set(a.triggers(now + V.ENGINE_DOWN_S + 1, down, "crypto", {})[0]) == {"flz"}
    up = hb(now, accounts={"flz": {"trader_alive": 1, "engine_dead_s": 0}})
    up["epoch"] = now + 200
    assert a.triggers(now + 201, up, "crypto", {})[0] == {}


def test_owned_account_ignores_per_account_signals_caused_by_standdown(tmp_path):
    a = V.Activation(tmp_path / "o.json", V.CRYPTO_ACCOUNTS)
    now = 4_000_000.0
    a.update(now, {"flz": "x"})
    h = hb(now, kinds=["not_trading_account"], not_trading_account_accounts=["flz", "inf"], account_dead_accounts=["flz"],
           accounts={"flz": {"trader_alive": 0, "engine_dead_s": 9999}})
    assert set(a.triggers(now, h, "crypto", {})[0]) == {"inf"}
    assert set(a.triggers(now, hb(now, kinds=["indicators_unproducible"]), "crypto", {})[0]) == set(V.CRYPTO_ACCOUNTS)
    assert "flz" in a.triggers(now, None, "crypto", {})[0]


def test_stock_market_closed_section_never_triggers(tmp_path):
    a = V.Activation(tmp_path / "s.json", V.STOCK_ACCOUNTS)
    now = 2_000_000.0
    h = {"epoch": now - 5, "trading_health": {"stocks": {"applicable": False, "kinds": ["traders_dead"]}}}
    assert a.triggers(now, h, "stocks", {})[0] == {}
    h["trading_health"]["stocks"] = {"applicable": True, "kinds": [], "accounts": {"trb": {"trader_alive": 1, "order_path_dead": True}, "tra": {"trader_alive": 1}}}
    assert set(a.triggers(now, h, "stocks", {})[0]) == {"trb"}


def test_binance_sizing(tmp_path):
    b = V.Binance({}, tmp_path)
    b._xi = {"XUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 5.0, "status": "TRADING"},
             "BTCUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 100.0, "status": "TRADING"},
             "BIGUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 500.0, "status": "TRADING"}}
    V.read_json = (lambda f: (lambda p, d=None: {"_fetched": 1e18} if "exchange_info" in str(p) else f(p, d)))(V.read_json)
    q, why = b.size({"ss": "XUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 2.0)
    assert q == 7.5 and why == ""
    q, _ = b.size({"ss": "BTCUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 100000.0)
    assert q * 100000.0 >= 100.0
    q, why = b.size({"ss": "BIGUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 1.0)
    assert q == 0 and "cap" in why
    q, _ = b.size({"ss": "XUSDT_LONG", "act": "REDUCE", "qty": 2.0005}, 10.0, 2.0)
    assert q == 2.0
    q, _ = b.size({"ss": "XUSDT_LONG", "act": "REDUCE", "qty": 9.9999}, 10.0, 2.0)
    assert q == 10.0
    assert b.size({"ss": "XUSDT_LONG", "act": "CLOSE"}, 3.21, 2.0)[0] == 3.21


def test_tradier_sizing_whole_shares(tmp_path):
    t = V.Tradier({}, tmp_path)
    assert t.size({"ss": "F_LONG", "act": "OPEN", "notional": 15.0}, 0, 11.0) == (1.0, "")
    assert t.size({"ss": "AAPL_LONG", "act": "OPEN", "notional": 15.0}, 0, 230.0) == (1.0, "")
    q, why = t.size({"ss": "SNDK_LONG", "act": "OPEN", "notional": 15.0}, 0, 1304.0)
    assert q == 0 and "one-share cap" in why
    assert t.size({"ss": "F_LONG", "act": "REDUCE", "qty": 0.4}, 3, 11.0)[0] == 0
    assert t.size({"ss": "F_LONG", "act": "CLOSE"}, 3, 11.0)[0] == 3.0


def test_dry_run_rows_never_count_as_done(tmp_path):
    import json, time
    class A:
        pass
    p = tmp_path / "data" / "vector_direct"
    p.mkdir(parents=True)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    (p / f"ledger_crypto_{day}.jsonl").write_text(json.dumps({"key": "k1", "final": True, "status": "DRY_RUN"}) + "\n" + json.dumps({"key": "k2", "final": True, "status": "SENT"}) + "\n"
                                                  + json.dumps({"key": "k3", "final": True, "status": "SKIP_SIZE", "dry_run": True}) + "\n")
    t = V.Trader.__new__(V.Trader)
    t.out, t.venue = p, "crypto"
    assert t._load_done() == {"k2"}


def _bare_trader(tmp_path, positions):
    import types
    p = tmp_path / "data" / "vector_direct"
    p.mkdir(parents=True, exist_ok=True)
    t = V.Trader.__new__(V.Trader)
    t.out, t.venue, t.accounts = p, "crypto", ["ang", "fin"]
    t.done, t.missed_rows, t.paper_plan, t.paper_done = set(), {}, {}, set()
    sent = []
    t.broker = types.SimpleNamespace(positions=lambda acct: dict(positions.get(acct, {})))
    t.send_orders = lambda acct, orders, pos, killed: (sent.extend((acct, o) for o in orders), len(orders))[1]
    return t, sent


def test_pin_keeps_accounts_until_expiry(tmp_path):
    import json, time
    t, _ = _bare_trader(tmp_path, {})
    now = time.time()
    (t.out / "pin_crypto.json").write_text(json.dumps({"until_epoch": now + 60, "accounts": ["ang"]}))
    assert list(t.pinned(now)) == ["ang"]
    assert t.pinned(now + 61) == {}


def test_missed_board_close_is_alerted_and_backstopped_without_takeover(tmp_path):
    t, sent = _bare_trader(tmp_path, {"ang": {"XUSDT_SHORT": {"qty": 5.0, "price": 1.0}}, "fin": {}})
    states = {"XUSDT_SHORT": st(False, acts=[{"type": "CLOSE"}]), "YUSDT_LONG": st(True, acts=[{"type": "OPEN"}], eq=2, oq=1)}
    t.missed_rows = {"ang": [{"acct": "ang", "ss": "XUSDT_SHORT", "want": "FLAT", "bar": BAR}],
                     "fin": [{"acct": "fin", "ss": "YUSDT_LONG", "want": "HOLD", "bar": BAR}]}
    n = t.backstop(0, False, {}, states, ALLOW)
    assert n == 2 and t.missed_rows == {}
    acts = {(a, o["ss"]): o for a, o in sent}
    assert acts[("ang", "XUSDT_SHORT")]["act"] == "CLOSE" and acts[("ang", "XUSDT_SHORT")]["qty"] == 5.0
    assert acts[("fin", "YUSDT_LONG")]["act"] == "OPEN" and acts[("fin", "YUSDT_LONG")]["notional"] == V.START_POSITION_SIZE * 2
    assert (t.out / "ALERTS.jsonl").read_text().count("BACKSTOP_FAILURE_MAC_MISSED_ORDER") == 2


def test_backstop_skips_when_board_moved_on_or_mac_caught_up(tmp_path):
    t, sent = _bare_trader(tmp_path, {"ang": {}})
    t.missed_rows = {"ang": [{"acct": "ang", "ss": "XUSDT_SHORT", "want": "FLAT", "bar": BAR}]}
    assert t.backstop(0, False, {}, {"XUSDT_SHORT": st(False)}, ALLOW) == 0 and not sent


def test_paper_logs_plan_for_mac_accounts_once(tmp_path):
    t, sent = _bare_trader(tmp_path, {"ang": {"XUSDT_SHORT": {"qty": 5.0, "price": 1.0}}})
    states = {"XUSDT_SHORT": st(False)}
    t.paper(1000.0, ["ang"], {"ang": {"XUSDT_SHORT"}}, states, BAR, ALLOW)
    t.paper(1000.0 + V.REPLAN_S + 1, ["ang"], {"ang": {"XUSDT_SHORT"}}, states, BAR, ALLOW)
    rows = [l for p in t.out.glob("paper_crypto_*.jsonl") for l in p.read_text().splitlines()]
    assert len(rows) == 1 and '"CLOSE"' in rows[0] and not sent


def test_held_side_outside_universe_gets_its_state_and_is_published(tmp_path):
    """2026-10-27: a held side outside tradeable_keys must still be managed (board flat -> CLOSE) and be published to
    held_<venue>.json so the S1 state producer computes it first."""
    import json
    t, sent = _bare_trader(tmp_path, {"ang": {"ZUSDT_LONG": {"qty": 3.0, "price": 1.0}}})
    t.state_dir = tmp_path / "fs"
    t.state_dir.mkdir()
    (t.state_dir / "ZUSDT_LONG.json").write_text(json.dumps(st(False)))
    states = {}
    t.paper(1000.0, ["ang"], {"ang": set()}, states, BAR, ALLOW)
    assert "ZUSDT_LONG" in states
    rows = [l for p in t.out.glob("paper_crypto_*.jsonl") for l in p.read_text().splitlines()]
    assert len(rows) == 1 and '"CLOSE"' in rows[0] and '"ZUSDT_LONG"' in rows[0]
    held = json.loads((t.out / "held_crypto.json").read_text())
    assert held["sides"] == ["ZUSDT_LONG"] and held["by_account"] == {"ang": ["ZUSDT_LONG"]}


def test_non_tradeable_held_closes_when_wt15_goes_against():
    """USER 2026-10-27 "non tradeable keys get closes as soon as wt_15m goes against it"."""
    pos = {"NTUSDT_LONG": {"qty": 4.0, "price": 1.0}, "NSUSDT_SHORT": {"qty": 2.0, "price": 1.0}, "OKUSDT_LONG": {"qty": 1.0, "price": 1.0}}
    states = {"NTUSDT_LONG": st(True), "NSUSDT_SHORT": st(True), "OKUSDT_LONG": st(True)}
    wt = {"NTUSDT_LONG": {"bar_ts": BAR, "wt1_15m": -5.0, "wt2_15m": -3.0},   # LONG, bearish -> close
          "NSUSDT_SHORT": {"bar_ts": BAR, "wt1_15m": 1.0, "wt2_15m": 4.0},    # SHORT, bearish -> with it, keep
          "OKUSDT_LONG": {"bar_ts": BAR, "wt1_15m": -5.0, "wt2_15m": -3.0}}   # tradeable key: board rules, wt ignored
    orders, notes = V.plan_account("ang", "crypto", {"OKUSDT_LONG"}, states, pos, BAR, set(), ALLOW, wt15=wt.get)
    assert [(o["ss"], o["act"], o["qty"]) for o in orders] == [("NTUSDT_LONG", "CLOSE", 4.0)]
    assert orders[0]["key"] == f"ang|NTUSDT_LONG|{BAR}|NON_TRADEABLE_WT15_AGAINST" and "NON_TRADEABLE_WT15_AGAINST" in orders[0]["why"]
    assert any(ss == "NSUSDT_SHORT" and n.startswith("NON_TRADEABLE_WT15_WITH") for ss, n in notes)
    # SHORT mirror: bullish wt closes the short, even with no fresh board state
    wt["NSUSDT_SHORT"] = {"bar_ts": BAR, "wt1_15m": 6.0, "wt2_15m": 2.0}
    orders, _ = V.plan_account("ang", "crypto", {"OKUSDT_LONG"}, {}, pos, BAR, set(), ALLOW, wt15=wt.get)
    assert {o["ss"] for o in orders} == {"NTUSDT_LONG", "NSUSDT_SHORT"}
    # stale wt reading never acts
    stale = {k: dict(v, bar_ts=BAR - 7200) for k, v in wt.items()}
    orders, notes = V.plan_account("ang", "crypto", {"OKUSDT_LONG"}, {}, pos, BAR, set(), ALLOW, wt15=stale.get)
    assert orders == [] and ("NTUSDT_LONG", "NON_TRADEABLE_WT15_STALE") in notes


def test_wt15_reader_prefers_newer_tail_row(tmp_path):
    import json, types
    import numpy as np
    d = tmp_path / "ind"
    (d / ".tail").mkdir(parents=True)
    np.savez(d / "QUSDT.npz", timestamps=np.array([BAR - 900, BAR]), wt1_15m=np.array([1.0, 2.0]), wt2_15m=np.array([0.0, 3.0]))
    t = V.Trader.__new__(V.Trader)
    t.a = types.SimpleNamespace(npz_dir=str(d))
    assert t.wt15("QUSDT_LONG") == {"bar_ts": BAR, "wt1_15m": 2.0, "wt2_15m": 3.0, "src": "zip"}
    (d / ".tail" / "QUSDT.jsonl").write_text(json.dumps({"timestamps": BAR + 900, "wt1_15m": 5.0, "wt2_15m": 1.0}) + "\n")
    assert t.wt15("QUSDT_LONG")["bar_ts"] == BAR + 900 and t.wt15("QUSDT_LONG")["src"] == "tail"
