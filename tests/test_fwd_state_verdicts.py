import json
import time
from pathlib import Path
import board_state_converge as bsc


def _fwd(root, ss, open, tail, mtime=None, ok=True, src="s"):
    d = Path(root) / "data" / "forward_state"
    d.mkdir(parents=True, exist_ok=True)
    p = d / f"{ss}.json"
    p.write_text(json.dumps({"ok": ok, "open": open, "src": src, "npz_tail": tail,
                             "sym_side": ss, "trades": 3, "gain_pct": 1.0}))
    if mtime is not None:
        import os
        os.utime(p, (mtime, mtime))
    return p


def test_forward_fallback_open(tmp_path):
    now = time.time()
    tail = now - 100
    _fwd(tmp_path, "AAA_LONG", True, tail, mtime=now - 10)
    out = bsc.fresh_verdicts(str(tmp_path / "data" / "pure_board"), "ang", now=now)
    assert len(out) == 1
    assert out[0]["action"] == bsc.CONVERGE_OPEN
    assert out[0]["sym_side"] == "AAA_LONG"
    assert out[0]["vec_leg"].startswith("fwd:")


def test_forward_fallback_close(tmp_path):
    now = time.time()
    _fwd(tmp_path, "BBB_SHORT", False, now - 100, mtime=now - 10)
    out = bsc.fresh_verdicts(str(tmp_path / "data" / "pure_board"), "ang", now=now)
    assert len(out) == 1
    assert out[0]["action"] == bsc.CONVERGE_CLOSE


def test_pure_verdict_wins_over_forward(tmp_path):
    now = time.time()
    tail = now - 100
    _fwd(tmp_path, "AAA_LONG", True, tail, mtime=now - 10)
    pb = Path(tmp_path) / "data" / "pure_board"
    pb.mkdir(parents=True, exist_ok=True)
    (pb / "intents_x.jsonl").write_text(json.dumps(
        {"acct": "ang", "action": "CONVERGE_CLOSE", "sym_side": "AAA_LONG",
         "bar_ts": tail, "ts": now - 5, "vec_leg": "pure:test"}) + "\n")
    out = bsc.fresh_verdicts(str(pb), "ang", now=now)
    assert len(out) == 1
    assert out[0]["vec_leg"] != "" and not out[0]["vec_leg"].startswith("fwd:")


def test_stale_or_bad_forward_ignored(tmp_path):
    now = time.time()
    _fwd(tmp_path, "OLD_LONG", True, now - 9000, mtime=now - 9000)
    _fwd(tmp_path, "BAD_SHORT", None, now - 100, mtime=now - 10)
    _fwd(tmp_path, "NOTOK_LONG", True, now - 100, mtime=now - 10, ok=False)
    out = bsc.fresh_verdicts(str(tmp_path / "data" / "pure_board"), "ang", now=now)
    assert out == []
