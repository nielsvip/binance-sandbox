# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""V8 Sweep Coordinator — MacBook master, prevents duplicate parameter tests across machines.

Non-invasive: scans all V8 sweep CSVs across local+S1+S2, fingerprints each config by
(sorted cfg tuple + tier tag). If the SAME fingerprint appears on >1 machine, that is
wasted compute — writes a dupe-waste alert naming the later duplicate.

Also maintains a global "completed configs" state cockpit can display.

State: data/sweep_alerts/_coordinator_plan.json
Runs via LaunchAgent every 5 min.
"""
import csv
import hashlib
import io
import json
import logging
import os
import subprocess
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ALERT_DIR = BASE / "data" / "sweep_alerts"
LOG_DIR = Path(os.path.expanduser("~/logs"))
PLAN = ALERT_DIR / "_coordinator_plan.json"
ALERT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "sweep_coordinator_v8.log"), logging.StreamHandler()],
)
log = logging.getLogger("coord_v8")

MACHINES = [
    {"name": "Local", "host": None, "sweep_dir": str(BASE / "backtest_v8" / "sweeps")},
    {"name": "S1", "host": "s1-int", "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
    # 2026-05-28 S2 DEAD permanently — S2 machine entry removed
]


def _ssh(host, cmd, timeout=15):
    r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", host, cmd], capture_output=True, text=True, timeout=timeout)
    return r.stdout


def _list_csvs(m):
    if m["host"] is None:
        d = Path(m["sweep_dir"])
        return sorted([str(p) for p in d.glob("v8_sweep_*.csv")]) if d.exists() else []
    return [p.strip() for p in _ssh(m["host"], f'ls -1 {m["sweep_dir"]}/v8_sweep_*.csv 2>/dev/null').splitlines() if p.strip()]


def _read_csv(m, path):
    if m["host"] is None: return open(path).read()
    return _ssh(m["host"], f"cat {path}")


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
            if len(vals) == len(header):
                rows.append(dict(zip(header, vals)))
        except Exception:
            continue
    return rows


def _csv_tier(path):
    name = os.path.basename(path)
    parts = name.replace("v8_sweep_", "").split("_")
    return "_".join(parts[:3]) if len(parts) >= 3 else name


def _fingerprint(row, tier):
    cfg_items = sorted((k, v) for k, v in row.items() if k.startswith("cfg_"))
    blob = json.dumps({"tier": tier, "cfg": cfg_items}, sort_keys=True)
    return hashlib.sha1(blob.encode()).hexdigest()[:16]


def _write_dupe_alert(fp, first, second):
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    p = ALERT_DIR / f"coord_dupe_{second['machine']}_{ts}_{fp}.json"
    p.write_text(json.dumps({
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "fingerprint": fp,
        "first_run": first,
        "duplicate_run": second,
        "waste_seconds": second.get("elapsed", 0),
    }, indent=2))
    log.warning(f"DUPE fp={fp} first={first['machine']}:{first['run_id']} dupe={second['machine']}:{second['run_id']} (wasted {second.get('elapsed',0)}s)")


def main():
    plan = {"by_fingerprint": {}, "by_machine": defaultdict(lambda: {"ok": 0, "fail": 0, "rows": 0, "elapsed_total": 0})}
    if PLAN.exists():
        try:
            prev = json.loads(PLAN.read_text())
            plan["by_fingerprint"] = prev.get("by_fingerprint", {})
        except Exception:
            pass
    reported_dupes = set()
    for m in MACHINES:
        try: csvs = _list_csvs(m)
        except Exception as e:
            log.error(f"{m['name']} list failed: {e}")
            continue
        for path in csvs:
            tier = _csv_tier(path)
            try: raw = _read_csv(m, path)
            except Exception as e:
                log.error(f"{path} read failed: {e}")
                continue
            for r in _parse_rows(raw):
                stats = plan["by_machine"][m["name"]]
                stats["rows"] += 1
                if r.get("status") == "ok": stats["ok"] += 1
                else: stats["fail"] += 1
                try: stats["elapsed_total"] += float(r.get("elapsed", "0") or 0)
                except Exception: pass
                fp = _fingerprint(r, tier)
                entry = {"machine": m["name"], "csv": path, "run_id": r.get("run_id"), "sharpe": r.get("sharpe"), "pnl": r.get("pnl"), "trades": r.get("trades"), "elapsed": float(r.get("elapsed", "0") or 0)}
                if fp in plan["by_fingerprint"]:
                    first = plan["by_fingerprint"][fp]
                    if first["machine"] != m["name"] and fp not in reported_dupes:
                        _write_dupe_alert(fp, first, entry)
                        reported_dupes.add(fp)
                else:
                    plan["by_fingerprint"][fp] = entry
    plan["by_machine"] = dict(plan["by_machine"])
    plan["updated_at"] = datetime.now(timezone.utc).isoformat()
    plan["total_unique_configs"] = len(plan["by_fingerprint"])
    plan["total_duplicates_detected"] = len(reported_dupes)
    PLAN.write_text(json.dumps(plan, indent=2))
    log.info(f"scan complete — {plan['total_unique_configs']} unique configs, {len(reported_dupes)} new cross-machine dupes")


if __name__ == "__main__":
    main()
