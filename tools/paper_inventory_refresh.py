"""Send bounded paper evidence snapshots to the S1 SQL inventory service."""
import argparse
import fcntl
import json
import os
from pathlib import Path
import shlex
import sqlite3
import subprocess
import tempfile

try:
    from .paper_forward_lab import build_plan, digest
except ImportError:
    from paper_forward_lab import build_plan, digest

def run(argv):
    return subprocess.run(argv, check=True, timeout=180, text=True, capture_output=True)

def refresh(root, host):
    state = root / "data/paper_forward_lab"
    state.mkdir(parents=True, exist_ok=True)
    with (state / "inventory_refresh.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {"status": "ALREADY_RUNNING"}
        plan = build_plan(root)
        with sqlite3.connect((root / "data/hourly_reconfig/per_sym_store.db").resolve().as_uri() + "?mode=ro", uri=True) as source:
            source.execute("BEGIN")
            for group in plan["groups"]:
                row = source.execute("SELECT full_config_json FROM per_sym_active WHERE sym_side=?", (group["sym_side"],)).fetchone()
                settings = json.loads(row[0]) if row else None
                if digest(settings) != group["settings_sha256"]:
                    raise RuntimeError("settings changed during plan export; retry next refresh")
                group["effective_settings"] = settings
        with tempfile.TemporaryDirectory(prefix="inventory-upload-", dir=state) as temporary:
            stage = Path(temporary)
            (stage / "plan.json").write_text(json.dumps(plan, allow_nan=False))
            destination = stage / "data/paper_forward_comprehensive"
            destination.mkdir(parents=True)
            for name in ("inventory.json", "scores.json"):
                raw = (root / "data/paper_forward_comprehensive" / name).read_bytes()
                json.loads(raw)
                (destination / name).write_bytes(raw)
            action_scores = root / "data/paper_forward_comprehensive/counterfactual_action_scores.json"
            if action_scores.exists():
                raw = action_scores.read_bytes()
                json.loads(raw)
                (destination / action_scores.name).write_bytes(raw)
            events_destination = stage / "data/paper_nonvec"
            events_destination.mkdir(parents=True)
            for event_file in sorted((root / "data/paper_nonvec").glob("events_*.jsonl")):
                (events_destination / event_file.name).write_bytes(event_file.read_bytes())
            destination = stage / "data/paper_shadow"
            destination.mkdir(parents=True)
            with sqlite3.connect((root / "data/paper_shadow/shadow.db").resolve().as_uri() + "?mode=ro", uri=True) as source, sqlite3.connect(destination / "shadow.db") as target:
                source.backup(target)
            remote = "/home/niels/binance-sandbox/data/paper_forward_lab"
            run(["ssh", "-o", "ConnectTimeout=10", host, "mkdir -p " + shlex.quote(remote + "/incoming")])
            run(["rsync", "-az", "--timeout=30", "-e", "ssh -o ConnectTimeout=10", str(stage) + "/", host + ":" + remote + "/incoming/"])
            response = run(["ssh", "-o", "ConnectTimeout=10", host, "systemctl --user start paper-inventory-refresh.service && systemctl --user show paper-inventory-refresh.service -p Result -p ExecMainStatus"])
            snapshot = state / "scenarios.s1.db.pending"
            run(["rsync", "-az", "--timeout=30", "-e", "ssh -o ConnectTimeout=10", host + ":" + remote + "/scenarios.snapshot.db", str(snapshot)])
            with sqlite3.connect(snapshot.resolve().as_uri() + "?mode=ro", uri=True) as check:
                if check.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                    raise RuntimeError("received inventory snapshot failed integrity check")
            os.replace(snapshot, state / "scenarios.s1.db")
            return {"status": "REFRESHED", "master": host + ":" + remote + "/scenarios.db", "mirror": str(state / "scenarios.s1.db"), "service": response.stdout.strip()}

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--host", default="s1-pub")
    args = parser.parse_args()
    try:
        print(json.dumps(refresh(args.root, args.host), indent=2))
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"inventory transport failed: {exc.stderr}") from exc

if __name__ == "__main__":
    main()
