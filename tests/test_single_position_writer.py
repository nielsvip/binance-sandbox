"""USER 2026-10-14 single position writer law: ez_manage never writes position files; ez_positions_realtime writes only
while the ez_positions_service heartbeat is stale; opened_at of a held position can never be rewritten."""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import position_writer_guard as pwg  # noqa: E402

OA_TRUE = "2026-10-13T22:50:13.017278Z"
OA_RESTAMP = "2026-10-14T00:22:05.000000Z"


def _row(amt=0.025, oa=OA_TRUE, ep=2509.33, mg=0.5, **kw):
    r = {"symbol": "ETHUSDC", "position_side": "LONG", "positionAmt": amt, "entry_price": ep, "opened_at": oa, "max_gain": mg}
    r.update(kw)
    return r


@pytest.fixture
def acct(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_BASE_DIR", str(tmp_path))
    d = tmp_path / "fin"
    d.mkdir()
    (d / "long_positions.json").write_text(json.dumps({"fin:ETHUSDC_LONG": _row()}))
    (d / "short_positions.json").write_text(json.dumps({}))
    return d


def test_role_detection(monkeypatch):
    monkeypatch.delenv(pwg.ROLE_ENV, raising=False)
    monkeypatch.setattr(sys, "argv", ["/x/ez_manage.py", "--account", "fin"])
    assert pwg.process_role() == "manage"
    monkeypatch.setattr(sys, "argv", ["ez_positions_realtime.py", "fin"])
    assert pwg.process_role() == "failover"
    monkeypatch.setattr(sys, "argv", ["ez_positions_service_main.py", "fin"])
    assert pwg.process_role() == "service"
    monkeypatch.setenv(pwg.ROLE_ENV, "service")
    monkeypatch.setattr(sys, "argv", ["ez_manage.py"])
    assert pwg.process_role() == "service"


def test_ez_manage_cannot_write_position_file(acct):
    before = (acct / "long_positions.json").read_bytes()
    rows = {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, ep=2500.72, fixed_stop_price=2485.2, exchange_stop_order_id="300")}
    assert pwg.before_write(acct / "long_positions.json", rows, role="manage") is False
    assert (acct / "long_positions.json").read_bytes() == before
    ov = json.loads((acct / ".manage_overlay_long.json").read_text())["rows"]["fin:ETHUSDC_LONG"]
    assert ov["fixed_stop_price"] == 2485.2 and "entry_price" not in ov


def test_ez_positions_atomic_save_refused_for_manage(acct, monkeypatch):
    import ez_positions
    monkeypatch.setenv(pwg.ROLE_ENV, "manage")
    before = (acct / "long_positions.json").read_bytes()
    res = ez_positions._atomic_save_blocking("fin", {"LONG": {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, ep=1.0)}}, {"LONG": acct / "long_positions.json"}, {"LONG": False})
    assert res["LONG"] == "REFUSED_SINGLE_WRITER"
    assert (acct / "long_positions.json").read_bytes() == before


def test_service_writes_but_opened_at_change_rejected(acct, monkeypatch):
    import ez_positions
    monkeypatch.setenv(pwg.ROLE_ENV, "service")
    res = ez_positions._atomic_save_blocking("fin", {"LONG": {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, ep=2509.33, mg=0.7)}}, {"LONG": acct / "long_positions.json"}, {"LONG": False})
    assert res["LONG"] == "OK"
    row = json.loads((acct / "long_positions.json").read_text())["fin:ETHUSDC_LONG"]
    assert row["opened_at"] == OA_TRUE
    assert row["max_gain"] == 0.7


def test_fresh_lifecycle_after_close_may_set_opened_at(acct):
    (acct / "long_positions.json").write_text(json.dumps({"fin:ETHUSDC_LONG": _row(amt=0.0)}))
    rows = {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP)}
    assert pwg.before_write(acct / "long_positions.json", rows, role="service") is True
    assert rows["fin:ETHUSDC_LONG"]["opened_at"] == OA_RESTAMP


def test_empty_opened_at_on_held_position_restored(acct):
    rows = {"fin:ETHUSDC_LONG": _row(oa=None)}
    pwg.before_write(acct / "long_positions.json", rows, role="service")
    assert rows["fin:ETHUSDC_LONG"]["opened_at"] == OA_TRUE


def test_realtime_writes_only_when_service_heartbeat_stale(acct):
    p = acct / "long_positions.json"
    assert pwg.may_write(p, role="failover")[0] is True  # no heartbeat at all -> service failed
    pwg.write_heartbeat(acct)
    assert pwg.may_write(p, role="failover")[0] is False  # service alive -> realtime silent
    hb = json.loads((acct / pwg.HEARTBEAT_NAME).read_text())
    hb["ts"] = time.time() - pwg.stale_after_s() - 5
    (acct / pwg.HEARTBEAT_NAME).write_text(json.dumps(hb))
    assert pwg.may_write(p, role="failover")[0] is True  # stale -> momentary takeover
    hb["ts"] = time.time()
    hb["pid"] = 999999  # dead pid -> stale
    (acct / pwg.HEARTBEAT_NAME).write_text(json.dumps(hb))
    assert pwg.may_write(p, role="failover")[0] is True
    assert pwg.may_write(p, role="service")[0] is True
    assert pwg.may_write(p, role="manage")[0] is False


def test_failover_atomic_save_blocked_while_service_alive(acct, monkeypatch):
    import ez_positions
    monkeypatch.setenv(pwg.ROLE_ENV, "failover")
    pwg.write_heartbeat(acct)
    before = (acct / "long_positions.json").read_bytes()
    res = ez_positions._atomic_save_blocking("fin", {"LONG": {"fin:ETHUSDC_LONG": _row(ep=2600.0)}}, {"LONG": acct / "long_positions.json"}, {"LONG": False})
    assert res["LONG"] == "REFUSED_SINGLE_WRITER"
    assert (acct / "long_positions.json").read_bytes() == before
    (acct / pwg.HEARTBEAT_NAME).unlink()
    res = ez_positions._atomic_save_blocking("fin", {"LONG": {"fin:ETHUSDC_LONG": _row(ep=2600.0)}}, {"LONG": acct / "long_positions.json"}, {"LONG": False})
    assert res["LONG"] == "OK"


def test_realtime_failover_gate_takeover_and_release(acct, monkeypatch):
    monkeypatch.setenv(pwg.ROLE_ENV, "failover")
    import ez_positions_realtime as rt

    class _Svc:
        def get_position_file(self, a, side):
            return acct / f"{side.lower()}_positions.json"
    up = rt.RealtimePositionUpdater(_Svc(), "fin")
    assert up.role == "failover" and up._active is False
    assert up.failover_gate() is True and up._active is True  # no heartbeat -> TAKEOVER
    pwg.write_heartbeat(acct)
    assert up.failover_gate() is False and up._active is False  # service back -> RELEASE


def test_service_role_heartbeats_on_write(acct, monkeypatch):
    monkeypatch.setenv(pwg.ROLE_ENV, "service")
    import ez_positions_realtime as rt

    class _Svc:
        def get_position_file(self, a, side):
            return acct / f"{side.lower()}_positions.json"
    up = rt.RealtimePositionUpdater(_Svc(), "fin")
    assert up.failover_gate() is True
    up.mark_written()
    assert pwg.heartbeat_age(acct) < 5


def test_overlay_merge_same_lifecycle_only(acct):
    pwg.write_overlay(acct / "long_positions.json", {"fin:ETHUSDC_LONG": _row(fixed_stop_price=2485.2, max_gain=0.9)})
    rows = {"fin:ETHUSDC_LONG": _row(fixed_stop_price=0.0, max_gain=0.4)}
    pwg.apply_overlay(rows, pwg.read_json(acct / ".manage_overlay_long.json"))
    assert rows["fin:ETHUSDC_LONG"]["fixed_stop_price"] == 2485.2 and rows["fin:ETHUSDC_LONG"]["max_gain"] == 0.9
    rows2 = {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, fixed_stop_price=0.0)}
    pwg.apply_overlay(rows2, pwg.read_json(acct / ".manage_overlay_long.json"))
    assert rows2["fin:ETHUSDC_LONG"]["fixed_stop_price"] == 0.0


def _svc_set(pos, name, value):
    """Set an attribute as the service module would (caller file = ez_positions_service.py)."""
    exec(compile(f"pos.{name} = value", "ez_positions_service.py", "exec"), {"pos": pos, "value": value})


def _position(oa, amt=0.025):
    from ez_positions_service import Position
    return Position.from_dict(_row(amt=amt, oa=oa))


def test_memory_opened_at_change_rejected_on_held_position():
    pos = _position(OA_TRUE)
    _svc_set(pos, "opened_at", datetime(2026, 10, 14, 0, 22, tzinfo=timezone.utc))
    assert abs(pwg.to_epoch(pos.opened_at) - pwg.to_epoch(OA_TRUE)) < 1
    with pwg.identity_write_allowed():
        _svc_set(pos, "opened_at", datetime(2026, 10, 14, 0, 22, tzinfo=timezone.utc))
    assert abs(pwg.to_epoch(pos.opened_at) - pwg.to_epoch(OA_RESTAMP)) < 6


def test_memory_opened_at_may_change_when_flat():
    pos = _position(OA_TRUE, amt=0.0)
    _svc_set(pos, "opened_at", datetime(2026, 10, 14, 0, 22, tzinfo=timezone.utc))
    assert abs(pwg.to_epoch(pos.opened_at) - pwg.to_epoch(OA_RESTAMP)) < 6


def test_reader_memory_resyncs_from_writer_files(acct):
    import positions_truth
    for side in ("long", "short"):
        (acct / f".{side}_positions.json.meta.json").write_text(json.dumps({"written_at": time.time(), "writer": "ez_positions.atomic_save:service:1"}))
    pos = _position(OA_RESTAMP)
    _svc_set(pos, "entry_price", 2500.72)
    mem = {"fin:ETHUSDC_LONG": pos}
    ch = pwg.resync_memory_from_files(mem, acct)
    assert {c[1] for c in ch} >= {"opened_at", "entry_price"}
    assert abs(pwg.to_epoch(pos.opened_at) - pwg.to_epoch(OA_TRUE)) < 1
    assert pos.entry_price == 2509.33


def test_reader_resync_skipped_when_files_stale(acct):
    for side in ("long", "short"):
        (acct / f".{side}_positions.json.meta.json").write_text(json.dumps({"written_at": time.time() - 600, "writer": "x:service:1"}))
    pos = _position(OA_RESTAMP)
    assert pwg.resync_memory_from_files({"fin:ETHUSDC_LONG": pos}, acct) == []


def test_broker_rewrite_deferred_in_manage_role(acct, monkeypatch):
    import positions_truth
    monkeypatch.setenv(pwg.ROLE_ENV, "manage")
    before = (acct / "long_positions.json").read_bytes()
    assert positions_truth.rewrite_row_from_broker("binance", "fin", "ETHUSDC", "LONG", {"amt": 0.03, "entry": 2511.0}) is True
    assert (acct / "long_positions.json").read_bytes() == before


def test_max_gain_never_decreases_within_lifecycle(acct):
    rows = {"fin:ETHUSDC_LONG": _row(mg=0.1)}
    pwg.before_write(acct / "long_positions.json", rows, role="service")
    assert rows["fin:ETHUSDC_LONG"]["max_gain"] == 0.5
    (acct / "long_positions.json").write_text(json.dumps({"fin:ETHUSDC_LONG": _row(amt=0.0, mg=0.5)}))
    rows = {"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, mg=0.0)}
    pwg.before_write(acct / "long_positions.json", rows, role="service")
    assert rows["fin:ETHUSDC_LONG"]["max_gain"] == 0.0  # new lifecycle after a real close starts fresh


def _restorer(tmp_path):
    import types
    from ez_positions_service import PositionService
    o = types.SimpleNamespace(config=types.SimpleNamespace(BASE_PATH=tmp_path))
    o._restore_lifecycle_sanitize = types.MethodType(PositionService._restore_lifecycle_sanitize, o)
    o.restore = types.MethodType(PositionService._restore_position_from_backups_blocking, o)
    return o


def test_restore_refuses_other_account(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_BASE_DIR", str(tmp_path))
    for a in ("fin", "men"):
        (tmp_path / a / "backups").mkdir(parents=True)
    (tmp_path / "men" / "long_positions.json").write_text(json.dumps({"men:ETHUSDC_LONG": _row()}))
    (tmp_path / "fin" / "backups" / "long_positions_backup_20261014_000000.json").write_text(json.dumps({}))
    assert _restorer(tmp_path).restore("fin", "ETHUSDC", "LONG") is None


def test_restore_flat_backup_row_carries_no_closed_lifecycle_identity(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_BASE_DIR", str(tmp_path))
    (tmp_path / "fin" / "backups").mkdir(parents=True)
    (tmp_path / "fin" / "backups" / "long_positions_backup_20261014_000000.json").write_text(json.dumps({"fin:ETHUSDC_LONG": _row(amt=0.0, mg=3.0)}))
    pos = _restorer(tmp_path).restore("fin", "ETHUSDC", "LONG")
    assert pos is not None and pos.opened_at is None and pos.entry_price == 0.0 and pos.max_gain == 0.0


def test_restore_held_own_backup_row_kept(tmp_path, monkeypatch):
    monkeypatch.setenv("POSITIONS_BASE_DIR", str(tmp_path))
    (tmp_path / "fin" / "backups").mkdir(parents=True)
    (tmp_path / "fin" / "backups" / "long_positions_backup_20261014_000000.json").write_text(json.dumps({"fin:ETHUSDC_LONG": _row()}))
    pos = _restorer(tmp_path).restore("fin", "ETHUSDC", "LONG")
    assert abs(pwg.to_epoch(pos.opened_at) - pwg.to_epoch(OA_TRUE)) < 1 and pos.entry_price == 2509.33


def test_tracker_record_mirrors_service_identity(acct):
    ec = {"opened_at": OA_RESTAMP, "entry_price": 2500.72, "average_entry_price": 2500.72, "positionAmt": 0.02, "max_gain": 0.9}
    ch = pwg.mirror_identity(ec, pwg.held_identity(acct, "fin:ETHUSDC_LONG"))
    assert ec["opened_at"] == OA_TRUE and ec["entry_price"] == 2509.33 and ec["average_entry_price"] == 2509.33
    assert ec["positionAmt"] == 0.025 and ec["max_gain"] == 0.9  # tracker max_gain never pulled below
    assert {c[0] for c in ch} >= {"opened_at", "entry_price", "positionAmt"}
    assert pwg.held_identity(acct, "fin:NOPE_LONG") is None


def test_tracker_save_mirrors_identity(acct):
    import types
    import ez_positions_quick as q
    tm = types.SimpleNamespace(base_path=acct.parent, exit_candidates={"fin:ETHUSDC_LONG": {"opened_at": OA_RESTAMP, "entry_price": 1.0, "positionAmt": 0.025, "max_gain": 0.0}})
    n = q.TrackerManager._mirror_identity_from_service(tm, "fin")
    assert n >= 2 and tm.exit_candidates["fin:ETHUSDC_LONG"]["opened_at"] == OA_TRUE


def test_tradier_rows_opened_at_immutable(tmp_path):
    d = tmp_path / "tra"
    d.mkdir()
    (d / "long_positions.json").write_text(json.dumps({"tra:META_LONG": {"symbol": "META", "positionAmt": 1.0, "entry_price": 606.92, "opened_at": "2026-08-10T13:39:15.121Z", "max_gain": 4.0}}))
    rows = {"tra:META_LONG": {"symbol": "META", "positionAmt": 1.0, "entry_price": 606.92, "opened_at": "2026-09-01T15:29:44.907202Z", "max_gain": 1.0}}
    assert pwg.before_write(d / "long_positions.json", rows, role="other", overlay=False) is True
    assert rows["tra:META_LONG"]["opened_at"] == "2026-08-10T13:39:15.121Z" and rows["tra:META_LONG"]["max_gain"] == 4.0


def test_pull_never_overwrites_opened_at_of_held_position(acct):
    inc = acct / "incoming_long.json"
    inc.write_text(json.dumps({"fin:ETHUSDC_LONG": _row(oa=OA_RESTAMP, ep=2500.72, mg=0.2), "fin:NEWUSDT_LONG": _row(oa="2026-10-14T01:00:00Z", ep=1.5), "fin:GONEUSDT_LONG": _row(amt=0.0)}))
    lp = acct / "long_positions.json"
    d = json.loads(lp.read_text())
    d["fin:GONEUSDT_LONG"] = _row(oa="2026-10-13T10:00:00Z", ep=3.0)
    d["fin:KEEPUSDT_LONG"] = _row(oa="2026-10-13T11:00:00Z", ep=4.0)
    lp.write_text(json.dumps(d))
    ev = pwg.merge_file(lp, inc)
    out = json.loads(lp.read_text())
    assert out["fin:ETHUSDC_LONG"]["opened_at"] == OA_TRUE and out["fin:ETHUSDC_LONG"]["entry_price"] == 2509.33 and out["fin:ETHUSDC_LONG"]["max_gain"] == 0.5
    assert ("OPENED_AT_PULL_REJECTED", "fin:ETHUSDC_LONG", OA_TRUE, OA_RESTAMP) in ev
    assert out["fin:NEWUSDT_LONG"]["opened_at"] == "2026-10-14T01:00:00Z"  # new lifecycle keeps the opening time it carries
    assert out["fin:GONEUSDT_LONG"]["positionAmt"] == 0.0 and out["fin:GONEUSDT_LONG"]["opened_at"] == "2026-10-13T10:00:00Z"
    assert out["fin:KEEPUSDT_LONG"]["opened_at"] == "2026-10-13T11:00:00Z"
    alerts = (acct.parent / "data" / "alerts" / "position_writer_guard.jsonl").read_text()
    assert "OPENED_AT_PULL_REJECTED" in alerts


def test_pull_qty_change_takes_incoming_qty_but_keeps_opened_at(acct):
    merged, ev = pwg.merge_incoming({"fin:ETHUSDC_LONG": _row()}, {"fin:ETHUSDC_LONG": _row(amt=0.05, oa=OA_RESTAMP, ep=2520.0, mg=0.1)})
    r = merged["fin:ETHUSDC_LONG"]
    assert r["positionAmt"] == 0.05 and r["entry_price"] == 2520.0 and r["opened_at"] == OA_TRUE and r["max_gain"] == 0.5


def test_new_lifecycle_gets_broker_opening_fill_time_not_now():
    import asyncio
    import types
    from ez_positions_service import PositionService
    fills = [{"time": 1791932401888, "id": 1, "side": "BUY", "qty": "0.025", "positionSide": "LONG"}, {"time": 1791900000000, "id": 0, "side": "SELL", "qty": "0.01", "positionSide": "LONG"}, {"time": 1791899000000, "id": -1, "side": "BUY", "qty": "0.01", "positionSide": "LONG"}]
    cli = types.SimpleNamespace(futures_account_trades=lambda **kw: fills)
    svc = types.SimpleNamespace(accounts={"fin": types.SimpleNamespace(client=cli)})
    t = asyncio.run(PositionService._broker_open_fill_time(svc, "fin", "ETHUSDC", "LONG", 0.025))
    assert t is not None and abs(t.timestamp() - 1791932401.888) < 0.01
