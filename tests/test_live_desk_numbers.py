"""live_desk numbers must be byte-identical to engine files (USER 2026-10-22).

The desk never recomputes P&L and never touches orders. These tests pin that:
positions snapshots carry file values through untouched, realized windows follow
average-cost math with unknown (never fabricated) basis, and intent validation
rejects bad shapes before anything is staged.
"""

import importlib.util
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "live_desk_server", str(ROOT / "tools" / "live_desk" / "server.py")
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["live_desk_server"] = mod
    spec.loader.exec_module(mod)
    return mod


srv = _load_server()

ROW = {
    "symbol": "TSTUSDT",
    "position_side": "LONG",
    "positionAmt": 2.5,
    "entry_price": 100.0,
    "mark_price": 110.0,
    "gain": 10.0,
    "max_gain": 12.0,
    "unrealized_pnl_USD": 25.0,
    "realized_pnl": 0.0,
    "opened_at": "2026-10-22T10:00:00Z",
    "last_updated": "2026-10-22T11:00:00Z",
}


@pytest.fixture
def repo(tmp_path, monkeypatch):
    acct = tmp_path / "fin"
    acct.mkdir()
    (acct / "long_positions.json").write_text(
        json.dumps(
            {
                "fin:TSTUSDT_LONG": dict(ROW),
                "fin:FLATUSDT_LONG": dict(
                    ROW,
                    symbol="FLATUSDT",
                    positionAmt=0.0,
                    unrealized_pnl_USD=0.0,
                    gain=0.0,
                ),
            }
        )
    )
    (acct / "short_positions.json").write_text(
        json.dumps(
            {
                "fin:SHOUSDT_SHORT": dict(
                    ROW,
                    symbol="SHOUSDT",
                    position_side="SHORT",
                    positionAmt=1.0,
                    unrealized_pnl_USD=-5.0,
                )
            }
        )
    )
    monkeypatch.setattr(srv, "ROOT", tmp_path)
    monkeypatch.setattr(srv, "_cache", {})
    return tmp_path


def test_positions_passthrough_byte_identical(repo):
    snap = srv.read_positions("fin")
    assert set(snap["sides"]) == {
        "long",
        "short",
    }, "both sides must survive (no side-collapse)"
    assert snap["totals"]["count"] == 2
    assert snap["totals"]["flat"] == 1
    row = snap["sides"]["long"][0]
    for k in (
        "symbol",
        "side",
        "qty",
        "entry",
        "mark",
        "gain",
        "max_gain",
        "upnl",
        "realized",
        "opened_at",
        "updated",
    ):
        assert (
            row[k]
            == {
                "symbol": "TSTUSDT",
                "side": "LONG",
                "qty": 2.5,
                "entry": 100.0,
                "mark": 110.0,
                "gain": 10.0,
                "max_gain": 12.0,
                "upnl": 25.0,
                "realized": 0.0,
                "opened_at": "2026-10-22T10:00:00Z",
                "updated": "2026-10-22T11:00:00Z",
            }[k]
        )
    assert snap["totals"]["upnl"] == 20.0
    assert snap["sides"]["short"][0]["side"] == "SHORT"


def _ev(ts, sym, side, act, qty, price):
    return {
        "ts": ts,
        "symbol": sym,
        "side": side,
        "action": act,
        "qty": qty,
        "price": price,
    }


def _expected_day_sums(out, now):
    """Per-day realized sums straight from priced events, independent of the walk's bucketing."""
    from datetime import date, timedelta

    per_day = {}
    for e in out:
        rp = e.get("rpnl")
        if rp is None:
            continue
        ts = datetime.fromisoformat(e["ts"])
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts > now:
            continue
        d = ts.strftime("%Y-%m-%d")
        per_day[d] = per_day.get(d, 0.0) + rp
    today, yest = now.date(), now.date() - timedelta(days=1)

    def in_trail(n):
        return round(
            sum(
                v
                for d, v in per_day.items()
                if (today - date.fromisoformat(d)).days <= n
            ),
            2,
        )

    return {
        "today": round(per_day.get(today.strftime("%Y-%m-%d"), 0.0), 2),
        "yest": round(per_day.get(yest.strftime("%Y-%m-%d"), 0.0), 2),
        "d7": in_trail(6),
        "d30": in_trail(29),
    }


def test_realized_windows_and_unknown_basis():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()

    from datetime import timedelta

    evts = [
        _ev(iso(now - timedelta(hours=2)), "A", "LONG", "OPEN", 10.0, 100.0),
        _ev(iso(now - timedelta(hours=1)), "A", "LONG", "REDUCE", 4.0, 110.0),  # +40
        _ev(iso(now - timedelta(hours=26)), "D", "LONG", "OPEN", 1.0, 100.0),
        _ev(
            iso(now - timedelta(hours=25)), "D", "LONG", "REDUCE", 1.0, 120.0
        ),  # +20 prior day
        _ev(iso(now - timedelta(days=10)), "B", "SHORT", "OPEN", 5.0, 50.0),
        _ev(
            iso(now - timedelta(days=9)), "B", "SHORT", "CLOSE", 5.0, 40.0
        ),  # +50 in 30d only
        _ev(
            iso(now - timedelta(minutes=5)), "C", "LONG", "CLOSE", 2.0, 9.0
        ),  # no basis: unknown
    ]
    out, s = srv.realized_walk(evts)
    exp = _expected_day_sums(out, now)
    assert s["pnl_total"] == 110.0
    assert s["pnl_today"] == exp["today"]
    assert s["pnl_yest"] == exp["yest"]
    assert s["pnl_7d"] == exp["d7"]
    assert s["pnl_30d"] == exp["d30"]
    assert s["d_today"] == now.strftime("%Y-%m-%d")
    assert s["d_yest"] == (now - timedelta(days=1)).strftime("%Y-%m-%d")
    assert s["unknown"] == 1
    assert s["unknown_phantom"] == 1
    assert s["unknown_partial"] == 0
    by_sym = {e["symbol"]: e for e in out if e["action"] in ("REDUCE", "CLOSE")}
    assert by_sym["A"]["rpnl"] == 40.0
    assert by_sym["B"]["rpnl"] == 50.0
    assert "rpnl" not in by_sym["C"]


def test_unknown_partial_vs_phantom():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    from datetime import timedelta

    evts = [
        _ev(iso(now - timedelta(hours=2)), "P", "LONG", "OPEN", 10.0, 100.0),
        _ev(
            iso(now - timedelta(hours=1)), "P", "LONG", "REDUCE", 14.0, 110.0
        ),  # 10 known (+100) + 4 partial
    ]
    out, s = srv.realized_walk(evts)
    exp = _expected_day_sums(out, now)
    assert s["known"] == 1
    assert s["unknown"] == 1
    assert s["unknown_partial"] == 1
    assert s["unknown_phantom"] == 0
    assert s["pnl_total"] == 100.0
    assert s["pnl_today"] == exp["today"]
    assert s["pnl_7d"] == exp["d7"]
    assert s["pnl_30d"] == exp["d30"]
    by_sym = {e["symbol"]: e for e in out if e["action"] in ("REDUCE", "CLOSE")}
    assert by_sym["P"]["rpnl"] == 100.0


def test_broker_fills_counted_once_and_unattributed_hidden():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    from datetime import timedelta

    o = _ev(iso(now - timedelta(hours=4)), "LRCX", "SHORT", "OPEN", 13.0, 263.12)
    r1 = _ev(iso(now - timedelta(hours=3)), "LRCX", "SHORT", "REDUCE", 13.0, 235.70)
    r1["reason"] = "Broker sync"
    p = _ev(
        iso(now - timedelta(hours=62, minutes=0)), "E", "LONG", "OPEN", 43.4, 0.6433
    )
    f1 = _ev(
        iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 30.7, 0.6432
    )
    f1["reason"] = "BROKER_RECONCILED order=1"
    f2 = _ev(
        iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 8.8, 0.6431
    )
    f2["reason"] = "BROKER_RECONCILED order=1"
    f3 = _ev(
        iso(now - timedelta(hours=1, minutes=1)), "E", "LONG", "REDUCE", 3.9, 0.6431
    )
    f3["reason"] = "BROKER_RECONCILED order=1"
    dup = _ev(iso(now - timedelta(hours=1)), "E", "LONG", "REDUCE", 43.4, 0.6435)
    dup["reason"] = "REAL_STRATEGY_CLOSE"
    q0 = _ev(iso(now - timedelta(minutes=30)), "R", "LONG", "OPEN", 10.0, 100.0)
    c0 = _ev(iso(now - timedelta(minutes=29)), "R", "LONG", "REDUCE", 10.0, 120.0)
    c0["reason"] = "REAL_STRATEGY_CLOSE"
    d0 = _ev(iso(now - timedelta(minutes=28)), "R", "LONG", "REDUCE", 10.0, 120.0)
    d0["reason"] = "BROKER_RECONCILED order=2"
    out, s = srv.realized_walk([o, r1, p, f1, f2, f3, dup, q0, c0, d0], expiry_days=60)
    lrcx = [e for e in out if e.get("symbol") == "LRCX" and e["action"] == "REDUCE"]
    assert len(lrcx) == 1 and all("rpnl" not in e for e in lrcx)
    fills = [
        e
        for e in out
        if (e.get("reason") or "").startswith("BROKER_RECONCILED order=1")
    ]
    assert len(fills) == 3 and all("rpnl" in e for e in fills)
    strat = [
        e
        for e in out
        if e.get("reason") == "REAL_STRATEGY_CLOSE" and e.get("symbol") == "E"
    ][0]
    assert "rpnl" not in strat and strat.get("sync_hidden") is True
    assert [e for e in out if e.get("reason") == "BROKER_RECONCILED order=2"][0].get(
        "sync_hidden"
    ) is True
    assert s["sync_hidden"] == 3
    assert s["known"] == 4
    assert s["unknown"] == 0
    assert s["pnl_total"] == round(
        30.7 * (0.6432 - 0.6433)
        + 8.8 * (0.6431 - 0.6433)
        + 3.9 * (0.6431 - 0.6433)
        + 200.0,
        2,
    )
    assert srv._is_broker_reconstruction("BROKER_SYNC_CLOSE") is True
    assert srv._is_broker_reconstruction("Broker sync") is True
    assert srv._is_broker_reconstruction("BROKER_RECONCILED order=1") is False
    assert srv._is_broker_fill("BROKER_RECONCILED order=1") is True
    assert srv._is_broker_fill("Broker sync") is False
    assert srv._is_broker_reconstruction("VEC_DRIVEN_CLOSE_RECONCILE_") is False
    assert srv._is_broker_reconstruction("FIXED_STOP_SHORT_px1_g-0.3") is False
    assert srv._is_broker_reconstruction(None) is False


def test_asof_blanks_unsettled_days_never_zero():
    w = {
        "b_today": 0.0,
        "b_yest": 0.0,
        "b_7d": -5193.33,
        "b_30d": -24782.32,
        "d_today": "2026-10-23",
        "d_yest": "2026-10-22",
    }
    lagged = srv._apply_asof(w, "2026-10-21")
    assert lagged["b_today"] is None
    assert lagged["b_yest"] is None
    assert lagged["b_7d"] == -5193.33
    assert lagged["b_30d"] == -24782.32
    fresh = srv._apply_asof(w, "2026-10-23")
    assert fresh["b_today"] == 0.0 and fresh["b_yest"] == 0.0
    mid = srv._apply_asof(w, "2026-10-22")
    assert mid["b_today"] is None and mid["b_yest"] == 0.0


def test_account_value_ledger_roundtrip_and_throttle(tmp_path):
    srv.LEDGER_PATH = tmp_path / "account_values.json"
    srv._LEDGER_WRITE_AT.clear()
    bal = {"equity": 45902.05, "avail": 45902.05, "open_pl": 0.0, "close_pl": -0.52}
    srv._record_account_day("trb", bal, 0.0)
    led = srv._load_ledger()
    today = time.strftime("%Y-%m-%d", time.gmtime(time.time()))
    assert led["trb"][today]["equity"] == 45902.05
    assert led["trb"][today]["close_pl"] == -0.52
    assert led["trb"][today]["ts_first"] == led["trb"][today]["ts_last"]
    srv._record_account_day("trb", dict(bal, equity=45903.0), 0.0)
    assert srv._load_ledger()["trb"][today]["equity"] == 45902.05
    srv._LEDGER_WRITE_AT.clear()
    srv._record_account_day("trb", dict(bal, equity=45903.0), 1.0)
    rec = srv._load_ledger()["trb"][today]
    assert rec["equity"] == 45903.0 and rec["unreal_desk"] == 1.0
    srv._LEDGER_WRITE_AT.clear()
    srv._record_account_day(
        "trb", {"equity": None, "avail": None, "open_pl": None, "close_pl": None}, None
    )
    rec = srv._load_ledger()["trb"][today]
    assert rec["equity"] == 45903.0 and rec["close_pl"] == -0.52


def test_implied_today_from_equity_marks(tmp_path):
    srv.LEDGER_PATH = tmp_path / "account_values.json"
    srv._LEDGER_WRITE_AT.clear()
    now = time.time()
    yest = time.strftime("%Y-%m-%d", time.gmtime(now - 86400))
    srv.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    srv.LEDGER_PATH.write_text(
        json.dumps({"trb": {yest: {"equity": 46000.0, "open_pl": 100.0}}})
    )
    assert srv._implied_today("trb", 45902.05, 0.0) == round(
        (45902.05 - 46000.0) - (0.0 - 100.0), 2
    )
    assert srv._implied_today("trb", None, 0.0) is None
    assert srv._implied_today("nosuch", 1.0, 0.0) is None


def test_ledger_windows_slots_and_never_zero(tmp_path):
    srv.LEDGER_PATH = tmp_path / "account_values.json"
    srv.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    now_s = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    days = {}
    eq, op = 45000.0, 0.0
    for off, pnl in ((8, 0.0), (7, 10.0), (2, 20.0), (1, -5.0)):
        eq += pnl
        d = time.strftime("%Y-%m-%d", time.gmtime(now_s - off * 86400))
        days[d] = {"equity": eq, "open_pl": op, "cash": eq}
    srv.LEDGER_PATH.write_text(json.dumps({"trb": days}))
    w = srv._ledger_windows("trb", eq + 3.0, 7.0, now_s)
    assert w["l_today"] == round(3.0 - 7.0, 2)
    assert w["l_yest"] == -5.0
    d7 = days[time.strftime("%Y-%m-%d", time.gmtime(now_s - 7 * 86400))]["equity"]
    assert w["l_7d"] == round((eq + 3.0 - d7) - 7.0, 2)
    assert w["l_30d"] is None
    assert w["l_cov"] == {"today": True, "yest": True, "d7": True, "d30": False}
    assert w["l_asof"] == max(days)
    empty = srv._ledger_windows("nosuch", 1.0, 0.0, now_s)
    assert empty["l_today"] is None and empty["l_cov"] == {} and empty["l_asof"] is None
    nones = srv._ledger_windows("trb", None, None, now_s)
    assert nones["l_today"] is None and nones["l_cov"]["today"] is False


def test_ledger_audit_gap(tmp_path):
    srv.LEDGER_PATH = tmp_path / "account_values.json"
    srv.LEDGER_PATH.parent.mkdir(parents=True, exist_ok=True)
    now_s = datetime(2026, 10, 24, 12, 0, tzinfo=timezone.utc).timestamp()
    days = {}
    for off in (7, 30, 1, 2):
        d = time.strftime("%Y-%m-%d", time.gmtime(now_s - off * 86400))
        days[d] = {"equity": 1000.0 + off, "open_pl": 0.0, "cash": 1000.0 + off}
    srv.LEDGER_PATH.write_text(json.dumps({"fin": days}))
    a = srv._ledger_audit("fin", 1100.0, 10.0, 50.0, 60.0, now_s)
    assert a["v_7d"] == 50.0 and a["v_30d"] == 60.0
    assert a["d_7d"] == round(a["l_7d"] - 50.0, 2)
    assert srv._ledger_audit("nosuch", 1.0, 0.0, 1.0, 1.0, now_s) is None


def test_sync_rejected_orders_never_form_basis_or_pnl():
    evs = [
        _ev("2026-09-02T13:36:00+00:00", "BABA", "SHORT", "OPEN", 10.0, 112.845),
        _ev("2026-09-02T13:43:00+00:00", "BABA", "SHORT", "OPEN", 11.0, 112.53),
        _ev("2026-10-22T18:17:00+00:00", "BABA", "SHORT", "OPEN", 12.0, 101.835),
        _ev("2026-10-23T19:58:00+00:00", "BABA", "SHORT", "CLOSE", 11.0, 99.555),
    ]
    evs[0]["reason"] = "BROKER_SYNC_REJECTED_TOTAL_VALUE_CAP_2720>2500"
    evs[1]["reason"] = "BROKER_SYNC_REJECTED_TOTAL_VALUE_CAP_2720>2500"
    evs[2]["reason"] = "WT_15M_BOUNCE_OPEN_S"
    evs[3]["reason"] = "DAYTRADE_TARGET_dc_4h_low"
    out, s = srv.realized_walk(evs)
    assert s["rejected_skipped"] == 2
    assert s["known"] == 1
    closes = [e for e in out if e.get("rpnl") is not None]
    assert len(closes) == 1
    assert closes[0]["rpnl"] == round(11.0 * (101.835 - 99.555), 2)
    rej = _ev("2026-10-23T20:00:00+00:00", "BABA", "SHORT", "CLOSE", 5.0, 99.0)
    rej["reason"] = "BROKER_SYNC_REJECTED_AUG_VALUE_CAP_3007>2000"
    out2, s2 = srv.realized_walk(evs + [rej])
    assert s2["rejected_skipped"] == 3 and s2["known"] == 1
    assert sum(1 for e in out2 if e.get("rpnl") is not None) == 1
    assert all(not e.get("rejected_hidden") or e.get("rpnl") is None for e in out2)


def test_close_carries_basis_provenance():
    o1 = _ev("2026-10-22T18:17:00+00:00", "BAS", "SHORT", "OPEN", 12.0, 101.835)
    o1["reason"] = "WT_15M_BOUNCE_OPEN_S"
    o2 = _ev("2026-10-22T18:18:00+00:00", "BAS", "SHORT", "OPEN", 11.0, 101.865)
    o2["reason"] = "BROKER_SYNC_OPEN_FROM_FLAT_STUB"
    cl = _ev("2026-10-23T19:58:00+00:00", "BAS", "SHORT", "CLOSE", 11.0, 99.555)
    cl["reason"] = "DAYTRADE_TARGET"
    out, _ = srv.realized_walk([o1, o2, cl])
    row = [e for e in out if e.get("rpnl") is not None][0]
    assert row["basis_n"] == 1
    assert row["basis"][0]["px"] == 101.835
    assert row["basis"][0]["q"] == 11.0
    assert row["basis"][0]["ts"] == "2026-10-22T18:17:00+00:00"
    assert row["basis"][0]["reason"] == "WT_15M_BOUNCE_OPEN_S"


def test_stale_close_provenance_shows_fresh_lots_not_zombies():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    zt = _ev(old, "ZMB", "SHORT", "OPEN", 100.0, 112.0)
    zt["reason"] = "DC_BREAK_LOW_15M"
    fr = _ev(new, "ZMB", "SHORT", "OPEN", 12.0, 101.0)
    fr["reason"] = "WT_15M_BOUNCE_OPEN_S"
    cl = _ev(now.isoformat(), "ZMB", "SHORT", "CLOSE", 11.0, 99.0)
    out, s = srv.realized_walk(
        [zt, fr, cl], stale_basis_days=7, use_kline_px=True, expiry_days=60
    )
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row.get("stale_excluded") is True
    assert row["rpnl"] == round(11.0 * (101.0 - 99.0), 2)
    assert row["basis_n"] == 1
    assert row["basis"][0]["px"] == 101.0
    assert row["basis"][0]["q"] == 11.0
    assert row["basis"][0]["reason"] == "WT_15M_BOUNCE_OPEN_S"
    assert all("DC_BREAK" not in (b.get("reason") or "") for b in row["basis"])
    assert s["pnl_total"] == 0.0


def test_collapse_drops_sync_before_strategy_twin():
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    s = _ev(iso(now), "W", "LONG", "REDUCE", 1.0, 10.0)
    s["reason"] = "Broker sync"
    t = _ev(iso(now + timedelta(seconds=6)), "W", "LONG", "REDUCE", 1.0, 10.0)
    t["reason"] = "CYCLE_TP_TIERED"
    kept, n = srv.collapse_echoes([s, t])
    assert n == 1 and len(kept) == 1 and kept[0]["reason"] == "CYCLE_TP_TIERED"


def test_collapse_chains_echo_train_beyond_120s():
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    k = _ev(iso(now), "W", "LONG", "CLOSE", 4.0, 10.0)
    k["reason"] = "DAYTRADE_TARGET"
    train = [k]
    for s in (40, 132, 212, 259):
        r = _ev(iso(now + timedelta(seconds=s)), "W", "LONG", "CLOSE", 4.0, 10.0)
        r["reason"] = "BROKER_SYNC_CLOSE"
        train.append(r)
    kept, n = srv.collapse_echoes(train)
    assert len(kept) == 1 and kept[0]["reason"] == "DAYTRADE_TARGET" and n == 4


def test_collapse_keeps_lone_sync_beyond_120s():
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    k = _ev(iso(now), "W", "LONG", "CLOSE", 4.0, 10.0)
    k["reason"] = "DAYTRADE_TARGET"
    s = _ev(iso(now + timedelta(seconds=500)), "W", "LONG", "CLOSE", 4.0, 10.0)
    s["reason"] = "Broker sync"
    kept, n = srv.collapse_echoes([k, s])
    assert len(kept) == 2 and n == 0


def test_adopt_stub_accepts_only_excess_over_book():
    o = _ev("2026-10-22T10:00:00+00:00", "AX", "LONG", "OPEN", 30.0, 10.0)
    o["reason"] = "STRAT_OPEN"
    s = _ev("2026-10-22T11:00:00+00:00", "AX", "LONG", "OPEN", 38.0, 10.5)
    s["reason"] = "BROKER_SYNC_ADOPTED_EXACT_X"
    c = _ev("2026-10-22T12:00:00+00:00", "AX", "LONG", "CLOSE", 38.0, 11.0)
    out, sm = srv.realized_walk([o, s, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(30.0 * 1.0 + 8.0 * 0.5, 2)
    assert row["basis_n"] == 2
    assert sm["sync_hidden"] == 0


def test_adopt_exact_rereport_still_skips():
    o = _ev("2026-10-22T10:00:00+00:00", "AX", "LONG", "OPEN", 30.0, 10.0)
    o["reason"] = "STRAT_OPEN"
    s = _ev("2026-10-22T11:00:00+00:00", "AX", "LONG", "OPEN", 30.0, 10.5)
    s["reason"] = "BROKER_SYNC_OPEN_FROM_FLAT_STUB"
    c = _ev("2026-10-22T12:00:00+00:00", "AX", "LONG", "CLOSE", 30.0, 11.0)
    out, sm = srv.realized_walk([o, s, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(30.0 * 1.0, 2)
    assert row["basis_n"] == 1


def test_lots_held_over_7d_expire_exact():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    new = (now - timedelta(days=1)).isoformat()
    z = _ev(old, "EX", "LONG", "OPEN", 100.0, 50.0)
    z["reason"] = "PHANTOM_ERA"
    f = _ev(new, "EX", "LONG", "OPEN", 10.0, 52.0)
    f["reason"] = "STRAT"
    c = _ev(now.isoformat(), "EX", "LONG", "CLOSE", 10.0, 55.0)
    out, sm = srv.realized_walk([z, f, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert sm["lots_expired"] == 1
    assert row["rpnl"] == round(10.0 * 3.0, 2)
    assert row.get("stale_excluded") is not True
    assert row["basis"][0]["px"] == 52.0


def test_backfill_prices_phantom_from_broker_book():
    cl = _ev("2026-10-23T19:00:00+00:00", "BF", "SHORT", "CLOSE", 10.0, 99.0)
    books = {("BF", "SHORT"): [[None, 10.0, 101.0]]}
    out, sm = srv.realized_walk([cl], broker_books=books)
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(10.0 * (101.0 - 99.0), 2)
    assert row["basis"][-1]["reason"] == "BROKER_FILL Basis"
    assert sm["unknown"] == 0 and sm["broker_filled"] == 1
    assert sm["pnl_total"] == row["rpnl"]


def test_backfill_covers_partial_remainder_only():
    o = _ev("2026-10-23T10:00:00+00:00", "BF", "LONG", "OPEN", 4.0, 10.0)
    o["reason"] = "STRAT"
    c = _ev("2026-10-23T19:00:00+00:00", "BF", "LONG", "CLOSE", 10.0, 12.0)
    books = {("BF", "LONG"): [[None, 6.0, 11.0]]}
    out, sm = srv.realized_walk([o, c], broker_books=books)
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(4.0 * 2.0 + 6.0 * 1.0, 2)
    assert sm["unknown"] == 0 and sm["unknown_partial"] == 0
    assert sm["broker_filled"] == 1


def test_backfill_short_book_leaves_honest_unknown():
    cl = _ev("2026-10-23T19:00:00+00:00", "BF", "SHORT", "CLOSE", 10.0, 99.0)
    books = {("BF", "SHORT"): [[None, 3.0, 101.0]]}
    out, sm = srv.realized_walk([cl], broker_books=books)
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(3.0 * (101.0 - 99.0), 2)
    assert sm["unknown"] == 1 and sm["unknown_partial"] == 1


def test_is_paper_row_markers():
    live = [
        "WT_15M_BOUNCE_OPEN_S_wt1=-44.6",
        "BROKER_RECONCILED order=9745492051",
        "VEC_DRIVEN_OPEN_RECONCILE_",
        "MAKER_PROFIT_EXIT_FIXED_STOP_SHORT_px3.58",
        "Broker sync",
        "BROKER_SYNC_CLOSE",
        "VEC_DRIVEN_CLOSE_RECONCILE_",
        None,
        "",
    ]
    paper = [
        "VEC_DRIVEN_OPEN_PURE_BOARD_BOARD_HOLDS_LIVE_FLAT_BOARD_QTY_9",
        "VEC_CONVERGE_OPEN vec-holds-live-flat |VEC_EXACT",
        "MAKER_PROFIT_EXIT_VEC_CONVERGE_TO_FLAT paper-flat-live-holds",
        "VEC_DRIVEN_CLOSE_PURE_BOARD_BOARD_FLAT_LIVE_HOLDS_BOARD_QTY_0",
    ]
    assert not any(srv._is_paper_row(r) for r in live)
    assert all(srv._is_paper_row(r) for r in paper)


def test_paper_open_never_forms_basis():
    po = _ev("2026-10-23T10:00:00+00:00", "PP", "LONG", "OPEN", 10.0, 90.0)
    po["reason"] = "VEC_DRIVEN_OPEN_PURE_BOARD_BOARD_HOLDS_LIVE_FLAT_BOARD_QTY_1"
    o = _ev("2026-10-23T11:00:00+00:00", "PP", "LONG", "OPEN", 10.0, 100.0)
    o["reason"] = "STRAT"
    c = _ev("2026-10-23T19:00:00+00:00", "PP", "LONG", "CLOSE", 10.0, 110.0)
    out, sm = srv.realized_walk([po, o, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == 100.0
    assert all(b["reason"] != po["reason"] for b in row["basis"])
    assert sm["paper_skipped"] == 1 and sm["unknown"] == 0


def test_paper_close_never_consumes_or_prices():
    o = _ev("2026-10-23T10:00:00+00:00", "PP", "LONG", "OPEN", 10.0, 100.0)
    o["reason"] = "STRAT"
    pc = _ev("2026-10-23T15:00:00+00:00", "PP", "LONG", "CLOSE", 10.0, 110.0)
    pc["reason"] = "VEC_DRIVEN_CLOSE_PURE_BOARD_BOARD_FLAT_LIVE_HOLDS_BOARD_QTY_0"
    c = _ev("2026-10-23T19:00:00+00:00", "PP", "LONG", "CLOSE", 10.0, 110.0)
    out, sm = srv.realized_walk([o, pc, c])
    rows = {e["ts"]: e for e in out if e.get("action") == "CLOSE"}
    assert rows[pc["ts"]]["rpnl"] is None and rows[pc["ts"]]["paper"] is True
    assert rows[c["ts"]]["rpnl"] == 100.0
    assert sm["paper_skipped"] == 1 and sm["unknown"] == 0
    assert sm["pnl_total"] == 100.0


def test_paper_rows_never_burn_fills():
    fills = {"PB": [(1, -10.0, 100.0, "2026-10-23")]}
    po = _ev("2026-10-23T10:00:00+00:00", "PB", "SHORT", "OPEN", 10.0, 90.0)
    po["reason"] = "VEC_DRIVEN_OPEN_PURE_BOARD_BOARD_HOLDS_LIVE_FLAT_BOARD_QTY_1"
    chains = {("PB", "SHORT"): [po]}
    books = srv._build_broker_books(chains, fills)
    assert books.get(("PB", "SHORT")) == [[1, 10.0, 100.0]]


def test_intraday_first_close_eats_same_day_lots():
    # EGLD 10-22 shape: an older AUG must not feed a close that has
    # same-day basis; the orphan otherwise prints next-day phantom P&L.
    # (Old lot is 23h fresh so only tiering — not expiry — can explain it.)
    old = _ev("2026-10-22T20:00:00+00:00", "ID", "LONG", "OPEN", 10.0, 90.0)
    old["reason"] = "STRAT_OLD"
    new = _ev("2026-10-23T06:00:00+00:00", "ID", "LONG", "OPEN", 10.0, 100.0)
    new["reason"] = "STRAT_NEW"
    c = _ev("2026-10-23T19:00:00+00:00", "ID", "LONG", "CLOSE", 10.0, 110.0)
    out, sm = srv.realized_walk([old, new, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == 100.0
    assert [b["reason"] for b in row["basis"]] == ["STRAT_NEW"]
    assert sm["unknown"] == 0


def test_intraday_first_partial_spills_to_older_tier():
    old = _ev("2026-10-22T20:00:00+00:00", "ID", "LONG", "OPEN", 10.0, 90.0)
    old["reason"] = "STRAT_OLD"
    new = _ev("2026-10-23T06:00:00+00:00", "ID", "LONG", "OPEN", 10.0, 100.0)
    new["reason"] = "STRAT_NEW"
    c = _ev("2026-10-23T19:00:00+00:00", "ID", "LONG", "CLOSE", 15.0, 110.0)
    out, sm = srv.realized_walk([old, new, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row["rpnl"] == round(10.0 * 10.0 + 5.0 * 20.0, 2)
    assert [b["reason"] for b in row["basis"]] == ["STRAT_NEW", "STRAT_OLD"]


def test_attach_order_commissions_exact_multiset_only():
    omap = {
        "1": [
            {"q": 4.6, "comm": "0.000001", "asset": "BNB"},
            {"q": 11.0, "comm": "0.000003", "asset": "BNB"},
        ],
        "2": [{"q": 5.0, "comm": "0.000002", "asset": "BNB"}],
    }
    a = {"reason": "BROKER_RECONCILED order=1", "qty": 11.0}
    b = {"reason": "BROKER_RECONCILED order=1", "qty": 4.6}
    c = {"reason": "BROKER_RECONCILED order=2", "qty": 9.9}  # qty mismatch: skip
    d = {"reason": "STRAT", "qty": 1.0}  # no order: skip
    e = {"reason": "BROKER_RECONCILED order=9", "qty": 1.0}  # unknown order: skip
    n = srv._attach_order_commissions([a, b, c, d, e], omap)
    assert n == 2
    assert (a["comm"], a["comm_asset"]) == ("0.000003", "BNB")
    assert (b["comm"], b["comm_asset"]) == ("0.000001", "BNB")
    assert "comm" not in c and "comm" not in d and "comm" not in e


def test_attach_order_commissions_converts_via_fx():
    omap = {
        "1": [{"q": 2.0, "comm": "0.00001", "asset": "BNB", "t": 1792800000000}],
        "2": [{"q": 3.0, "comm": "0.05", "asset": "USDT", "t": 1792800000000}],
        "3": [{"q": 4.0, "comm": "0.00002", "asset": "BNB", "t": 1000000000000}],
    }
    a = {"reason": "BROKER_RECONCILED order=1", "qty": 2.0}
    b = {"reason": "BROKER_RECONCILED order=2", "qty": 3.0}
    c = {"reason": "BROKER_RECONCILED order=3", "qty": 4.0}
    n = srv._attach_order_commissions(
        [a, b, c], omap, fx=lambda asset, t: 700.0 if asset == "BNB" else 1.0
    )
    assert n == 3
    assert a["comm_usd"] == round(0.00001 * 700.0, 6) and "comm" not in a
    assert b["comm_usd"] == 0.05
    assert c["comm_usd"] == round(0.00002 * 700.0, 6)


def test_attach_order_commissions_falls_back_to_raw_without_bar():
    omap = {"1": [{"q": 2.0, "comm": "0.00001", "asset": "BNB", "t": 1000000000000}]}
    a = {"reason": "BROKER_RECONCILED order=1", "qty": 2.0}
    n = srv._attach_order_commissions([a], omap)
    assert n == 1
    assert "comm_usd" not in a
    assert (a["comm"], a["comm_asset"]) == ("0.00001", "BNB")


def test_comm_fx_stable_and_bnb_known():
    assert srv._comm_fx("USDT", 1792800000000) == 1.0
    assert srv._comm_fx("USDC", None) == 1.0
    assert srv._comm_fx("ETH", 1792800000000) is None
    px = srv._comm_fx("BNB", 1792800000000)
    assert px is None or 100.0 < px < 10000.0


def test_attach_order_commissions_empty_map_safe():
    assert srv._attach_order_commissions([{"reason": "X", "qty": 1.0}], {}) == 0
    assert srv._attach_order_commissions([], {"1": []}) == 0


def test_order_fill_map_reads_cache_files():
    import json

    fp = srv.ROOT / "data" / "live_desk" / "broker_fill_cache" / "tst_COMMX.json"
    rows = [
        {
            "symbol": "COMMX",
            "orderId": 77,
            "qty": "2.5",
            "price": "1.0",
            "commission": "0.000004",
            "commissionAsset": "BNB",
            "time": 1792800000000,
            "id": 1,
        },
        {
            "symbol": "COMMX",
            "orderId": 77,
            "qty": "7.5",
            "price": "1.0",
            "commission": "0.000011",
            "commissionAsset": "BNB",
            "time": 1792800001000,
            "id": 2,
        },
        {
            "symbol": "COMMX",
            "qty": "9.0",
            "price": "1.0",
            "time": 1792800002000,
            "id": 3,
        },
    ]
    try:
        fp.write_text(json.dumps(rows))
        srv._cache.pop("ordercomm:tst", None)
        m = srv._order_fill_map("tst")
        assert sorted(f["q"] for f in m["77"]) == [2.5, 7.5]
        assert m["77"][0]["comm"] == "0.000004" and m["77"][0]["asset"] == "BNB"
    finally:
        try:
            fp.unlink()
        except OSError:
            pass
        srv._cache.pop("ordercomm:tst", None)


def test_twopass_releases_unpriced_close_burn():
    # SELL fills feed the SHORT book, but a phantom LONG reduce burns them
    # in pass one — leaving the SHORT close (no journal basis) unknown.
    # Releasing the unpriced reduce lets the close backfill honestly.
    fills = {"TP": [(None, -10.0, 100.0, "2026-10-23")]}
    phantom = _ev("2026-10-23T15:00:00+00:00", "TP", "LONG", "REDUCE", 10.0, 95.0)
    close = _ev("2026-10-23T19:00:00+00:00", "TP", "SHORT", "CLOSE", 10.0, 95.0)
    chains = {("TP", "LONG"): [phantom], ("TP", "SHORT"): [close]}
    books1 = srv._build_broker_books(chains, fills)
    assert books1.get(("TP", "SHORT")) is None
    out1, _ = srv.realized_walk([phantom, close], broker_books=books1)
    assert all(
        e.get("rpnl") is None for e in out1 if e.get("action") in ("CLOSE", "REDUCE")
    )
    release = ({srv._burn_key(phantom)}, set())
    books2 = srv._build_broker_books(chains, fills, release=release)
    assert books2.get(("TP", "SHORT")) == [[None, 10.0, 100.0]]
    out2, sm2 = srv.realized_walk([phantom, close], broker_books=books2)
    rows = {e["ts"]: e for e in out2 if e.get("action") in ("CLOSE", "REDUCE")}
    assert rows[close["ts"]]["rpnl"] == 50.0
    assert "rpnl" not in rows[phantom["ts"]]
    assert sm2["broker_filled"] == 1 and sm2["unknown"] == 1


def test_twopass_priced_rows_keep_burns():
    # A fully journal-priced round trip owns its fills: the phantom close
    # must stay unknown in both passes (no dilution from freed books).
    fills = {"TQ": [(7, 10.0, 100.0, "2026-10-23"), (8, -10.0, 110.0, "2026-10-23")]}
    o = _ev("2026-10-23T10:00:00+00:00", "TQ", "LONG", "OPEN", 10.0, 100.0)
    c = _ev("2026-10-23T15:00:00+00:00", "TQ", "LONG", "CLOSE", 10.0, 110.0)
    ph = _ev("2026-10-23T19:00:00+00:00", "TQ", "LONG", "CLOSE", 10.0, 110.0)
    chains = {("TQ", "LONG"): [o, c, ph]}
    books1 = srv._build_broker_books(chains, fills)
    out1, sm1 = srv.realized_walk([o, c, ph], broker_books=books1)
    assert sm1["unknown"] == 1 and sm1["pnl_total"] == 100.0
    release = ({srv._burn_key(ph)}, set())
    books2 = srv._build_broker_books(chains, fills, release=release)
    out2, sm2 = srv.realized_walk([o, c, ph], broker_books=books2)
    assert sm2["unknown"] == 1 and sm2["pnl_total"] == 100.0


def test_twopass_veto_burns_never_release():
    fills = {"TV": [(7, -10.0, 100.0, "2026-10-23")]}
    veto = _ev("2026-10-23T10:00:00+00:00", "TV", "SHORT", "OPEN", 10.0, 90.0)
    veto["reason"] = "BROKER_SYNC_REJECTED_VALUE_CAP"
    close = _ev("2026-10-23T19:00:00+00:00", "TV", "SHORT", "CLOSE", 10.0, 95.0)
    chains = {("TV", "SHORT"): [veto, close]}
    books = srv._build_broker_books(
        chains, fills, release=({srv._burn_key(veto)}, {("TV", "SHORT")})
    )
    assert books.get(("TV", "SHORT")) is None


def test_36h_lots_expire_per_max_hold_ruling():
    o = _ev("2026-10-20T10:00:00+00:00", "ZX", "LONG", "OPEN", 10.0, 50.0)
    o["reason"] = "STRAT"
    c = _ev("2026-10-23T19:00:00+00:00", "ZX", "LONG", "CLOSE", 10.0, 60.0)
    out, sm = srv.realized_walk([o, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert "rpnl" not in row
    assert sm["unknown"] == 1 and sm["lots_expired"] >= 1


def test_load_crypto_maps_sides_and_days():
    import json

    fp = srv.ROOT / "data" / "live_desk" / "broker_fill_cache" / "ang_TESTLOADUSDT.json"
    rows = [
        {
            "symbol": "TESTLOADUSDT",
            "side": "BUY",
            "qty": "5",
            "price": "2.0",
            "time": 1792800000000,
            "id": 1,
        },
        {
            "symbol": "TESTLOADUSDT",
            "side": "SELL",
            "qty": "2",
            "price": "2.5",
            "time": 1792886400000,
            "id": 2,
        },
        {
            "symbol": "TESTLOADUSDT",
            "side": "",
            "qty": "9",
            "price": "9.0",
            "time": 1792886400000,
            "id": 3,
        },
    ]
    try:
        fp.write_text(json.dumps(rows))
        out = srv._load_crypto("ang")
        got = out.get("TESTLOADUSDT", [])
        assert [(round(q, 4), p) for (_, q, p, _) in got] == [(5.0, 2.0), (-2.0, 2.5)]
        assert got[0][3] == "2026-10-24" and got[1][3] == "2026-10-25"
    finally:
        try:
            fp.unlink()
        except OSError:
            pass


def test_clean_reason_swaps_placeholder_for_fill_qty():
    assert srv._clean_reason("DAYTRADE_TARGET_g2.27_qty_999999", 11.0).endswith(
        "_qty_11"
    )
    assert srv._clean_reason("X_qty_9999|Y", 0.5) == "X_qty_0.5|Y"
    assert srv._clean_reason("X_qty_9999_Y", 0.5) == "X_qty_9999_Y"
    # real threaded qtys untouched, even nine-ish ones matching the row
    assert srv._clean_reason("A_qty_1.5_B", 1.5) == "A_qty_1.5_B"
    assert srv._clean_reason("A_qty_9999_B", 9999.0) == "A_qty_9999_B"
    assert srv._clean_reason(None, 11.0) is None
    assert (
        srv._clean_reason("BROKER_SYNC_REJECTED_X_2555>2500", 6.0)
        == "BROKER_SYNC_REJECTED_X_2555>2500"
    )


def test_backfill_never_uses_future_fills():
    # Lookahead bias: a September fill must not price a March close.
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    fut_ep = (now + timedelta(days=180)).timestamp()
    cl = _ev(
        (now - timedelta(days=200)).isoformat(), "BF", "SHORT", "CLOSE", 10.0, 99.0
    )
    books = {("BF", "SHORT"): [[fut_ep, 10.0, 101.0]]}
    out, sm = srv.realized_walk([cl], broker_books=books)
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row.get("rpnl") is None
    assert sm["unknown"] == 1 and sm["broker_filled"] == 0
    assert books[("BF", "SHORT")] == [[fut_ep, 10.0, 101.0]]


def test_backfill_broker_lots_outlive_journal_expiry():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old_ep = (now - timedelta(days=42)).timestamp()
    old_cl = _ev(
        (now - timedelta(days=41, hours=20)).isoformat(),
        "BF",
        "SHORT",
        "CLOSE",
        10.0,
        99.0,
    )
    books = {("BF", "SHORT"): [[old_ep, 10.0, 101.0]]}
    out, sm = srv.realized_walk([old_cl], broker_books=books)
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row.get("rpnl") == 20.0
    assert sm["unknown"] == 0 and sm["broker_filled"] == 1
    cl = _ev(now.isoformat(), "BF", "SHORT", "CLOSE", 10.0, 99.0)
    out2, sm2 = srv.realized_walk([cl], broker_books=books)
    row2 = [e for e in out2 if e.get("action") == "CLOSE"][0]
    assert row2.get("rpnl") is None
    assert sm2["unknown"] == 1 and sm2["broker_filled"] == 0
    ancient_ep = (now - timedelta(days=121)).timestamp()
    books3 = {("BF", "SHORT"): [[ancient_ep, 10.0, 101.0]]}
    out3, sm3 = srv.realized_walk([cl], broker_books=books3)
    row3 = [e for e in out3 if e.get("action") == "CLOSE"][0]
    assert row3.get("rpnl") is None
    assert sm3["unknown"] == 1 and sm3["broker_filled"] == 0
    assert sm3["lots_expired"] == 1


def test_match_never_burns_for_echo_opens():
    st = {
        "ts": "2026-10-20T10:00:00+00:00",
        "symbol": "MM",
        "side": "LONG",
        "action": "OPEN",
        "qty": 10.0,
        "price": 50.0,
        "reason": "BROKER_SYNC_OPEN_FROM_FLAT_STUB",
    }
    fills = {"MM": [[None, 10.0, 50.0, "2026-10-20"]]}
    books = srv._build_broker_books({("MM", "LONG"): [st]}, fills)
    assert books.get(("MM", "LONG"), []) == [[None, 10.0, 50.0]]


def test_backfill_never_touches_stale_excluded():
    old = _ev("2026-09-02T10:00:00+00:00", "BF", "SHORT", "OPEN", 10.0, 112.0)
    old["reason"] = "ZOMBIE"
    cl = _ev("2026-10-23T19:00:00+00:00", "BF", "SHORT", "CLOSE", 15.0, 99.0)
    books = {("BF", "SHORT"): [[None, 5.0, 101.0]]}
    out, sm = srv.realized_walk(
        [old, cl], stale_basis_days=7, expiry_days=60, broker_books=books
    )
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row.get("stale_excluded") is True
    assert sm["broker_filled"] == 0
    assert sm["unknown"] == 1


def test_broker_match_burns_twins_and_quarantines_veto():
    j1 = {
        "ts": "2026-10-20T10:00:00+00:00",
        "symbol": "MM",
        "side": "LONG",
        "action": "OPEN",
        "qty": 10.0,
        "price": 50.0,
        "reason": "STRAT",
    }
    j2 = {
        "ts": "2026-10-20T11:00:00+00:00",
        "symbol": "MM",
        "side": "LONG",
        "action": "CLOSE",
        "qty": 10.0,
        "price": 52.0,
        "reason": "STRAT_X",
    }
    v = {
        "ts": "2026-10-21T10:00:00+00:00",
        "symbol": "MM",
        "side": "LONG",
        "action": "OPEN",
        "qty": 5.0,
        "price": 51.0,
        "reason": "BROKER_SYNC_REJECTED_X",
    }
    fills = {
        "MM": [
            [None, 10.0, 50.0, "2026-10-20"],
            [None, -10.0, 52.0, "2026-10-20"],
            [None, 5.0, 51.0, "2026-10-21"],
        ]
    }
    chains = {("MM", "LONG"): [j1, j2, v]}
    books = srv._build_broker_books(chains, fills)
    assert books.get(("MM", "LONG"), []) == []
    assert books.get(("MM", "SHORT"), []) == []


def test_close_on_expired_only_book_is_honest_unknown():
    from datetime import timedelta

    now = datetime.now(timezone.utc)
    old = (now - timedelta(days=30)).isoformat()
    z = _ev(old, "EX", "LONG", "OPEN", 100.0, 50.0)
    z["reason"] = "PHANTOM_ERA"
    c = _ev(now.isoformat(), "EX", "LONG", "CLOSE", 5.0, 55.0)
    out, sm = srv.realized_walk([z, c])
    row = [e for e in out if e.get("action") == "CLOSE"][0]
    assert row.get("rpnl") is None
    assert sm["unknown"] == 1 and sm["lots_expired"] == 1


def test_collapse_keeps_strategy_pair():
    from datetime import timedelta

    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    a = _ev(iso(now), "W", "LONG", "REDUCE", 4.0, 10.0)
    a["reason"] = "STRAT_A"
    b = _ev(iso(now + timedelta(seconds=30)), "W", "LONG", "REDUCE", 4.0, 10.0)
    b["reason"] = "STRAT_B"
    kept, n = srv.collapse_echoes([a, b])
    assert len(kept) == 2 and n == 0


def test_crosscheck_direction_mapping():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "trb_broker_crosscheck",
        str(ROOT / "tools" / "live_desk" / "trb_broker_crosscheck.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._jdir("OPEN", "LONG") == "BUY"
    assert mod._jdir("CLOSE", "SHORT") == "BUY"
    assert mod._jdir("OPEN", "SHORT") == "SELL"
    assert mod._jdir("REDUCE", "LONG") == "SELL"
    assert mod._kline_day_check("NOSUCH", "2026-10-22", 1.0) is None


def test_stale_basis_excluded_from_sums_not_hidden():
    old = _ev("2026-09-02T13:36:00+00:00", "BABA", "SHORT", "OPEN", 10.0, 112.0)
    old["reason"] = "DC_BREAK_LOW_15M"
    new = _ev("2026-10-22T18:17:00+00:00", "BABA", "SHORT", "OPEN", 11.0, 101.0)
    new["reason"] = "WT_OPEN"
    close = _ev("2026-10-23T19:58:00+00:00", "BABA", "SHORT", "CLOSE", 5.0, 99.0)
    close["reason"] = "DAYTRADE_TARGET"
    out, s = srv.realized_walk([old, new, close], stale_basis_days=7, expiry_days=60)
    assert s["known"] == 1 and s["stale_excluded"] == 1
    assert s["stale_excluded_sum"] == round(
        5.0 * ((10 * 112.0 + 11 * 101.0) / 21.0 - 99.0), 2
    )
    assert s["pnl_total"] == 0.0
    row = [e for e in out if e.get("stale_excluded")][0]
    assert row["rpnl"] is None
    out2, s2 = srv.realized_walk([old, new, close])
    assert s2["stale_excluded"] == 0 and s2["known"] == 1 and s2["pnl_total"] != 0.0


def _seed_1m(symbol, pairs):
    import time as _t

    srv._cache[f"kline1m:{symbol}"] = (_t.time(), sorted(pairs))


def test_clean_close_keeps_journal_price_despite_klines():
    # HOOD lesson: journal 98.12 matched broker 98.15 to 3c while the 1m bar
    # printed 99.64 (displaced timestamp). Kline must never overrule a clean
    # close — it only prices the fresh-basis display of stale closes.
    e0 = srv._parse_ts("2026-10-22T18:17:00+00:00").timestamp()
    e1 = srv._parse_ts("2026-10-23T19:58:00+00:00").timestamp()
    _seed_1m("KLINE", [(e0 - 30, 101.85), (e1 - 30, 99.55)])
    op = _ev("2026-10-22T18:17:00+00:00", "KLINE", "SHORT", "OPEN", 11.0, 102.10)
    op["reason"] = "WT_OPEN"
    cl = _ev("2026-10-23T19:58:00+00:00", "KLINE", "SHORT", "CLOSE", 11.0, 99.50)
    cl["reason"] = "DAYTRADE_TARGET"
    out, s = srv.realized_walk([op, cl], use_kline_px=True)
    row = [e for e in out if e.get("rpnl") is not None][0]
    assert row["rpnl"] == round(11.0 * (102.10 - 99.50), 2)
    assert "px_warn" not in row


def test_stale_close_shows_fresh_basis_number():
    e0 = srv._parse_ts("2026-09-02T13:36:00+00:00").timestamp()
    e1 = srv._parse_ts("2026-10-22T18:17:00+00:00").timestamp()
    e2 = srv._parse_ts("2026-10-23T19:58:00+00:00").timestamp()
    _seed_1m("FRESH", [(e0 - 30, 112.0), (e1 - 30, 101.85), (e2 - 30, 99.55)])
    old = _ev("2026-09-02T13:36:00+00:00", "FRESH", "SHORT", "OPEN", 10.0, 112.0)
    old["reason"] = "DC_OPEN"
    new = _ev("2026-10-22T18:17:00+00:00", "FRESH", "SHORT", "OPEN", 11.0, 101.90)
    new["reason"] = "WT_OPEN"
    close = _ev("2026-10-23T19:58:00+00:00", "FRESH", "SHORT", "CLOSE", 11.0, 99.60)
    close["reason"] = "DAYTRADE_TARGET"
    out, s = srv.realized_walk(
        [old, new, close], stale_basis_days=7, use_kline_px=True, expiry_days=60
    )
    row = [e for e in out if e.get("stale_excluded")][0]
    assert row["rpnl"] == round(11.0 * (101.85 - 99.55), 2)
    assert row["fresh_basis"] is True
    assert s["pnl_total"] == 0.0 and s["stale_excluded"] == 1


def test_stale_close_without_fresh_cover_shows_dash():
    old = _ev("2026-09-02T13:36:00+00:00", "NOFRESH", "SHORT", "OPEN", 10.0, 112.0)
    old["reason"] = "DC_OPEN"
    close = _ev("2026-10-23T19:58:00+00:00", "NOFRESH", "SHORT", "CLOSE", 5.0, 99.0)
    close["reason"] = "DAYTRADE_TARGET"
    out, s = srv.realized_walk(
        [old, close], stale_basis_days=7, use_kline_px=True, expiry_days=60
    )
    row = [e for e in out if e.get("stale_excluded")][0]
    assert row["rpnl"] is None and s["pnl_total"] == 0.0


def test_recent_basis_still_sums_with_stale_param():
    new = _ev("2026-10-22T18:17:00+00:00", "BABA", "SHORT", "OPEN", 11.0, 101.0)
    new["reason"] = "WT_OPEN"
    close = _ev("2026-10-23T19:58:00+00:00", "BABA", "SHORT", "CLOSE", 11.0, 99.0)
    close["reason"] = "DAYTRADE_TARGET"
    out, s = srv.realized_walk([new, close], stale_basis_days=7)
    assert s["stale_excluded"] == 0 and s["known"] == 1
    assert [e for e in out if e.get("rpnl") is not None][0]["rpnl"] == 22.0


def test_strategy_reject_exits_still_price():
    evs = [
        _ev("2026-10-23T13:00:00+00:00", "X", "LONG", "OPEN", 10.0, 100.0),
        _ev("2026-10-23T14:00:00+00:00", "X", "LONG", "CLOSE", 10.0, 101.0),
    ]
    evs[0]["reason"] = "SOME_OPEN"
    evs[1]["reason"] = "WICK_REJECT_EXIT_1h_upper-wick0.94_g1.64_qty_10"
    out, s = srv.realized_walk(evs)
    assert s["rejected_skipped"] == 0 and s["known"] == 1
    assert [e for e in out if e.get("rpnl") is not None][0]["rpnl"] == 10.0


def test_unpriced_row_stays_missing_never_zero(repo):
    rows = {
        "x:ZOMB_SHORT": {
            "symbol": "ZOMB",
            "position_side": "SHORT",
            "positionAmt": 102.7,
            "entry_price": 0.08525143297099039,
            "mark_price": None,
            "gain": None,
            "max_gain": 1.246,
            "unrealized_pnl_USD": None,
            "realized_pnl": None,
            "opened_at": "2026-03-10T21:29:05Z",
            "last_updated": None,
        }
    }
    (repo / "fin" / "short_positions.json").write_text(json.dumps(rows))
    snap = srv.read_positions("fin")
    z = [r for r in snap["sides"]["short"] if r["symbol"] == "ZOMB"][0]
    assert z["mark"] is None and z["gain"] is None and z["upnl"] is None
    assert z["entry"] == 0.08525143297099039
    assert snap["totals"]["upnl"] == 25.0


def test_close_flag_key_and_label():
    k = srv._flag_key("BABA", "short", "2026-10-23T19:58:35.139028+00:00", 99.555)
    assert k == "BABA|SHORT|2026-10-23T19:58:35.139028+00:00|99.555"
    assert srv._flag_label({"stub": True, "stale_d": 51.2}) == "stub-basis basis~51.2d"
    assert srv._flag_label({"oor": [90.0, 91.0]}) == "off-kline"
    assert srv._flag_label({}) == "?"


def test_basisless_echo_twins_hidden():
    now = datetime.now(timezone.utc).replace(microsecond=0)
    iso = lambda dt: dt.isoformat()
    from datetime import timedelta

    a1 = _ev(iso(now - timedelta(minutes=10)), "G", "LONG", "REDUCE", 10.0, 200.0)
    a1["reason"] = "HTF_AGAINST_FORCE_CLOSE_wt1h-96"
    a2 = _ev(
        iso(now - timedelta(minutes=10) + timedelta(seconds=2)),
        "G",
        "LONG",
        "REDUCE",
        10.0,
        200.0,
    )
    a2["reason"] = "HEDGE_FAILED_FALLBACK_CLOSE_mateX_fail"
    b1 = _ev(iso(now - timedelta(hours=5)), "H", "LONG", "REDUCE", 7.0, 50.0)
    b1["reason"] = "SOME_EXIT"
    out, s = srv.realized_walk([a1, a2, b1])
    assert s["unknown"] == 2
    assert s["unknown_phantom"] == 2
    assert s["sync_hidden"] == 1
    assert s["pnl_total"] == 0.0
    assert [e for e in out if e.get("reason", "").startswith("HEDGE_FAILED")][0].get(
        "sync_hidden"
    ) is True


def test_intent_validation():
    ok, err = srv.validate_intent(
        {"account": "fin", "symbol": "dogeusdt", "side": "long", "qty": 5}
    )
    assert err is None and ok == {
        "acct": "fin",
        "symbol": "DOGEUSDT",
        "side": "LONG",
        "qty": 5.0,
    }
    for bad in (
        {"account": "zzz", "symbol": "DOGEUSDT", "side": "LONG", "qty": 5},
        {"account": "fin", "symbol": "../x", "side": "LONG", "qty": 5},
        {"account": "fin", "symbol": "DOGEUSDT", "side": "HOLD", "qty": 5},
        {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": 0},
        {"account": "fin", "symbol": "DOGEUSDT", "side": "LONG", "qty": -3},
        [],
    ):
        _, err = srv.validate_intent(bad)
        assert err, bad


def test_low_funds_boundary():
    assert srv.low_funds(10000.0, 400.0) is True
    assert srv.low_funds(10000.0, 500.0) is False
    assert srv.low_funds(10000.0, 9000.0) is False
    assert srv.low_funds(None, 10.0) is False
    assert srv.low_funds(100.0, None) is False
    assert srv.low_funds(0.0, 0.0) is False


def test_sum_windows_buckets_and_future_clamp():
    import time

    now = time.time()
    noon = now - (now % 86400) + 12 * 3600
    if noon > now:
        noon -= 86400
    now_s = noon + 3600
    w, daily = srv.sum_windows(
        [
            (noon - 100, 10.0),
            (noon - 86400 + 50, 3.0),
            (noon - 2 * 86400, -4.0),
            (noon - 10 * 86400, 7.0),
            (noon - 40 * 86400, 100.0),
            (noon + 12 * 3600, 5.0),
        ],
        now_s,
    )
    assert w["b_today"] == 15.0
    assert w["b_yest"] == 3.0
    assert w["b_7d"] == 14.0
    assert w["b_30d"] == 21.0
    assert sum(daily.values()) == 21.0
    assert w["d_today"] == time.strftime("%Y-%m-%d", time.gmtime(now_s))
    assert w["d_yest"] == time.strftime("%Y-%m-%d", time.gmtime(now_s - 86400))
    assert srv.sum_windows([("bad", 1.0)], now_s)[0]["b_today"] == 0.0
    assert srv.sum_windows([("bad", 1.0)], now_s)[1] == {}


def test_binance_sign_shape():
    import re

    s1 = srv._binance_sign("secretA", "timestamp=123&recvWindow=10000")
    s2 = srv._binance_sign("secretA", "timestamp=123&recvWindow=10000")
    s3 = srv._binance_sign("secretB", "timestamp=123&recvWindow=10000")
    assert re.fullmatch(r"[0-9a-f]{64}", s1)
    assert s1 == s2
    assert s1 != s3


def test_write_intent_ttl_and_audit(repo):
    before = time.time()
    intent = srv.write_intent("fin", "DOGEUSDT", "LONG", 5.0)
    assert intent["status"] == "STAGED"
    assert intent["expires_at"] - intent["armed_at"] == 300
    assert intent["armed_at"] >= before
    p = repo / "data" / "manual_orders" / "fin" / f"{intent['uuid']}.json"
    assert json.loads(p.read_text())["symbol"] == "DOGEUSDT"
    audit = (repo / "data" / "manual_orders" / "audit.jsonl").read_text()
    assert intent["uuid"] in audit and "STAGED" in audit


def _paper(ts, sym, side, qty, price):
    e = _ev(ts, sym, side, "REDUCE", qty, price)
    e["reason"] = "VEC_DRIVEN_CLOSE_PURE_BOARD_BOARD_FLAT_LIVE_HOLDS_X"
    return e


def test_paper_twin_exact_match_prices():
    day = "2026-10-24"
    prow = _paper(day + "T02:21:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    twins = {("MSK", day, "BUY"): [[100.0, 65.0, 0.3955, 2.24, "1"]]}
    out, sm = srv.realized_walk([prow], broker_twins=twins)
    row = [e for e in out if e.get("action") == "REDUCE"][0]
    assert row.get("paper") is True
    assert row.get("paper_twin") is True
    assert row.get("rpnl") == 2.24
    assert sm["paper_twinned"] == 1 and sm["paper_twinned_pnl"] == 2.24


def test_paper_twin_priced_rows_claim_first():
    day = "2026-10-24"
    live = _ev(day + "T01:00:00+00:00", "MSK", "SHORT", "OPEN", 65.0, 0.39)
    live["reason"] = "STRAT"
    close = _ev(day + "T02:00:00+00:00", "MSK", "SHORT", "REDUCE", 65.0, 0.3955)
    close["reason"] = "STRAT_X"
    prow = _paper(day + "T03:00:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    twins = {("MSK", day, "BUY"): [[100.0, 65.0, 0.3955, 2.24, "1"]]}
    out, sm = srv.realized_walk([live, close, prow], broker_twins=twins)
    prow_out = [e for e in out if e.get("paper")]
    assert len(prow_out) == 1 and prow_out[0].get("rpnl") is None
    assert sm["paper_twinned"] == 0


def test_paper_twin_first_of_dupes_only():
    day = "2026-10-24"
    p1 = _paper(day + "T02:21:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    p2 = _paper(day + "T03:21:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    twins = {("MSK", day, "BUY"): [[100.0, 65.0, 0.3955, 2.24, "1"]]}
    out, sm = srv.realized_walk([p1, p2], broker_twins=twins)
    rpnls = sorted(
        (e.get("rpnl") is None, e.get("rpnl"))
        for e in out
        if e.get("action") == "REDUCE"
    )
    assert rpnls == [(False, 2.24), (True, None)]
    assert sm["paper_twinned"] == 1


def test_paper_twin_never_cross_day_or_veto():
    day = "2026-10-24"
    prow = _paper(day + "T02:21:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    veto = _paper(day + "T03:21:00+00:00", "MSK", "SHORT", 65.0, 0.3955)
    veto["reason"] = "BROKER_SYNC_REJECTED_X"
    twins = {("MSK", "2026-10-23", "BUY"): [[100.0, 65.0, 0.3955, 9.99, "1"]]}
    out, sm = srv.realized_walk([prow, veto], broker_twins=twins)
    assert all(e.get("rpnl") is None for e in out if e.get("action") == "REDUCE")
    assert sm["paper_twinned"] == 0
