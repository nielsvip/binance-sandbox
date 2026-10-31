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


def test_open_retries_on_new_bar_after_earlier_final_size_skip():
    first = st(True, eq=75.0, oq=100.0)
    prior_key = f"ang|XUSDT_LONG|{first['engine_open_bar_ts']}|OPEN"
    orders, _ = V.plan_account('ang', 'crypto', ['XUSDT_LONG'], {'XUSDT_LONG': first}, {}, BAR, {prior_key}, ALLOW)
    assert len(orders) == 1 and orders[0]['key'] == f'ang|XUSDT_LONG|{BAR}|OPEN'
    next_bar = st(True, eq=75.0, oq=100.0, tail=BAR + 900)
    orders, _ = V.plan_account('ang', 'crypto', ['XUSDT_LONG'], {'XUSDT_LONG': next_bar}, {}, BAR + 900, {prior_key, orders[0]['key']}, ALLOW)
    assert len(orders) == 1 and orders[0]['key'] == f'ang|XUSDT_LONG|{BAR + 900}|OPEN'


def test_filled_board_entry_is_not_opened_again_after_broker_goes_flat(tmp_path):
    import json
    import time
    from types import SimpleNamespace
    trader = V.Trader.__new__(V.Trader)
    trader.out = tmp_path
    trader.venue = "crypto"
    trader.a = SimpleNamespace(dry_run=False, force_active=False)
    trader.done = set()
    trader.done_ts = {}
    trader.filled_open_anchors = set()
    trader._ledger_path().write_text(json.dumps({"event": "ORDER", "acct": "flz", "ss": "XUSDT_LONG", "act": "OPEN", "status": "SENT", "why": f"board holds since {BAR - 7 * 86400}, broker flat", "resp": {"status": "FILLED", "executedQty": "0.001"}}) + "\n")
    trader.filled_open_anchors = trader._load_filled_open_anchors()
    assert ("flz", "XUSDT_LONG", BAR - 7 * 86400) in trader.filled_open_anchors
    state = st(True, tail=BAR)
    state["engine_open_bar_ts"] = BAR - 7 * 86400
    orders, _ = V.plan_account("flz", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": state}, {}, BAR, set(), ALLOW)
    assert orders[0]["anchor"] == BAR - 7 * 86400
    class Broker:
        def size(self, *args):
            raise AssertionError("repeat OPEN reached sizing")
    trader.broker = Broker()
    assert trader.send_orders("flz", orders, {}, False) == 1
    assert any("SKIP_ALREADY_FILLED_ANCHOR" in line for line in trader._ledger_path().read_text().splitlines())


def test_observed_stock_position_records_entry_for_future_flat_broker(tmp_path):
    from types import SimpleNamespace
    trader = V.Trader.__new__(V.Trader)
    trader.out = tmp_path
    trader.venue = "stocks"
    trader.a = SimpleNamespace(dry_run=False, force_active=False)
    trader.filled_open_anchors = set()
    state = st(True)
    trader._remember_held_anchors("trb", {"AAPL_LONG": {"qty": 2}}, {"AAPL_LONG": state})
    anchor = state["engine_open_bar_ts"]
    assert ("trb", "AAPL_LONG", anchor) in trader._load_filled_open_anchors()


def test_unpublished_fallback_settings_never_open_and_close_held():
    fallback = dict(st(True), src='fallback:cat_side_defaults:STOCKS_SHORT', settings_fallback=True)
    orders, notes = V.plan_account('trb', 'stocks', ['ELD_SHORT'], {'ELD_SHORT': fallback}, {}, BAR, set(), ALLOW)
    assert orders == [] and notes == [('ELD_SHORT', 'UNPUBLISHED_SETTINGS_NO_OPEN')]
    orders, _ = V.plan_account('trb', 'stocks', ['ELD_SHORT'], {'ELD_SHORT': fallback}, {'ELD_SHORT': {'qty': 2.0}}, BAR, set(), ALLOW)
    assert len(orders) == 1 and orders[0]['act'] == 'CLOSE' and orders[0]['qty'] == 2.0


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


def test_binance_sizing(tmp_path, monkeypatch):
    monkeypatch.setattr(V, 'MAX_OPEN_NOTIONAL', 80.0)
    b = V.Binance({}, tmp_path)
    b._xi = {"XUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 5.0, "status": "TRADING"},
             "BTCUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 100.0, "status": "TRADING"},
             "BIGUSDT": {"step": 0.001, "min_qty": 0.001, "max_qty": 0, "min_notional": 500.0, "status": "TRADING"}}
    V.read_json = (lambda f: (lambda p, d=None: {"_fetched": 1e18} if "exchange_info" in str(p) else f(p, d)))(V.read_json)
    q, why = b.size({"ss": "XUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 2.0)
    assert q == 7.5 and why == ""
    q, why = b.size({"ss": "BTCUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 100000.0)
    assert q == 0 and 'cap' in why
    q, why = b.size({"ss": "BIGUSDT_LONG", "act": "OPEN", "notional": 15.0}, 0, 1.0)
    assert q == 0 and "cap" in why
    q, _ = b.size({"ss": "XUSDT_LONG", "act": "REDUCE", "qty": 2.0005}, 10.0, 2.0)
    assert q == 2.0
    q, _ = b.size({"ss": "XUSDT_LONG", "act": "REDUCE", "qty": 9.9999}, 10.0, 2.0)
    assert q == 10.0
    assert b.size({"ss": "XUSDT_LONG", "act": "CLOSE"}, 3.21, 2.0)[0] == 3.21


def test_tradier_sizing_whole_shares(tmp_path, monkeypatch):
    monkeypatch.setattr(V, 'MAX_OPEN_NOTIONAL', 4000.0)
    monkeypatch.setattr(V, 'STOCK_ONE_SHARE_MAX', 4000.0)
    t = V.Tradier({}, tmp_path)
    assert t.size({"ss": "F_LONG", "act": "OPEN", "notional": 900.0}, 0, 11.0) == (81.0, "")
    assert t.size({"ss": "AAPL_LONG", "act": "OPEN", "notional": 900.0}, 0, 230.0) == (3.0, "")
    assert t.size({"ss": "SNDK_LONG", "act": "OPEN", "notional": 900.0}, 0, 1304.0) == (1.0, "")
    q, why = t.size({"ss": "SNDK_LONG", "act": "OPEN", "notional": 900.0}, 0, 4001.0)
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


def test_no_information_close_only_for_true_missing_settings_after_grace():
    """USER 2026-10-27 "any sym_side with no information is considered non tradeable and closed" — scoped to a side the
    store has no settings for, after one bar past the deadline; pipeline failures never close; a breaker stops mass closes."""
    pos = {f"S{i}USDT_LONG": {"qty": 1.0, "price": 1.0} for i in range(8)}
    missing = {"ok": False, "reason": "missing live settings", "src": "missing_published_per_sym_row"}
    pipeline = {"ok": False, "reason": "override MIN_HOLD_BARS_BEFORE_EXIT=10 incompatible with bool field (rejected before eval)", "src": "per_sym_store.active"}
    keys = set(pos)
    states = {ss: st(True) for ss in pos}
    states["S0USDT_LONG"] = dict(missing)
    states["S1USDT_LONG"] = dict(missing)
    states["S2USDT_LONG"] = dict(pipeline)
    wt = {"S0USDT_LONG": {"bar_ts": BAR, "wt1_15m": 1.0, "wt2_15m": 2.0}}  # S0: wt against LONG; S1: no wt reading
    since = {}
    age = lambda ss, s, now=0.0: (now - since.setdefault(ss, now)) if V.no_information(s) else None
    orders, _ = V.plan_account("ang", "crypto", keys, states, pos, BAR, set(), ALLOW, wt15=wt.get, noinfo_age=lambda ss, s: age(ss, s, 0.0))
    assert not [o for o in orders if o["act"] == "CLOSE"]  # inside the grace: nothing
    orders, _ = V.plan_account("ang", "crypto", keys, states, pos, BAR, set(), ALLOW, wt15=wt.get, noinfo_age=lambda ss, s: age(ss, s, V.NOINFO_GRACE_S))
    closes = {o["ss"]: o for o in orders if o["act"] == "CLOSE"}
    assert set(closes) == {"S0USDT_LONG", "S1USDT_LONG"}  # the pipeline failure S2 is never closed
    assert closes["S0USDT_LONG"]["reason"] == "NON_TRADEABLE_WT15_AGAINST" and closes["S1USDT_LONG"]["reason"] == "NO_INFORMATION_CLOSE"
    assert "missing_published_per_sym_row" in closes["S1USDT_LONG"]["why"]
    # breaker: most held sides lose information at once -> no closes at all
    for i in range(2, 8):
        states[f"S{i}USDT_LONG"] = dict(missing)
        since[f"S{i}USDT_LONG"] = 0.0
    orders, notes = V.plan_account("ang", "crypto", keys, states, pos, BAR, set(), ALLOW, wt15=wt.get, noinfo_age=lambda ss, s: age(ss, s, V.NOINFO_GRACE_S))
    assert not [o for o in orders if o["act"] == "CLOSE"] and any(n.startswith("NOINFO_BREAKER") for _, n in notes)


def test_wait_next_wakes_on_new_state_and_on_plan_now(tmp_path):
    """2026-10-28 USER "executes it immediately": a state landing in the state dir (or a PLAN_NOW poke from
    tools/s1_bar_guarantee.py) ends the sleep after MIN_PASS_GAP_S instead of the 10 s loop."""
    import os, threading, time
    t, _ = _bare_trader(tmp_path, {})
    t.state_dir = tmp_path / "fs"
    t.state_dir.mkdir()
    t.dir_seen = t.state_dir.stat().st_mtime_ns
    threading.Timer(0.3, lambda: (t.state_dir / "XUSDT_LONG.json").write_text("{}")).start()
    t0 = time.time()
    t.wait_next(t0 - V.MIN_PASS_GAP_S, 30.0)
    assert time.time() - t0 < 3.0 and t.last_wake == "state_dir"
    (t.out / "PLAN_NOW_crypto").write_text("guarantee XUSDT_LONG")
    t0 = time.time()
    t.wait_next(t0 - V.MIN_PASS_GAP_S, 30.0)
    assert time.time() - t0 < 1.0 and t.plan_now and t.last_wake.startswith("plan_now") and not (t.out / "PLAN_NOW_crypto").exists()


def test_untradeable_no_verdict_closes_held_and_blocks_opens():
    """OPERATOR 2026-10-28: a side with no verdict from the new kline is closed on every account and gets no opens; no
    breaker applies (even when every held side is untradeable)."""
    pos = {"XUSDT_LONG": {"qty": 3.0, "price": 1.0}, "YUSDT_SHORT": {"qty": 2.0, "price": 1.0}}
    states = {"XUSDT_LONG": st(True), "YUSDT_SHORT": st(True), "ZUSDT_LONG": st(True)}
    un = {s: {"bar": BAR, "reason": "state ok=False"} for s in ("XUSDT_LONG", "YUSDT_SHORT", "ZUSDT_LONG")}
    orders, notes = V.plan_account("ang", "crypto", {"XUSDT_LONG", "YUSDT_SHORT", "ZUSDT_LONG"}, states, pos, BAR, set(), ALLOW, untradeable=un)
    acts = {o["ss"]: o for o in orders}
    assert set(acts) == {"XUSDT_LONG", "YUSDT_SHORT"} and all(o["act"] == "CLOSE" and o["reason"] == "UNTRADEABLE_NO_VERDICT" for o in orders)
    assert acts["XUSDT_LONG"]["qty"] == 3.0 and any(s == "ZUSDT_LONG" and n.startswith("UNTRADEABLE_NO_OPEN") for s, n in notes)
    orders, notes = V.plan_account("ang", "crypto", {"XUSDT_LONG"}, states, pos, BAR, set(), ALLOW, untradeable=un, untradeable_close=False)
    assert not orders and any("CLOSE_DISABLED" in n for _, n in notes)


def test_tra_entries_refused_but_exits_pass(tmp_path):
    import types
    t = types.SimpleNamespace(root=ROOT, venue="stocks", done=set(), rows=[])
    t._ledger = t.rows.append
    t._entry_banned = lambda acct: V.Trader._entry_banned(t, acct) if hasattr(V, "Trader") else None
    cls = [c for c in vars(V).values() if isinstance(c, type) and hasattr(c, "send_orders")][0]
    t._entry_banned = lambda acct: cls._entry_banned(t, acct)
    assert t._entry_banned("tra") is True and t._entry_banned("trb") is False
    t.a = types.SimpleNamespace(dry_run=True)
    t.quiet, t.attempts, t.done_ts = set(), {}, {}
    t.broker = types.SimpleNamespace(price=lambda s: 10.0, size=lambda o, h, p: (5.0, ""))
    orders = [{"ss": "AAPL_LONG", "act": a, "key": f"k{a}", "why": "x"} for a in ("OPEN", "AUGMENT", "REDUCE", "CLOSE")]
    cls.send_orders(t, "tra", orders, {"AAPL_LONG": {"qty": 5.0}}, False)
    refused = [r["act"] for r in t.rows if r.get("status") == "REFUSED_TRA_EXITS_ONLY"]
    assert refused == ["OPEN", "AUGMENT"]
    assert sorted(r["act"] for r in t.rows if r.get("status") == "DRY_RUN") == ["CLOSE", "REDUCE"]


def test_same_bar_augment_and_close_state_is_flat_and_trader_closes():
    sys.path.insert(0, str(ROOT))
    from tools import v15_forward_state as F
    led = [{"type": "OPEN", "bar": 1, "qty": 40.0, "price": 10.0},
           {"type": "AUGMENT", "bar": 3, "qty": 14.0, "price": 10.0},
           {"type": "CLOSE", "bar_exit": 3, "qty": 54.0, "exit_price": 10.0, "reason": "GAP_MOC_EXIT"}]
    tails = [100, 1000, 1900, 2800]
    assert F._open_ledger_position(led, 3) is not None
    pos, acts = F._vector_acts(led, 3, tails)
    assert pos["engine_qty"] == 0.0
    assert any(a["type"] == "CLOSE" and a["qty_after"] == 0.0 for a in acts)
    orders, _ = V.plan_account("trb", "stocks", ["NUE_LONG"], {"NUE_LONG": st(False, acts)}, {"NUE_LONG": {"qty": 54.0}}, BAR, set(), ALLOW)
    assert [(o["act"], o["qty"]) for o in orders] == [("CLOSE", 54.0)]


def test_tradier_send_gate_refuses_tra_entries_but_not_exits():
    import pytest
    posted = []
    b = V.Tradier.__new__(V.Tradier)
    b.account = lambda acct: ("ID", "K", "B")
    b._req = lambda method, acct, path, params=None, data=None: posted.append(data) or {"order": {"id": 1}}
    o = {"ss": "AAPL_LONG", "act": "OPEN", "key": "k1"}
    with pytest.raises(RuntimeError, match="REFUSED_TRA_EXITS_ONLY"):
        b.send("tra", o, 1.0)
    with pytest.raises(RuntimeError, match="REFUSED_TRA_EXITS_ONLY"):
        b.send("tra", dict(o, act="AUGMENT"), 1.0)
    assert not posted
    b.send("tra", dict(o, act="CLOSE", key="k2"), 1.0)
    b.send("trb", o, 1.0)
    assert [d["side"] for d in posted] == ["sell", "buy"]


def test_untradeable_mass_hold_blocks_opens_without_closing():
    pos = {"XUSDT_LONG": {"qty": 3.0, "price": 1.0}}
    states = {"XUSDT_LONG": st(True), "ZUSDT_LONG": st(True)}
    un = {s: {"bar": BAR, "mode": "hold"} for s in ("XUSDT_LONG", "ZUSDT_LONG")}
    orders, notes = V.plan_account("ang", "crypto", {"XUSDT_LONG", "ZUSDT_LONG"}, states, pos, BAR, set(), ALLOW, untradeable=un)
    assert not orders and sum(1 for _, n in notes if n.startswith("UNTRADEABLE_MASS_HOLD_NO_OPEN")) == 2


def test_held_file_carries_per_side_notional_summed_over_accounts(tmp_path):
    import json
    t, _ = _bare_trader(tmp_path, {"ang": {"XUSDT_LONG": {"qty": 2.0, "price": 10.0}, "YUSDT_SHORT": {"qty": 1.0, "price": 5.0}},
                                   "fin": {"XUSDT_LONG": {"qty": 1.0, "price": 10.0}}})
    t.positions("ang")
    t.held_written = (0.0, None)
    t.positions("fin")
    doc = json.loads((t.out / "held_crypto.json").read_text())
    assert doc["notional"] == {"XUSDT_LONG": 30.0, "YUSDT_SHORT": 5.0} and doc["total_notional"] == 35.0


def test_deterioration_halt_waits_for_fresh_wt15_against_and_never_reenters(tmp_path):
    halted = {"XUSDT_LONG": {"halted_utc": "2026-10-30T04:00:00Z"}}
    orders, notes = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True, eq=75.0, oq=100.0)}, {}, BAR, set(), ALLOW, halted=halted)
    assert orders == [] and notes[0][1] == "DETERIORATED_NO_REENTRY"
    acts = [{"type": "REDUCE", "qty": 25.0, "qty_before": 100.0, "qty_frac": 0.25}, {"type": "AUGMENT", "qty": 50.0, "qty_before": 75.0}]
    held = {"XUSDT_LONG": {"qty": 8.0}}
    with_wt = lambda ss: {"bar_ts": BAR, "wt1_15m": 2.0, "wt2_15m": 1.0}
    orders, notes = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True, acts=acts)}, held, BAR, set(), ALLOW, wt15=with_wt, halted=halted)
    assert [o["act"] for o in orders] == ["REDUCE"]
    assert any("DETERIORATION_HOLD_WAIT_WT15" in n for _, n in notes)
    assert any("DETERIORATION_HALT_NO_AUGMENT" in n for _, n in notes)
    stale_wt = lambda ss: {"bar_ts": BAR - 3600, "wt1_15m": -2.0, "wt2_15m": 1.0}
    orders, notes = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True)}, held, BAR, set(), ALLOW, wt15=stale_wt, halted=halted)
    assert orders == [] and any("stale/unavailable" in n for _, n in notes)
    prior_bar_wt = lambda ss: {"bar_ts": BAR - 900, "wt1_15m": -2.0, "wt2_15m": 1.0}
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True)}, held, BAR, set(), ALLOW, wt15=prior_bar_wt, halted=halted)
    assert orders == []
    against_wt = lambda ss: {"bar_ts": BAR, "wt1_15m": -2.0, "wt2_15m": 1.0}
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(True)}, held, BAR, set(), ALLOW, wt15=against_wt, halted=halted)
    assert [o["act"] for o in orders] == ["CLOSE"] and orders[0]["reason"] == "DETERIORATED_CLOSE"
    assert str(BAR) in orders[0]["key"]
    orders, _ = V.plan_account("ang", "crypto", ["XUSDT_LONG"], {"XUSDT_LONG": st(False)}, {"XUSDT_LONG": {"qty": 12.0}}, BAR, set(), ALLOW, wt15=with_wt, halted=halted)
    assert [(o["act"], o["qty"]) for o in orders] == [("CLOSE", 12.0)] and orders[0].get("reason") != "DETERIORATED_CLOSE"
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "deterioration_halt.json").write_text('{"halts": {"xusdt_long": {"halted_utc": "x"}}}')
    assert set(V.deterioration_halts(tmp_path)) == {"XUSDT_LONG"}
    (tmp_path / "data" / "deterioration_halt.json").write_text("{broken")
    assert V.deterioration_halts(tmp_path) == {}


def test_deterioration_short_requires_bullish_wt15_and_rejects_future_bar():
    ss = "XUSDT_SHORT"
    args = ("ang", "crypto", [ss], {ss: st(True)}, {ss: {"qty": 2.0}}, BAR, set(), ALLOW)
    halted = {ss: {"halted_utc": "now"}}
    future = lambda _: {"bar_ts": BAR + 900, "wt1_15m": 2.0, "wt2_15m": 1.0}
    orders, _ = V.plan_account(*args, wt15=future, halted=halted)
    assert orders == []
    with_trade = lambda _: {"bar_ts": BAR, "wt1_15m": -2.0, "wt2_15m": 1.0}
    orders, _ = V.plan_account(*args, wt15=with_trade, halted=halted)
    assert orders == []
    against = lambda _: {"bar_ts": BAR, "wt1_15m": 2.0, "wt2_15m": 1.0}
    orders, _ = V.plan_account(*args, wt15=against, halted=halted)
    assert len(orders) == 1 and orders[0]["reason"] == "DETERIORATED_CLOSE"
