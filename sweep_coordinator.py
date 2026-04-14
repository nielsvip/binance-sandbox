#!/usr/bin/env python3
"""
SWEEP COORDINATOR — Orchestrates V5 backtests across all machines.

Architecture:
  - 101.csv = SINGLE SOURCE OF TRUTH (on 157, synced to all)
    - QUEUE rows: status=PENDING → workers pick them up
    - DONE rows: status=DONE with full results
    - Any agent/script can add PENDING rows with new params to test
  - Each worker: pulls next PENDING, marks RUNNING, runs V5, writes DONE
  - Coordinator (this script on 157): monitors workers, restarts dead ones, syncs 101.csv

Machines:
  157.180.125.52 = COORDINATOR + WORKER (30GB, main)
  204.168.181.211 = WORKER (30GB)
  MacBook = WORKER (when available) + XLS viewer

Usage:
  python3 sweep_coordinator.py                    # Run coordinator (on 157)
  python3 sweep_coordinator.py --worker           # Run as worker (any machine)
  python3 sweep_coordinator.py --add-configs      # Generate and add PENDING configs to 101.csv
  python3 sweep_coordinator.py --status           # Show progress
  python3 sweep_coordinator.py --xls              # Generate 101.xlsx from 101.csv
"""
import argparse, csv, fcntl, json, logging, os, platform, subprocess, sys, time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
import socket

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("coordinator")

HOSTNAME = socket.gethostname()
IS_MAC = platform.system() == "Darwin"
IS_SERVER = not IS_MAC

if IS_MAC:
    BASE = Path("/Users/niels/Documents/binance")
    PY = "/opt/anaconda3/envs/binance_env/bin/python3"
    SANDBOX = BASE
else:
    BASE = Path("/home/niels/binance")
    PY = str(Path.home() / ".conda/envs/binance_env/bin/python3")
    if not Path(PY).exists():
        PY = str(Path.home() / "miniconda3/envs/binance_env/bin/python3")
    SANDBOX = Path("/home/niels/binance-sandbox")

CSV_PATH = BASE / "101.csv"
LOCK_PATH = Path.home() / "SWEEP_RUNNING"

SERVERS = {
    "157.180.125.52": {"name": "s1", "ram_gb": 30, "role": "coordinator+worker"},
    "204.168.181.211": {"name": "s2", "ram_gb": 30, "role": "worker"},
}

# ─── CSV LOCKING ────────────────────────────────────────────────────────────
def locked_read_csv():
    """Read 101.csv with file lock."""
    if not CSV_PATH.exists():
        return [], []
    with open(CSV_PATH, "r") as f:
        fcntl.flock(f, fcntl.LOCK_SH)
        reader = csv.DictReader(f)
        header = reader.fieldnames or []
        rows = list(reader)
        fcntl.flock(f, fcntl.LOCK_UN)
    return header, rows

def locked_write_csv(header, rows):
    """Write 101.csv with exclusive lock."""
    with open(CSV_PATH, "w", newline="") as f:
        fcntl.flock(f, fcntl.LOCK_EX)
        writer = csv.DictWriter(f, fieldnames=header)
        writer.writeheader()
        writer.writerows(rows)
        fcntl.flock(f, fcntl.LOCK_UN)

CSV_HEADER = [
    "config_id", "status", "worker", "started_at", "finished_at",
    # Parameters (every param needed to reproduce)
    "noloss", "wt_exit_tfs", "wt_exit_mode", "wt_vel_threshold",
    "entry_d_gate", "ablation", "start_date", "capital", "account",
    "extra_config",
    # Results
    "total_trades", "realized_pnl", "unrealized_pnl", "total_pnl",
    "win_rate", "wins", "losses", "opens", "augments",
    "sharpe", "max_dd_pct", "avg_win_pct", "avg_loss_pct",
    "ibs_exits", "wt_exits", "dc_exits", "struct_exits", "other_exits",
    "open_positions", "elapsed_s",
    # Reproduce
    "reproduce_command",
]

# ─── CONFIG GENERATION ──────────────────────────────────────────────────────
def generate_configs():
    """Generate all configs to test — skips already-done ones."""
    header, existing = locked_read_csv()
    done_ids = {r["config_id"] for r in existing if r.get("status") in ("DONE", "RUNNING", "PENDING")}
    configs = []
    noloss_vals = [0, 0.3, 0.5, 1.0, 2.0, 3.0, 5.0]
    wt_tfs_vals = ["off", "4h", "D", "4h,D", "1h,4h,D"]
    wt_modes = ["cross", "velocity"]
    wt_vels = ["-2.0", "-5.0", "-8.0"]
    dgates = ["0", "1"]
    ablations = ["ALL", "WT_EXIT"]
    extra_cfgs = [
        ("default", "{}"),
        ("high_score", '{"ENTRY_SCORE_THRESHOLD":24}'),
        ("loose_score", '{"ENTRY_SCORE_THRESHOLD":14}'),
        ("mfi_sizing", '{"SIZING_INDICATOR":"mfi"}'),
        ("no_reentry", '{"REENTRY_MANDATORY":false,"FAST_REENTRY_ENABLED":false}'),
        ("fast_reentry", '{"REENTRY_MANDATORY":true,"FAST_REENTRY_ENABLED":true}'),
        ("no_augment", '{"MAX_AUGMENTS_PER_POSITION":0}'),
        ("patient_exit", '{"MIN_HOLD_BARS_BEFORE_EXIT":64}'),
    ]
    for nl in noloss_vals:
        for wtf in wt_tfs_vals:
            for wm in wt_modes:
                if wtf == "off" and wm == "velocity":
                    continue
                vel_list = wt_vels if wm == "velocity" else ["0"]
                for vel in vel_list:
                    for dg in dgates:
                        for abl in ablations:
                            for extra_name, extra_json in extra_cfgs:
                                cid = f"nl{nl}_wt{wtf.replace(',','+')}_" \
                                      f"{wm}_v{vel}_dg{dg}_{abl}_{extra_name}"
                                if cid in done_ids:
                                    continue
                                configs.append({
                                    "config_id": cid, "status": "PENDING",
                                    "worker": "", "started_at": "", "finished_at": "",
                                    "noloss": str(nl), "wt_exit_tfs": wtf,
                                    "wt_exit_mode": wm, "wt_vel_threshold": vel,
                                    "entry_d_gate": dg, "ablation": abl,
                                    "start_date": "2024-06-01", "capital": "70000",
                                    "account": "trb", "extra_config": extra_json,
                                    "total_trades": "", "realized_pnl": "", "unrealized_pnl": "",
                                    "total_pnl": "", "win_rate": "", "wins": "", "losses": "",
                                    "opens": "", "augments": "", "sharpe": "", "max_dd_pct": "",
                                    "avg_win_pct": "", "avg_loss_pct": "", "ibs_exits": "",
                                    "wt_exits": "", "dc_exits": "", "struct_exits": "",
                                    "other_exits": "", "open_positions": "", "elapsed_s": "",
                                    "reproduce_command": "",
                                })
    return configs

def add_configs():
    """Add new PENDING configs to 101.csv."""
    new_configs = generate_configs()
    if not new_configs:
        log.info("No new configs to add — all already in 101.csv")
        return
    header, existing = locked_read_csv()
    if not header:
        header = CSV_HEADER
    existing.extend(new_configs)
    locked_write_csv(header, existing)
    log.info(f"Added {len(new_configs)} new PENDING configs to 101.csv (total: {len(existing)})")

# ─── WORKER ─────────────────────────────────────────────────────────────────
def claim_next_config():
    """Atomically claim the next PENDING config."""
    header, rows = locked_read_csv()
    if not header:
        return None, None, None
    for r in rows:
        if r.get("status") == "PENDING":
            r["status"] = "RUNNING"
            r["worker"] = HOSTNAME
            r["started_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            locked_write_csv(header, rows)
            return header, rows, r
    return header, rows, None

def build_command(cfg):
    """Build the exact V7 engine command from config."""
    # Use V7 engine (on sandbox) if available, fallback to V5
    v7_path = SANDBOX / "backtest_v7_engine.py"
    v5_path = BASE / "backtest_v5_full_tradier.py"
    if v7_path.exists():
        engine = str(v7_path)
        cmd = f"{PY} {engine} --mode tradier --all"
        cmd += f" --start {cfg['start_date']} --capital {cfg['capital']} --account {cfg['account']}"
        nl = float(cfg.get("noloss", 0))
        if nl == 0:
            cmd += " --allow-loss"
        else:
            cmd += " --no-loss"
        extra = cfg.get("extra_config", "{}")
        if extra and extra != "{}":
            # Pass config overrides via V5_CONFIG_OVERRIDES env var (V7 reads this)
            override_file = BASE / f"sweep_logs/{cfg['config_id']}_overrides.json"
            override_file.parent.mkdir(parents=True, exist_ok=True)
            merged = json.loads(extra)
            if nl > 0:
                merged["NOLOSS_MIN_PROFIT_PCT_TRADIER"] = nl
            override_file.write_text(json.dumps(merged))
            cmd = f"V5_CONFIG_OVERRIDES={override_file} {cmd}"
        elif nl > 0:
            override_file = BASE / f"sweep_logs/{cfg['config_id']}_overrides.json"
            override_file.parent.mkdir(parents=True, exist_ok=True)
            override_file.write_text(json.dumps({"NOLOSS_MIN_PROFIT_PCT_TRADIER": nl}))
            cmd = f"V5_CONFIG_OVERRIDES={override_file} {cmd}"
    else:
        engine = str(v5_path)
        cmd = f"{PY} {engine} --all"
        cmd += f" --start {cfg['start_date']} --capital {cfg['capital']} --account {cfg['account']}"
        cmd += f" --noloss {cfg['noloss']} --ablation {cfg.get('ablation', 'ALL')}"
        wtf = cfg.get("wt_exit_tfs", "off")
        if wtf != "off":
            cmd += f" --wt-exit-tfs {wtf} --wt-exit-mode {cfg['wt_exit_mode']}"
            if cfg["wt_exit_mode"] != "cross":
                cmd += f" --wt-vel-threshold {cfg['wt_vel_threshold']}"
        if cfg.get("entry_d_gate") == "1":
            cmd += " --entry-d-gate"
        extra = cfg.get("extra_config", "{}")
        if extra and extra != "{}":
            cmd += f" --config '{extra}'"
    return cmd

def parse_engine_output(logfile):
    """Parse V5 engine output for results."""
    import re, glob
    import numpy as np
    lines = open(logfile).readlines() if os.path.exists(logfile) else []
    # Find trades JSONL
    trades_file = None
    for l in lines:
        m = re.search(r'(/[^\s]+\.jsonl)', l)
        if m and 'full_' in m.group(1):
            trades_file = m.group(1)
    if not trades_file or not os.path.exists(trades_file):
        tdir = SANDBOX / "backtest_v5" / "logs"
        files = sorted(tdir.glob("full_*_trb_*.jsonl"), key=lambda f: f.stat().st_mtime)
        if files:
            trades_file = str(files[-1])
    if not trades_file or not os.path.exists(trades_file):
        return {}
    trades = []
    for l in open(trades_file):
        try:
            trades.append(json.loads(l))
        except Exception:
            pass
    closes = [t for t in trades if t.get("action") == "CLOSE"]
    opens_t = [t for t in trades if t.get("action") == "OPEN"]
    augments = [t for t in trades if t.get("action") == "AUGMENT"]
    wins = [t for t in closes if t.get("gain", 0) > 0]
    losses = [t for t in closes if t.get("gain", 0) <= 0]
    realized = sum(t.get("pnl", 0) for t in closes)
    wr = len(wins) / max(1, len(closes)) * 100
    avg_w = float(np.mean([t.get("gain", 0) for t in wins])) if wins else 0
    avg_l = float(np.mean([t.get("gain", 0) for t in losses])) if losses else 0
    rc = defaultdict(int)
    for t in closes:
        r = t.get("reason", "").upper()
        if "IBS" in r:
            rc["IBS"] += 1
        elif "WT" in r:
            rc["WT"] += 1
        elif "DC" in r:
            rc["DC"] += 1
        elif "STRUCT" in r:
            rc["STRUCT"] += 1
        else:
            rc["OTHER"] += 1
    sharpe = 0.0
    max_dd = 0.0
    open_pos = 0
    unrealized = 0.0
    for l in lines:
        if "Sharpe:" in l:
            m = re.search(r"Sharpe: ([\d.-]+)", l)
            if m:
                sharpe = float(m.group(1))
            m = re.search(r"Max DD: ([\d.]+)", l)
            if m:
                max_dd = float(m.group(1))
        if "Still open:" in l:
            m = re.search(r"Still open: (\d+)", l)
            if m:
                open_pos = int(m.group(1))
        if "Unrealized:" in l:
            m = re.search(r"Unrealized: \$([\d.-]+)", l)
            if m:
                unrealized = float(m.group(1))
    return {
        "total_trades": len(closes), "realized_pnl": f"{realized:.2f}",
        "unrealized_pnl": f"{unrealized:.2f}", "total_pnl": f"{realized + unrealized:.2f}",
        "win_rate": f"{wr:.1f}", "wins": len(wins), "losses": len(losses),
        "opens": len(opens_t), "augments": len(augments),
        "sharpe": f"{sharpe:.3f}", "max_dd_pct": f"{max_dd:.1f}",
        "avg_win_pct": f"{avg_w:.3f}", "avg_loss_pct": f"{avg_l:.3f}",
        "ibs_exits": rc.get("IBS", 0), "wt_exits": rc.get("WT", 0),
        "dc_exits": rc.get("DC", 0), "struct_exits": rc.get("STRUCT", 0),
        "other_exits": rc.get("OTHER", 0), "open_positions": open_pos,
    }

def run_worker():
    """Worker loop: claim config, run engine, write results, repeat."""
    log.info(f"Worker started on {HOSTNAME}")
    LOCK_PATH.write_text(f"SWEEP WORKER on {HOSTNAME} — PID {os.getpid()} — {datetime.now(timezone.utc)}")
    while True:
        header, rows, cfg = claim_next_config()
        if cfg is None:
            log.info("No more PENDING configs. Worker done.")
            break
        cid = cfg["config_id"]
        cmd = build_command(cfg)
        logfile = BASE / f"sweep_logs/{cid}.log"
        logfile.parent.mkdir(parents=True, exist_ok=True)
        log.info(f"[{cid}] RUNNING: {cmd}")
        t0 = time.time()
        try:
            subprocess.run(cmd, shell=True, stdout=open(logfile, "w"), stderr=subprocess.STDOUT, timeout=1800)
        except subprocess.TimeoutExpired:
            log.warning(f"[{cid}] TIMEOUT after 30min")
        except Exception as e:
            log.error(f"[{cid}] ERROR: {e}")
        elapsed = int(time.time() - t0)
        results = parse_engine_output(str(logfile))
        # Update CSV
        header, rows = locked_read_csv()
        for r in rows:
            if r["config_id"] == cid:
                r["status"] = "DONE"
                r["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
                r["elapsed_s"] = str(elapsed)
                r["reproduce_command"] = cmd
                r.update({k: str(v) for k, v in results.items()})
                break
        locked_write_csv(header, rows)
        trades = results.get("total_trades", 0)
        pnl = results.get("realized_pnl", "0")
        wr = results.get("win_rate", "0")
        sharpe = results.get("sharpe", "0")
        log.info(f"[{cid}] DONE: Trades={trades} PnL=${pnl} WR={wr}% Sharpe={sharpe} ({elapsed}s)")
    LOCK_PATH.unlink(missing_ok=True)

# ─── COORDINATOR ────────────────────────────────────────────────────────────
def sync_csv_to_servers():
    """Push 101.csv to all workers."""
    for ip in SERVERS:
        try:
            subprocess.run(["scp", "-o", "ConnectTimeout=10", str(CSV_PATH), f"niels@{ip}:{BASE}/101.csv"], capture_output=True, timeout=30)
            log.info(f"Synced 101.csv → {ip}")
        except Exception as e:
            log.warning(f"Failed to sync to {ip}: {e}")

def sync_csv_from_servers():
    """Pull 101.csv updates from workers, merge."""
    header, local_rows = locked_read_csv()
    local_map = {r["config_id"]: r for r in local_rows}
    for ip in SERVERS:
        tmp = f"/tmp/101_{SERVERS[ip]['name']}.csv"
        try:
            subprocess.run(["scp", "-o", "ConnectTimeout=10", f"niels@{ip}:{BASE}/101.csv", tmp], capture_output=True, timeout=30)
            with open(tmp) as f:
                reader = csv.DictReader(f)
                for r in reader:
                    cid = r["config_id"]
                    if cid in local_map:
                        if r.get("status") == "DONE" and local_map[cid].get("status") != "DONE":
                            local_map[cid] = r
                    else:
                        local_map[cid] = r
            log.info(f"Merged from {ip}")
        except Exception as e:
            log.warning(f"Failed to pull from {ip}: {e}")
    merged = list(local_map.values())
    locked_write_csv(header or CSV_HEADER, merged)

def check_worker(ip):
    """Check if worker is alive and producing."""
    try:
        result = subprocess.run(["ssh", "-o", "ConnectTimeout=10", f"niels@{ip}", "cat /home/niels/SWEEP_RUNNING 2>/dev/null; echo '==='; ps aux | grep backtest_v5_full | grep -v grep | wc -l"], capture_output=True, text=True, timeout=20)
        output = result.stdout
        lines = output.strip().split("\n")
        has_lock = "SWEEP" in lines[0] if lines else False
        n_procs = int(lines[-1]) if lines else 0
        return has_lock or n_procs > 0
    except Exception:
        return False

def restart_worker(ip):
    """Restart worker on a server."""
    log.info(f"Restarting worker on {ip}")
    try:
        subprocess.run(["scp", "-o", "ConnectTimeout=10", str(CSV_PATH), f"niels@{ip}:{BASE}/101.csv"], capture_output=True, timeout=30)
        subprocess.run(["scp", "-o", "ConnectTimeout=10", str(BASE / "sweep_coordinator.py"), f"niels@{ip}:{BASE}/"], capture_output=True, timeout=30)
        py_remote = "/home/niels/.conda/envs/binance_env/bin/python3" if "157.180" in ip else "/home/niels/miniconda3/envs/binance_env/bin/python3"
        subprocess.run(["ssh", "-o", "ConnectTimeout=10", f"niels@{ip}", f"screen -X -S sweep48h quit 2>/dev/null; screen -dmS sweep48h {py_remote} /home/niels/binance/sweep_coordinator.py --worker"], capture_output=True, timeout=20)
        log.info(f"Worker restarted on {ip}")
    except Exception as e:
        log.warning(f"Failed to restart {ip}: {e}")

def run_coordinator():
    """Main coordinator loop."""
    log.info("COORDINATOR starting on " + HOSTNAME)
    LOCK_PATH.write_text(f"COORDINATOR on {HOSTNAME} — PID {os.getpid()} — {datetime.now(timezone.utc)}")
    # Also run as worker locally
    import threading
    worker_thread = threading.Thread(target=run_worker, daemon=True)
    worker_thread.start()
    while worker_thread.is_alive():
        time.sleep(300)  # Check every 5 minutes
        log.info("Coordinator check...")
        # Sync results
        sync_csv_from_servers()
        sync_csv_to_servers()
        # Check workers
        for ip, info in SERVERS.items():
            alive = check_worker(ip)
            if not alive:
                log.warning(f"Worker {info['name']} ({ip}) NOT producing — restarting")
                restart_worker(ip)
            else:
                log.info(f"Worker {info['name']} ({ip}) OK")
        # Print status
        show_status()
    LOCK_PATH.unlink(missing_ok=True)
    log.info("COORDINATOR done")

# ─── STATUS & XLS ───────────────────────────────────────────────────────────
def show_status():
    """Print current sweep status."""
    header, rows = locked_read_csv()
    done = [r for r in rows if r.get("status") == "DONE"]
    pending = [r for r in rows if r.get("status") == "PENDING"]
    running = [r for r in rows if r.get("status") == "RUNNING"]
    log.info(f"STATUS: {len(done)} DONE / {len(running)} RUNNING / {len(pending)} PENDING / {len(rows)} TOTAL")
    if done:
        done_sorted = sorted(done, key=lambda r: float(r.get("realized_pnl", 0) or 0), reverse=True)
        log.info("TOP 5 by PnL:")
        for r in done_sorted[:5]:
            log.info(f"  {r['config_id']}: Trades={r.get('total_trades',0)} PnL=${r.get('realized_pnl',0)} WR={r.get('win_rate',0)}% Sharpe={r.get('sharpe',0)}")

def generate_xls():
    """Generate 101.xlsx from 101.csv."""
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font, Alignment
        from openpyxl.utils import get_column_letter
    except ImportError:
        log.error("openpyxl not installed. Run: pip install openpyxl")
        return
    header, rows = locked_read_csv()
    done = [r for r in rows if r.get("status") == "DONE"]
    pending = [r for r in rows if r.get("status") == "PENDING"]
    running = [r for r in rows if r.get("status") == "RUNNING"]
    done.sort(key=lambda r: float(r.get("realized_pnl", 0) or 0), reverse=True)
    green = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    red = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    yellow = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    hdr_fill = PatternFill(start_color="4472C4", end_color="4472C4", fill_type="solid")
    hdr_font = Font(color="FFFFFF", bold=True)
    wb = openpyxl.Workbook()
    # DASHBOARD
    ws = wb.active
    ws.title = "DASHBOARD"
    ws["A1"] = f"SWEEP STATUS — {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}"
    ws["A1"].font = Font(bold=True, size=16)
    ws["A3"] = f"DONE: {len(done)} | RUNNING: {len(running)} | PENDING: {len(pending)} | TOTAL: {len(rows)}"
    ws["A3"].font = Font(bold=True, size=14, color="FF0000")
    ws["A5"] = "Progress:"
    ws["B5"] = f"{len(done)*100//max(len(rows),1)}%"
    # RESULTS sheet
    ws2 = wb.create_sheet("RESULTS")
    display_cols = ["config_id", "status", "noloss", "wt_exit_tfs", "wt_exit_mode", "wt_vel_threshold", "entry_d_gate", "ablation", "extra_config", "total_trades", "realized_pnl", "win_rate", "sharpe", "max_dd_pct", "avg_win_pct", "avg_loss_pct", "ibs_exits", "wt_exits", "dc_exits", "struct_exits", "other_exits", "elapsed_s", "reproduce_command"]
    for j, h in enumerate(display_cols):
        c = ws2.cell(row=1, column=j + 1, value=h)
        c.fill = hdr_fill
        c.font = hdr_font
    all_sorted = done + running + pending
    for i, r in enumerate(all_sorted):
        for j, col in enumerate(display_cols):
            val = r.get(col, "")
            try:
                val = float(val)
            except (ValueError, TypeError):
                pass
            c = ws2.cell(row=i + 2, column=j + 1, value=val)
            if r.get("status") == "DONE":
                c.fill = green
            elif r.get("status") == "RUNNING":
                c.fill = yellow
    for col in ws2.columns:
        ml = max(len(str(c.value or "")) for c in col)
        ws2.column_dimensions[get_column_letter(col[0].column)].width = min(ml + 2, 50)
    ws2.freeze_panes = "A2"
    ws2.auto_filter.ref = ws2.dimensions
    xls_path = BASE / "101.xlsx"
    wb.save(str(xls_path))
    log.info(f"XLS saved: {xls_path} ({len(done)} done, {len(pending)} pending)")

# ─── MAIN ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--worker", action="store_true", help="Run as worker")
    parser.add_argument("--add-configs", action="store_true", help="Add PENDING configs")
    parser.add_argument("--status", action="store_true", help="Show status")
    parser.add_argument("--xls", action="store_true", help="Generate 101.xlsx")
    args = parser.parse_args()
    if args.add_configs:
        add_configs()
    elif args.status:
        show_status()
    elif args.xls:
        generate_xls()
    elif args.worker:
        run_worker()
    else:
        # Default: coordinator mode
        add_configs()  # Ensure configs exist
        run_coordinator()

if __name__ == "__main__":
    main()
