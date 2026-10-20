"""Self-improvement loop closure (BIBLE §94, 2026-10-13): JIT autopsy base producer, missed-trend round driver,
daily chain hardening (FAILED stamp on any non-DONE exit, stale RUNNING -> FAILED, oom_guard chain exemption)."""

import json
import os
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
import v15_autopsy_jit as AJ  # noqa: E402
import v15_missed_trend_round as MR  # noqa: E402


def test_base_ok_mirrors_scheduler(tmp_path):
    (tmp_path / "A_LONG_autopsy_base.json").write_text(json.dumps({"autopsy": {"effective_switches": ["X"]}}))
    (tmp_path / "B_LONG_autopsy.json").write_text(json.dumps({"base": {"trades": 5}}))
    (tmp_path / "C_LONG_autopsy.json").write_text(json.dumps({"base": {"trades": 0}}))
    assert AJ.base_ok(tmp_path, "A_LONG") == (True, "base")
    assert AJ.base_ok(tmp_path, "B_LONG") == (True, "no_rescue_report")
    assert AJ.base_ok(tmp_path, "C_LONG") == (True, "report_zero_trades")
    assert AJ.base_ok(tmp_path, "D_LONG") == (False, "missing")


def test_jit_candidates_priority_order_and_skips(tmp_path):
    afd, npz = tmp_path / "af", tmp_path / "npz"
    afd.mkdir()
    npz.mkdir()
    for s in ("AAA", "BBB"):
        (npz / f"{s}.npz").write_bytes(b"x")
    (afd / "BBB_LONG_autopsy_base.json").write_text('{"autopsy": {"effective_switches": []}}')
    snap = {"pending_stocks": ["BBB", "CCC"], "pending_crypto": ["AAA"], "running": [], "non_shortable": ["BBB"]}
    state = {"attempts": {"AAA_SHORT": time.time()}}
    c, sk = AJ.candidates(snap, ["AAA"], afd, npz, state, time.time(), 6)
    assert c[0] == ("AAA_LONG", False)
    assert ("CCC_LONG", True) in c and ("CCC_SHORT", True) in c
    assert sk["AAA_SHORT"] == "backoff" and sk["BBB_SHORT"] == "non_shortable" and sk["CCC_LONG"] == "no_npz"


def test_jit_dry_run_end_to_end(tmp_path, monkeypatch):
    afd, npz = tmp_path / "af", tmp_path / "npz"
    npz.mkdir()
    (npz / "AAA.npz").write_bytes(b"x")
    snap = tmp_path / "snap.json"
    snap.write_text(json.dumps({"pending_crypto": ["AAA"], "at": "t"}))
    rc = AJ.main(["--out", str(afd), "--snapshot", str(snap), "--priority", str(tmp_path / "none.txt"), "--npz-dir", str(npz), "--dry-run", "--max-load", "999"])
    assert rc == 0


def test_missed_trend_select_and_install_gate(tmp_path):
    p = tmp_path / "X_SHORT_v14_progress.json"
    p.write_text("{}")
    led = {"sym_sides": {
        "X_SHORT": {"verdict": "BEST_EFFORT", "tim": 3.0, "path": str(p), "potential": 9},
        "Y_SHORT": {"verdict": "BEST_EFFORT", "tim": 35.0, "path": str(p), "potential": 99},
        "Z_SHORT": {"verdict": "IMPOSSIBLE", "tim": 1.0, "path": str(p), "potential": 99},
        "W_SHORT": {"verdict": "BEST_EFFORT", "tim": None, "path": str(p), "potential": 99},
    }}
    picks = MR.select(led, tmp_path, 20, 7, time.time(), 5)
    assert [s for s, _ in picks] == ["X_SHORT"]
    (tmp_path / "X_SHORT_missed_trend.json").write_text("{}")
    assert MR.select(led, tmp_path, 20, 7, time.time(), 5) == []
    good = {"base": {"gain": 1.0, "tim": 5.0}, "combo_gain": 2.0, "combo_tim": 9.0, "combo_overrides": {"A": 1}}
    assert MR.should_install(good)
    assert not MR.should_install({**good, "combo_gain": 0.5})
    assert not MR.should_install({**good, "combo_tim": 4.0})
    assert not MR.should_install({**good, "combo_overrides": {}})


CHAIN = ROOT / "tools" / "v15_daily_chain_s1.sh"


def test_chain_static_contract():
    s = CHAIN.read_text()
    assert subprocess.run(["bash", "-n", str(CHAIN)]).returncode == 0
    for needle in ("trap on_exit EXIT", "export V15_DAILY_CHAIN=1", ".RUNNING", "step2a", "--queue data/template_row_queue.json",
                   "step4e", "v15_invention_loop.py mine --write", "promote-check", "step8a", "v15_missed_trend_round.py",
                   "step8b", "v15_cat_avg_matrix.py", "step8c", "v15_causal_rescue.py run", "ingest-outcomes", "v15_causal_rescue.py cap --apply",
                   "v15_switch_flip_study.py", 'CHAIN_STATUS=DONE'):
        assert needle in s, needle
    assert s.index("CHAIN_STATUS=DONE") < s.index("# 8. POST-STAMP")
    assert s.index("step2a") < s.index("# 6. push")


def _fake_root(tmp_path, py_body):
    root = tmp_path / "root"
    (root / "tools").mkdir(parents=True)
    (root / ".venv" / "bin").mkdir(parents=True)
    (root / "backups").mkdir()
    shutil.copy(CHAIN, root / "tools" / CHAIN.name)
    # USER 2026-10-17: the chain resolves its interpreter via tools/pyinterp.sh (conda; the .venv rung is gone) - the fake root ships a stub
    # resolver that points at the fake interpreter, so the step-1 child is the test's `sleep`, as before
    py = root / ".venv" / "bin" / "python"
    py.write_text("#!/bin/bash\n" + py_body + "\n")
    py.chmod(0o755)
    resolver = root / "tools" / "pyinterp.sh"
    resolver.write_text('#!/bin/bash\necho "' + str(py) + '"\n')
    resolver.chmod(0o755)
    return root


@pytest.mark.skipif(shutil.which("timeout") is None, reason="needs coreutils timeout")
def test_chain_writes_failed_stamp_on_sigterm_and_converts_stale_running(tmp_path):
    root = _fake_root(tmp_path, "exec sleep 60")
    sd = root / "data" / "daily_chain"
    sd.mkdir(parents=True)
    (sd / "20990101.RUNNING").write_text("999999 2099-01-01T00:00:00Z")
    env = dict(os.environ, V15_CHAIN_TARGETS="", PATH=os.environ.get("PATH", ""))
    p = subprocess.Popen(["bash", str(root / "tools" / CHAIN.name)], cwd=str(root), env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    date = time.strftime("%Y%m%d", time.gmtime())
    for _ in range(100):
        if (sd / f"{date}.RUNNING").exists():
            break
        time.sleep(0.1)
    assert (sd / f"{date}.RUNNING").exists()
    stale = json.loads((sd / "20990101.FAILED.json").read_text())
    assert stale["status"] == "FAILED" and "died without trap" in stale["why"]
    assert not (sd / "20990101.RUNNING").exists()
    time.sleep(0.5)
    # TERM to the chain, then to its running step (GNU timeout keeps the step in its own process group): bash runs its TERM
    # trap as soon as the foreground step returns -> exit 143 -> on_exit writes the FAILED stamp.
    os.kill(p.pid, signal.SIGTERM)
    kids = subprocess.run(["pgrep", "-P", str(p.pid)], capture_output=True, text=True).stdout.split()
    for k in kids:
        os.kill(int(k), signal.SIGTERM)
    p.wait(timeout=30)
    f = json.loads((sd / f"{date}.FAILED.json").read_text())
    assert f["status"] == "FAILED" and f["failed_step"] == "1" and "rc=143" in f["why"]
    assert not (sd / f"{date}.RUNNING").exists()


def test_oom_guard_exempts_chain():
    s = (ROOT / "tools" / "oom_guard.sh").read_text()
    assert subprocess.run(["bash", "-n", str(ROOT / "tools" / "oom_guard.sh")]).returncode == 0
    assert "V15_DAILY_CHAIN=1" in s and "stop_pat baseline_backtest" in s and "pkill -STOP" not in s
