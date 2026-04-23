#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""Unified Newsletter — delta-only digest combining copy-trader research + backtest sweeps.

Only reports what's NEW since the last newsletter:
  - New copy-trader patterns/indicators/health changes
  - Server 1 (157.180) backtest sweep progress + results
  - Server 2 (204.168) backtest sweep progress + results
  - Local MacBook pre-testing status
  - What was concluded and applied to live

State tracked in data/newsletter_state.json to avoid repeating findings.

Usage:
  python unified_newsletter.py              # Generate + send
  python unified_newsletter.py --dry-run    # Generate but don't send
  python unified_newsletter.py --force      # Send even if no new findings
"""
import argparse
import csv
import json
import logging
import os
import platform
import subprocess
import sys
import time
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
IS_MAC = platform.system() == "Darwin"
if IS_MAC:
    BASE_PATH = Path("/Users/niels/Documents/binance")
    LOG_DIR = Path("/Users/niels/logs")
else:
    BASE_PATH = Path("/home/niels/binance")
    LOG_DIR = Path("/home/niels/logs")
sys.path.insert(0, str(BASE_PATH))
from config import Config
config = Config()
DATA_DIR = config.DATA_DIR
REPORT_DIR = DATA_DIR / "trader_research_reports"
STATE_FILE = DATA_DIR / "newsletter_state.json"
LOG_DIR.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("unified_newsletter")
logger.setLevel(logging.INFO)
if not logger.handlers:
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(console)
    fh = RotatingFileHandler(LOG_DIR / "unified_newsletter.log", maxBytes=5_000_000, backupCount=3)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)
SERVER_1 = "s1-int"
SERVER_2 = "s2-int"


def load_state() -> dict:
    """Load previous newsletter state."""
    if STATE_FILE.exists():
        try:
            with open(STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"last_sent": None, "last_n_trades": 0, "last_n_traders": 0, "last_patterns": [], "last_indicator_edges": [], "last_health_green": [], "last_health_red": [], "last_sweep_s1": {}, "last_sweep_s2": {}, "last_sweep_local": {}, "applied_configs": []}


def save_state(state: dict):
    """Save newsletter state."""
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2, default=str)


def _ssh_cmd(server: str, cmd: str, timeout: int = 15) -> str:
    """Run SSH command, return stdout or empty string on failure."""
    try:
        result = subprocess.run(["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", server, cmd], capture_output=True, text=True, timeout=timeout)
        return result.stdout.strip()
    except Exception as e:
        logger.warning(f"SSH to {server} failed: {e}")
        return ""


# ═══════════════════════════════════════════════════════════════════
# SECTION 1 — COPY TRADER DELTA
# ═══════════════════════════════════════════════════════════════════

def get_latest_sweep_findings() -> dict:
    """Get the most recent sweep findings JSON."""
    findings_files = sorted(REPORT_DIR.glob("sweep_findings_*.json"), reverse=True)
    if not findings_files:
        return {}
    try:
        with open(findings_files[0]) as f:
            return json.load(f)
    except Exception:
        return {}


def get_latest_sweep_report() -> dict:
    """Parse the latest sweep report for structured data."""
    report_files = sorted(REPORT_DIR.glob("sweep_report_*.md"), reverse=True)
    if not report_files:
        return {}
    try:
        text = report_files[0].read_text()
        result = {"file": report_files[0].name, "indicators": [], "patterns": [], "health": ""}
        in_indicators = False
        in_patterns = False
        for line in text.splitlines():
            if "Discriminative Indicators" in line:
                in_indicators = True
                in_patterns = False
                continue
            if "Win-Rate Patterns" in line:
                in_patterns = True
                in_indicators = False
                continue
            if "Trader Health:" in line:
                result["health"] = line.strip().replace("## ", "")
                in_indicators = False
                in_patterns = False
                continue
            if line.startswith("## "):
                in_indicators = False
                in_patterns = False
                continue
            if in_indicators and "|" in line and "Indicator" not in line and "---" not in line:
                parts = [p.strip() for p in line.split("|") if p.strip()]
                if len(parts) >= 5:
                    result["indicators"].append({"name": parts[0], "winner": parts[1], "loser": parts[2], "d": parts[3], "p": parts[4]})
            if in_patterns and line.startswith("- **WR="):
                result["patterns"].append(line.strip("- "))
        return result
    except Exception:
        return {}


def compute_trader_delta(state: dict) -> dict:
    """Compare current findings vs last newsletter — return only NEW stuff."""
    findings = get_latest_sweep_findings()
    report = get_latest_sweep_report()
    if not findings:
        return {"has_new": False}
    delta = {"has_new": False, "new_trades": 0, "new_traders": 0, "total_trades": findings.get("n_trades", 0), "total_traders": findings.get("n_traders", 0), "overall_wr": findings.get("overall_wr", 0), "v8_results": findings.get("v8_results"), "new_patterns": [], "new_indicators": [], "health_changes": [], "exchanges": findings.get("exchanges_scraped", {})}
    # New trades since last newsletter
    delta["new_trades"] = max(0, findings.get("n_trades", 0) - state.get("last_n_trades", 0))
    delta["new_traders"] = max(0, findings.get("n_traders", 0) - state.get("last_n_traders", 0))
    # New patterns (compare rule strings)
    old_patterns = set(state.get("last_patterns", []))
    for p in report.get("patterns", []):
        # Normalize — strip minor decimal differences by rounding to 1 decimal
        p_key = p.split(" — ")[0] if " — " in p else p[:60]
        if not any(_pattern_similar(p_key, op) for op in old_patterns):
            delta["new_patterns"].append(p)
            delta["has_new"] = True
    # New indicator edges (compare indicator names — only report NEW ones)
    old_indicators = set(state.get("last_indicator_edges", []))
    for ind in report.get("indicators", []):
        if ind["name"] not in old_indicators:
            delta["new_indicators"].append(ind)
            delta["has_new"] = True
    # Health changes
    current_health = report.get("health", "")
    old_health = state.get("last_health_str", "")
    if current_health and current_health != old_health:
        delta["health_changes"].append(f"{old_health} → {current_health}" if old_health else current_health)
        delta["has_new"] = True
    if delta["new_trades"] > 50:
        delta["has_new"] = True
    # Store current state for next comparison
    delta["_state_update"] = {"last_n_trades": findings.get("n_trades", 0), "last_n_traders": findings.get("n_traders", 0), "last_patterns": [p.split(" — ")[0] if " — " in p else p[:60] for p in report.get("patterns", [])], "last_indicator_edges": [ind["name"] for ind in report.get("indicators", [])], "last_health_str": current_health}
    return delta


def _pattern_similar(a: str, b: str) -> bool:
    """Check if two pattern keys are essentially the same (ignoring minor threshold diffs)."""
    # Strip WR numbers and compare structure
    import re
    a_clean = re.sub(r"[\d.]+", "X", a)
    b_clean = re.sub(r"[\d.]+", "X", b)
    return a_clean == b_clean


# ═══════════════════════════════════════════════════════════════════
# SECTION 2 — BACKTEST SWEEP STATUS FROM SERVERS
# ═══════════════════════════════════════════════════════════════════

def get_server_sweep_status(server: str, label: str) -> dict:
    """SSH to server and get current sweep status."""
    logger.info(f"Checking {label} sweep status...")
    result = {"label": label, "server": server, "reachable": False, "active_sweeps": [], "latest_results": [], "screen_sessions": []}
    # Check if reachable
    test = _ssh_cmd(server, "echo OK")
    if test != "OK":
        return result
    result["reachable"] = True
    # Get screen sessions
    screens = _ssh_cmd(server, "screen -ls 2>/dev/null | grep -E '\\.(sweep|t[0-9]|crypto|fh_)' | head -5")
    if screens:
        result["screen_sessions"] = [s.strip() for s in screens.splitlines() if s.strip()]
    # Get running sweep processes (legacy v8_sweep + autonomous_search)
    procs = _ssh_cmd(server, "ps aux | grep -E 'backtest_v8_sweep|backtest_v5_sweep|autonomous_search\\.py' | grep -v grep | head -5")
    for line in procs.splitlines():
        if not line.strip():
            continue
        parts = line.split()
        cmd = " ".join(parts[10:]) if len(parts) > 10 else line
        sweep_info = {"cmd_short": cmd[-120:], "running": True}
        # Extract --mode and --symbols for autonomous_search
        for i, part in enumerate(parts):
            if part == "--mode" and i + 1 < len(parts):
                sweep_info["mode"] = parts[i + 1]
            if part == "--symbols" and i + 1 < len(parts):
                sweep_info["n_syms"] = parts[i + 1]
            if part == "--out-dir" and i + 1 < len(parts):
                sweep_info["out_dir"] = parts[i + 1].split("/")[-2] if "/" in parts[i + 1] else parts[i + 1]
        result["active_sweeps"].append(sweep_info)
    # Get latest results — first try autonomous_search dirs, fall back to legacy v8_sweep CSVs
    csv_info = _ssh_cmd(server, r"""
        latest_run=$(ls -dt /home/niels/binance-sandbox/data/autonomous/*/  2>/dev/null | head -1)
        if [ -n "$latest_run" ]; then
            run_name=$(basename "$latest_run")
            echo "FILE:autonomous/$run_name"
            # Aggregate all worker CSVs, filter header rows, sort by pool_sharpe (col 2) desc, top 5
            cat "$latest_run"/w*/autonomous_*.csv 2>/dev/null \
              | grep -v '^iter' \
              | awk -F',' 'NF>=5 && $2+0>0 && $5+0>=10' \
              | sort -t, -k2 -rn \
              | head -5
        else
            f=$(ls -t /home/niels/binance-sandbox/backtest_v8/sweeps/v8_sweep_*.csv 2>/dev/null | head -1)
            if [ -n "$f" ]; then
                echo "FILE:$(basename $f)"
                tail -n +2 "$f" | sort -t, -k3 -rn | head -5
            fi
        fi
    """)
    if csv_info:
        lines = csv_info.splitlines()
        filename = ""
        is_autonomous = False
        for line in lines:
            if line.startswith("FILE:"):
                filename = line[5:]
                is_autonomous = filename.startswith("autonomous/")
                continue
            parts = line.split(",")
            if is_autonomous:
                # autonomous CSV: iter,pool_sharpe,acc_gain_pct,max_dd_pct,trades,avg_gain,wr_pct,n_syms,is_best,...,config_json
                if len(parts) < 5:
                    continue
                try:
                    sharpe = float(parts[1]) if parts[1] else 0
                    acc_gain = float(parts[2]) if parts[2] else 0
                    trades_n = int(parts[4]) if parts[4] else 0
                    wr_pct = float(parts[6]) if len(parts) > 6 and parts[6] else 0
                    wr = f"{wr_pct:.0f}%" if wr_pct else "?"
                    result["latest_results"].append({"file": filename, "name": f"iter{parts[0]}", "sharpe": sharpe, "pnl_pct": acc_gain, "trades": trades_n, "wr": wr})
                except (ValueError, IndexError):
                    pass
            else:
                # legacy v8_sweep CSV: run_id,name,sharpe,pnl_pct,trades,wins,...
                if "run_id" in line:
                    continue
                if len(parts) >= 6:
                    try:
                        trades_n = int(parts[4]) if parts[4] else 0
                        wins_n = int(parts[5]) if len(parts) > 5 and parts[5] else 0
                        wr = f"{wins_n/max(trades_n,1)*100:.0f}%" if trades_n else "?"
                        result["latest_results"].append({"file": filename, "name": parts[1] if len(parts) > 1 else "", "sharpe": float(parts[2]) if parts[2] else 0, "pnl_pct": float(parts[3]) if parts[3] else 0, "trades": trades_n, "wr": wr})
                    except (ValueError, IndexError):
                        pass
    # Get latest log tail for active sweep progress
    log_tail = _ssh_cmd(server, """
        for f in /tmp/v8_*.log; do
            if [ -f "$f" ]; then
                echo "=== $(basename $f) ===";
                tail -5 "$f" 2>/dev/null | grep -E 'Sweep|Progress|sharpe|REPORT|\\[|TOP|BOTTOM' | tail -3;
            fi
        done
    """)
    if log_tail:
        result["log_snippets"] = [l.strip() for l in log_tail.splitlines() if l.strip()][:8]
    return result


def get_local_sweep_status() -> dict:
    """Check local MacBook for any running backtests or recent results."""
    result = {"active": False, "recent_results": []}
    # Check for running backtest processes
    try:
        ps = subprocess.run(["pgrep", "-lf", "backtest_v[5-9]"], capture_output=True, text=True, timeout=5)
        if ps.stdout.strip():
            result["active"] = True
            result["processes"] = [l.strip() for l in ps.stdout.splitlines()[:3]]
    except Exception:
        pass
    # Check recent local sweep results
    sweep_dir = DATA_DIR / "sweep_results"
    if sweep_dir.exists():
        csvs = sorted(sweep_dir.glob("*.csv"), key=lambda p: p.stat().st_mtime, reverse=True)
        for csv_path in csvs[:2]:
            age_hours = (time.time() - csv_path.stat().st_mtime) / 3600
            if age_hours < 72:
                result["recent_results"].append({"file": csv_path.name, "age_hours": round(age_hours, 1), "size": csv_path.stat().st_size})
    return result


# ═══════════════════════════════════════════════════════════════════
# SECTION 3 — APPLIED CONFIGS TRACKER
# ═══════════════════════════════════════════════════════════════════

def get_recently_applied() -> list:
    """Check 100.md and git log for recently applied config changes."""
    applied = []
    # Check 100.md for recent BC entries marked as APPLIED
    md_path = BASE_PATH / "100.md"
    if md_path.exists():
        try:
            text = md_path.read_text()
            for line in text.splitlines():
                if "APPLIED" in line and ("BC_" in line or "P15_" in line or "T_" in line):
                    # Only keep recent ones (last 7 days check by date in line)
                    applied.append(line.strip()[:120])
                    if len(applied) >= 10:
                        break
        except Exception:
            pass
    return applied[-5:]


# ═══════════════════════════════════════════════════════════════════
# SECTION 4 — BUILD HTML EMAIL
# ═══════════════════════════════════════════════════════════════════

def build_newsletter_html(trader_delta: dict, s1_status: dict, s2_status: dict, local_status: dict, applied: list) -> str:
    """Build the unified newsletter HTML."""
    now = datetime.now(timezone.utc)
    css = """
body { font-family: -apple-system, 'Segoe UI', Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 15px; background: #fafafa; color: #222; font-size: 13px; }
h1 { color: #1a1a2e; border-bottom: 3px solid #e94560; padding-bottom: 8px; font-size: 20px; margin-bottom: 5px; }
h2 { color: #1a1a2e; border-bottom: 1px solid #ddd; padding-bottom: 5px; margin-top: 20px; font-size: 15px; }
h3 { color: #333; font-size: 13px; margin: 10px 0 4px 0; }
table { border-collapse: collapse; width: 100%; font-size: 12px; border: 1px solid #ddd; margin-bottom: 10px; }
th { background: #1a1a2e; color: #fff; padding: 4px 7px; text-align: left; font-size: 11px; }
td { padding: 3px 7px; border-bottom: 1px solid #eee; font-size: 12px; }
tr:nth-child(even) { background: #f5f5f5; }
.g { color: #2e7d32; } .r { color: #c62828; } .o { color: #ef6c00; } .gr { color: #888; }
.b { font-weight: 700; }
.new { background: #e8f5e9; padding: 2px 6px; border-radius: 3px; font-size: 10px; font-weight: 600; color: #2e7d32; }
.unchanged { color: #999; font-style: italic; font-size: 11px; }
.box { padding: 8px 12px; background: #fff; border: 1px solid #e0e0e0; border-radius: 6px; margin: 6px 0; }
.sweep-active { border-left: 3px solid #2e7d32; }
.sweep-idle { border-left: 3px solid #999; }
code { background: #f0f0f0; padding: 1px 4px; border-radius: 2px; font-size: 11px; }
"""
    parts = [f"<html><head><style>{css}</style></head><body>"]
    parts.append(f"<h1>Unified Research Newsletter &mdash; {now.strftime('%Y-%m-%d %H:%M UTC')}</h1>")
    # ── Section 1: Copy Trader Delta ──
    parts.append("<h2>Copy Trader Research</h2>")
    if trader_delta.get("total_trades"):
        parts.append(f"<p>Total: <b>{trader_delta['total_trades']}</b> trades from <b>{trader_delta['total_traders']}</b> traders, WR={trader_delta['overall_wr']:.1f}%</p>")
        if trader_delta.get("new_trades", 0) > 0:
            parts.append(f"<p><span class='new'>NEW</span> +{trader_delta['new_trades']} trades, +{trader_delta['new_traders']} traders since last newsletter</p>")
        else:
            parts.append("<p class='unchanged'>No new trade data since last newsletter</p>")
        # V8 validation
        v8 = trader_delta.get("v8_results")
        if v8:
            total = v8.get("would_have_entered", 0) + v8.get("missed", 0)
            if total:
                hit = v8["would_have_entered"] / total * 100
                parts.append(f"<p>V8 reconstruction: {v8['would_have_entered']}/{total} entries caught ({hit:.0f}%)</p>")
        # New indicators
        if trader_delta.get("new_indicators"):
            parts.append("<h3><span class='new'>NEW</span> Indicator Edges</h3>")
            parts.append("<table><tr><th>Indicator</th><th>Winner</th><th>Loser</th><th>d</th><th>p</th></tr>")
            for ind in trader_delta["new_indicators"]:
                parts.append(f"<tr><td><b>{ind['name']}</b></td><td>{ind['winner']}</td><td>{ind['loser']}</td><td>{ind['d']}</td><td>{ind['p']}</td></tr>")
            parts.append("</table>")
        else:
            parts.append("<p class='unchanged'>No new indicator edges (same indicators as last report)</p>")
        # New patterns
        if trader_delta.get("new_patterns"):
            parts.append("<h3><span class='new'>NEW</span> Patterns</h3>")
            parts.append("<ul>")
            for p in trader_delta["new_patterns"]:
                parts.append(f"<li>{p}</li>")
            parts.append("</ul>")
        else:
            parts.append("<p class='unchanged'>No new high-WR patterns</p>")
        # Health changes
        if trader_delta.get("health_changes"):
            parts.append("<h3>Health Changes</h3>")
            for hc in trader_delta["health_changes"]:
                parts.append(f"<p>{hc}</p>")
        # Exchange status
        exch = trader_delta.get("exchanges", {})
        if exch:
            active = [k.upper() for k, v in exch.items() if v]
            failed = [k.upper() for k, v in exch.items() if not v]
            parts.append(f"<p class='gr'>Sources: {', '.join(active) if active else 'none'}")
            if failed:
                parts.append(f" | <span class='r'>Failed: {', '.join(failed)}</span>")
            parts.append("</p>")
    else:
        parts.append("<p class='unchanged'>No copy-trader data available</p>")
    # ── Section 2: Backtest Sweeps ──
    parts.append("<h2>Backtest Sweeps</h2>")
    for status in [s1_status, s2_status]:
        label = status["label"]
        if not status["reachable"]:
            parts.append(f"<div class='box sweep-idle'><h3>{label}</h3><p class='r'>Unreachable</p></div>")
            continue
        is_active = bool(status.get("active_sweeps"))
        cls = "sweep-active" if is_active else "sweep-idle"
        parts.append(f"<div class='box {cls}'><h3>{label}</h3>")
        # Screen sessions
        if status.get("screen_sessions"):
            parts.append(f"<p>Screens: {', '.join(s.split('(')[0].strip() for s in status['screen_sessions'])}</p>")
        # Active sweeps with progress
        if is_active:
            for sw in status.get("active_sweeps", []):
                mode = sw.get("mode", "")
                n_syms = sw.get("n_syms", "")
                out_dir = sw.get("out_dir", "")
                label_parts = []
                if mode:
                    label_parts.append(f"mode={mode}")
                if n_syms:
                    label_parts.append(f"{n_syms} syms")
                if out_dir:
                    label_parts.append(out_dir)
                desc = " &bull; ".join(label_parts) if label_parts else sw.get("cmd_short", "")[-80:]
                parts.append(f"<p class='g'>&#9654; autonomous_search running: <code>{desc}</code></p>")
        elif status.get("log_snippets"):
            parts.append("<p><b>Active progress:</b></p><pre style='font-size:11px;background:#f0f0f0;padding:6px;overflow-x:auto;'>")
            for snippet in status["log_snippets"]:
                parts.append(f"{snippet}\n")
            parts.append("</pre>")
        else:
            parts.append("<p class='unchanged'>No active sweeps</p>")
        # Latest results
        if status.get("latest_results"):
            src = status["latest_results"][0].get("file", "")
            src_label = "autonomous_search" if "autonomous" in src else "v8_sweep"
            parts.append(f"<p><b>Top configs ({src_label} — {src}):</b></p>")
            parts.append("<table><tr><th>#</th><th>Iter</th><th>Sharpe</th><th>AccGain%</th><th>Trades</th><th>WR</th></tr>")
            for i, r in enumerate(status["latest_results"][:5]):
                sharpe_cls = "g" if r["sharpe"] >= 2.0 else "o" if r["sharpe"] >= 1.0 else "r"
                pnl_pct = r.get("pnl_pct", 0)
                pnl_cls = "g" if pnl_pct > 0 else "r"
                name_short = r["name"][:20] if r["name"] else "?"
                parts.append(f"<tr><td>{i+1}</td><td><code>{name_short}</code></td><td class='{sharpe_cls} b'>{r['sharpe']:.4f}</td><td class='{pnl_cls}'>{pnl_pct:+.1f}%</td><td>{r['trades']}</td><td>{r.get('wr','?')}</td></tr>")
            parts.append("</table>")
        parts.append("</div>")
    # Local MacBook
    if local_status.get("active") or local_status.get("recent_results"):
        parts.append("<div class='box sweep-active'><h3>MacBook (local)</h3>")
        if local_status.get("active"):
            parts.append(f"<p class='g'>Active backtests running</p>")
            for proc in local_status.get("processes", []):
                parts.append(f"<p><code>{proc[-80:]}</code></p>")
        for r in local_status.get("recent_results", []):
            parts.append(f"<p>{r['file']} ({r['age_hours']:.0f}h ago, {r['size']/1024:.0f}KB)</p>")
        parts.append("</div>")
    # ── Section 3: Applied to Live ──
    if applied:
        parts.append("<h2>Recently Applied to Live</h2>")
        parts.append("<ul>")
        for a in applied:
            parts.append(f"<li>{a}</li>")
        parts.append("</ul>")
    # Footer
    parts.append(f"<hr><p style='font-size:10px;color:#aaa'>Unified Newsletter &mdash; {now.strftime('%Y-%m-%d %H:%M UTC')} &mdash; only showing changes since last newsletter</p>")
    parts.append("</body></html>")
    return "\n".join(parts)


# ═══════════════════════════════════════════════════════════════════
# ORCHESTRATION
# ═══════════════════════════════════════════════════════════════════

def run(dry_run: bool = False, force: bool = False):
    """Generate and optionally send the unified newsletter."""
    start = time.time()
    logger.info("=" * 60)
    logger.info("UNIFIED NEWSLETTER — generating delta digest")
    logger.info(f"Time: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    logger.info("=" * 60)
    state = load_state()
    logger.info(f"Last newsletter: {state.get('last_sent', 'never')}")
    # 1. Copy trader delta
    logger.info("Computing copy-trader delta...")
    trader_delta = compute_trader_delta(state)
    # 2. Server sweep status (parallel SSH)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(get_server_sweep_status, SERVER_1, "Server 1 (157.180) — Crypto/Tradier")
        f2 = pool.submit(get_server_sweep_status, SERVER_2, "Server 2 (204.168) — Tradier/Crypto")
        s1_status = f1.result()
        s2_status = f2.result()
    # 3. Local status
    local_status = get_local_sweep_status()
    # 4. Applied configs
    applied = get_recently_applied()
    # Check if there's anything new
    has_new_sweep = bool(s1_status.get("active_sweeps") or s2_status.get("active_sweeps") or s1_status.get("latest_results") or s2_status.get("latest_results"))
    has_anything = trader_delta.get("has_new") or has_new_sweep or applied
    if not has_anything and not force:
        logger.info("No new findings or sweep activity — skipping newsletter")
        print("No new findings. Use --force to send anyway.")
        return
    # Build HTML
    html = build_newsletter_html(trader_delta, s1_status, s2_status, local_status, applied)
    # Count what's new for subject line
    new_parts = []
    if trader_delta.get("new_indicators"):
        new_parts.append(f"{len(trader_delta['new_indicators'])} new indicators")
    if trader_delta.get("new_patterns"):
        new_parts.append(f"{len(trader_delta['new_patterns'])} new patterns")
    if trader_delta.get("new_trades", 0) > 0:
        new_parts.append(f"+{trader_delta['new_trades']} trades")
    n_active = len(s1_status.get("active_sweeps", [])) + len(s2_status.get("active_sweeps", []))
    if n_active:
        new_parts.append(f"{n_active} sweeps running")
    subject = f"Research Digest — {', '.join(new_parts) if new_parts else 'status update'}"
    if dry_run:
        # Save to file instead of sending
        out_path = REPORT_DIR / f"newsletter_preview_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}.html"
        with open(out_path, "w") as f:
            f.write(html)
        logger.info(f"DRY RUN — preview saved to {out_path}")
        print(f"Preview: {out_path}")
        print(f"Subject: {subject}")
    else:
        try:
            from morning_email import send_email
            send_email(html, subject=subject)
            logger.info(f"Newsletter sent: {subject}")
        except Exception as e:
            logger.error(f"Send failed: {e}")
            return
    # Update state
    now = datetime.now(timezone.utc).isoformat()
    state["last_sent"] = now
    if trader_delta.get("_state_update"):
        state.update(trader_delta["_state_update"])
    state["last_sweep_s1"] = {"timestamp": now, "active": bool(s1_status.get("active_sweeps")), "n_results": len(s1_status.get("latest_results", []))}
    state["last_sweep_s2"] = {"timestamp": now, "active": bool(s2_status.get("active_sweeps")), "n_results": len(s2_status.get("latest_results", []))}
    save_state(state)
    elapsed = time.time() - start
    logger.info(f"Done in {elapsed:.0f}s")


def main():
    parser = argparse.ArgumentParser(description="Unified Research Newsletter — delta-only digest")
    parser.add_argument("--dry-run", action="store_true", help="Generate preview, don't send")
    parser.add_argument("--force", action="store_true", help="Send even if nothing new")
    args = parser.parse_args()
    run(dry_run=args.dry_run, force=args.force)


if __name__ == "__main__":
    main()
