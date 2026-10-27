"""USER 2026-10-27 "Every REAL trade has to be added to the /history/ folder on macbook with the quantity $ value and reason":
S1/S5 vector_direct fills land once as order rows; positions-service echoes/re-logs are dropped by readers, not deleted.
"""

import json
import time
from datetime import datetime, timezone

from tools import vector_direct_fill_export as vx
from tools import vector_direct_history_import as vi
from vec_decisions import execution_ledger as el
from vec_decisions import history_canon as hc


def _iso(t):
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def _rec(
    acct="ang",
    ss="GRTUSDT_LONG",
    act="OPEN",
    oid="13969292558",
    q=497.0,
    px=0.02261,
    t=1793137718.0,
    why="board holds since 1793116800, broker flat (engine qty ratio 1.000)",
):
    return {
        "venue": "crypto",
        "acct": acct,
        "ss": ss,
        "act": act,
        "why": why,
        "key": f"{acct}|{ss}|1793116800|{act}",
        "ledger_ts": t,
        "ledger_iso": "2026-10-27T21:48:38Z",
        "ledger_status": "SENT",
        "sent_mark": 0.0226,
        "sent_qty": q,
        "resp": {"orderId": int(oid)},
        "fill": {
            "found": True,
            "order_id": oid,
            "status": "FILLED",
            "exec_qty": q,
            "avg_price": px,
            "quote": q * px,
            "fill_ms": int(t * 1000),
            "src": "test",
        },
        "final": True,
    }


def _echo(t, typ, q, px, reason="VEC_DRIVEN_OPEN_RECONCILE_", ind=None):
    return {
        "ts": _iso(t),
        "type": typ,
        "qty": q,
        "price": px,
        "value": round(q * px, 2),
        "reason": reason,
        "indicators": ind if ind is not None else {"price": px, "k_3m": 17.6},
        "prov": {"v": 1, "src": "NATIVE"},
    }


def test_build_row_carries_fill_reason_value_and_order_id():
    r = vi.build_row(_rec(), "S1_VECTOR_DIRECT")
    assert (
        r["type"] == "OPEN"
        and r["qty"] == 497.0
        and r["price"] == 0.02261
        and r["value"] == round(497 * 0.02261, 2)
    )
    assert (
        r["order_id"] == "13969292558"
        and r["reason"].startswith("board holds")
        and r["prov"]["src"] == "S1_VECTOR_DIRECT"
    )
    assert r["ts"].startswith("2026-10-27T21:48:38")
    bad = _rec()
    bad["fill"]["exec_qty"] = 0.0
    assert vi.build_row(bad, "S1_VECTOR_DIRECT") is None
    trav = _rec(ss="../../etc_LONG")
    assert vi.build_row(trav, "S1_VECTOR_DIRECT") is None


def test_ingest_exactly_once_even_without_state(tmp_path):
    hist = tmp_path / "history"
    st = {}
    out = vi.ingest("crypto", [_rec()], st, hist, time.time())
    assert out["appended"] == 1
    assert vi.ingest("crypto", [_rec()], st, hist, time.time())["appended"] == 0
    assert (
        vi.ingest("crypto", [_rec()], {}, hist, time.time())["appended"] == 0
    )  # state lost: the file scan still dedupes
    lines = (hist / "ang" / "GRTUSDT_LONG.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["order_id"] == "13969292558"


def test_unresolved_order_stays_pending_never_guessed(tmp_path):
    rec = _rec()
    rec["fill"], rec["final"] = None, False
    st = {}
    out = vi.ingest("crypto", [rec], st, tmp_path, time.time())
    assert out == {"seen": 1, "appended": 0, "already": 0, "no_fill": 0, "pending": 1}
    assert not (tmp_path / "ang").exists() and len(st["pending"]) == 1


def test_canon_drops_echo_and_restart_relog_measured_grt_20261027():
    t = 1793137718.0  # 21:48:38Z S1 OPEN 497 (broker: 310+187 @ 0.02261); Mac echo 21:50:05 and restart re-log 22:26:09, no broker trade then
    echo = _echo(t + 87, "AUGMENT", 497.0, 0.02264)
    relog = dict(echo, ts=_iso(t + 2251), price=0.02248784)
    order = vi.build_row(_rec(t=t), "S1_VECTOR_DIRECT")
    gap = {
        "ts": _iso(t - 4767),
        "type": "REDUCE",
        "qty": 1244.0,
        "price": 0.02242,
        "value": 27.89,
        "reason": "BROKER_RECONCILED_GAP order=13969254995",
        "indicators": {},
    }
    rows = [gap, echo, relog, order]
    sh = hc.classify(rows)
    assert sh == {1: ("FILL_ECHO", "13969292558"), 2: ("RESTART_RELOG", "13969292558")}
    kept = hc.canonical(rows)
    assert [r["type"] for r in kept] == ["REDUCE", "OPEN"] and sum(
        r["qty"] for r in kept if r["type"] == "OPEN"
    ) == 497.0


def test_canon_netted_echo_and_reconciled_dup():
    t = 1793136000.0  # FLOKI: S1 OPEN 483 then REDUCE 120 one minute later, Mac logged one AUGMENT 363
    o1 = vi.build_row(
        _rec(ss="1000FLOKIUSDT_SHORT", oid="1", q=483.0, px=0.02326, t=t),
        "S1_VECTOR_DIRECT",
    )
    o2 = vi.build_row(
        _rec(
            ss="1000FLOKIUSDT_SHORT",
            act="REDUCE",
            oid="2",
            q=120.0,
            px=0.02327,
            t=t + 60,
        ),
        "S1_VECTOR_DIRECT",
    )
    net = _echo(t + 426, "AUGMENT", 363.0, 0.02321)
    dup = {
        "ts": _iso(t + 60),
        "type": "REDUCE",
        "qty": 120.0,
        "price": 0.02327,
        "reason": "BROKER_RECONCILED_GAP order=2",
        "indicators": {},
    }
    sh = hc.classify([o1, o2, net, dup])
    assert sh[2][0] == "NET_ECHO" and sh[3] == ("RECONCILED_DUP", "2")


def test_canon_leaves_files_without_order_rows_untouched():
    rows = [_echo(1.0e9, "AUGMENT", 5.0, 1.0), _echo(1.0e9 + 10, "AUGMENT", 5.0, 1.0)]
    assert hc.classify(rows) == {} and hc.canonical(rows) == rows


def test_external_echo_guard_blocks_writer_echo_only(tmp_path):
    base = tmp_path / "base"
    p = base / "data" / "history" / "ang" / "XRPUSDC_LONG.jsonl"
    p.parent.mkdir(parents=True)
    now = time.time()
    order = vi.build_row(
        _rec(ss="XRPUSDC_LONG", oid="8557280250", q=11.3, px=1.324, t=now - 56),
        "S1_VECTOR_DIRECT",
    )
    p.write_text(json.dumps(order) + "\n")
    assert (
        el.external_echo(str(p), "AUGMENT", 11.3, now) is True
    )  # the echo of S1's fill
    assert el.history_write_allowed(str(p), "AUGMENT", 11.3, 1.3237) is False
    assert (
        el.external_echo(str(p), "AUGMENT", 7.0, now) is False
    )  # a different quantity is a different trade
    assert el.external_echo(str(p), "REDUCE", 11.3, now) is False
    with open(p, "a") as fh:
        fh.write(json.dumps(_echo(now - 5, "AUGMENT", 11.3, 1.3237)) + "\n")
    assert (
        el.external_echo(str(p), "AUGMENT", 11.3, now) is False
    )  # already echoed once: real mode lets a genuine repeat through
    (base / "data" / "MAC_PAPER_TRADING").write_text("PAPER")
    assert (
        el.external_echo(str(p), "AUGMENT", 11.3, now + 2000) is True
    )  # paper: the Mac cannot fill, restart re-log refused
    assert (
        el.history_write_allowed(str(p), "AUGMENT", 11.3, 1.3, echo_guard=False) is True
    )


def test_export_resolves_once_then_serves_cache(tmp_path):
    out = tmp_path / "data" / "vector_direct"
    out.mkdir(parents=True)
    now = time.time()
    day = datetime.fromtimestamp(now, tz=timezone.utc).strftime("%Y-%m-%d")
    rows = [
        {
            "ts": now - 100,
            "event": "ORDER",
            "dry_run": False,
            "acct": "flz",
            "ss": "DASHUSDT_SHORT",
            "act": "OPEN",
            "key": "flz|DASHUSDT_SHORT|1|OPEN",
            "why": "w",
            "price": 55.38,
            "qty": 0.27,
            "status": "SENT",
            "resp": {"orderId": 10950681875, "avgPrice": None},
        },
        {
            "ts": now - 90,
            "event": "ORDER",
            "dry_run": False,
            "acct": "fin",
            "ss": "KAVAUSDT_LONG",
            "act": "OPEN",
            "key": "fin|KAVAUSDT_LONG|1|OPEN",
            "why": "w",
            "status": "ERROR",
            "detail": "HTTP 400 /fapi/v1/order: Margin is insufficient.",
        },
        {
            "ts": now - 80,
            "event": "ORDER",
            "dry_run": False,
            "acct": "ang",
            "ss": "SOLUSDC_LONG",
            "act": "OPEN",
            "key": "ang|SOLUSDC_LONG|1|OPEN",
            "why": "w",
            "status": "ERROR",
            "detail": "ReadTimeout",
        },
    ]
    (out / f"ledger_crypto_{day}.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + "\n"
    )

    class Fake:
        calls = []

        def resolve(self, acct, ss, order_id=None, client=None):
            self.calls.append((acct, order_id, client))
            if order_id:
                return {
                    "found": True,
                    "order_id": order_id,
                    "status": "FILLED",
                    "exec_qty": 0.27,
                    "avg_price": 55.37,
                    "quote": 14.9499,
                    "fill_ms": int((now - 99) * 1000),
                }
            return {"found": False}

    fake = Fake()
    got = []
    st = vx.export(
        "crypto", tmp_path, now - 3600, 10, broker=fake, now=now, emit=got.append
    )
    assert len(fake.calls) == 2 and fake.calls[1][2] == vx.client_id(
        "crypto", "ang|SOLUSDC_LONG|1|OPEN"
    )  # timeout row looked up by client id; 4xx never
    recs = [json.loads(x) for x in got if "_stats" not in x]
    assert len(recs) == 1 and recs[0]["fill"]["avg_price"] == 55.37 and recs[0]["final"]
    fake.calls.clear()
    vx.export(
        "crypto", tmp_path, now - 3600, 10, broker=fake, now=now, emit=lambda x: None
    )
    assert fake.calls == [] and st["sent"] == 1


def test_guardian_reconcile_matches_order_rows_by_broker_id(tmp_path, monkeypatch):
    from tools.forward_parity import live_guardian as lg

    monkeypatch.setattr(lg, "ROOT", tmp_path)
    hdir = tmp_path / "data" / "history" / "ang"
    hdir.mkdir(parents=True)
    now = time.time()
    order = vi.build_row(_rec(t=now - 1200), "S1_VECTOR_DIRECT")
    (hdir / "GRTUSDT_LONG.jsonl").write_text(json.dumps(order) + "\n")

    def fake_fetch(
        sym, start_ms
    ):  # two partial broker fills of the one order: neither qty equals the order row
        return [
            {
                "time": int((now - 1200) * 1000),
                "qty": "310",
                "price": "0.02261",
                "side": "BUY",
                "positionSide": "LONG",
                "orderId": 13969292558,
            },
            {
                "time": int((now - 1200) * 1000),
                "qty": "187",
                "price": "0.02261",
                "side": "BUY",
                "positionSide": "LONG",
                "orderId": 13969292558,
            },
            {
                "time": int((now - 100) * 1000),
                "qty": "5",
                "price": "0.0227",
                "side": "SELL",
                "positionSide": "LONG",
                "orderId": 9,
            },
        ]

    out = lg.reconcile_account("ang", fake_fetch, ["GRTUSDT"], now, min_age_s=600.0)
    assert (
        out["matched"] == 2 and out["appended"] == 0 and out["broker_fills"] == 2
    )  # the 100 s-old fill waits for the next hourly pass


def test_readers_count_one_trade(tmp_path, monkeypatch):
    from tools.forward_parity import live_guardian as lg
    from tools.forward_parity import live_vs_vec as lv

    monkeypatch.setattr(lg, "ROOT", tmp_path)
    monkeypatch.setattr(lv, "ROOT", tmp_path)
    hdir = tmp_path / "data" / "history" / "ang"
    hdir.mkdir(parents=True)
    now = time.time()
    t = now - 3000
    order = vi.build_row(_rec(t=t), "S1_VECTOR_DIRECT")
    echo = _echo(t + 87, "AUGMENT", 497.0, 0.02264)
    relog = dict(echo, ts=_iso(t + 2251))
    (hdir / "GRTUSDT_LONG.jsonl").write_text(
        "".join(json.dumps(r) + "\n" for r in (echo, relog, order))
    )
    assert lg.ledger_amount("ang", "GRTUSDT_LONG") == 497.0
    assert lg.amount_at("ang", "GRTUSDT_LONG", now) == 497.0
    fills = lg.recent_fills("ang", now - 7200)["ang:GRTUSDT_LONG"]
    assert len(fills) == 1 and fills[0]["ext"] and fills[0]["price"] == 0.02261
    h = lv.load_history("ang", "GRTUSDT_LONG")
    assert len(h) == 1 and h[0]["type"] == "OPEN" and h[0]["mkt_px"] == 0.02261
