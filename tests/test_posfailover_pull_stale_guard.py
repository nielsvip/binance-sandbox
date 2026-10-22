"""tools/mac_posfailover_pull.sh staleness guard (USER 2026-10-22 positions-reliability).

Measured 2026-10-22: S1's s1_posfailover_active_* flags existed (07:03Z) while S1's
live-dir position files were from March and its sandbox files 23 h old. Flags say
"S1 is producing" but can outlive S1's writers, so the pull must never merge staged
S1 rows older than POSFAILOVER_MAX_S1_FILE_AGE_S (fail closed).

All three tests are hermetic: fake ssh/rsync/merge-stub via the script's
POSFAILOVER_* overrides, fake REPO under tmp_path — no network, no live files.
"""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "mac_posfailover_pull.sh"

LIVE_LONG = {"fin:ETHUSDC_LONG": {"symbol": "ETHUSDC", "position_side": "LONG", "positionAmt": 0.025, "entry_price": 2509.33, "opened_at": "2026-10-22T16:00:00Z", "max_gain": 0.5}}
LIVE_SHORT = {}
STALE_ROW = {"fin:ETHUSDC_LONG": {"symbol": "ETHUSDC", "position_side": "LONG", "positionAmt": 999.0, "entry_price": 1.0, "opened_at": "2026-03-12T06:19:25Z", "max_gain": 0.0}}


@pytest.fixture
def stage(tmp_path):
    repo = tmp_path / "repo"
    fin = repo / "fin"
    incoming = repo / "data" / "posfailover_incoming" / "fin"
    fin.mkdir(parents=True)
    incoming.mkdir(parents=True)
    (fin / "long_positions.json").write_text(json.dumps(LIVE_LONG))
    (fin / "short_positions.json").write_text(json.dumps(LIVE_SHORT))
    bindir = tmp_path / "bin"
    bindir.mkdir()
    fake_ssh = bindir / "fake_ssh"
    fake_ssh.write_text('#!/bin/bash\nprintf "%s" "$POSFAILOVER_FAKE_FLAGS"\n')
    fake_ssh.chmod(0o755)
    fake_rsync = bindir / "fake_rsync"
    fake_rsync.write_text("#!/bin/bash\nexit 1\n")  # dead transport: pre-seeded stage must decide alone
    fake_rsync.chmod(0o755)
    merge_marker = tmp_path / "merge_calls.txt"
    merge_stub = bindir / "fake_merge_py"
    merge_stub.write_text('#!/bin/bash\necho "$@" >> "$POSFAILOVER_MERGE_MARKER"\n')
    merge_stub.chmod(0o755)
    log = tmp_path / "pull.log"
    return {"repo": repo, "fin": fin, "incoming": incoming, "bindir": bindir, "fake_ssh": fake_ssh, "fake_rsync": fake_rsync, "merge_marker": merge_marker, "merge_stub": merge_stub, "log": log}


def _run_pull(stage, flags):
    env = dict(os.environ)
    env.update({
        "POSFAILOVER_REPO": str(stage["repo"]),
        "POSFAILOVER_LOG": str(stage["log"]),
        "POSFAILOVER_SSH": str(stage["fake_ssh"]),
        "POSFAILOVER_RSYNC": str(stage["fake_rsync"]),
        "POSFAILOVER_PY": str(stage["merge_stub"]),
        "POSFAILOVER_FAKE_FLAGS": flags,
        "POSFAILOVER_MERGE_MARKER": str(stage["merge_marker"]),
    })
    return subprocess.run(["/bin/bash", str(SCRIPT)], env=env, capture_output=True, text=True, timeout=60)


def _seed_stage(stage, rows, age_s):
    for side in ("long", "short"):
        p = stage["incoming"] / f"{side}_positions.json"
        p.write_text(json.dumps(rows))
        old = time.time() - age_s
        os.utime(p, (old, old))


def _live_untouched(stage):
    assert json.loads((stage["fin"] / "long_positions.json").read_text()) == LIVE_LONG
    assert json.loads((stage["fin"] / "short_positions.json").read_text()) == LIVE_SHORT


def test_no_flags_no_merge(stage):
    r = _run_pull(stage, "")
    assert r.returncode == 0
    assert not stage["merge_marker"].exists()
    _live_untouched(stage)


def test_stale_s1_files_never_merged(stage):
    # The 2026-10-22 incident shape: takeover flags present, S1 rows ancient.
    _seed_stage(stage, STALE_ROW, age_s=3600)
    r = _run_pull(stage, "/home/niels/binance/data/s1_posfailover_active_fin")
    assert r.returncode == 0
    assert not stage["merge_marker"].exists(), "stale S1 rows must not reach the merge"
    _live_untouched(stage)
    log = stage["log"].read_text()
    assert "SKIP fin long" in log and "SKIP fin short" in log


def test_fresh_s1_files_still_merge(stage):
    _seed_stage(stage, STALE_ROW, age_s=5)
    r = _run_pull(stage, "/home/niels/binance/data/s1_posfailover_active_fin")
    assert r.returncode == 0
    calls = stage["merge_marker"].read_text()
    assert "long_positions.json" in calls and "short_positions.json" in calls
