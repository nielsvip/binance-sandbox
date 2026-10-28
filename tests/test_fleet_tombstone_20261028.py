import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "tools"))
import fleet_dispatch as F

NOW = 2_000_000_000
TAIL = NOW - 600


def host(running=(), progress=None, launches=None, refused=None):
    return {"running": {s: 100 for s in running}, "mem_avail_mb": 20000, "disk_free_mb": 50000,
            "pdir": "/home/niels/v15_run37_x/progress", "progress": progress or {}, "launches": launches or {},
            "tails": {"BLURUSDT": TAIL, "AAPL": TAIL}, "impossible": {}, "start_refused": refused or {}}


UNI = {"CRYPTO_SHORT": ["BLURUSDT_SHORT"], "CRYPTO_LONG": [], "STOCKS_SHORT": ["AAPL_SHORT"], "STOCKS_LONG": []}


def test_crypto_tombstone_not_pending():
    prog = {"BLURUSDT_SHORT": {"fg": False, "verdict": "IMPOSSIBLE", "rows": 3000, "ts": TAIL}}
    pend, partial, skipped = F.pending_by_cat(UNI, {"s1": host(progress=prog)}, NOW)
    assert "BLURUSDT_SHORT" not in pend["CRYPTO_SHORT"]
    assert skipped["BLURUSDT_SHORT"].startswith("IMPOSSIBLE tombstone")


def test_stock_tombstone_stays_pending_force_dc_rerun():
    prog = {"AAPL_SHORT": {"fg": False, "verdict": "IMPOSSIBLE", "rows": 3000, "ts": TAIL}}
    pend, _, _ = F.pending_by_cat(UNI, {"s6": host(progress=prog)}, NOW)
    assert "AAPL_SHORT" in pend["STOCKS_SHORT"]


def test_verdict_board_is_not_a_death():
    launches = {"BLURUSDT_SHORT": {"ts": NOW - 1800}, "ENSUSDT_SHORT": {"ts": NOW - 1700}}
    prog = {"BLURUSDT_SHORT": {"fg": False, "verdict": "IMPOSSIBLE"}, "ENSUSDT_SHORT": {"fg": False, "verdict": "IMPOSSIBLE"}}
    assert F.unfinished_deaths(host(progress=prog, launches=launches), NOW) == []


def test_tombstone_start_exit_is_not_a_death():
    launches = {"MOVRUSDT_SHORT": {"ts": NOW - 1800}}
    d = host(launches=launches, refused={"MOVRUSDT_SHORT": NOW - 1790})
    assert F.unfinished_deaths(d, NOW) == []


def test_real_death_still_counts():
    launches = {"MOVRUSDT_SHORT": {"ts": NOW - 1800}}
    d = host(launches=launches, progress={"MOVRUSDT_SHORT": {"fg": False, "verdict": "", "rows": 900}})
    assert F.unfinished_deaths(d, NOW) == [(NOW - 1800, "MOVRUSDT_SHORT")]


def test_probe_marks_tombstone_skip_as_start_refusal(tmp_path, monkeypatch):
    import json
    import os
    import subprocess
    probe = pathlib.Path(__file__).resolve().parents[1] / "tools" / "fleet_probe.py"
    home = tmp_path
    (home / "fleet_dispatch" / "launches").mkdir(parents=True)
    ss = "ZZTESTUSDT_SHORT"
    (home / "fleet_dispatch" / "launches" / f"{ss}.json").write_text(json.dumps({"ss": ss, "ts": 1}))
    log = pathlib.Path(f"/tmp/sweep_{ss}_30D.log")
    log.write_text(f"[DEDUP] acquired\n[IMPOSSIBLE-SKIP] {ss} tombstoned (x) — manual revision required, MUST NOT RETOUCH.\n")
    try:
        env = dict(os.environ, HOME=str(home))
        launch = home / "fleet_dispatch" / "launches" / f"{ss}.json"
        launch.write_text(json.dumps({"ss": ss, "ts": int(log.stat().st_mtime) - 5}))
        out = subprocess.run([sys.executable, str(probe), "{}"], capture_output=True, text=True, env=env, timeout=60).stdout
        assert ss in json.loads(out)["start_refused"]
    finally:
        log.unlink()


def test_priority_side_picked_before_own_partial():
    uni = {"CRYPTO_SHORT": ["AAAUSDT_SHORT", "ZZZUSDT_SHORT"], "CRYPTO_LONG": [], "STOCKS_SHORT": [], "STOCKS_LONG": []}
    d = host()
    d["tails"] = {"AAAUSDT": TAIL, "ZZZUSDT": TAIL}
    d["progress"] = {"AAAUSDT_SHORT": {"fg": False, "rows": 500, "ts": TAIL}}
    pend, partial, _ = F.pending_by_cat(uni, {"s1": d}, NOW)
    picks, _, _ = F.plan(pend, partial, {"s1": d}, NOW, {}, frozenset({"ZZZUSDT_SHORT"}))
    assert picks[0][1] == "ZZZUSDT_SHORT"
    picks, _, _ = F.plan(pend, partial, {"s1": d}, NOW, {})
    assert picks[0][1] == "AAAUSDT_SHORT"


def test_probe_normal_end_and_stale_npz_are_not_deaths(tmp_path):
    import json
    import os
    import subprocess
    probe = pathlib.Path(__file__).resolve().parents[1] / "tools" / "fleet_probe.py"
    (tmp_path / "fleet_dispatch" / "launches").mkdir(parents=True)
    lines = {"ZZA_LONG": "[spec-row] x\n[spec-fill] workbook complete, 2865 rows filled, returning early cum=30.3\n[mandatory-yellows] a\n",
             "ZZB_LONG": "[NPZ-HOT] x\n[STALE_NPZ_SKIPPED] ZZB_LONG STALE_NPZ_SKIPPED: NPZ last bar ...\n",
             "ZZC_LONG": "[spec-row] x\n[slow-cell] y\n"}
    logs = []
    try:
        for ss, txt in lines.items():
            log = pathlib.Path(f"/tmp/sweep_{ss}_30D.log")
            log.write_text(txt)
            logs.append(log)
            (tmp_path / "fleet_dispatch" / "launches" / f"{ss}.json").write_text(json.dumps({"ss": ss, "ts": int(log.stat().st_mtime) - 5}))
        out = subprocess.run([sys.executable, str(probe), "{}"], capture_output=True, text=True, env=dict(os.environ, HOME=str(tmp_path)), timeout=60).stdout
        refused = json.loads(out)["start_refused"]
        assert "ZZA_LONG" in refused and "ZZB_LONG" in refused and "ZZC_LONG" not in refused
    finally:
        for log in logs:
            log.unlink()
