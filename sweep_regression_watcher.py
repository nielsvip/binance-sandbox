# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Sweep Regression Watcher — flags configs that underperform running best.

For each active sweep CSV across local/S1/S2: track running-best Sharpe. When
a new row's Sharpe drops > REGRESSION_PCT vs best, write an alert naming which
cfg_* columns differed vs the best config — those are the suspected culprits.

State persists in data/sweep_alerts/_regression_state.json between runs.
Runs via LaunchAgent every 2 min.
"""
import csv
import io
import json
import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

BASE = Path(__file__).resolve().parent
ALERT_DIR = BASE / "data" / "sweep_alerts"
LOG_DIR = Path(os.path.expanduser("~/logs"))
STATE = ALERT_DIR / "_regression_state.json"
ALERT_DIR.mkdir(parents=True, exist_ok=True)
LOG_DIR.mkdir(parents=True, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_DIR / "sweep_regression_watcher.log"), logging.StreamHandler()],
)
log = logging.getLogger("regression")

REGRESSION_PCT = 0.50  # new sharpe < best * (1 - 0.50) = 50% drop triggers alert
MIN_BEST_SHARPE = 0.30  # don't flag when best is basically noise
MIN_TRADES = 15         # ignore runs with too few trades — not meaningful signal
REGRESSION_COOLDOWN_SECS = 14400  # 4 hours: at most one alert per CSV per 4h

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
    if m["host"] is None:
        return open(path).read()
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


def _load_state():
    if STATE.exists():
        try: return json.loads(STATE.read_text())
        except Exception: return {}
    return {}


def _save_state(s):
    STATE.write_text(json.dumps(s, indent=2))


def _diff_cfgs(a, b):
    return {k.replace("cfg_", ""): {"best": a.get(k), "worse": b.get(k)} for k in a if k.startswith("cfg_") and a.get(k) != b.get(k)}


def _write_regression_alert(machine, csv_path, best_row, worse_row, drop_pct):
    # One alert FILE per CSV (not per run_id). Filename uses CSV stem so cooldown is per-CSV.
    import re
    csv_stem = re.sub(r'[^a-zA-Z0-9_]', '_', Path(csv_path).stem)[:60]
    p = ALERT_DIR / f"regression_{machine}_{csv_stem}.json"
    if p.exists():
        age = time.time() - p.stat().st_mtime
        if age < REGRESSION_COOLDOWN_SECS:
            log.info(f"regression cooldown {csv_stem} ({age:.0f}s < {REGRESSION_COOLDOWN_SECS}s) — skip")
            return False
    payload = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "machine": machine,
        "csv": csv_path,
        "drop_pct": round(drop_pct * 100, 1),
        "best": {"run_id": best_row.get("run_id"), "sharpe": best_row.get("sharpe"), "pnl": best_row.get("pnl"), "trades": best_row.get("trades")},
        "worse": {"run_id": worse_row.get("run_id"), "sharpe": worse_row.get("sharpe"), "pnl": worse_row.get("pnl"), "trades": worse_row.get("trades")},
        "suspect_knobs": _diff_cfgs(best_row, worse_row),
    }
    p.write_text(json.dumps(payload, indent=2))
    suspects = list(payload["suspect_knobs"].keys())
    log.warning(f"REGRESSION {machine}: {worse_row.get('run_id')} sharpe {worse_row.get('sharpe')} vs best {best_row.get('sharpe')} ({drop_pct*100:.0f}% drop) suspects={suspects}")
    return True


def main():
    state = _load_state()
    new_alerts = 0
    for m in MACHINES:
        try: csvs = _list_csvs(m)
        except Exception as e:
            log.error(f"{m['name']} list failed: {e}")
            continue
        for path in csvs:
            key = f"{m['name']}:{path}"
            csv_state = state.setdefault(key, {"best_sharpe": None, "best_row": None, "seen_run_ids": []})
            try: raw = _read_csv(m, path)
            except Exception as e:
                log.error(f"{key} read failed: {e}")
                continue
            rows = _parse_rows(raw)
            ok_rows = [r for r in rows if r.get("status") == "ok"]
            seen = set(csv_state["seen_run_ids"])
            worst_drop, worst_row = 0.0, None
            for r in ok_rows:
                rid = r.get("run_id")
                if not rid or rid in seen: continue
                try:
                    sh = float(r.get("sharpe", "0"))
                    tr = int(r.get("trades", "0"))
                except Exception: continue
                best = csv_state.get("best_sharpe")
                if tr >= MIN_TRADES:
                    # Only meaningful runs (MIN_TRADES+) can set the best baseline or trigger regression
                    if best is not None and best >= MIN_BEST_SHARPE and sh < best * (1 - REGRESSION_PCT):
                        drop = (best - sh) / best
                        if drop > worst_drop:
                            worst_drop = drop
                            worst_row = r
                    if best is None or sh > best:
                        csv_state["best_sharpe"] = sh
                        csv_state["best_row"] = r
                csv_state["seen_run_ids"].append(rid)
            if worst_row is not None:
                if _write_regression_alert(m["name"], path, csv_state["best_row"], worst_row, worst_drop):
                    new_alerts += 1
    _save_state(state)
    log.info(f"scan complete — {new_alerts} regression alerts raised")


if __name__ == "__main__":
    main()
