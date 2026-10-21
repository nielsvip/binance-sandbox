"""USER 2026-10-21 BOARD-STATE CONVERGE: real tests over tmp fixtures."""
import json
import time

import board_state_converge as B


def _write(fp, rows):
    with open(fp, "w") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")


def test_stale_verdict_refused(tmp_path):
    now = time.time()
    d = tmp_path / "pure_board"
    d.mkdir()
    _write(d / "intents_2026-10-21.jsonl", [
        {"ts": now - 5000, "bar_ts": now - 6000, "acct": "men",
         "sym_side": "ADAUSDC_LONG", "action": "CONVERGE_OPEN", "vec_leg": "B_SRS_ENTRY"},
        {"ts": now - 60, "bar_ts": now - 6000, "acct": "men",
         "sym_side": "XRPUSDC_LONG", "action": "CONVERGE_OPEN", "vec_leg": "B14"},
        {"ts": now - 60, "bar_ts": now - 300, "acct": "men",
         "sym_side": "SOLUSDC_LONG", "action": "CONVERGE_OPEN", "vec_leg": "B12"},
    ])
    got = B.fresh_verdicts(str(d), "men", now=now)
    assert [r["sym_side"] for r in got] == ["SOLUSDC_LONG"]


def test_snapshot_rot_refuses(tmp_path):
    import os

    acc = tmp_path / "men"
    acc.mkdir()
    fp = acc / "long_positions.json"
    fp.write_text(json.dumps({"men:SOLUSDC_LONG": {"positionAmt": 1.0}}))
    old = time.time() - 9999
    os.utime(acc / "long_positions.json", (old, old))
    fp2 = acc / "short_positions.json"
    fp2.write_text(json.dumps({}))
    try:
        B.read_live_snapshots(str(tmp_path), "men", now=time.time())
        assert False, "rotted snapshots must raise"
    except B.StaleInput:
        pass


def test_already_forced_dedups(tmp_path):
    rp = tmp_path / "converge_men.jsonl"
    _write(rp, [{"acct": "men", "sym_side": "SOLUSDC_LONG", "bar_ts": 1000.0}])
    assert B.already_forced(str(rp), "men", "SOLUSDC_LONG", 1000.0) is True
    assert B.already_forced(str(rp), "men", "SOLUSDC_LONG", 2000.0) is False


def test_diagnose_cites_refusal_and_vec_leg(tmp_path):
    now = time.time()
    hd = tmp_path / "ang"
    hd.mkdir()
    _write(hd / "ADAUSDC_LONG.jsonl", [
        {"ts": now - 3600, "type": "OPEN", "reason": "REFUSED_MIN_GAIN_TO_BUY threshold"},
    ])
    s = B.diagnose(str(tmp_path), "ang", "ADAUSDC_LONG", B.CONVERGE_OPEN, 0.0, "B_SRS_ENTRY", now=now)
    assert "REFUSED_MIN_GAIN_TO_BUY" in s
    assert "B_SRS_ENTRY" in s
    assert "live flat" in s


def test_record_schema(tmp_path):
    rec = B.build_record(1.0, 2.0, "men", "SOLUSDC_LONG", B.CONVERGE_CLOSE,
                         "EXIT_VELOCITY_WT", "md5", 91, 30.9, 5.0, "why", "ok")
    for k in ("ts", "bar_ts", "acct", "sym_side", "action", "vector_function",
              "vec_leg", "live_qty_before", "live_refusal", "exec_result"):
        assert k in rec
    p = B.append_record(B.record_path(str(tmp_path), "men"), rec)
    assert json.load(open(p))["vec_leg"] == "EXIT_VELOCITY_WT"
