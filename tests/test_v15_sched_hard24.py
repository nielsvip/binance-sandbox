"""USER 2026-10-17 hard rule: a sym_side boarded (published OR refused) < 24 h ago is never re-dispatched; only an
explicit operator pin overrides. Covers the pure rule, the dispatch chokepoint and the host-side done-epoch probe."""
import json
import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_fleet_scheduler as S


def test_boarded_within_rule(monkeypatch):
    monkeypatch.delenv("V15_SCHED_HARD_RECENT_H", raising=False)
    now = 1_800_000_000.0
    at = {"SNXUSDT_LONG": now - 2 * 3600, "OLD_LONG": now - 25 * 3600}
    assert S._boarded_within("SNXUSDT_LONG", at, now) is True
    assert S._boarded_within("OLD_LONG", at, now) is False
    assert S._boarded_within("NEVER_LONG", at, now) is False
    assert S._boarded_within("SNXUSDT_LONG", at, now, pins={"SNXUSDT_LONG"}) is False  # explicit pin only
    monkeypatch.setenv("V15_SCHED_HARD_RECENT_H", "0")
    assert S._boarded_within("SNXUSDT_LONG", at, now) is False  # kill switch


def test_dispatch_chokepoint_blocks_recent(monkeypatch, capsys):
    now = time.time()
    S._HARD24.update(at={"SNXUSDT_LONG": now - 3600}, pins=set(), now=now, blocked=[])
    launched = []
    monkeypatch.setattr(S, "_drop_untradeable", lambda sym, acts, tag: acts)
    monkeypatch.setattr(S.subprocess, "Popen", lambda *a, **k: launched.append(a))
    host = {"name": "s1", "ssh": ["127.0.0.1"], "root": "~/binance-sandbox"}
    S._dispatch_async(host, "SNXUSDT", [{"sym": "SNXUSDT", "side": "LONG", "window": "30D"}], "/tmp/p", 16, "s1:SNXUSDT:L30D")
    out = capsys.readouterr().out
    assert "HARD-24H SNXUSDT_LONG" in out and not launched
    assert S._HARD24["blocked"] == ["SNXUSDT_LONG"]


def test_host_probe_done_epoch(tmp_path):
    ns = {}
    src = S.HOST_PY
    start = src.index("def _progress_done_epoch")
    end = src.index("def _progress_marks")
    exec("import json\n" + src[start:end], ns)
    p = tmp_path / "X_LONG_v14_progress.json"
    p.write_text(json.dumps({"result_done_utc": "2026-10-17T15:40:00+00:00", "not_compliant": "ESSENTIAL-ROWS: x"}))
    assert abs(ns["_progress_done_epoch"](str(p)) - 1792251600.0) < 1.0
    p.write_text(json.dumps({}))
    assert ns["_progress_done_epoch"](str(p)) is None
