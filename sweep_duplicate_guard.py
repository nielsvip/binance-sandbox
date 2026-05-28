# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Sweep Duplicate Guard — detects dead sweep knobs and raises red alerts.

Scans V8 sweep CSVs across local, S1, S2. Any group of >=DUPLICATE_THRESHOLD
rows sharing (sharpe, pnl, trades) is analysed: any cfg_* column whose value
differs within the group while outputs are identical is flagged as a DEAD KNOB.

Outputs:
  - data/sweep_alerts/duplicate_<timestamp>.json — red-alert file (cockpit reads)
  - data/sweep_alerts/FIX_REQUIRED_<knob>.json   — actionable fix request
  - logs/sweep_duplicate_guard.log               — run log

Runs via LaunchAgent every 2 min. Rule: broken switch = FIX, never skip.
"""
import csv
import io
import json
import logging
import os
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ALERT_DIR = BASE / "data" / "sweep_alerts"
LOG_DIR = Path(os.path.expanduser("~/logs"))
ALERT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "sweep_duplicate_guard.log"), logging.StreamHandler()],
)
log = logging.getLogger("dup_guard")

DUPLICATE_THRESHOLD = 8  # N configs sharing identical output = suspect (3 was too noisy — coincidental matches in 200+ config sweeps)
KILL_ON_DETECT = False    # set True to auto-kill offending sweep screen

MACHINES = [
    {"name": "Local", "host": None, "sweep_dir": str(BASE / "backtest_v8" / "sweeps")},
    {"name": "S1", "host": "s1-int", "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
    # 2026-05-28 S2 DEAD permanently — S2 machine entry removed
]


def _ssh_run(host, cmd, timeout=15):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", host, cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout


MAX_CSV_AGE_HOURS = 12  # Only scan CSVs newer than this — old CSVs keep regenerating stale alerts

def _list_csvs(m):
    cutoff = time.time() - MAX_CSV_AGE_HOURS * 3600
    if m["host"] is None:
        d = Path(m["sweep_dir"])
        if not d.exists(): return []
        return sorted([str(p) for p in d.glob("v8_sweep_*.csv") if p.stat().st_mtime >= cutoff])
    # For remote: use ls -lt and filter by timestamp via find (-maxdepth 1 excludes archive/ subdir)
    raw = _ssh_run(m["host"], f'find {m["sweep_dir"]} -maxdepth 1 -name "v8_sweep_*.csv" -mmin -{MAX_CSV_AGE_HOURS * 60} 2>/dev/null')
    return [p.strip() for p in raw.splitlines() if p.strip()]


def _read_csv(m, path):
    if m["host"] is None:
        with open(path, "r") as f: return f.read()
    return _ssh_run(m["host"], f"cat {path}")


def _parse_rows(raw):
    rows, header = [], None
    for line in raw.splitlines():
        if not line.strip(): continue
        if line.startswith("run_id,"):
            header = next(csv.reader(io.StringIO(line)))
            continue
        if header is None: continue
        try:
            vals = next(csv.reader(io.StringIO(line)))
            if len(vals) != len(header): continue
            rows.append(dict(zip(header, vals)))
        except Exception:
            continue
    return rows


def _output_key(row):
    try:
        sharpe = round(float(row.get("sharpe", "0")), 3)
        pnl = round(float(row.get("pnl", "0")), 2)
        trades = int(row.get("trades", "0"))
        return (sharpe, pnl, trades)
    except (ValueError, TypeError):
        return None


def _analyse_csv(machine_name, csv_path, rows):
    """Return list of alert dicts; each alert names a dead knob with evidence."""
    ok_rows = [r for r in rows if r.get("status", "") == "ok"]
    if len(ok_rows) < DUPLICATE_THRESHOLD:
        return []
    groups = defaultdict(list)
    for r in ok_rows:
        k = _output_key(r)
        if k is None: continue
        groups[k].append(r)
    alerts = []
    for out_key, members in groups.items():
        if len(members) < DUPLICATE_THRESHOLD: continue
        # Skip low-trade groups — too few trades to distinguish switch effect from noise
        if out_key[2] < 5: continue
        # Skip losing configs — dead knobs on negative-sharpe groups are noise, not actionable
        if out_key[0] <= 0: continue
        cfg_cols = [c for c in members[0].keys() if c.startswith("cfg_")]
        dead = []
        for col in cfg_cols:
            vals = {m.get(col) for m in members}
            if len(vals) > 1:
                dead.append({"knob": col.replace("cfg_", ""), "values_tried": sorted(vals), "count": len(members)})
        if dead:
            alerts.append({
                "machine": machine_name,
                "csv": csv_path,
                "output_key": {"sharpe": out_key[0], "pnl": out_key[1], "trades": out_key[2]},
                "dead_knobs": dead,
                "example_run_ids": [m.get("run_id") for m in members[:5]],
            })
    return alerts


def _write_alert(alert):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    p = ALERT_DIR / f"duplicate_{alert['machine']}_{ts}.json"
    with open(p, "w") as f:
        json.dump({"timestamp": datetime.now(timezone.utc).isoformat(), **alert}, f, indent=2)
    log.warning(f"ALERT WRITTEN: {p.name} — {len(alert['dead_knobs'])} dead knobs on {alert['machine']}")
    return p


FIX_REQUIRED_COOLDOWN_SECS = 7200  # 2 hours: don't re-write same FIX_REQUIRED within this window


def _write_fix_request(alert):
    """One fix-request per dead knob — picked up by agent spawn loop.

    Cooldown: if a FIX_REQUIRED for this knob already exists and is < FIX_REQUIRED_COOLDOWN_SECS
    old, skip writing to avoid refreshing the timestamp every 2 min (which would keep spawning
    new agents even when no fix has been deployed yet).
    """
    written = []
    now = time.time()
    for dk in alert["dead_knobs"]:
        knob = dk["knob"]
        p = ALERT_DIR / f"FIX_REQUIRED_{knob}.json"
        if p.exists():
            age = now - p.stat().st_mtime
            if age < FIX_REQUIRED_COOLDOWN_SECS:
                log.info(f"FIX_REQUIRED_{knob} cooldown ({age:.0f}s < {FIX_REQUIRED_COOLDOWN_SECS}s) — skip re-write")
                continue
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "knob": knob,
            "values_tried": dk["values_tried"],
            "identical_output": alert["output_key"],
            "evidence_csv": alert["csv"],
            "machine": alert["machine"],
            "example_run_ids": alert["example_run_ids"],
            "status": "pending_agent",
        }
        with open(p, "w") as f:
            json.dump(payload, f, indent=2)
        log.warning(f"FIX REQUEST: {p.name} ← knob '{knob}' values {dk['values_tried']} produced identical {alert['output_key']}")
        written.append(str(p))
    return written


def _kill_sweep(machine_name):
    if not KILL_ON_DETECT: return
    if machine_name == "Local": return
    host = "s1-int" if machine_name == "S1" else "s2-int"
    log.warning(f"KILLING sweep on {host} (KILL_ON_DETECT=True)")
    _ssh_run(host, "screen -S sweep -X quit 2>/dev/null; pkill -f backtest_v8_sweep.py")


def main():
    t0 = time.time()
    total_alerts = 0
    for m in MACHINES:
        try:
            csvs = _list_csvs(m)
        except Exception as e:
            log.error(f"{m['name']}: list failed {e}")
            continue
        for path in csvs:
            try:
                raw = _read_csv(m, path)
                rows = _parse_rows(raw)
                alerts = _analyse_csv(m["name"], path, rows)
                for a in alerts:
                    _write_alert(a)
                    _write_fix_request(a)
                    total_alerts += 1
                if alerts:
                    _kill_sweep(m["name"])
            except Exception as e:
                log.error(f"{m['name']}:{path} failed: {e}")
    log.info(f"scan complete in {time.time()-t0:.1f}s — {total_alerts} dead-knob alerts raised")


if __name__ == "__main__":
    main()
