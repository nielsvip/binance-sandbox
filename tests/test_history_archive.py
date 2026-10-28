"""History is never deleted (USER 2026-10-27): a live ledger rolls at ~10 MB into archive/ segments, every byte survives;
Mac fills carry their broker order_id (additive field), each order_id lands on exactly one row."""
import asyncio
import hashlib
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import ez_positions_service as _eps
from tools import history_7d_sync as _sync
from vec_decisions import execution_ledger as _el
from vec_decisions import history_archive as _ha
from vec_decisions import history_canon as _hc


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _sha(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _fill(path, nbytes, ts=None):
    ts = ts or datetime.now(timezone.utc) - timedelta(days=30)
    row = json.dumps({"ts": ts.isoformat(), "type": "AUGMENT", "qty": 1.0, "price": 1.0, "reason": "x" * 200})
    n = nbytes // (len(row) + 1) + 1
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text((row + "\n") * n)


def test_default_limit_is_10mb_not_200kb():
    assert _ha.LIVE_MAX_BYTES == 10 * 1024 * 1024
    assert _ha.live_max_bytes() >= 1024 * 1024


def test_below_limit_never_rotates(tmp_path):
    p = tmp_path / "flz" / "XRPUSDC_LONG.jsonl"
    _fill(p, 300 * 1024)
    assert _ha.rotate_if_needed(p) is None and p.exists()


def test_rotation_renames_into_archive_and_never_unlinks(tmp_path):
    p = tmp_path / "flz" / "XRPUSDC_LONG.jsonl"
    _fill(p, 2000)
    h1 = _sha(p)
    s1 = _ha.rotate_if_needed(p, max_bytes=1000)
    assert s1 is not None and not p.exists() and s1.parent.name == "archive" and _sha(s1) == h1
    assert _ha.ledger_name(s1) == "XRPUSDC_LONG"
    _fill(p, 3000)
    h2 = _sha(p)
    s2 = _ha.rotate_if_needed(p, max_bytes=1000)
    assert s2 != s1 and _sha(s1) == h1 and _sha(s2) == h2
    assert [x.name for x in _ha.local_segments(p)] == [s1.name, s2.name]


def test_iter_rows_reads_legacy_bak_archive_and_live_oldest_first(tmp_path):
    d = tmp_path / "fin"
    d.mkdir()
    live = d / "BTCUSDC_SHORT.jsonl"
    old = d / "BTCUSDC_SHORT.jsonl.bak"
    old.write_text(json.dumps({"ts": "2026-08-01T00:00:00+00:00", "n": 1}) + "\n")
    os.utime(old, (time.time() - 3000, time.time() - 3000))
    seg = _ha.segment_path(live)
    seg.parent.mkdir()
    seg.write_text(json.dumps({"ts": "2026-09-01T00:00:00+00:00", "n": 2}) + "\n")
    os.utime(seg, (time.time() - 2000, time.time() - 2000))
    live.write_text(json.dumps({"ts": "2026-10-01T00:00:00+00:00", "n": 3}) + "\n")
    assert [r["n"] for r in _ha.iter_rows(live)] == [1, 2, 3]


def test_iter_rows_reads_zst_segment(tmp_path):
    live = tmp_path / "men" / "ETHUSDC_LONG.jsonl"
    seg = _ha.segment_path(live)
    seg.parent.mkdir(parents=True)
    seg.write_text(json.dumps({"ts": "2026-09-01T00:00:00+00:00", "n": 7}) + "\n")
    os.system(f"PATH=/opt/homebrew/bin:$PATH zstd -q --rm {seg}")
    assert Path(str(seg) + ".zst").exists()
    assert [r["n"] for r in _ha.iter_rows(live)] == [7]


def _service(tmp_path):
    stub = SimpleNamespace(_history_dedup={}, _HISTORY_DEDUP_WINDOW_S=1.0, config=SimpleNamespace(DATA_DIR=tmp_path, HISTORY_PROV_ENABLED=False), redis_manager=None)
    return _eps.PositionService._append_to_history, stub


def test_service_writer_rolls_over_without_losing_a_byte(tmp_path, monkeypatch):
    monkeypatch.setenv("HISTORY_LIVE_MAX_BYTES", str(1024 * 1024))
    fn, stub = _service(tmp_path)
    live = tmp_path / "history" / "flz" / "SOLUSDC_LONG.jsonl"
    _fill(live, 1024 * 1024 + 10)
    before = _sha(live)
    _run(fn(stub, "flz:SOLUSDC_LONG", "AUGMENT", 2.0, 150.0, {"reason": "T"}))
    segs = _ha.local_segments(live)
    assert len(segs) == 1 and _sha(segs[0]) == before
    rows = [json.loads(x) for x in live.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["qty"] == 2.0 and "order_id" not in rows[0]
    assert not list(live.parent.glob("*.bak"))


def test_service_writer_under_limit_appends_in_place(tmp_path):
    fn, stub = _service(tmp_path)
    live = tmp_path / "history" / "flz" / "SOLUSDC_LONG.jsonl"
    _fill(live, 250 * 1024)
    n0 = len(live.read_text().splitlines())
    _run(fn(stub, "flz:SOLUSDC_LONG", "AUGMENT", 2.0, 150.0, {"reason": "T"}))
    assert len(live.read_text().splitlines()) == n0 + 1 and not (live.parent / "archive").exists()


def test_claim_single_filled_order_once():
    pk = "men:AAA_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 111, 5.0, client_order_id="abc", status="FILLED", increases=True)
    assert _eps.claim_order_fill(pk, 5.0, "AUGMENT") == {"order_id": "111"}
    assert _eps.claim_order_fill(pk, 5.0, "AUGMENT") == {}


def test_claim_respects_direction():
    pk = "men:BBB_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 222, 5.0, status="FILLED", increases=False)
    assert _eps.claim_order_fill(pk, 5.0, "AUGMENT") == {}
    assert _eps.claim_order_fill(pk, 5.0, "REDUCE") == {"order_id": "222"}


def test_partial_fills_order_id_on_completing_piece_only():
    pk = "men:CCC_SHORT"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 333, 3.0, status="PARTIALLY_FILLED", increases=True)
    assert _eps.claim_order_fill(pk, 3.0, "AUGMENT") == {"order_id_partial": "333"}
    _eps.note_order_fill(pk, 333, 10.0, status="FILLED", increases=True)
    assert _eps.claim_order_fill(pk, 7.0, "AUGMENT") == {"order_id": "333", "order_fill_prior_qty": 3.0}


def test_external_vector_direct_order_is_echo_not_order_row():
    pk = "fin:DDD_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 444, 2.0, client_order_id="vd0123456789abcdef", status="FILLED", increases=False)
    assert _eps.claim_order_fill(pk, 2.0, "REDUCE") == {"echo_of_order_id": "444"}


def test_netted_orders_listed():
    pk = "fin:EEE_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    now = time.time()
    _eps.note_order_fill(pk, 1, 1.0, status="FILLED", increases=True, now=now - 5)
    _eps.note_order_fill(pk, 2, 2.0, status="FILLED", increases=True, now=now - 4)
    assert _eps.claim_order_fill(pk, 3.0, "AUGMENT", now=now) == {"order_ids": ["1", "2"]}


def test_expired_order_not_claimed():
    pk = "fin:FFF_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 9, 1.0, status="FILLED", increases=True, now=time.time() - 4000)
    assert _eps.claim_order_fill(pk, 1.0, "AUGMENT") == {}


def test_service_row_carries_order_id_and_canon_keeps_it_once(tmp_path):
    fn, stub = _service(tmp_path)
    pk = "ang:GGGUSDC_LONG"
    _eps._RECENT_ORDER_FILLS.pop(pk, None)
    _eps.note_order_fill(pk, 98765, 4.0, status="FILLED", increases=True)
    _run(fn(stub, pk, "AUGMENT", 4.0, 2.5, {"reason": "B12"}))
    live = tmp_path / "history" / "ang" / "GGGUSDC_LONG.jsonl"
    rows = [json.loads(x) for x in live.read_text().splitlines()]
    assert rows[-1]["order_id"] == "98765" and rows[-1]["reason"] == "B12" and rows[-1]["qty"] == 4.0
    recon = {"ts": datetime.now(timezone.utc).isoformat(), "type": "AUGMENT", "qty": 4.0, "price": 2.5, "reason": "BROKER_RECONCILED_GAP order=98765"}
    canon = _hc.canonical(rows + [recon])
    assert sum(1 for r in canon if r.get("order_id") == "98765" or "98765" in str(r.get("reason"))) == 1


def test_conservation_sees_rows_rolled_into_archive(tmp_path):
    live = tmp_path / "X_LONG.jsonl"
    now = datetime.now(timezone.utc)
    rows = [{"ts": (now - timedelta(hours=3)).isoformat(), "type": "OPEN", "qty": 100.0, "price": 1.0},
            {"ts": (now - timedelta(hours=2)).isoformat(), "type": "REDUCE", "qty": 100.0, "price": 1.0}]
    live.write_text("".join(json.dumps(r) + "\n" for r in rows))
    assert _el.conservation_allows(str(live), "REDUCE", 100.0) is False
    assert _ha.rotate_if_needed(live, max_bytes=10) is not None
    live.write_text("")
    assert _el.conservation_allows(str(live), "REDUCE", 100.0) is False


def test_external_echo_sees_order_row_rolled_into_archive(tmp_path):
    live = tmp_path / "data" / "history" / "fin" / "X_LONG.jsonl"
    live.parent.mkdir(parents=True)
    row = {"ts": datetime.now(timezone.utc).isoformat(), "type": "REDUCE", "qty": 5.0, "price": 1.0, "order_id": "77", "prov": {"src": "S1_VECTOR_DIRECT"}}
    live.write_text(json.dumps(row) + "\n")
    assert _el.external_echo(str(live), "REDUCE", 5.0) is True
    _ha.rotate_if_needed(live, max_bytes=10)
    live.write_text("")
    assert _el.external_echo(str(live), "REDUCE", 5.0) is True


def test_7d_sync_treats_archive_segment_as_same_ledger(tmp_path):
    history = tmp_path / "data" / "history"
    live = history / "flz" / "XRPUSDC_LONG.jsonl"
    live.parent.mkdir(parents=True)
    seg = _ha.segment_path(live)
    assert _sync._rel(seg, history) == "flz/XRPUSDC_LONG.jsonl"
    assert _sync._rel(Path(str(seg) + ".zst"), history) is None
    assert _sync._rel(live, history) == "flz/XRPUSDC_LONG.jsonl"
    now = time.time()
    r = {"ts": datetime.fromtimestamp(now - 3600, timezone.utc).isoformat(), "type": "REDUCE", "qty": 1.0, "price": 2.0}
    seg.parent.mkdir()
    seg.write_text(json.dumps(r) + "\n")
    pack = _sync.export(tmp_path, now)
    assert pack["rows"].get("flz/XRPUSDC_LONG.jsonl")
    out = _sync.apply(tmp_path, pack, "t", dry_run=True)
    assert not live.exists() or live.read_text() == ""
    assert (out.get("added") or 0) == 0


# ───────────────────────── tools/history_archive_offload.py ─────────────────────────
from tools import history_archive_offload as _off  # noqa: E402


def _rows(n, start_days_ago=40, ss_tag=""):
    t0 = datetime.now(timezone.utc) - timedelta(days=start_days_ago)
    return [{"ts": (t0 + timedelta(minutes=i)).isoformat(), "type": "REDUCE", "qty": 1.0 + i, "price": 2.0, "reason": f"r{i}{ss_tag}"} for i in range(n)]


def test_fold_legacy_bak_into_archive_keeps_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(_off, "REPORT", tmp_path / "rep")
    d = tmp_path / "flz"
    d.mkdir()
    b1 = d / "BTCUSDC_LONG.jsonl.bak"
    b2 = d / "MU_LONG.jsonl.20260924150010868039.bak"
    for b in (b1, b2):
        b.write_text("".join(json.dumps(r) + "\n" for r in _rows(3)))
        os.utime(b, (time.time() - 7200, time.time() - 7200))
    h = {b.name: _sha(b) for b in (b1, b2)}
    assert _off.fold_legacy(tmp_path) == 2
    segs = sorted((d / "archive").glob("*.legacy.jsonl"))
    assert len(segs) == 2 and not b1.exists() and not b2.exists()
    assert sorted(_sha(s) for s in segs) == sorted(h.values())
    assert [len(list(_ha.iter_rows(d / "BTCUSDC_LONG.jsonl")))] == [3]


def test_compress_then_offload_needs_verified_copies_before_local_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(_off, "REPORT", tmp_path / "rep")
    root = tmp_path / "hist"
    live = root / "men" / "ETHUSDC_LONG.jsonl"
    seg = _ha.segment_path(live)
    seg.parent.mkdir(parents=True)
    seg.write_text("".join(json.dumps(r) + "\n" for r in _rows(50)))
    raw = _sha(seg)
    old = time.time() - 9 * 86400
    os.utime(seg, (old, old))
    assert _off.compress_all(root) == 1
    zst = Path(str(seg) + ".zst")
    assert zst.exists() and not seg.exists()
    assert len(list(_ha.iter_rows(live))) == 50
    ext = tmp_path / "EXT"
    ext.mkdir()
    st = _off.offload_root("history", root, "mac", drives=[str(ext)], use_gcs=False, min_copies=2)
    assert st["ext_copies"] == 1 and st["local_removed"] == 0 and zst.exists()
    st = _off.offload_root("history", root, "mac", drives=[str(ext)], use_gcs=False, min_copies=1)
    assert st["local_removed"] == 1 and not zst.exists()
    copy = ext / "binance_archive" / "history" / "mac" / "history" / "men" / zst.name
    assert copy.exists()
    assert len(list(_ha.iter_rows(live))) == 50
    dec = __import__("subprocess").run(["zstd", "-dc", str(copy)], capture_output=True, env=_off.ZENV).stdout
    assert hashlib.sha256(dec).hexdigest() == raw


def test_offload_without_any_drive_keeps_local(tmp_path, monkeypatch):
    monkeypatch.setattr(_off, "REPORT", tmp_path / "rep")
    root = tmp_path / "hist"
    live = root / "men" / "X_LONG.jsonl"
    seg = _ha.segment_path(live)
    seg.parent.mkdir(parents=True)
    z = Path(str(seg) + ".zst")
    z.write_bytes(b"not really zstd")
    st = _off.offload_root("history", root, "mac", drives=[], use_gcs=False, min_copies=1)
    assert st["kept_local"] == 1 and z.exists()


def test_recover_adds_only_missing_old_rows_and_is_idempotent(tmp_path, monkeypatch):
    monkeypatch.setattr(_off, "REPORT", tmp_path / "rep")
    tgt = tmp_path / "mac"
    src = tmp_path / "s1"
    rows = _rows(10)
    (tgt / "fin").mkdir(parents=True)
    (src / "fin").mkdir(parents=True)
    (tgt / "fin" / "X_SHORT.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows[5:]))
    (src / "fin" / "X_SHORT.jsonl.bak").write_text("".join(json.dumps(r) + "\n" for r in rows[:7]))
    recent = {"ts": datetime.now(timezone.utc).isoformat(), "type": "REDUCE", "qty": 99.0, "price": 1.0}
    with open(src / "fin" / "X_SHORT.jsonl", "w") as fh:
        fh.write(json.dumps(recent) + "\n")
    rep = _off.recover(tgt, [str(src)])
    assert rep["rows_added"] == 5
    got = list(_ha.iter_rows(tgt / "fin" / "X_SHORT.jsonl"))
    assert [r["reason"] for r in got] == [r["reason"] for r in rows]
    assert _off.recover(tgt, [str(src)])["rows_added"] == 0
    assert (tgt / "fin" / "X_SHORT.jsonl").read_text().count("\n") == 5


def test_ext_health_refuses_toshiba_and_unmounted(tmp_path):
    assert _off.ext_health("/Volumes/TOSHIBA_EXT")["ok"] is False
    assert _off.ext_health(str(tmp_path))["ok"] is False


class _FakeClient:
    def __init__(self, trades, orders):
        self.trades, self.orders, self.calls = trades, orders, []

    def futures_account_trades(self, **kw):
        self.calls.append(("trades", kw.get("symbol")))
        return self.trades

    def futures_get_order(self, symbol, orderId):
        self.calls.append(("order", orderId))
        return self.orders[orderId]


def _rest_service(tmp_path, client, paper=False):
    base = tmp_path / "base"
    (base / "data").mkdir(parents=True)
    if paper:
        (base / "data" / "MAC_PAPER_TRADING").write_text("PAPER")
    stub = SimpleNamespace(_history_dedup={}, _HISTORY_DEDUP_WINDOW_S=1.0, config=SimpleNamespace(DATA_DIR=tmp_path, HISTORY_PROV_ENABLED=False, BASE_PATH=base), redis_manager=None, accounts={"men": SimpleNamespace(client=client)})
    stub._note_broker_fills = lambda *a: _eps.PositionService._note_broker_fills(stub, *a)
    return stub


def test_rest_fallback_stamps_mac_order_id(tmp_path):
    t = int(time.time() * 1000) - 5000
    cli = _FakeClient([{"orderId": 555, "qty": "3", "side": "BUY", "positionSide": "LONG", "time": t}, {"orderId": 555, "qty": "2", "side": "BUY", "positionSide": "LONG", "time": t + 10}], {555: {"clientOrderId": "x123", "status": "FILLED"}})
    stub = _rest_service(tmp_path, cli)
    _eps._RECENT_ORDER_FILLS.pop("men:RRRUSDC_LONG", None)
    _run(_eps.PositionService._append_to_history(stub, "men:RRRUSDC_LONG", "AUGMENT", 5.0, 1.0, {"reason": "B"}))
    row = json.loads((tmp_path / "history" / "men" / "RRRUSDC_LONG.jsonl").read_text().splitlines()[-1])
    assert row["order_id"] == "555" and ("trades", "RRRUSDC") in cli.calls


def test_rest_fallback_vector_direct_order_is_echo(tmp_path):
    t = int(time.time() * 1000) - 5000
    cli = _FakeClient([{"orderId": 777, "qty": "4", "side": "SELL", "positionSide": "LONG", "time": t}], {777: {"clientOrderId": "vdabcdef", "status": "FILLED"}})
    stub = _rest_service(tmp_path, cli)
    _eps._RECENT_ORDER_FILLS.pop("men:SSSUSDC_LONG", None)
    _run(_eps.PositionService._append_to_history(stub, "men:SSSUSDC_LONG", "REDUCE", 4.0, 1.0, {"reason": "X"}))
    row = json.loads((tmp_path / "history" / "men" / "SSSUSDC_LONG.jsonl").read_text().splitlines()[-1])
    assert row.get("echo_of_order_id") == "777" and "order_id" not in row


def test_rest_fallback_paper_mode_unknown_coid_is_echo(tmp_path):
    t = int(time.time() * 1000) - 5000

    class _NoOrder(_FakeClient):
        def futures_get_order(self, symbol, orderId):
            raise RuntimeError("boom")

    cli = _NoOrder([{"orderId": 888, "qty": "1", "side": "BUY", "positionSide": "SHORT", "time": t}], {})
    stub = _rest_service(tmp_path, cli, paper=True)
    _eps._RECENT_ORDER_FILLS.pop("men:TTTUSDC_SHORT", None)
    _run(_eps.PositionService._append_to_history(stub, "men:TTTUSDC_SHORT", "REDUCE", 1.0, 1.0, {"reason": "X"}))
    row = json.loads((tmp_path / "history" / "men" / "TTTUSDC_SHORT.jsonl").read_text().splitlines()[-1])
    assert row.get("echo_of_order_id") == "888" and "order_id" not in row


def test_rest_fallback_budget_and_no_client_never_block_the_write(tmp_path):
    stub = _rest_service(tmp_path, None)
    _run(_eps.PositionService._append_to_history(stub, "men:UUUUSDC_LONG", "AUGMENT", 1.0, 1.0, {"reason": "X"}))
    row = json.loads((tmp_path / "history" / "men" / "UUUUSDC_LONG.jsonl").read_text().splitlines()[-1])
    assert row["qty"] == 1.0 and "order_id" not in row
