"""Cash-account T+1 settlement (GFV) rule — one implementation (vec_decisions/cash_settlement) shared by live
tradier_manage, v12_quick_engine (vector) and backtest_v12_engine (scalar). USER 2026-10-14: "no violations accepted;
make sure it works identically in the vectorized backtests of the v15_pilot so parity can never be put in danger."
"""
import asyncio
import copy
import datetime as dt
import os
from pathlib import Path

import pytest

from vec_decisions import cash_settlement as C

ROOT = Path(__file__).resolve().parents[1]
D = dt.date


def test_nyse_holidays_rule_based():
    assert C.nyse_holidays(2026) == frozenset({D(2026, 1, 1), D(2026, 1, 19), D(2026, 2, 16), D(2026, 4, 3), D(2026, 5, 25), D(2026, 6, 19), D(2026, 7, 3), D(2026, 9, 7), D(2026, 11, 26), D(2026, 12, 25)})
    assert D(2027, 3, 26) in C.nyse_holidays(2027)  # Good Friday 2027 (Easter Mar 28)
    assert D(2025, 1, 9) in C.nyse_holidays(2025)  # national day of mourning
    assert not any(d.year == 2027 and d.month == 12 and d.day == 31 for d in C.nyse_holidays(2027))  # Sat New Year 2028 not observed


def test_settlement_calendar_skips_fed_holidays_and_weekends():
    assert C.next_settlement_day(D(2026, 10, 9)) == D(2026, 10, 13)  # Fri -> (Columbus Mon) -> Tue
    assert C.next_settlement_day(D(2026, 11, 10)) == D(2026, 11, 12)  # Veterans Day
    assert C.next_settlement_day(D(2026, 4, 2)) == D(2026, 4, 6)  # Good Friday
    assert C.next_settlement_day(D(2026, 10, 14)) == D(2026, 10, 15)
    assert C.is_exchange_day(D(2026, 10, 12)) and not C.is_settlement_day(D(2026, 10, 12))


def _ts(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=C.ET).timestamp()


class _Cfg:
    ACCOUNT_TYPE_TRA = "cash"
    ACCOUNT_TYPE_TRB = "margin"
    ACCOUNT_TYPE_TRC = "paper"
    CASH_ACCOUNT_SETTLEMENT_ENABLED = True
    ACCOUNT_SIDE_MAPPING = {"tra": ["LONG"]}


def test_account_resolution_matches_live_types():
    cfg = _Cfg()
    assert C.settlement_applies(cfg, "tra") and not C.settlement_applies(cfg, "trb") and not C.settlement_applies(cfg, "trc")
    assert C.settlement_applies(cfg, "xyz")  # unknown -> cash (fail closed, = tradier_manage._gfv_guard_applies)
    assert "tra" in C.banned_accounts(cfg)
    assert C.board_account("AAPL", "LONG", cfg) == "trb"  # tra is entry-banned -> never the board account
    cfg2 = _Cfg()
    cfg2.ACCOUNT_TYPE_TRC = "cash"
    assert C.board_account("AAPL", "LONG", cfg2) == "trc"  # strictest account that can open the board


def test_book_strict_rule_and_deferral():
    cfg = _Cfg()
    cfg.ACCOUNT_TYPE_TRB = "cash"
    b = C.SettlementBook(cfg, "trb")
    assert b.active and b.settling and not b.banned
    buy = _ts(2026, 10, 9, 10, 0)
    assert b.buy_allowed(buy)
    b.on_buy(buy)
    assert not b.can_sell(_ts(2026, 10, 9, 15, 45))
    assert not b.can_sell(_ts(2026, 10, 12, 10, 0))  # Columbus Day: exchange open, buy not settled
    assert b.can_sell(_ts(2026, 10, 13, 9, 30))
    b.on_sell(_ts(2026, 10, 13, 9, 30), flat=True)
    assert not b.buy_allowed(_ts(2026, 10, 13, 12, 0))  # own proceeds unsettled
    assert b.buy_allowed(_ts(2026, 10, 14, 9, 30))
    pos = {"qty": 10.0}
    snap = C.snapshot(b, _ts(2026, 10, 14, 9, 30), pos, [], [], {"cd": 0})
    assert not snap["full"]  # nothing forbidden -> no copy


def test_audit_rolls_back_restricted_sell_and_owes_it():
    cfg = _Cfg()
    cfg.ACCOUNT_TYPE_TRB = "cash"
    b = C.SettlementBook(cfg, "trb")
    t0 = _ts(2026, 10, 14, 10, 0)
    b.on_buy(t0)
    trades, events = [], []
    t1 = _ts(2026, 10, 14, 11, 0)
    pos = {"qty": 5.0}
    snap = C.snapshot(b, t1, pos, trades, events, {"cd": 0, "hcb": False, "bip": 3, "otc": {}})
    assert snap["full"] and snap["pos"] == pos and snap["pos"] is not pos
    trades.append({"type": "CLOSE", "ts": t1, "qty": 5.0, "exit_reason": "UNCOND_DC_LOW_15M"})
    dec = C.audit_bar(b, snap, trades, events, None)
    assert dec["rollback"] and dec["owed"]["full"] and dec["owed"]["reason"] == "UNCOND_DC_LOW_15M"
    assert b.stats["sell_deferred"] == 1


def test_banned_account_refuses_every_buy():
    b = C.SettlementBook(_Cfg(), "tra")
    assert b.banned and not b.buy_allowed(_ts(2026, 10, 14, 10, 0))
    snap = C.snapshot(b, _ts(2026, 10, 14, 10, 0), None, [], [], {"cd": 0, "hcb": False, "bip": 0, "otc": {}})
    events = [{"type": "OPEN", "ts": _ts(2026, 10, 14, 10, 0), "qty": 3.0}]
    assert C.audit_bar(b, snap, [], events, {"qty": 3.0})["rollback"]


def test_margin_book_inert():
    b = C.SettlementBook(_Cfg(), "trb")
    assert not b.active


# ───────────────────────────── vector engine (real simulate_one on a local NPZ) ─────────────────────────────
def _vec_case(sym_side="AAPL_LONG"):
    if not (ROOT / "backtest_v8" / "indicators" / (sym_side.split("_")[0] + ".npz")).exists():
        pytest.skip("no local stock NPZ")
    from tools.opt import evaluate_v12 as E

    cfg, npz, sym, is_long, mode, _ = E.build_cfg_npz(sym_side, {}, 30)
    if npz is None or mode != "tradier":
        pytest.skip("NPZ unavailable")
    return cfg, npz, sym, is_long


def _ledger_key(r):
    return [(t.get("type"), round(float(t.get("ts") or 0), 1), round(float(t.get("price") or 0), 6), round(float(t.get("qty") or 0), 6), t.get("reason")) for t in r["ledger"]]


def test_vector_margin_account_bit_identical_switch_on_off():
    import v12_quick_engine as V

    cfg, npz, sym, is_long = _vec_case()
    a = V.simulate_one(npz, sym, is_long, copy.deepcopy(cfg))
    c2 = copy.deepcopy(cfg)
    c2.CASH_ACCOUNT_SETTLEMENT_ENABLED = False
    b = V.simulate_one(npz, sym, is_long, c2)
    assert _ledger_key(a) == _ledger_key(b) and a["gain_pct"] == b["gain_pct"] and "cash_settlement" not in a


def test_vector_tra_board_never_trades():
    import v12_quick_engine as V

    cfg, npz, sym, is_long = _vec_case()
    c = copy.deepcopy(cfg)
    c.SIM_ACCOUNT = "tra"
    r = V.simulate_one(npz, sym, is_long, c)
    assert r["trades"] == 0 and r["cash_settlement"]["banned"]


def test_vector_cash_account_never_sells_unsettled_and_defers():
    import v12_quick_engine as V

    cfg, npz, sym, is_long = _vec_case()
    c = copy.deepcopy(cfg)
    c.ACCOUNT_TYPE_TRB = "cash"
    r = V.simulate_one(npz, sym, is_long, c)
    restricted, unsettled, viol_sell, viol_buy, deferred = None, [], 0, 0, 0
    for t in sorted(r["ledger"], key=lambda x: (float(x.get("ts") or 0), 0 if x.get("type") in ("OPEN", "AUGMENT") else 1)):
        ty, d = str(t.get("type")).upper(), C.to_et_date(t["ts"])
        if ty in ("OPEN", "AUGMENT"):
            viol_buy += any(s > d for s in unsettled)
            nxt = C.next_settlement_day(d)
            restricted = nxt if restricted is None else max(restricted, nxt)
        elif ty in ("CLOSE", "REDUCE") and t.get("reason") != "FINAL_MTM":
            viol_sell += bool(restricted and d < restricted)
            deferred += C.DEFERRED_TAG in str(t.get("reason"))
            unsettled.append(C.next_settlement_day(d))
            if ty == "CLOSE":
                restricted = None
    assert viol_sell == 0 and viol_buy == 0
    assert deferred == r["cash_settlement"]["owed_executed"] <= r["cash_settlement"]["sell_deferred"]


# ───────────────────────────── live: GFVTracker + exit_guarantee ─────────────────────────────
def test_live_gfv_tracker_uses_settlement_calendar_and_owes(tmp_path):
    import tradier_manage as tm

    trk = tm.GFVTracker(tmp_path)
    assert trk._next_business_day(D(2026, 10, 9)) == D(2026, 10, 13)
    trk.record_buy_settlement("tra:AAPL_LONG")
    ok, why = trk.can_sell("tra:AAPL_LONG")
    assert not ok and "GFV_BLOCK" in why
    trk.owe_exit("tra:AAPL_LONG", True, 0.0, "UNCOND_DC_LOW_15M")
    trk2 = tm.GFVTracker(tmp_path)  # persisted
    assert trk2.owed_exit("tra:AAPL_LONG")["full"] is True
    trk2.clear_owed("tra:AAPL_LONG")
    assert tm.GFVTracker(tmp_path).owed_exit("tra:AAPL_LONG") is None


def test_exit_guarantee_gfv_refusals_are_policy():
    import exit_guarantee as xg

    for r in ("GFV_BLOCKED", "BLOCKED_GFV_ACCOUNT_RULE", "GFV_SETTLED_CASH_BLOCK_0"):
        assert xg.classify(r, "UNCOND_DC_LOW_15M |VEC_EXACT", True) == "POLICY"


def test_exit_guarantee_replay_passthrough(monkeypatch):
    import exit_guarantee as xg

    monkeypatch.setenv("EXIT_GUARANTEE_REPLAY", "1")
    calls = {"force": 0, "confirmed": 0}

    async def _force(*a, **k):
        calls["force"] += 1
        return "SUCCESS_FORCE_CLOSE_CONFIRMED"

    async def _confirmed(*a, **k):
        calls["confirmed"] += 1

    hooks = xg.Hooks(force=_force, broker=lambda *a: None, confirmed=_confirmed)

    class M:
        @xg.wrap_execute_now("tradier", lambda: hooks)
        async def execute_now(self, position_key=None, account_key=None, symbol=None, position_side=None, quantity=0.0, reason="", is_full_close=False, action=None):
            return "BLOCKED_SOMETHING"

    xg.replay_stats(reset=True)
    res = asyncio.run(M().execute_now(position_key="trb:AAPL_LONG", account_key="trb", symbol="AAPL", position_side="LONG", quantity=3, reason="FIXED_STOP", is_full_close=True, action="CLOSE"))
    assert res == "BLOCKED_SOMETHING" and calls == {"force": 0, "confirmed": 0}
    assert xg.replay_stats().get("WOULD_ESCALATE_FORCE") == 1
