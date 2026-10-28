"""per_sym_minute_absorb: a board with progress.snapshot_mismatch is adopted only while the Mac live snapshot equals its start snapshot."""
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import per_sym_minute_absorb as A  # noqa: E402


def _snap(tmp, ss, body):
    d = tmp / "data" / "live_baseline_snapshots"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ss}.json").write_bytes(body)
    return hashlib.sha256(body).hexdigest()


def test_no_mismatch_keeps_old_rule(tmp_path):
    assert A.snapshot_conflict("X_LONG", {"snapshot_mismatch": None}, tmp_path) is None
    assert A.snapshot_conflict("X_LONG", None, tmp_path) is None


def test_mismatch_but_live_unchanged_is_adopted(tmp_path):
    sha = _snap(tmp_path, "X_LONG", b'{"a": 1}')
    assert A.snapshot_conflict("X_LONG", {"snapshot_mismatch": {"start_sha": sha, "now_sha": "zz"}}, tmp_path) is None


def test_mismatch_and_live_changed_is_a_conflict(tmp_path):
    sha_new = _snap(tmp_path, "X_LONG", b'{"a": 2}')
    cf = A.snapshot_conflict("X_LONG", {"snapshot_mismatch": {"start_sha": "old", "now_sha": "zz"}, "final_path": "f.xlsx"}, tmp_path)
    assert cf["mac_live_sha"] == sha_new and cf["start_sha"] == "old" and cf["board"] == "f.xlsx"


def test_remote_snapshot_query_runs(tmp_path):
    pd = tmp_path / "progress"
    pd.mkdir()
    (pd / "X_LONG_v14_progress.json").write_text(json.dumps({"snapshot_mismatch": {"start_sha": "s"}, "result_done_utc": "t"}))
    home = tmp_path / "home"
    home.mkdir()
    (home / "v15_current_progress_dir.txt").write_text(str(pd) + "\n")
    r = subprocess.run([sys.executable, "-", "X_LONG", "Y_LONG"], input=A.REMOTE_SNAP_PY, capture_output=True, text=True, env=dict(os.environ, HOME=str(home)))
    out = json.loads(r.stdout)
    assert out["X_LONG"]["snapshot_mismatch"] == {"start_sha": "s"} and "error" in out["Y_LONG"]
