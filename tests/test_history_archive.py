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
