# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Sweep Cockpit — Real-time V8 backtest sweep dashboard on port 5051."""
import csv
import glob
import html
import io
import json
import math
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime, timezone
from flask import Flask, request, redirect

app = Flask(__name__)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# ---------------------------------------------------------------------------
# Server definitions
# ---------------------------------------------------------------------------
SERVERS = [
    {
        "name": "S1",
        "host": "s1-int",
        "user": "niels",
        "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps",
        "log_path": "/tmp/v8_t25.log",
        "is_local": False,
    },
    {
        "name": "S2",
        "host": "s2-int",
        "user": "niels",
        "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps",
        "log_path": "/tmp/v8_t25.log",
        "is_local": False,
    },
    {
        "name": "Local",
        "host": None,
        "user": None,
        "sweep_dir": os.path.join(BASE_DIR, "backtest_v8", "sweeps"),
        "log_path": "/tmp/v8_t25.log",
        "is_local": True,
    },
]

# ---------------------------------------------------------------------------
# Cache layer — avoid hammering SSH
# ---------------------------------------------------------------------------
_cache = {}
CACHE_TTL = 30  # seconds


def _cached(key, fn):
    now = time.time()
    if key in _cache and now - _cache[key]["ts"] < CACHE_TTL:
        return _cache[key]["data"]
    try:
        data = fn()
    except Exception as e:
        data = {"error": str(e)}
    _cache[key] = {"ts": now, "data": data}
    return data


def _ssh_run(host, user, cmd, timeout=10):
    # 2026-04-14: host is now an SSH alias (s1-int, s2-int) — alias embeds user+ProxyJump via ~/.ssh/config.
    # If caller still passes old IP-based host, fall back to legacy user@host form.
    target = host if (host and host.endswith("-int")) else f"{user}@{host}"
    full = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", target, cmd]
    r = subprocess.run(full, capture_output=True, text=True, timeout=timeout)
    return r.stdout.strip()


# ---------------------------------------------------------------------------
# Data fetching
# ---------------------------------------------------------------------------
def fetch_csv_rows(server):
    """Return list of dicts from all v8_sweep_tradier_t25*.csv files."""
    def _fetch():
        rows = []
        if server["is_local"]:
            files = sorted(glob.glob(os.path.join(server["sweep_dir"], "v8_sweep_tradier_t25*.csv")))
            raw_parts = []
            for f in files:
                with open(f, "r") as fh:
                    raw_parts.append(fh.read())
            raw = "\n".join(raw_parts)
        else:
            raw = _ssh_run(server["host"], server["user"], f'cat {server["sweep_dir"]}/v8_sweep_tradier_t25*.csv 2>/dev/null')
        if not raw:
            return rows
        # Parse CSV — may have multiple headers from concatenated files
        header = None
        for line in raw.splitlines():
            line = line.strip()
            if not line:
                continue
            if line.startswith("run_id,"):
                header = line.split(",")
                continue
            if header is None:
                continue
            vals = line.split(",")
            if len(vals) < len(header):
                vals += [""] * (len(header) - len(vals))
            row = {}
            for i, h in enumerate(header):
                row[h] = vals[i] if i < len(vals) else ""
            rows.append(row)
        return rows
    return _cached(f"csv_{server['name']}", _fetch)


def fetch_log_tail(server):
    def _fetch():
        if server["is_local"]:
            try:
                r = subprocess.run(["tail", "-5", server["log_path"]], capture_output=True, text=True, timeout=5)
                return r.stdout.strip()
            except Exception:
                return "(no log)"
        else:
            try:
                return _ssh_run(server["host"], server["user"], f"tail -5 {server['log_path']}")
            except Exception:
                return "(ssh failed)"
    return _cached(f"log_{server['name']}", _fetch)


def fetch_is_running(server):
    def _fetch():
        if server["is_local"]:
            try:
                r = subprocess.run(["bash", "-c", "ps aux | grep -E 'backtest_v8_sweep|autonomous_search\\.py' | grep -v grep | wc -l"], capture_output=True, text=True, timeout=5)
                return int(r.stdout.strip()) > 0
            except Exception:
                return False
        else:
            try:
                out = _ssh_run(server["host"], server["user"], "ps aux | grep -E 'backtest_v8_sweep|autonomous_search\\.py' | grep -v grep | wc -l")
                return int(out.strip()) > 0
            except Exception:
                return False
    return _cached(f"running_{server['name']}", _fetch)


def fetch_progress(server):
    """Try to read progress JSON for ETA info."""
    def _fetch():
        if server["is_local"]:
            files = sorted(glob.glob(os.path.join(server["sweep_dir"], "v8_sweep_tradier_t25*_progress.json")))
            if files:
                try:
                    import json
                    with open(files[-1], "r") as f:
                        return json.load(f)
                except Exception:
                    return {}
            return {}
        else:
            try:
                import json
                raw = _ssh_run(server["host"], server["user"], f'cat {server["sweep_dir"]}/v8_sweep_tradier_t25*_progress.json 2>/dev/null | tail -1')
                if raw:
                    return json.loads(raw)
            except Exception:
                pass
            return {}
    return _cached(f"progress_{server['name']}", _fetch)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def safe_float(v, default=0.0):
    try:
        return float(v)
    except (ValueError, TypeError):
        return default


def fmt_pnl(v):
    f = safe_float(v)
    color = "#4caf50" if f >= 0 else "#f44336"
    return f'<span style="color:{color}">${f:,.2f}</span>'


def fmt_sharpe(v):
    f = safe_float(v)
    if f >= 2.0:
        color = "#4caf50"
    elif f >= 1.0:
        color = "#8bc34a"
    elif f >= 0:
        color = "#ffeb3b"
    else:
        color = "#f44336"
    return f'<span style="color:{color}">{f:.3f}</span>'


def extract_current_config(log_text):
    """Extract currently running config name from log tail."""
    if not log_text:
        return "(unknown)"
    lines = log_text.strip().splitlines()
    for line in reversed(lines):
        # Look for config name patterns
        for marker in ["Running config:", "Config:", "START", "config="]:
            if marker in line:
                # Extract the part after the marker
                idx = line.index(marker) + len(marker)
                rest = line[idx:].strip().rstrip(".")
                if rest:
                    return rest[:80]
        # Look for a name-like pattern with underscores
        if "T_DC" in line or "cfg_" in line or "t25_" in line:
            return line.strip()[:80]
    return lines[-1].strip()[:80] if lines else "(unknown)"


def compute_histogram(values, bins=15):
    """Text-based histogram of values."""
    if not values:
        return "(no data)"
    mn, mx = min(values), max(values)
    if mn == mx:
        return f"All values = {mn:.3f}"
    bin_width = (mx - mn) / bins
    counts = [0] * bins
    for v in values:
        idx = min(int((v - mn) / bin_width), bins - 1)
        counts[idx] += 1
    max_count = max(counts) if counts else 1
    bar_max = 40
    lines = []
    for i, c in enumerate(counts):
        lo = mn + i * bin_width
        hi = lo + bin_width
        bar_len = int(c / max_count * bar_max) if max_count > 0 else 0
        bar = "█" * bar_len
        lines.append(f"  {lo:7.3f} - {hi:7.3f} | {bar} {c}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------
STYLE = """
body { background: #1a1a2e; color: #e0e0e0; font-family: 'JetBrains Mono', 'Fira Code', 'Consolas', monospace; margin: 0; padding: 20px; font-size: 13px; }
h1 { color: #00d4ff; margin-bottom: 5px; }
h2 { color: #7c4dff; margin-top: 30px; border-bottom: 1px solid #333; padding-bottom: 5px; }
h3 { color: #ff9800; margin-top: 20px; }
.subtitle { color: #888; font-size: 12px; margin-bottom: 20px; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(420px, 1fr)); gap: 15px; margin-bottom: 20px; }
.card { background: #16213e; border: 1px solid #333; border-radius: 8px; padding: 15px; }
.card-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; }
.card-title { font-size: 16px; font-weight: bold; color: #00d4ff; }
.status-running { color: #4caf50; font-weight: bold; }
.status-stopped { color: #f44336; font-weight: bold; }
.status-unknown { color: #888; }
.metric { display: inline-block; margin-right: 20px; margin-bottom: 5px; }
.metric-label { color: #888; font-size: 11px; }
.metric-value { font-size: 18px; font-weight: bold; }
table { width: 100%; border-collapse: collapse; margin-top: 10px; font-size: 12px; }
th { background: #0f3460; color: #00d4ff; padding: 6px 8px; text-align: left; border-bottom: 2px solid #444; }
td { padding: 5px 8px; border-bottom: 1px solid #2a2a4a; }
tr:hover { background: #1f3a6e; }
.log-box { background: #0d1117; border: 1px solid #333; border-radius: 4px; padding: 10px; font-size: 11px; white-space: pre-wrap; word-break: break-all; max-height: 120px; overflow-y: auto; color: #aaa; }
.hist-box { background: #0d1117; border: 1px solid #333; border-radius: 4px; padding: 10px; font-size: 11px; white-space: pre; color: #8bc34a; overflow-x: auto; }
a { color: #00d4ff; text-decoration: none; }
a:hover { text-decoration: underline; }
.tag { display: inline-block; background: #0f3460; color: #7c4dff; padding: 2px 8px; border-radius: 4px; font-size: 11px; margin: 1px; }
.refresh-bar { position: fixed; top: 0; left: 0; right: 0; height: 3px; background: #00d4ff; animation: shrink 30s linear; z-index: 999; }
@keyframes shrink { from { width: 100%; } to { width: 0%; } }
.navbar { background: #0f3460; padding: 10px 20px; margin: -20px -20px 20px -20px; display: flex; gap: 20px; align-items: center; border-bottom: 2px solid #00d4ff; }
.navbar a { color: #e0e0e0; text-decoration: none; font-size: 14px; padding: 5px 12px; border-radius: 4px; }
.navbar a:hover, .navbar a.active { background: #1a1a2e; color: #00d4ff; }
.progress-bar { background: #333; border-radius: 4px; height: 20px; overflow: hidden; margin: 5px 0; }
.progress-fill { background: linear-gradient(90deg, #00d4ff, #7c4dff); height: 100%; border-radius: 4px; transition: width 0.5s; }
.form-group { margin-bottom: 10px; }
.form-group label { display: inline-block; width: 320px; color: #aaa; }
.form-group input { background: #0d1117; border: 1px solid #444; color: #e0e0e0; padding: 4px 8px; border-radius: 4px; width: 120px; font-family: monospace; }
.form-group .ablation { color: #888; font-size: 11px; margin-left: 10px; }
.btn { background: #0f3460; color: #00d4ff; border: 1px solid #00d4ff; padding: 8px 20px; border-radius: 4px; cursor: pointer; font-family: monospace; font-size: 13px; }
.btn:hover { background: #1a1a4e; }
.btn-danger { border-color: #f44336; color: #f44336; }
.btn-danger:hover { background: #2a1111; }
"""


NAV_ITEMS = [
    ("Home", "/"),
    ("🚀 Sweeps", "/sweeps"),
    ("🤖 Autonomous", "/autonomous"),
    ("Live Stocks", "/live"),
    ("Live Crypto", "/live/crypto"),
    ("Symbols Stocks", "/symbols"),
    ("Symbols Crypto", "/symbols/crypto"),
    ("Params Stocks", "/params"),
    ("Params Crypto", "/params/crypto"),
    ("History", "/history"),
    ("Trades", "http://localhost:5050/feed"),
    ("Monitor", "/monitor"),
    ("🔬 Swarm", "/swarm"),
]


def _alert_summary():
    """2026-04-14: Scan data/sweep_alerts/ and return a HTML banner with counts.
    Aggregates from duplicate_guard, regression_watcher, coordinator_v8."""
    d = os.path.join(BASE_DIR, "data", "sweep_alerts")
    if not os.path.isdir(d): return ""
    counts = {"dupe": 0, "regression": 0, "coord_dupe": 0, "fix_required": 0}
    latest = None
    try:
        for fn in os.listdir(d):
            p = os.path.join(d, fn)
            if fn.startswith("duplicate_"): counts["dupe"] += 1
            elif fn.startswith("regression_"): counts["regression"] += 1
            elif fn.startswith("coord_dupe_"): counts["coord_dupe"] += 1
            elif fn.startswith("FIX_REQUIRED_"): counts["fix_required"] += 1
            try: mt = os.path.getmtime(p)
            except Exception: continue
            if latest is None or mt > latest[0]: latest = (mt, fn)
    except Exception:
        return ""
    total = sum(counts.values())
    if total == 0:
        return '<div style="background:#1a3a1a; color:#9fe89f; padding:8px; border-radius:4px; margin:8px 0;">✅ No active sweep alerts</div>'
    latest_ago = int(time.time() - latest[0]) if latest else 0
    return f'''<div style="background:#3a1a1a; color:#ff8888; padding:10px; border:2px solid #ff4444; border-radius:4px; margin:8px 0;">
        ⚠️ <strong>ACTIVE ALERTS:</strong>
        <a href="/alerts" style="color:#ffaaaa">{counts["fix_required"]} FIX_REQUIRED</a> ·
        {counts["dupe"]} dead-knob groups ·
        {counts["regression"]} regressions ·
        {counts["coord_dupe"]} cross-machine dupes
        &nbsp;·&nbsp; latest: {latest[1] if latest else "?"} ({latest_ago}s ago)
    </div>'''


def render_page(body, title="Sweep Cockpit", active_nav="Home"):
    nav_html = ""
    for label, href in NAV_ITEMS:
        cls = ' class="active"' if label == active_nav else ""
        nav_html += f'<a href="{href}"{cls}>{label}</a>'
    # 2026-04-14: alert banner from sweep_duplicate_guard + regression_watcher + coordinator
    alert_banner = _alert_summary()
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>{title}</title>
<style>{STYLE}</style>
</head><body>
<div class="refresh-bar"></div>
<div class="navbar">{nav_html} &nbsp;|&nbsp; <a href="/results">🏆 Results</a> &nbsp;|&nbsp; <a href="/alerts">🚨 Alerts</a> &nbsp;|&nbsp; <a href="/switches">🔌 Switches</a> &nbsp;|&nbsp; <a href="/live_vs_sandbox">🟢 Live vs 🧪 Sandbox</a> &nbsp;|&nbsp; <a href="/live_vs_frozen">🧊 Live vs Frozen</a></div>
<h1>V8 Sweep Cockpit</h1>
<div class="subtitle">Auto-refresh 30s | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
{alert_banner}
{body}
</body></html>"""


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.route("/")
def dashboard():
    all_rows = []
    server_sections = []

    for srv in SERVERS:
        rows = fetch_csv_rows(srv)
        is_running = fetch_is_running(srv)
        log_tail = fetch_log_tail(srv)
        progress = fetch_progress(srv)

        if isinstance(rows, dict) and "error" in rows:
            error_msg = rows["error"]
            rows = []
        else:
            error_msg = None

        # Tag rows with server name
        for r in rows:
            r["_server"] = srv["name"]
        all_rows.extend(rows)

        done = len(rows)
        total = progress.get("total", 0) if isinstance(progress, dict) else 0
        elapsed_avg = 0
        if rows:
            elapsed_vals = [safe_float(r.get("elapsed", 0)) for r in rows if safe_float(r.get("elapsed", 0)) > 0]
            elapsed_avg = sum(elapsed_vals) / len(elapsed_vals) if elapsed_vals else 0

        remaining = max(0, total - done) if total > 0 else 0
        eta_seconds = remaining * elapsed_avg if elapsed_avg > 0 else 0
        if eta_seconds > 0:
            eta_h = int(eta_seconds // 3600)
            eta_m = int((eta_seconds % 3600) // 60)
            eta_str = f"{eta_h}h {eta_m}m"
        elif total > 0 and done >= total:
            eta_str = "COMPLETE"
        else:
            eta_str = "N/A"

        current_config = extract_current_config(log_tail)
        status_class = "status-running" if is_running else "status-stopped"
        status_text = "RUNNING" if is_running else "STOPPED"

        # Detect tier from filenames or run_id
        tier = "?"
        if rows:
            rid = rows[-1].get("run_id", "")
            parts = rid.split("_")
            for p in parts:
                if p.startswith("t") and p[1:].isdigit():
                    tier = p.upper()
                    break

        # Last 5 results
        last5 = rows[-5:] if rows else []
        last5_html = ""
        if last5:
            last5_html = '<table><tr><th>Name</th><th>Sharpe</th><th>PnL</th><th>Trades</th><th>W/L</th></tr>'
            for r in reversed(last5):
                name = r.get("name", "?")
                short_name = name[:50] + "..." if len(name) > 50 else name
                link = f'<a href="/config/{name}">{short_name}</a>'
                last5_html += f'<tr><td>{link}</td><td>{fmt_sharpe(r.get("sharpe", 0))}</td><td>{fmt_pnl(r.get("pnl", 0))}</td><td>{r.get("trades", 0)}</td><td>{r.get("wins", 0)}/{r.get("losses", 0)}</td></tr>'
            last5_html += "</table>"
        else:
            last5_html = '<div style="color:#888">No results yet</div>'

        error_html = f'<div style="color:#f44336;margin-top:5px">Error: {error_msg}</div>' if error_msg else ""

        card = f"""
        <div class="card">
            <div class="card-header">
                <span class="card-title">{srv['name']}</span>
                <span class="{status_class}">{status_text}</span>
            </div>
            {error_html}
            <div>
                <span class="metric"><span class="metric-label">Tier</span><br><span class="metric-value">{tier}</span></span>
                <span class="metric"><span class="metric-label">Done</span><br><span class="metric-value">{done}{f'/{total}' if total > 0 else ''}</span></span>
                <span class="metric"><span class="metric-label">ETA</span><br><span class="metric-value">{eta_str}</span></span>
                <span class="metric"><span class="metric-label">Avg Time</span><br><span class="metric-value">{elapsed_avg:.0f}s</span></span>
            </div>
            <h3>Current Config</h3>
            <div class="log-box">{current_config}</div>
            <h3>Last 5 Results</h3>
            {last5_html}
        </div>
        """
        server_sections.append(card)

    # --- Aggregate view ---
    total_done = len(all_rows)
    sharpe_vals = [safe_float(r.get("sharpe", 0)) for r in all_rows]
    pnl_vals = [safe_float(r.get("pnl", 0)) for r in all_rows]

    # Differentiation check
    if sharpe_vals:
        s_min, s_max = min(sharpe_vals), max(sharpe_vals)
        s_mean = sum(sharpe_vals) / len(sharpe_vals)
        s_stdev = math.sqrt(sum((x - s_mean) ** 2 for x in sharpe_vals) / len(sharpe_vals)) if len(sharpe_vals) > 1 else 0
        diff_verdict = "DIFFERENTIATED" if s_stdev > 0.1 else "FLAT (stdev < 0.1)"
        diff_color = "#4caf50" if s_stdev > 0.1 else "#f44336"
    else:
        s_min = s_max = s_mean = s_stdev = 0
        diff_verdict = "NO DATA"
        diff_color = "#888"

    # Top 10 / Bottom 5
    sorted_rows = sorted(all_rows, key=lambda r: safe_float(r.get("sharpe", 0)), reverse=True)
    top10 = sorted_rows[:10]
    bottom5 = sorted_rows[-5:] if len(sorted_rows) >= 5 else sorted_rows

    def render_ranked_table(rows, label):
        if not rows:
            return f"<div style='color:#888'>No {label} data</div>"
        html = '<table><tr><th>#</th><th>Server</th><th>Name</th><th>Sharpe</th><th>PnL</th><th>Trades</th><th>W/L</th></tr>'
        for i, r in enumerate(rows, 1):
            name = r.get("name", "?")
            short_name = name[:60] + "..." if len(name) > 60 else name
            link = f'<a href="/config/{name}">{short_name}</a>'
            html += f'<tr><td>{i}</td><td>{r.get("_server", "?")}</td><td>{link}</td><td>{fmt_sharpe(r.get("sharpe", 0))}</td><td>{fmt_pnl(r.get("pnl", 0))}</td><td>{r.get("trades", 0)}</td><td>{r.get("wins", 0)}/{r.get("losses", 0)}</td></tr>'
        html += "</table>"
        return html

    histogram = compute_histogram(sharpe_vals) if sharpe_vals else "(no data)"

    # --- Live swarm summary (replaces stale v8_sweep_* aggregate) ---
    import pandas as _pd2, io as _io2
    swarm_rows_html = ""
    swarm_bests = []
    swarm_dirs = [
        ("Local", os.path.join(BASE_DIR, "data", "autonomous", "*", "w*", "autonomous_*.csv")),
        ("S1 cache", os.path.join(BASE_DIR, "data", "swarm_cache", "s1", "data", "autonomous", "*", "w*", "autonomous_*.csv")),
        ("S2 cache", os.path.join(BASE_DIR, "data", "swarm_cache", "s2", "data", "autonomous", "*", "w*", "autonomous_*.csv")),
    ]
    validated_dirs = [
        ("Local S2-validated", os.path.join(BASE_DIR, "data", "stage2_validated", "**", "*.csv")),
        ("Local funnel", os.path.join(BASE_DIR, "data", "funnel_validated", "**", "*.csv")),
        ("S2 cache S2-validated", os.path.join(BASE_DIR, "data", "swarm_cache", "s2", "data", "stage2_validated", "**", "*.csv")),
    ]
    for label, pat in swarm_dirs:
        files = sorted(glob.glob(pat))
        if not files:
            continue
        pieces = []
        for f in files:
            try:
                df = _pd2.read_csv(f, usecols=lambda c: c in ["pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades"])
                pieces.append(df)
            except Exception:
                pass
        if not pieces:
            continue
        combined = _pd2.concat(pieces, ignore_index=True)
        combined["pool_sharpe"] = _pd2.to_numeric(combined["pool_sharpe"], errors="coerce")
        combined = combined.dropna(subset=["pool_sharpe"])
        combined = combined[combined["pool_sharpe"] > 0]
        if combined.empty:
            continue
        best = combined.nlargest(3, "pool_sharpe")
        age_s = int(time.time() - min(os.path.getmtime(f) for f in files))
        age_str = f"{age_s//60}m ago"
        ac = "#4caf50" if age_s < 600 else ("#ffaa00" if age_s < 3600 else "#f44336")
        n = len(combined)
        top_sharpe = combined["pool_sharpe"].max()
        swarm_bests.append(top_sharpe)
        rows_h = "".join(
            f'<tr><td style="color:#4caf50">{r["pool_sharpe"]:.4f}</td>'
            f'<td>{r.get("acc_gain_pct",0):.0f}%</td>'
            f'<td>{r.get("max_dd_pct",0):.1f}%</td>'
            f'<td>{int(r.get("trades",0))}</td></tr>'
            for _, r in best.iterrows()
        )
        swarm_rows_html += (
            f'<div style="margin-bottom:8px">'
            f'<b>{label}</b> — {n} configs, best=<span style="color:#4caf50">{top_sharpe:.4f}</span> '
            f'<span style="color:{ac}">({age_str})</span>'
            f'<table style="font-size:11px;margin-top:3px"><tr><th>Sharpe</th><th>Gain%</th><th>DD%</th><th>Trades</th></tr>{rows_h}</table>'
            f'</div>'
        )
    for label, pat in validated_dirs:
        files = sorted(glob.glob(pat, recursive=True))
        if not files:
            continue
        pieces = []
        newest_mtime = 0
        for f in files:
            try:
                df = _pd2.read_csv(f, usecols=lambda c: c in [
                    "pool_sharpe", "sharpe", "acc_gain_pct", "accumulated_gain_pct",
                    "max_dd_pct", "trades", "wins", "losses", "wr", "avg_pnl_pct",
                    "symbols_used", "syms_with_sharpe", "elapsed", "elapsed_s",
                    "gain_vs_bh", "reliable", "n_years", "start_date"
                ])
                pieces.append(df)
                try:
                    newest_mtime = max(newest_mtime, os.path.getmtime(f))
                except Exception:
                    pass
            except Exception:
                pass
        if not pieces:
            continue
        combined = _pd2.concat(pieces, ignore_index=True)
        # Normalize column aliases — autonomous_search uses acc_gain_pct, v8_quick uses accumulated_gain_pct
        if "accumulated_gain_pct" in combined.columns and "acc_gain_pct" not in combined.columns:
            combined["acc_gain_pct"] = combined["accumulated_gain_pct"]
        combined["pool_sharpe"] = _pd2.to_numeric(combined["pool_sharpe"], errors="coerce")
        combined = combined.dropna(subset=["pool_sharpe"])
        combined = combined[combined["pool_sharpe"] > 0]
        if "symbols_used" in combined.columns:
            combined["symbols_used"] = _pd2.to_numeric(combined["symbols_used"], errors="coerce")
            combined = combined[combined["symbols_used"] >= 12]
        if combined.empty:
            continue
        best = combined.nlargest(3, "pool_sharpe")
        top_sharpe = combined["pool_sharpe"].max()
        swarm_bests.append(top_sharpe)
        # Age in minutes since newest CSV mtime
        age_min = int((time.time() - newest_mtime) / 60) if newest_mtime else 0
        # n_years inference: prefer column, else default 4 (full 4yr Tier-3)
        def _infer_years(r):
            try:
                return float(r["n_years"]) if "n_years" in r and not _pd2.isna(r["n_years"]) else 4.0
            except Exception:
                return 4.0
        rows_h = ""
        for _, r in best.iterrows():
            tr = int(r.get("trades", 0) or 0)
            ng = float(r.get("acc_gain_pct", 0) or 0)
            ns = int(r.get("symbols_used", 0) or 0)
            ny = _infer_years(r)
            avg_gain_trade = (ng / tr) if tr > 0 else 0.0
            gain_per_yr = (ng / ny) if ny > 0 else 0.0
            gain_sym_yr = (ng / max(1, ns) / ny) if ny > 0 else 0.0
            sym_sharpe = float(r.get("sharpe", 0) or 0) if "sharpe" in r else 0.0
            wr_v = float(r.get("wr", 0) or 0) if "wr" in r else 0.0
            rel = r.get("reliable", "")
            rel_tag = '<span title="reliable=1: passed min-trades / consistency check" style="color:#4caf50;">✓</span>' if str(rel) in ("1", "True", "1.0") else ('<span title="reliable=0: passed but failed consistency check — treat with skepticism" style="color:#ff9800;">!</span>' if rel != "" else '')
            rows_h += (
                f'<tr>'
                f'<td title="pool_sharpe: mean(all_trade_returns)/std across all trades pooled. CANONICAL Sharpe per CLAUDE.md." style="color:#4caf50">{r["pool_sharpe"]:.4f}</td>'
                f'<td title="sym_sharpe: mean of per-symbol Sharpes (capped ±20). Diagnostic only — lies when trade counts vary." style="color:#bb86fc">{sym_sharpe:.3f}</td>'
                f'<td title="acc_gain_pct: sum of all per-trade %-returns across all symbols.">{ng:.0f}%</td>'
                f'<td title="avg_gain_trade = acc_gain_pct / trades. Per-trade % return.">{avg_gain_trade:.3f}%</td>'
                f'<td title="gain_per_yr = acc_gain_pct / n_years. Time-window neutral.">{gain_per_yr:.0f}%</td>'
                f'<td title="gain_sym_yr = acc_gain_pct / n_syms / n_years. Cross-machine comparable unit.">{gain_sym_yr:.2f}%</td>'
                f'<td title="max_dd_pct: peak-to-trough equity DD as % of starting capital.">{float(r.get("max_dd_pct",0) or 0):.1f}%</td>'
                f'<td title="wr: win rate %. Together with avg_gain_trade gives expectancy = wr*avg_win - (1-wr)*avg_loss.">{wr_v:.1f}%</td>'
                f'<td title="trades: total round-trip count across all syms. <30/sym = small-sample lies.">{tr}</td>'
                f'<td title="symbols_used: distinct syms that produced at least 1 trade. <48 = below CLAUDE.md min-sample floor.">{ns}</td>'
                f'<td title="n_years: time window of the test. <1yr = below floor.">{ny:.1f}y</td>'
                f'<td>{rel_tag}</td>'
                f'</tr>'
            )
        timespan_note = "n_years: column present" if "n_years" in combined.columns else "n_years: assumed 4 (column missing — write n_years to CSV in your sweep runner)"
        swarm_rows_html += (
            f'<div style="margin-bottom:12px">'
            f'<b>✅ {label}</b> — best pool_sharpe=<span style="color:#4caf50">{top_sharpe:.4f}</span> '
            f'<span style="color:#888;font-size:10px;" title="Minutes since the newest CSV in this group was last modified.">({age_min}m ago, {timespan_note})</span>'
            f'<table style="font-size:11px;margin-top:3px;border-collapse:collapse;">'
            f'<tr style="background:#1a2330;color:#fff;"><th title="pool_sharpe — canonical Sharpe">pool</th><th title="sym_sharpe — diagnostic, per-sym avg">sym</th><th title="acc_gain_pct">Gain%</th><th title="acc_gain_pct/trades">/trade%</th><th title="acc_gain_pct/n_years">/yr%</th><th title="acc_gain_pct/n_syms/n_years">/sym/yr%</th><th title="max drawdown">DD%</th><th title="win rate">WR</th><th title="trade count">Trades</th><th title="symbols">Syms</th><th title="years">Span</th><th title="reliable flag">Rel</th></tr>'
            f'{rows_h}</table>'
            f'</div>'
        )

    overall_best = max(swarm_bests) if swarm_bests else 0
    swarm_block = f"""
    <div style="background:#0d1f12; border:2px solid #4caf50; border-radius:8px; padding:16px; margin-bottom:20px">
        <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:10px">
            <h2 style="margin:0; color:#4caf50" title="Best pool_sharpe across all swarm result groups (Local + S1/S2 cache). Apples-to-apples only when groups use same sym scope + time window — see /sweeps for canonical metric definitions.">🔬 Live Swarm Results — Best pool_sharpe: {overall_best:.4f}</h2>
            <a href="/swarm" style="background:#4caf50; color:#000; padding:6px 14px; border-radius:4px; text-decoration:none; font-weight:bold">Full Swarm View →</a>
        </div>
        <p style="color:#aaa; margin:0 0 10px 0; font-size:11px" title="Stage-1 = small-sym screening (NOT decision-material). ✅ = validated (≥12 sym). The sweep_cache directory is populated by ./sync_swarm_cache.sh — run periodically to pull S1/S2 latest.">
          Stage-1 (small sym): screening only. ✅ = validated (≥12 sym). Run <code>./sync_swarm_cache.sh</code> to refresh S1/S2 cache.
          <br><b style="color:#ff9800;">⚠ Apples-vs-pears warning:</b>
          this panel shows the best pool_sharpe per group, but groups can have different sym scopes / time windows / NOLOSS settings. A 9.x value on 4 syms × 4 months ≠ a 1.x value on 48 syms × 4yr. Always check Syms + Span columns AND prefer pool_sharpe over sym_sharpe per CLAUDE.md.
        </p>
        {swarm_rows_html or '<div style="color:#888">No swarm data found — check data/autonomous/ and data/swarm_cache/</div>'}
    </div>
    """

    body = f"""
    {swarm_block}
    <div class="grid">
        {''.join(server_sections)}
    </div>

    <details style="margin-top:20px">
        <summary style="color:#888; cursor:pointer">Legacy v8_test_queue results ({total_done} configs — stale, no new data)</summary>
        <div style="margin-top:10px">
        <div style="margin-bottom:15px">
            <span class="metric"><span class="metric-label">Sharpe Range</span><br><span class="metric-value">{s_min:.3f} — {s_max:.3f}</span></span>
            <span class="metric"><span class="metric-label">Sharpe Mean</span><br><span class="metric-value">{s_mean:.3f}</span></span>
            <span class="metric"><span class="metric-label">Sharpe StDev</span><br><span class="metric-value">{s_stdev:.3f}</span></span>
        </div>
        <h3>TOP 10 by Sharpe</h3>
        {render_ranked_table(top10, "top")}
        <h3>Sharpe Distribution</h3>
        <div class="hist-box">{histogram}</div>
        </div>
    </details>
    """

    return render_page(body, active_nav="Home")


@app.route("/config/<path:name>")
def config_detail(name):
    """Show full settings for a specific config."""
    all_rows = []
    for srv in SERVERS:
        rows = fetch_csv_rows(srv)
        if isinstance(rows, dict):
            continue
        for r in rows:
            r["_server"] = srv["name"]
        all_rows.extend(rows)

    # Find matching row(s)
    matches = [r for r in all_rows if r.get("name") == name]
    if not matches:
        return render_page(f'<h2>Config Not Found</h2><p>No config named: <code>{name}</code></p><p><a href="/">Back to dashboard</a></p>')

    row = matches[0]

    # Separate cfg_ fields from core fields
    core_fields = ["run_id", "name", "sharpe", "pnl", "trades", "wins", "losses", "elapsed", "status", "_server"]
    cfg_fields = {k: v for k, v in row.items() if k.startswith("cfg_")}
    other_fields = {k: v for k, v in row.items() if k not in core_fields and not k.startswith("cfg_")}

    # Core metrics
    core_html = f"""
    <div style="margin-bottom:20px">
        <span class="metric"><span class="metric-label">Sharpe</span><br><span class="metric-value">{fmt_sharpe(row.get('sharpe', 0))}</span></span>
        <span class="metric"><span class="metric-label">PnL</span><br><span class="metric-value">{fmt_pnl(row.get('pnl', 0))}</span></span>
        <span class="metric"><span class="metric-label">Trades</span><br><span class="metric-value">{row.get('trades', 0)}</span></span>
        <span class="metric"><span class="metric-label">Win/Loss</span><br><span class="metric-value">{row.get('wins', 0)}/{row.get('losses', 0)}</span></span>
        <span class="metric"><span class="metric-label">Elapsed</span><br><span class="metric-value">{safe_float(row.get('elapsed', 0)):.0f}s</span></span>
        <span class="metric"><span class="metric-label">Server</span><br><span class="metric-value">{row.get('_server', '?')}</span></span>
        <span class="metric"><span class="metric-label">Status</span><br><span class="metric-value">{row.get('status', '?')}</span></span>
    </div>
    """

    # Config settings table
    cfg_html = ""
    if cfg_fields:
        cfg_html = '<table><tr><th>Parameter</th><th>Value</th></tr>'
        for k in sorted(cfg_fields.keys()):
            display_key = k.replace("cfg_", "")
            cfg_html += f"<tr><td>{display_key}</td><td><span class='tag'>{cfg_fields[k]}</span></td></tr>"
        cfg_html += "</table>"
    else:
        cfg_html = '<div style="color:#888">No config parameters found</div>'

    # Other fields
    other_html = ""
    if other_fields:
        other_html = "<h3>Other Fields</h3><table><tr><th>Field</th><th>Value</th></tr>"
        for k in sorted(other_fields.keys()):
            other_html += f"<tr><td>{k}</td><td>{other_fields[k]}</td></tr>"
        other_html += "</table>"

    # Rank among all configs
    all_sharpes = sorted([safe_float(r.get("sharpe", 0)) for r in all_rows], reverse=True)
    this_sharpe = safe_float(row.get("sharpe", 0))
    rank = 1
    for s in all_sharpes:
        if s > this_sharpe:
            rank += 1
        else:
            break

    body = f"""
    <p><a href="/">← Back to dashboard</a></p>
    <h2>Config: {name}</h2>
    <div style="margin-bottom:10px"><span class="metric"><span class="metric-label">Rank</span><br><span class="metric-value">#{rank} of {len(all_rows)}</span></span></div>
    {core_html}
    <h3>Config Parameters ({len(cfg_fields)})</h3>
    {cfg_html}
    {other_html}
    """

    return render_page(body, title=f"Config: {name}")


@app.route("/api/data")
def api_data():
    """JSON endpoint for programmatic access."""
    all_rows = []
    server_status = []
    for srv in SERVERS:
        rows = fetch_csv_rows(srv)
        is_running = fetch_is_running(srv)
        if isinstance(rows, dict):
            rows = []
        for r in rows:
            r["_server"] = srv["name"]
        all_rows.extend(rows)
        server_status.append({"name": srv["name"], "running": is_running, "configs_done": len(rows)})
    sharpes = [safe_float(r.get("sharpe", 0)) for r in all_rows]
    result = {
        "total_configs": len(all_rows),
        "servers": server_status,
        "sharpe_min": min(sharpes) if sharpes else 0,
        "sharpe_max": max(sharpes) if sharpes else 0,
        "sharpe_mean": sum(sharpes) / len(sharpes) if sharpes else 0,
        "top5": [{"name": r.get("name"), "sharpe": safe_float(r.get("sharpe", 0)), "pnl": safe_float(r.get("pnl", 0))} for r in sorted(all_rows, key=lambda r: safe_float(r.get("sharpe", 0)), reverse=True)[:5]],
    }
    return app.response_class(response=json.dumps(result, indent=2), status=200, mimetype="application/json")


# ---------------------------------------------------------------------------
# Live Run Monitor helpers
# ---------------------------------------------------------------------------
LIVE_SERVERS = [
    {"name": "S1", "host": "s1-int", "user": "niels"},
    {"name": "S2", "host": "s2-int", "user": "niels"},
]

LIVE_LOGS = ["/tmp/v8_full_stocks.log", "/tmp/v8_full_crypto.log"]


def fetch_live_status(srv, log_path):
    """Fetch tail of a live run log from a server, parse progress."""
    def _fetch():
        try:
            raw = _ssh_run(srv["host"], srv["user"], f"tail -30 {log_path} 2>/dev/null", timeout=10)
        except Exception as e:
            return {"error": str(e), "raw": ""}
        if not raw:
            return {"error": "empty/missing", "raw": ""}
        result = {"raw": raw, "error": None}
        lines = raw.strip().splitlines()
        # Parse progress: look for bar counts, elapsed, Sharpe, trades
        for line in reversed(lines):
            if "bar" in line.lower() and "/" in line and "bars" not in result:
                m = re.search(r'(\d+)\s*/\s*(\d+)\s*bar', line, re.IGNORECASE)
                if m:
                    result["bars_done"] = int(m.group(1))
                    result["bars_total"] = int(m.group(2))
            if "elapsed" in line.lower() and "elapsed" not in result:
                m = re.search(r'elapsed[:\s]+(\d+[\d:.hms]+)', line, re.IGNORECASE)
                if m:
                    result["elapsed"] = m.group(1)
            if "sharpe" in line.lower() and "sharpe" not in result:
                m = re.search(r'sharpe[:\s=]+([+-]?\d+\.?\d*)', line, re.IGNORECASE)
                if m:
                    result["sharpe"] = m.group(1)
            if "trade" in line.lower() and "trades" not in result:
                m = re.search(r'(\d+)\s*trade', line, re.IGNORECASE)
                if m:
                    result["trades"] = m.group(1)
            if "V8_RESULT" in line:
                result["completed"] = True
                result["result_line"] = line.strip()
        # Check if process is running
        try:
            ps_out = _ssh_run(srv["host"], srv["user"], f"ps aux | grep -E 'backtest_v8_(engine|full)' | grep -v grep | wc -l", timeout=10)
            result["running"] = int(ps_out.strip()) > 0
        except Exception:
            result["running"] = False
        return result
    return _cached(f"live_{srv['name']}_{log_path}", _fetch)


def _build_live_cards(log_paths, label_prefix=""):
    """Build live monitor cards for given log paths."""
    cards = []
    for srv in LIVE_SERVERS:
        for log_path in log_paths:
            log_label = label_prefix or ("Stocks" if "stocks" in log_path else "Crypto")
            status = fetch_live_status(srv, log_path)
            if isinstance(status, dict) and status.get("error"):
                error_msg = status["error"]
                raw = status.get("raw", "")
            else:
                error_msg = None
                raw = status.get("raw", "")
            is_running = status.get("running", False)
            status_class = "status-running" if is_running else "status-stopped"
            status_text = "RUNNING" if is_running else "STOPPED"
            bars_done = status.get("bars_done", 0)
            bars_total = status.get("bars_total", 1)
            pct = min(100, int(bars_done / max(bars_total, 1) * 100))
            progress_html = f'<div class="progress-bar"><div class="progress-fill" style="width:{pct}%"></div></div><div style="color:#aaa;font-size:11px">{bars_done:,} / {bars_total:,} bars ({pct}%)</div>' if bars_done > 0 else '<div style="color:#888">No bar progress detected</div>'
            elapsed = status.get("elapsed", "N/A")
            sharpe = status.get("sharpe", "N/A")
            trades = status.get("trades", "N/A")
            result_html = ""
            if status.get("completed"):
                result_html = f'<div style="background:#1a3a1a;border:1px solid #4caf50;border-radius:4px;padding:8px;margin-top:8px;font-size:11px"><b>COMPLETED:</b> {status.get("result_line", "")}</div>'
            error_html = f'<div style="color:#f44336;font-size:11px">{error_msg}</div>' if error_msg else ""
            tail_lines = raw.strip().splitlines()[-5:] if raw else []
            tail_text = "\n".join(tail_lines) if tail_lines else "(no output)"
            card = f"""
            <div class="card">
                <div class="card-header">
                    <span class="card-title">{srv['name']} — {log_label}</span>
                    <span class="{status_class}">{status_text}</span>
                </div>
                {error_html}
                <div>
                    <span class="metric"><span class="metric-label">Elapsed</span><br><span class="metric-value">{elapsed}</span></span>
                    <span class="metric"><span class="metric-label">Trades</span><br><span class="metric-value">{trades}</span></span>
                    <span class="metric"><span class="metric-label">Sharpe</span><br><span class="metric-value">{sharpe}</span></span>
                </div>
                <h3>Progress</h3>
                {progress_html}
                {result_html}
                <h3>Log Tail</h3>
                <div class="log-box">{tail_text}</div>
            </div>
            """
            cards.append(card)
    return cards


@app.route("/live")
def live_monitor():
    """Live run monitor — stock runs on S1/S2."""
    cards = _build_live_cards(["/tmp/v8_full_stocks.log"], "Stocks")
    body = f'<h2>Live Stocks Monitor</h2><div class="grid">{"".join(cards)}</div>'
    return render_page(body, title="Live Stocks", active_nav="Live Stocks")


@app.route("/live/crypto")
def live_crypto_monitor():
    """Live run monitor — crypto runs on S1/S2."""
    cards = _build_live_cards(["/tmp/v8_full_crypto.log"], "Crypto")
    body = f'<h2>Live Crypto Monitor</h2><div class="grid">{"".join(cards)}</div>'
    return render_page(body, title="Live Crypto", active_nav="Live Crypto")


# ---------------------------------------------------------------------------
# Per-Symbol Results
# ---------------------------------------------------------------------------
def fetch_latest_jsonl(srv_host, srv_user, remote_pattern=None, local_pattern=None, cache_suffix=""):
    """Fetch latest JSONL trade log from a server (or local)."""
    if remote_pattern is None:
        remote_pattern = "/home/niels/binance-sandbox/backtest_v8/logs/v8_tradier_trb_*.jsonl"
    if local_pattern is None:
        local_pattern = os.path.join(BASE_DIR, "backtest_v8", "logs", "v8_tradier_trb_*.jsonl")
    def _fetch():
        raw = ""
        if srv_host:
            try:
                latest = _ssh_run(srv_host, srv_user, f"ls -t {remote_pattern} 2>/dev/null | head -1", timeout=10)
                if latest:
                    raw = _ssh_run(srv_host, srv_user, f"cat {latest}", timeout=30)
            except Exception:
                pass
        else:
            files = sorted(glob.glob(local_pattern), key=os.path.getmtime, reverse=True)
            if files:
                try:
                    with open(files[0], "r") as f:
                        raw = f.read()
                except Exception:
                    pass
        if not raw:
            return []
        trades = []
        for line in raw.strip().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except Exception:
                continue
        return trades
    return _cached(f"jsonl_{srv_host or 'local'}{cache_suffix}", _fetch)


def _render_symbols_page(title, active_nav, jsonl_remote_pattern=None, jsonl_local_pattern=None, cache_suffix=""):
    """Shared symbol breakdown renderer."""
    all_trades = []
    source = "none"
    for srv in LIVE_SERVERS:
        trades = fetch_latest_jsonl(srv["host"], srv["user"], remote_pattern=jsonl_remote_pattern, local_pattern=jsonl_local_pattern, cache_suffix=cache_suffix)
        if trades:
            all_trades = trades
            source = srv["name"]
            break
    if not all_trades:
        trades = fetch_latest_jsonl(None, None, remote_pattern=jsonl_remote_pattern, local_pattern=jsonl_local_pattern, cache_suffix=cache_suffix)
        if trades:
            all_trades = trades
            source = "Local"
    if not all_trades:
        body = f'<h2>{title}</h2><div style="color:#888">No JSONL trade data found on any server or locally.</div>'
        return render_page(body, title=title, active_nav=active_nav)
    # Separate OPEN/CLOSE trades — only count CLOSE for PnL
    symbols = defaultdict(lambda: {"opens": 0, "closes": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    first_ts = float("inf")
    last_ts = 0
    for t in all_trades:
        sym = t.get("symbol", t.get("pair", "UNKNOWN"))
        action = str(t.get("action", "")).upper()
        ts = safe_float(t.get("timestamp", 0))
        if ts > 0:
            first_ts = min(first_ts, ts)
            last_ts = max(last_ts, ts)
        if action in ("CLOSE", "REDUCE", "FULL_CLOSE", "PROFIT_TAKE"):
            pnl = safe_float(t.get("pnl", t.get("pnl_dollars", t.get("realized_pnl", 0))))
            symbols[sym]["closes"] += 1
            symbols[sym]["pnl"] += pnl
            if pnl > 0:
                symbols[sym]["wins"] += 1
            else:
                symbols[sym]["losses"] += 1
        else:
            symbols[sym]["opens"] += 1
    sorted_symbols = sorted(symbols.items(), key=lambda x: x[1]["pnl"], reverse=True)
    rows_html = ""
    for sym, data in sorted_symbols:
        closes = data["closes"]
        wr = (data["wins"] / closes * 100) if closes > 0 else 0
        avg_pnl = data["pnl"] / closes if closes > 0 else 0
        color = "#4caf50" if data["pnl"] >= 0 else "#f44336"
        rows_html += f'<tr style="color:{color}"><td>{sym}</td><td>{data["opens"]}</td><td>{closes}</td><td>{data["wins"]}</td><td>{data["losses"]}</td><td>{wr:.1f}%</td><td>${data["pnl"]:,.2f}</td><td>${avg_pnl:,.2f}</td></tr>'
    total_pnl = sum(d["pnl"] for d in symbols.values())
    total_closes = sum(d["closes"] for d in symbols.values())
    total_opens = sum(d["opens"] for d in symbols.values())
    # Date range
    from datetime import datetime as _dt
    first_date = _dt.utcfromtimestamp(first_ts).strftime("%Y-%m-%d") if first_ts < float("inf") else "?"
    last_date = _dt.utcfromtimestamp(last_ts).strftime("%Y-%m-%d") if last_ts > 0 else "?"
    body = f"""
    <h2>{title}</h2>
    <div class="subtitle">
        Source: {source} | Single baseline run (current config) |
        Period: {first_date} → {last_date} |
        {len(symbols)} symbols | {total_opens} opens, {total_closes} closed trades |
        Total PnL: {fmt_pnl(total_pnl)}
    </div>
    <table>
        <tr><th>Symbol</th><th>Opens</th><th>Closes</th><th>Wins</th><th>Losses</th><th>WR%</th><th>Total PnL</th><th>Avg PnL/trade</th></tr>
        {rows_html}
    </table>
    """
    return render_page(body, title=title, active_nav=active_nav)


@app.route("/symbols")
def symbols_view():
    """Per-symbol breakdown from stock JSONL trade logs."""
    return _render_symbols_page("Per-Symbol Results (Stocks)", "Symbols Stocks", cache_suffix="_stocks")


@app.route("/symbols/crypto")
def symbols_crypto_view():
    """Per-symbol breakdown from crypto JSONL trade logs."""
    return _render_symbols_page(
        "Per-Symbol Results (Crypto)", "Symbols Crypto",
        jsonl_remote_pattern="/home/niels/binance-sandbox/backtest_v8/logs/v8_crypto_ang_*.jsonl",
        jsonl_local_pattern=os.path.join(BASE_DIR, "backtest_v8", "logs", "v8_crypto_ang_*.jsonl"),
        cache_suffix="_crypto",
    )


# ---------------------------------------------------------------------------
# Config File Parser — extracts ALL dataclass params with sections
# ---------------------------------------------------------------------------
def parse_config_params(config_path, dataclass_name="TradierConfig"):
    """Parse a config file and return list of {name, type, value, comment, section}."""
    def _fetch():
        params = []
        current_section = "General"
        try:
            with open(config_path, "r") as f:
                lines = f.readlines()
        except Exception:
            return params
        in_class = False
        for line in lines:
            stripped = line.strip()
            # Detect class start
            if f"class {dataclass_name}" in stripped:
                in_class = True
                continue
            if not in_class:
                continue
            # Detect end of class (unindented non-empty line that is not a comment)
            if stripped and not line.startswith(" ") and not line.startswith("\t") and not stripped.startswith("#") and not stripped.startswith("@"):
                break
            # Detect section headers: === ... === or --- N. Name ---
            section_m = re.search(r'#\s*===\s*(.+?)\s*===', stripped)
            if section_m:
                sec_text = section_m.group(1).strip()
                # Skip pure separator bars (e.g. # ========...========)
                if sec_text and not all(c == '=' for c in sec_text):
                    current_section = sec_text
                    if len(current_section) > 80:
                        current_section = current_section[:77] + "..."
                continue
            section_m2 = re.search(r'#\s*---\s*(\d+\.\s*.+?)\s*---', stripped)
            if section_m2:
                current_section = section_m2.group(1).strip()
                continue
            # Handle separator-bar sections: # ====\n# TITLE\n# ====
            if stripped.startswith("#") and re.match(r'^#\s*=+\s*$', stripped):
                continue
            # Standalone comment line as section header (between separator bars)
            sec_standalone = re.match(r'^#\s+([A-Z][A-Z _\-]+[A-Z])(?:\s+[-—].*)?$', stripped)
            if sec_standalone and len(sec_standalone.group(1)) > 8:
                current_section = sec_standalone.group(1).strip()
                if len(current_section) > 80:
                    current_section = current_section[:77] + "..."
                continue
            # Skip pure comment lines, blank lines, methods
            if stripped.startswith("#") or stripped.startswith("def ") or stripped.startswith("@") or not stripped:
                continue
            # Match: NAME: type = value  # comment
            m = re.match(r'^(\s+)(\w+)\s*:\s*(\w[\w\[\], ]*?)\s*=\s*(.+?)(?:\s*#\s*(.*))?$', line)
            if m:
                indent, name, ptype, value, comment = m.groups()
                # Skip complex defaults (field(default_factory=...), lambdas, os.getenv, etc)
                val_stripped = value.strip().rstrip(",")
                if "field(default_factory" in val_stripped or "os.getenv" in val_stripped or "Path" in val_stripped:
                    continue
                # Skip private/classvars
                if name.startswith("_"):
                    continue
                params.append({
                    "name": name,
                    "type": ptype.strip(),
                    "value": val_stripped,
                    "comment": (comment or "").strip(),
                    "section": current_section,
                })
                continue
            # Match: NAME = value (no type annotation)
            m2 = re.match(r'^(\s+)(\w+)\s*=\s*(.+?)(?:\s*#\s*(.*))?$', line)
            if m2:
                indent, name, value, comment = m2.groups()
                val_stripped = value.strip().rstrip(",")
                if name.startswith("_") or name == "None":
                    continue
                if "field(default_factory" in val_stripped or "os.getenv" in val_stripped or "Path" in val_stripped:
                    continue
                if val_stripped.startswith("lambda") or val_stripped.startswith("{") or val_stripped.startswith("["):
                    continue
                # Infer type
                ptype = "str"
                if val_stripped in ("True", "False"):
                    ptype = "bool"
                elif re.match(r'^-?\d+\.\d+$', val_stripped):
                    ptype = "float"
                elif re.match(r'^-?\d+$', val_stripped):
                    ptype = "int"
                elif val_stripped.startswith("'") or val_stripped.startswith('"'):
                    ptype = "str"
                params.append({
                    "name": name,
                    "type": ptype,
                    "value": val_stripped,
                    "comment": (comment or "").strip(),
                    "section": current_section,
                })
        return params
    return _cached(f"params_{config_path}", _fetch)


def read_config_param(config_path, param_name):
    """Read a single parameter value from a config file."""
    try:
        with open(config_path, "r") as f:
            for line in f:
                m = re.search(rf'^\s*{re.escape(param_name)}[\s:][^=]*=\s*(.+?)(?:\s*#|$)', line)
                if m:
                    return m.group(1).strip()
    except Exception:
        pass
    return "(not found)"


def render_params_page(config_path, dataclass_name, config_label, active_nav, param_route_prefix):
    """Render full parameter browser for a config file."""
    params = parse_config_params(config_path, dataclass_name)
    if not params:
        body = f'<h2>{config_label} Parameters</h2><div style="color:#888">No parameters found in {os.path.basename(config_path)}</div>'
        return render_page(body, title=f"Params {config_label}", active_nav=active_nav)
    # Group by section
    sections = {}
    for p in params:
        sec = p["section"]
        if sec not in sections:
            sections[sec] = []
        sections[sec].append(p)
    # Count by type
    type_counts = defaultdict(int)
    for p in params:
        type_counts[p["type"]] += 1
    type_summary = " | ".join(f"{t}: {c}" for t, c in sorted(type_counts.items(), key=lambda x: -x[1]))
    filter_js = """
    <script>
    function filterParams() {
        var q = document.getElementById('param-search').value.toLowerCase();
        var rows = document.querySelectorAll('.param-row');
        var sections = document.querySelectorAll('.param-section');
        rows.forEach(function(r) { r.style.display = r.textContent.toLowerCase().includes(q) ? '' : 'none'; });
        sections.forEach(function(s) {
            var visible = s.querySelectorAll('.param-row:not([style*="display: none"])').length;
            s.style.display = visible > 0 ? '' : 'none';
        });
    }
    </script>
    """
    body = f"""
    {filter_js}
    <h2>{config_label} Parameters ({len(params)} total)</h2>
    <div class="subtitle">{os.path.basename(config_path)} | {len(sections)} sections | {type_summary}</div>
    <div style="margin-bottom:15px">
        <input type="text" id="param-search" onkeyup="filterParams()" placeholder="Search parameters..." style="background:#0d1117;border:1px solid #444;color:#e0e0e0;padding:8px 12px;border-radius:4px;width:400px;font-family:monospace;font-size:13px" />
    </div>
    """
    for sec_name, sec_params in sections.items():
        sec_id = re.sub(r'[^a-zA-Z0-9]', '_', sec_name)[:40]
        body += f'<div class="param-section" id="sec_{sec_id}"><h3>{sec_name} ({len(sec_params)})</h3>'
        body += '<table><tr><th>Parameter</th><th>Type</th><th>Value</th><th>Comment</th></tr>'
        for p in sec_params:
            val = p["value"]
            # Color code booleans
            if val == "True":
                val_html = '<span style="color:#4caf50">True</span>'
            elif val == "False":
                val_html = '<span style="color:#f44336">False</span>'
            elif p["type"] == "float" or p["type"] == "int":
                val_html = f'<span style="color:#00d4ff">{val}</span>'
            else:
                val_html = f'<span style="color:#ff9800">{val}</span>'
            comment_html = f'<span style="color:#888;font-size:11px">{p["comment"][:120]}</span>' if p["comment"] else ""
            type_color = {"bool": "#7c4dff", "float": "#00d4ff", "int": "#8bc34a", "str": "#ff9800"}.get(p["type"], "#888")
            link = f'{param_route_prefix}/{p["name"]}'
            body += f'<tr class="param-row"><td><a href="{link}">{p["name"]}</a></td><td><span style="color:{type_color}">{p["type"]}</span></td><td>{val_html}</td><td>{comment_html}</td></tr>'
        body += '</table></div>'
    return render_page(body, title=f"Params {config_label}", active_nav=active_nav)


def render_param_detail(config_path, dataclass_name, config_label, param_name, param_route_prefix, params_route):
    """Render detail page for a single parameter."""
    params = parse_config_params(config_path, dataclass_name)
    match = [p for p in params if p["name"] == param_name]
    if not match:
        body = f'<h2>Parameter Not Found</h2><p>No parameter named <code>{param_name}</code> in {config_label}</p><p><a href="{params_route}">Back</a></p>'
        return render_page(body, title=f"Param: {param_name}")
    p = match[0]
    # Look for ablation results
    ablation_results = []
    # Check /tmp/v8_ablation_override.json
    try:
        with open("/tmp/v8_ablation_override.json", "r") as f:
            ablation_data = json.load(f)
        if isinstance(ablation_data, dict):
            for key, val in ablation_data.items():
                if param_name.lower() in key.lower():
                    ablation_results.append({"source": "v8_ablation_override", "key": key, "data": val})
    except Exception:
        pass
    # Check sweep CSVs for this param
    sweep_mentions = []
    sweep_dir = os.path.join(BASE_DIR, "backtest_v8", "sweeps")
    if os.path.isdir(sweep_dir):
        for csv_file in sorted(glob.glob(os.path.join(sweep_dir, "*.csv")))[-10:]:
            try:
                with open(csv_file, "r") as f:
                    header_line = f.readline()
                if f"cfg_{param_name}" in header_line.lower() or param_name.lower() in header_line.lower():
                    sweep_mentions.append(os.path.basename(csv_file))
            except Exception:
                pass
    ablation_html = ""
    if ablation_results:
        ablation_html = "<h3>Ablation Results</h3><table><tr><th>Source</th><th>Key</th><th>Data</th></tr>"
        for a in ablation_results:
            data_str = json.dumps(a["data"], indent=2) if isinstance(a["data"], (dict, list)) else str(a["data"])
            ablation_html += f'<tr><td>{a["source"]}</td><td>{a["key"]}</td><td><pre style="color:#aaa;font-size:11px">{data_str[:500]}</pre></td></tr>'
        ablation_html += "</table>"
    elif sweep_mentions:
        ablation_html = "<h3>Found in Sweep CSVs</h3><ul>"
        for sm in sweep_mentions:
            ablation_html += f'<li style="color:#aaa">{sm}</li>'
        ablation_html += "</ul>"
    else:
        ablation_html = '<h3>Ablation Results</h3><div style="color:#888">No ablation data found for this parameter.</div>'
    # Queue A/B test form
    queue_html = f"""
    <h3>Queue A/B Test</h3>
    <form method="POST" action="{param_route_prefix}/{param_name}/queue_test">
        <div class="form-group">
            <label>Test Value A (current = {p['value']})</label>
            <input type="text" name="value_a" value="{p['value']}" />
        </div>
        <div class="form-group">
            <label>Test Value B</label>
            <input type="text" name="value_b" value="" placeholder="Enter alternative value" />
        </div>
        <div class="form-group">
            <label>Notes</label>
            <input type="text" name="notes" value="" placeholder="Why testing this?" style="width:300px" />
        </div>
        <button type="submit" class="btn">Queue Test</button>
    </form>
    """
    msg = request.args.get("msg", "")
    msg_html = f'<div style="background:#1a3a1a;border:1px solid #4caf50;padding:8px;border-radius:4px;margin-bottom:15px">{msg}</div>' if msg else ""
    body = f"""
    <p><a href="{params_route}">Back to {config_label} Params</a></p>
    {msg_html}
    <h2>{p['name']}</h2>
    <div style="margin-bottom:20px">
        <span class="metric"><span class="metric-label">Type</span><br><span class="metric-value" style="color:#7c4dff">{p['type']}</span></span>
        <span class="metric"><span class="metric-label">Current Value</span><br><span class="metric-value" style="color:#00d4ff">{p['value']}</span></span>
        <span class="metric"><span class="metric-label">Section</span><br><span class="metric-value" style="color:#ff9800;font-size:13px">{p['section'][:50]}</span></span>
    </div>
    <div style="margin-bottom:15px;color:#aaa"><b>Comment:</b> {p['comment'] or '(none)'}</div>
    {ablation_html}
    {queue_html}
    """
    return render_page(body, title=f"Param: {param_name}")


# ---------------------------------------------------------------------------
# Params Routes — Stocks (config_tradier.py)
# ---------------------------------------------------------------------------
@app.route("/params")
def params_view():
    """Full parameter browser for config_tradier.py."""
    config_path = os.path.join(BASE_DIR, "config_tradier.py")
    return render_params_page(config_path, "TradierConfig", "Stocks", "Params Stocks", "/param")


@app.route("/param/<path:name>")
def param_detail(name):
    """Detail page for a single stock parameter."""
    config_path = os.path.join(BASE_DIR, "config_tradier.py")
    return render_param_detail(config_path, "TradierConfig", "Stocks", name, "/param", "/params")


@app.route("/param/<path:name>/queue_test", methods=["POST"])
def param_queue_test(name):
    """Queue an A/B test for a stock parameter."""
    value_a = request.form.get("value_a", "").strip()
    value_b = request.form.get("value_b", "").strip()
    notes = request.form.get("notes", "").strip()
    if not value_b:
        return redirect(f"/param/{name}?msg=Error:+value_b+is+required")
    queue_path = "/tmp/v8_test_queue.json"
    queue = []
    try:
        with open(queue_path, "r") as f:
            queue = json.load(f)
    except Exception:
        pass
    queue.append({
        "param": name,
        "config": "config_tradier.py",
        "value_a": value_a,
        "value_b": value_b,
        "notes": notes,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    })
    try:
        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        return redirect(f"/param/{name}?msg=Error+writing+queue:+{e}")
    return redirect(f"/param/{name}?msg=Test+queued:+{name}+A={value_a}+B={value_b}")


# ---------------------------------------------------------------------------
# Params Routes — Crypto (config.py)
# ---------------------------------------------------------------------------
@app.route("/params/crypto")
def params_crypto_view():
    """Full parameter browser for config.py (crypto)."""
    config_path = os.path.join(BASE_DIR, "config.py")
    return render_params_page(config_path, "Config", "Crypto", "Params Crypto", "/param/crypto")


@app.route("/param/crypto/<path:name>")
def param_crypto_detail(name):
    """Detail page for a single crypto parameter."""
    config_path = os.path.join(BASE_DIR, "config.py")
    return render_param_detail(config_path, "Config", "Crypto", name, "/param/crypto", "/params/crypto")


@app.route("/param/crypto/<path:name>/queue_test", methods=["POST"])
def param_crypto_queue_test(name):
    """Queue an A/B test for a crypto parameter."""
    value_a = request.form.get("value_a", "").strip()
    value_b = request.form.get("value_b", "").strip()
    notes = request.form.get("notes", "").strip()
    if not value_b:
        return redirect(f"/param/crypto/{name}?msg=Error:+value_b+is+required")
    queue_path = "/tmp/v8_test_queue.json"
    queue = []
    try:
        with open(queue_path, "r") as f:
            queue = json.load(f)
    except Exception:
        pass
    queue.append({
        "param": name,
        "config": "config.py",
        "value_a": value_a,
        "value_b": value_b,
        "notes": notes,
        "queued_at": datetime.now(timezone.utc).isoformat(),
        "status": "pending",
    })
    try:
        with open(queue_path, "w") as f:
            json.dump(queue, f, indent=2)
    except Exception as e:
        return redirect(f"/param/crypto/{name}?msg=Error+writing+queue:+{e}")
    return redirect(f"/param/crypto/{name}?msg=Test+queued:+{name}+A={value_a}+B={value_b}")


# ---------------------------------------------------------------------------
# Run History
# ---------------------------------------------------------------------------
def fetch_v8_results(srv):
    """Parse all V8_RESULT lines from /tmp/v8_full_*.log on a server."""
    def _fetch():
        results = []
        for log_path in LIVE_LOGS:
            try:
                raw = _ssh_run(srv["host"], srv["user"], f"grep 'V8_RESULT' {log_path} 2>/dev/null", timeout=10)
            except Exception:
                continue
            if not raw:
                continue
            for line in raw.strip().splitlines():
                entry = {"server": srv["name"], "log": os.path.basename(log_path), "raw": line.strip()}
                # Parse common fields: timestamp, sharpe, pnl, trades, wr
                m_ts = re.search(r'(\d{4}-\d{2}-\d{2}[\sT]\d{2}:\d{2}:\d{2})', line)
                if m_ts:
                    entry["timestamp"] = m_ts.group(1)
                m_sharpe = re.search(r'sharpe[:\s=]+([+-]?\d+\.?\d*)', line, re.IGNORECASE)
                if m_sharpe:
                    entry["sharpe"] = m_sharpe.group(1)
                m_pnl = re.search(r'pnl[:\s=]+([+-]?\d+\.?\d*%?)', line, re.IGNORECASE)
                if m_pnl:
                    entry["pnl"] = m_pnl.group(1)
                m_trades = re.search(r'(\d+)\s*trade', line, re.IGNORECASE)
                if m_trades:
                    entry["trades"] = m_trades.group(1)
                m_wr = re.search(r'wr[:\s=]+(\d+\.?\d*%?)', line, re.IGNORECASE)
                if m_wr:
                    entry["wr"] = m_wr.group(1)
                m_mode = re.search(r'mode[:\s=]+(\w+)', line, re.IGNORECASE)
                if m_mode:
                    entry["mode"] = m_mode.group(1)
                m_sym = re.search(r'(\d+)\s*symbol', line, re.IGNORECASE)
                if m_sym:
                    entry["symbols"] = m_sym.group(1)
                results.append(entry)
        return results
    return _cached(f"history_{srv['name']}", _fetch)


@app.route("/history")
def history_view():
    """Run history — all completed V8_RESULT entries."""
    all_results = []
    for srv in LIVE_SERVERS:
        results = fetch_v8_results(srv)
        if isinstance(results, list):
            all_results.extend(results)
    if not all_results:
        body = '<h2>Run History</h2><div style="color:#888">No V8_RESULT lines found in /tmp/v8_full_*.log on any server.</div>'
        return render_page(body, title="History", active_nav="History")
    # Sort by timestamp descending
    all_results.sort(key=lambda r: r.get("timestamp", ""), reverse=True)
    rows_html = ""
    for r in all_results:
        sharpe_val = r.get("sharpe", "N/A")
        sharpe_html = fmt_sharpe(sharpe_val) if sharpe_val != "N/A" else "N/A"
        rows_html += f"""<tr>
            <td>{r.get('timestamp', 'N/A')}</td>
            <td>{r.get('server', '?')}</td>
            <td>{r.get('mode', r.get('log', 'N/A'))}</td>
            <td>{r.get('symbols', 'N/A')}</td>
            <td>{sharpe_html}</td>
            <td>{r.get('pnl', 'N/A')}</td>
            <td>{r.get('trades', 'N/A')}</td>
            <td>{r.get('wr', 'N/A')}</td>
        </tr>"""
    body = f"""
    <h2>Run History ({len(all_results)} results)</h2>
    <table>
        <tr><th>Timestamp</th><th>Server</th><th>Mode</th><th>Symbols</th><th>Sharpe</th><th>PnL</th><th>Trades</th><th>WR%</th></tr>
        {rows_html}
    </table>
    """
    return render_page(body, title="History", active_nav="History")


# ---------------------------------------------------------------------------
# Monitor Agent Log
# ---------------------------------------------------------------------------
@app.route("/monitor")
def monitor_page():
    # Read from all agent logs
    lines = []
    for logfile in ["/tmp/cpu_enforcer.log", "/tmp/v8_watchdog.log", "/tmp/sweep_orchestrator.log"]:
        try:
            with open(logfile) as f:
                lines.extend(f.readlines()[-50:])
        except FileNotFoundError:
            pass
    if not lines:
        lines = ["No watchdog running. Start with: python -u v8_watchdog.py &"]
    lines = lines[-100:]
    # Color code alerts
    colored = []
    for line in lines:
        line = line.rstrip()
        if "ALERT" in line or "DEAD PARAMS" in line or "DUPLICATES" in line:
            colored.append(f'<span style="color:#f44336">{line}</span>')
        elif "COMPLETED" in line or "RESULT" in line:
            colored.append(f'<span style="color:#4caf50">{line}</span>')
        elif "RUNNING" in line:
            colored.append(f'<span style="color:#00d4ff">{line}</span>')
        else:
            colored.append(line)
    log_html = "\n".join(colored)
    import subprocess as _sp
    watchdog_on = False
    enforcer_on = False
    try:
        watchdog_on = bool(_sp.run(["pgrep", "-f", "v8_watchdog"], capture_output=True, text=True, timeout=3).stdout.strip())
        enforcer_on = bool(_sp.run(["pgrep", "-f", "cpu_enforcer"], capture_output=True, text=True, timeout=3).stdout.strip())
    except Exception:
        pass
    def _st(on):
        return '<span style="color:#4caf50">ON</span>' if on else '<span style="color:#f44336">OFF</span>'
    body = f"""
    <h2>Agents: Watchdog {_st(watchdog_on)} | CPU Enforcer {_st(enforcer_on)}</h2>
    <p style="color:#888">Watchdog: 30s process health + auto-restart | Enforcer: 20s per-core CPU fill + error scan + dead param detection</p>
    <div class="log-box" style="max-height:600px; overflow-y:auto; font-size:12px">{log_html}</div>
    """
    return render_page(body, title="Monitor Agent", active_nav="Monitor")


# ---------------------------------------------------------------------------
# 2026-04-14: New routes — /alerts, /switches, /live_vs_sandbox
# ---------------------------------------------------------------------------
@app.route("/alerts")
def alerts_page():
    d = os.path.join(BASE_DIR, "data", "sweep_alerts")
    if not os.path.isdir(d):
        return render_page("<p>No alerts directory.</p>", title="Alerts", active_nav="Home")
    entries = []
    for fn in sorted(os.listdir(d), reverse=True):
        if fn.startswith("_"): continue  # state files
        p = os.path.join(d, fn)
        try:
            mt = os.path.getmtime(p)
            data = json.load(open(p))
        except Exception:
            continue
        entries.append({"fn": fn, "mtime": mt, "data": data})
    entries.sort(key=lambda e: e["mtime"], reverse=True)
    rows = []
    for e in entries[:200]:
        ago = int(time.time() - e["mtime"])
        kind = "FIX" if e["fn"].startswith("FIX_REQUIRED_") else ("DUPE" if e["fn"].startswith("duplicate_") else ("REGRESSION" if e["fn"].startswith("regression_") else "COORD"))
        summary = ""
        if kind == "FIX":
            summary = f"knob={e['data'].get('knob')} values={e['data'].get('values_tried')} output={e['data'].get('identical_output')}"
        elif kind == "DUPE":
            knobs = [dk.get("knob") for dk in e["data"].get("dead_knobs", [])]
            summary = f"machine={e['data'].get('machine')} dead_knobs={knobs} output={e['data'].get('output_key')}"
        elif kind == "REGRESSION":
            suspects = list(e["data"].get("suspect_knobs", {}).keys())
            summary = f"machine={e['data'].get('machine')} drop={e['data'].get('drop_pct')}% suspects={suspects}"
        elif kind == "COORD":
            summary = f"fingerprint={e['data'].get('fingerprint')} first={e['data'].get('first_run', {}).get('machine')} dupe={e['data'].get('duplicate_run', {}).get('machine')}"
        rows.append(f'<tr><td>{kind}</td><td>{ago}s</td><td>{e["fn"]}</td><td style="font-size:11px">{summary}</td></tr>')
    body = f"""
    <h2>Sweep Alerts ({len(entries)} total)</h2>
    <p style="color:#888">Sources: sweep_duplicate_guard (2m) + sweep_regression_watcher (2m) + sweep_coordinator_v8 (5m). FIX_REQUIRED files are one-per-dead-knob.</p>
    <table style="width:100%; font-size:12px"><tr><th>Kind</th><th>Age</th><th>File</th><th>Summary</th></tr>
    {''.join(rows)}
    </table>
    """
    return render_page(body, title="Alerts", active_nav="Home")


@app.route("/switches")
def switches_page():
    try:
        r = subprocess.run(["/opt/anaconda3/envs/binance_env/bin/python", os.path.join(BASE_DIR, "verify_switches.py"), "--json"], capture_output=True, text=True, timeout=30)
        data = json.loads(r.stdout)
    except Exception as e:
        return render_page(f"<p>verify_switches failed: {e}</p>", title="Switches", active_nav="Home")
    def _tbl(title, items, color):
        rows_h = []
        for e in items:
            files_str = ", ".join(f"{k}={v}" for k, v in e["files"].items())
            rows_h.append(f'<tr><td>{e["switch"]}</td><td>{e["category"]}</td><td>{e.get("status","")}</td><td style="font-size:11px">{files_str}</td></tr>')
        return f'<h3 style="color:{color}">{title} ({len(items)})</h3><table style="width:100%; font-size:12px"><tr><th>Switch</th><th>Category</th><th>Status</th><th>Files</th></tr>{"".join(rows_h)}</table>'
    body = _tbl("❌ MISSING (revert alarm)", data["missing"], "#ff4444") + _tbl("⚠ Partial", data["partial"], "#ffaa00") + _tbl("✅ OK", data["ok"], "#4caf50") + _tbl("◼ Disabled by design", data["disabled_by_design"], "#888")
    return render_page(body, title="Switches", active_nav="Home")


@app.route("/results")
def results_page():
    return redirect("/swarm")


@app.route("/results_legacy")
def results_page_legacy():
    """2026-04-14: The HONEST results view. Not claim-based. Reads latest sweep CSVs
    from all 3 machines, shows Sharpe/PnL breakdown + per-switch variance test.

    A switch is 'verified alive' if paired runs (flipping ONLY that switch with
    everything else fixed) produce different Sharpe values in the LATEST data.
    canonical_switches.json marking a switch 'yes' does NOT imply verified —
    this page is the truth source."""
    from collections import defaultdict as _dd
    machines = [
        {"name": "Local", "host": None, "dir": os.path.join(BASE_DIR, "backtest_v8", "sweeps")},
        {"name": "S1", "host": "s1-int", "dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
        {"name": "S2", "host": "s2-int", "dir": "/home/niels/binance-sandbox/backtest_v8/sweeps"},
    ]
    all_rows = []
    for m in machines:
        try:
            if m["host"] is None:
                files = sorted(glob.glob(os.path.join(m["dir"], "v8_sweep_*.csv")))[-5:]
                raw = ""
                for f in files: raw += open(f).read() + "\n"
            else:
                raw = _ssh_run(m["host"], None, f'tail -n +1 {m["dir"]}/v8_sweep_*.csv 2>/dev/null | tail -5000')
        except Exception: continue
        header = None
        for line in raw.splitlines():
            line = line.strip()
            if not line: continue
            if line.startswith("run_id,"):
                header = next(csv.reader(io.StringIO(line)))
                continue
            if header is None: continue
            try:
                vals = next(csv.reader(io.StringIO(line)))
                if len(vals) == len(header):
                    r = dict(zip(header, vals))
                    r["_machine"] = m["name"]
                    all_rows.append(r)
            except Exception: continue
    ok_rows = [r for r in all_rows if r.get("status") == "ok"]
    def _f(x, d=0.0):
        try: return float(x)
        except Exception: return d
    # MIN_DISPLAY_TRADES: filter out small-sample noise (2-trade Sharpe=14 is not meaningful)
    MIN_DISPLAY_TRADES = 15
    meaningful_rows = [r for r in ok_rows if _f(r.get("trades", 0)) >= MIN_DISPLAY_TRADES]
    meaningful_rows.sort(key=lambda r: _f(r.get("sharpe", 0)), reverse=True)
    top = meaningful_rows[:25]
    top_rows = [f'<tr><td>{r["_machine"]}</td><td>{r.get("run_id","?")}</td><td>{r.get("sharpe","?")}</td><td>${_f(r.get("pnl")):.2f}</td><td>{r.get("trades","?")}</td><td>{r.get("wins","?")}/{r.get("losses","?")}</td><td style="font-size:10px; max-width:400px">{r.get("name","")[:80]}</td></tr>' for r in top]
    # Switch variance check: use meaningful rows only (>=15 trades, sharpe>0) to avoid noise
    variance_base = [r for r in ok_rows if _f(r.get("trades", 0)) >= MIN_DISPLAY_TRADES and _f(r.get("sharpe", 0)) > 0]
    cfg_cols = set()
    for r in variance_base:
        cfg_cols.update(c for c in r if c.startswith("cfg_"))
    variance_rows = []
    for col in sorted(cfg_cols):
        # Find pairs differing ONLY by this column
        sig_groups = _dd(list)
        for r in variance_base:
            sig = tuple((k, r.get(k)) for k in sorted(cfg_cols) if k != col)
            sig_groups[sig].append(r)
        pairs = [g for g in sig_groups.values() if len(g) >= 2 and len({r.get(col) for r in g}) >= 2]
        if not pairs:
            status, note = "no-data", "no paired runs yet"
        else:
            alive = 0; dead = 0
            for g in pairs:
                outputs = {(round(_f(r.get("sharpe")), 3), round(_f(r.get("pnl")), 2)) for r in g}
                if len(outputs) == 1: dead += 1
                else: alive += 1
            if alive == 0: status, note = "DEAD", f"{dead} paired groups all identical"
            elif dead == 0: status, note = "alive", f"{alive} paired groups varied"
            else: status, note = "partial", f"{alive} alive / {dead} dead"
        color = {"alive": "#4caf50", "DEAD": "#ff4444", "partial": "#ffaa00", "no-data": "#888"}[status]
        variance_rows.append(f'<tr style="color:{color}"><td>{col.replace("cfg_","")}</td><td>{status.upper()}</td><td>{note}</td></tr>')
    body = f"""
    <h2>🏆 Top 25 Configs by Sharpe ({len(ok_rows)} total OK, {len(meaningful_rows)} with ≥{MIN_DISPLAY_TRADES} trades)</h2>
    <p style="color:#888">Aggregated from last ~5 sweep CSVs per machine. Filtered to ≥{MIN_DISPLAY_TRADES} trades (removes small-sample noise). Sort: Sharpe desc.</p>
    <table style="width:100%; font-size:12px"><tr><th>Machine</th><th>Run ID</th><th>Sharpe</th><th>PnL</th><th>Trades</th><th>W/L</th><th>Name</th></tr>
    {''.join(top_rows) or '<tr><td colspan=7>No OK rows yet.</td></tr>'}
    </table>
    <h2 style="margin-top:30px">🔌 Switch-by-Switch Variance (empirical)</h2>
    <p style="color:#888">For each cfg_ column, finds rows differing ONLY in that column. If all such paired groups produce identical Sharpe+PnL → switch is DEAD regardless of what canonical_switches.json claims.</p>
    <table style="width:100%; font-size:12px"><tr><th>Switch</th><th>Verdict</th><th>Evidence</th></tr>
    {''.join(variance_rows)}
    </table>
    """
    return render_page(body, title="Results", active_nav="Home")


@app.route("/live_vs_sandbox")
def live_vs_sandbox():
    """2026-04-14: Distinguish real live trades from backtest sweep results."""
    # LIVE = recent data/decisions/ JSONL (MacBook, real orders)
    live_dir = os.path.join(BASE_DIR, "data", "decisions")
    live_rows = []
    if os.path.isdir(live_dir):
        for fn in sorted(os.listdir(live_dir), reverse=True)[:5]:
            p = os.path.join(live_dir, fn)
            try:
                size = os.path.getsize(p)
                mt = os.path.getmtime(p)
                with open(p) as fh:
                    lines = fh.readlines()
                live_rows.append(f'<tr><td>{fn}</td><td>{len(lines)}</td><td>{size//1024}KB</td><td>{int(time.time()-mt)}s ago</td></tr>')
            except Exception:
                continue
    # SANDBOX = sweep CSVs via coordinator plan
    plan_path = os.path.join(BASE_DIR, "data", "sweep_alerts", "_coordinator_plan.json")
    sandbox_info = {}
    try:
        with open(plan_path) as fh: sandbox_info = json.load(fh)
    except Exception: pass
    bm = sandbox_info.get("by_machine", {})
    sb_rows = [f'<tr><td>{m}</td><td>{s.get("ok",0)}</td><td>{s.get("fail",0)}</td><td>{s.get("rows",0)}</td><td>{int(s.get("elapsed_total",0)//60)} min</td></tr>' for m, s in bm.items()]
    body = f"""
    <h2 style="color:#4caf50">🟢 LIVE — Real orders (data/decisions/)</h2>
    <table style="width:100%"><tr><th>File</th><th>Decisions</th><th>Size</th><th>Age</th></tr>{''.join(live_rows) or '<tr><td colspan=4>No live decisions logged yet today.</td></tr>'}</table>
    <h2 style="color:#ffaa00; margin-top:20px">🧪 SANDBOX — Backtest sweeps</h2>
    <p style="color:#888">Source: sweep_coordinator_v8.py (every 5 min). Total unique configs: {sandbox_info.get('total_unique_configs', '?')}</p>
    <table style="width:100%"><tr><th>Machine</th><th>OK</th><th>Fail</th><th>Total Rows</th><th>Elapsed</th></tr>{''.join(sb_rows) or '<tr><td colspan=5>No data yet.</td></tr>'}</table>
    """
    return render_page(body, title="Live vs Sandbox", active_nav="Home")


# ---------------------------------------------------------------------------
# /live_vs_frozen — drift vs READONLY_LATEST (carved-in-stone proven baseline)
# ---------------------------------------------------------------------------
_DIFF_JSON = os.path.join(BASE_DIR, "data", "diff_live_vs_frozen.json")
_DIFF_TOOL = os.path.join(BASE_DIR, "tools", "diff_live_vs_frozen.py")


def _run_diff_tool() -> str:
    """Re-run diff_live_vs_frozen.py, return stderr+stdout snippet."""
    try:
        res = subprocess.run(
            ["python3", _DIFF_TOOL], capture_output=True, text=True, timeout=60
        )
        return (res.stdout or "") + (res.stderr or "")
    except Exception as exc:
        return f"diff tool failed: {exc}"


def _load_diff_report() -> dict:
    if not os.path.isfile(_DIFF_JSON):
        _run_diff_tool()
    try:
        with open(_DIFF_JSON) as fh:
            return json.load(fh)
    except Exception:
        return {}


def _fmt(v):
    s = repr(v)
    if len(s) > 100:
        s = s[:97] + "..."
    return s


@app.route("/live_vs_frozen/refresh", methods=["POST"])
def live_vs_frozen_refresh():
    _run_diff_tool()
    return redirect("/live_vs_frozen")


@app.route("/live_vs_frozen")
def live_vs_frozen():
    """Diff live config/code vs READONLY_LATEST frozen snapshot.

    The frozen snapshot at backups/READONLY_LATEST/ is the carved-in-stone
    proven baseline (Apr 4 2026). Any drift below is a place where live
    behavior may diverge from the proven run.
    """
    report = _load_diff_report()
    files = report.get("files", {})
    cfg_diffs = report.get("config_diffs", {})
    mtime = "—"
    if os.path.isfile(_DIFF_JSON):
        mtime = datetime.fromtimestamp(
            os.path.getmtime(_DIFF_JSON), tz=timezone.utc
        ).strftime("%Y-%m-%d %H:%M:%S UTC")

    # File-level drift table
    file_rows = []
    drift_count = 0
    for rel, meta in files.items():
        lm = meta.get("live", {})
        fm = meta.get("frozen", {})
        drift = meta.get("drift")
        if drift:
            drift_count += 1
        marker = (
            '<span style="color:#ff4444">🔴 DRIFT</span>'
            if drift
            else '<span style="color:#4caf50">✅ OK</span>'
        )
        file_rows.append(
            f"<tr><td>{rel}</td><td>{marker}</td>"
            f"<td><code>{lm.get('md5','-')[:10]}</code></td>"
            f"<td><code>{fm.get('md5','-')[:10]}</code></td>"
            f"<td>{lm.get('size','-')}</td>"
            f"<td>{fm.get('size','-')}</td></tr>"
        )

    # Constant-level tables per config file
    cfg_sections = []
    for rel, cd in cfg_diffs.items():
        changed = cd.get("changed", [])
        removed = cd.get("removed", [])
        added = cd.get("added", [])
        rows_changed = "".join(
            f'<tr><td><code>{c["name"]}</code></td>'
            f'<td style="color:#ff8844"><code>{_fmt(c["live"])}</code></td>'
            f'<td style="color:#888"><code>{_fmt(c["frozen"])}</code></td></tr>'
            for c in changed
        ) or '<tr><td colspan=3 style="color:#888">None</td></tr>'
        rows_removed = "".join(
            f'<tr><td><code>{c["name"]}</code></td>'
            f'<td style="color:#888">(gone)</td>'
            f'<td><code>{_fmt(c["frozen"])}</code></td></tr>'
            for c in removed
        ) or '<tr><td colspan=3 style="color:#888">None</td></tr>'
        added_preview = "".join(
            f"<li><code>{c['name']}</code> = <code>{_fmt(c['live'])}</code></li>"
            for c in added[:40]
        )
        added_more = (
            f"<p style='color:#888'>… and {len(added) - 40} more</p>"
            if len(added) > 40
            else ""
        )
        cfg_sections.append(
            f"""
<h3 style="margin-top:28px">{rel}</h3>
<p>Live defines <b>{cd.get('live_count', 0)}</b> constants, frozen defines
<b>{cd.get('frozen_count', 0)}</b>. Changed: <b style="color:#ff8844">{len(changed)}</b> ·
Removed: <b style="color:#ff4444">{len(removed)}</b> · Added: <b style="color:#44aaff">{len(added)}</b>.</p>

<h4>CHANGED (value differs live vs frozen — most likely drift source)</h4>
<table style="width:100%">
<tr><th>Name</th><th>Live</th><th>Frozen (proven)</th></tr>
{rows_changed}
</table>

<h4>REMOVED (present in frozen, gone from live)</h4>
<table style="width:100%">
<tr><th>Name</th><th>Live</th><th>Frozen (proven)</th></tr>
{rows_removed}
</table>

<details style="margin-top:10px"><summary>ADDED since frozen ({len(added)} new names)</summary>
<ul>{added_preview}</ul>{added_more}</details>
"""
        )

    body = f"""
<h2>🧊 Live vs Frozen (READONLY_LATEST)</h2>
<p>Frozen snapshot: <code>backups/READONLY_LATEST/</code> (Apr 4 2026, permissions <code>r--r--r--</code>).
This is the carved-in-stone proven baseline. Any constant CHANGED or REMOVED below is a place where live
behavior may diverge from the proven run.</p>
<p>Report generated: <b>{mtime}</b> ·
<form method="post" action="/live_vs_frozen/refresh" style="display:inline">
<button type="submit">🔄 Re-run diff now</button>
</form></p>

<h3>File-level drift ({drift_count}/{len(files)} files drift from frozen)</h3>
<table style="width:100%">
<tr><th>File</th><th>Status</th><th>Live md5</th><th>Frozen md5</th><th>Live size</th><th>Frozen size</th></tr>
{''.join(file_rows)}
</table>
{''.join(cfg_sections)}
"""
    return render_page(body, title="Live vs Frozen", active_nav="Home")


# ---------------------------------------------------------------------------
# /sweeps — One-click sweep launcher (the canonical agent + human entry point).
# Crypto tiers go to S1 ONLY. Tradier tiers go to S2 ONLY. (CLAUDE.md mandate.)
# ---------------------------------------------------------------------------
def _list_tier_names():
    sw = os.path.join(BASE_DIR, "v8_quick_sweep.py")
    if not os.path.exists(sw):
        return []
    try:
        src = open(sw).read()
    except Exception:
        return []
    m = re.search(r"TIER_MAP\s*=\s*\{(.+?)\n\}", src, re.DOTALL)
    if not m:
        return []
    return sorted(set(re.findall(r'"([\w_]+)"\s*:', m.group(1))))


def _sweep_status_for(host, mode):
    out = _ssh_run(host, None, f'pgrep -afc "v8_quick_sweep.*--mode {mode}"', timeout=5)
    nproc = 0
    try:
        nproc = int((out or "0").splitlines()[0])
    except Exception:
        nproc = 0
    procs_raw = _ssh_run(host, None, f'pgrep -af "v8_quick_sweep.*--mode {mode}" 2>/dev/null | head -8', timeout=5)
    csv = _ssh_run(host, None, f'ls -lt /home/niels/binance-sandbox/data/sweep_results/v8_quick_{mode}_*.csv 2>/dev/null | head -1', timeout=5)
    csv_rows = _ssh_run(host, None, f'ls /home/niels/binance-sandbox/data/sweep_results/v8_quick_{mode}_*.csv 2>/dev/null | head -1 | xargs wc -l 2>/dev/null', timeout=5)
    wrong_mode = "tradier" if mode == "crypto" else "crypto"
    bad = _ssh_run(host, None, f'pgrep -afc "v8_quick_sweep.*--mode {wrong_mode}"', timeout=5)
    bad_n = 0
    try:
        bad_n = int((bad or "0").splitlines()[0])
    except Exception:
        pass
    return {"nproc": nproc, "procs": procs_raw, "csv": csv, "csv_rows": csv_rows, "wrong_mode_n": bad_n}


@app.route("/sweeps")
def sweeps_page():
    msg = request.args.get("msg", "")
    tiers = _list_tier_names()
    crypto_tiers = [t for t in tiers if "crypto" in t.lower() or t in ("wt_dc_full", "mega", "mega_v2", "mega_v3", "mega_v4", "mega_v5", "mega_v6", "mega_v7", "rz_exit_sweep", "exit_wt_audit", "exit_wt_phase2", "exit_wt_48sym", "hunt_crypto", "hedge_wt_kill", "ratio_sentiment_crypto")]
    tradier_tiers = [t for t in tiers if "tradier" in t.lower() or "stock" in t.lower() or t in ("wt_dc_full", "rz_exit_sweep", "exit_wt_audit", "exit_wt_phase2", "exit_wt_48sym", "rz_breakout_tradier", "rz_noloss_mode", "ratio_sentiment_tradier", "hedge_wt_kill")]
    crypto_tiers = sorted(set(crypto_tiers))
    tradier_tiers = sorted(set(tradier_tiers))
    s1 = _sweep_status_for("s1-int", "crypto")
    s2 = _sweep_status_for("s2-int", "tradier")

    # Per-tier tooltips — shown on hover so any human/agent knows what each tier does without reading source.
    TIER_DESCRIPTIONS = {
        "wt_dc_full": "Full WT_DC + EXIT_SCORER + DELTA_EXIT sweep, 2304 configs. Anchored against 1.23 baseline. Mode-agnostic — runs on whichever server you launch from.",
        "mega_tradier_v8": "Tradier mega tier — 192 configs, le_dynamic_v2 winner params fixed, sweeps RZ_EXIT/EXIT_SCORER/DC_RECOVERY on top.",
        "mega_crypto_v8": "Crypto mega tier — broad sweep over WT/EXIT/RZ/DC primitives.",
        "mega_crypto_v8_a1234": "Crypto mega tier variant — alternative kernel for ablation.",
        "mega_tradier_v8_a134": "Tradier mega tier variant — alternative kernel.",
        "mega_tradier_v8_focused": "Tradier follow-up to mega_v8: 48 configs on full 128-symbol set, fixed winner params + 5 open questions.",
        "rz_exit_sweep": "Isolate RZ_EXIT + EXIT_SCORER impact across modes — 432 combos. K_EXIT 70-95, BB 0.80-0.90.",
        "rz_breakout_tradier": "RZ breakout entry — price exits extreme BB zone as entry signal. Paired with NO_LOSS guard.",
        "rz_noloss_mode": "Sweep RZ_BREAKOUT_NOLOSS_MODE: bar_structure / dc_low4_base / dc_low_base / dc_low4_15m.",
        "stock_v2": "Stock mega — currently active on S2 watchdog. CT_DC_CROSSOVER_SKIP / CT_WT_VELOCITY_GATE / RZ_EXIT / SRS combinations.",
        "stock_dc_hunt": "Stock DC_LOW exit hunt — large grid on 262 stock symbols.",
        "stock_dc_wide": "Stock DC widened — 1,152 configs on full 262 stock universe.",
        "exit_wt_audit": "Audit exit-WT primitives — measures incremental contribution of each exit signal.",
        "exit_wt_phase2": "Phase-2 follow-up to exit_wt_audit — narrowed configs.",
        "exit_wt_48sym": "Exit-WT validation on full 48-sym crypto / 48-sym tradier scope.",
        "hunt_crypto": "Crypto hunt sweep — broad search.",
        "hunt_stock": "Stock hunt sweep — broad search.",
        "hedge_wt_kill": "HEDGE_KILL_REVERSING_WT — when to close losing hedge based on WT alignment count.",
        "ratio_sentiment_crypto": "Crypto ratio rebalance + sentiment knobs.",
        "ratio_sentiment_tradier": "Tradier equivalent of ratio_sentiment_crypto.",
        "local_extremes_tradier": "LOCAL_EXTREMES_SCORER for tradier — pivot+extreme entry.",
        "local_extremes_tradier_scorer": "LE scorer subgrid — score thresholds.",
        "local_extremes_tradier_validate": "LE validation on full universe.",
        "le_dynamic_tradier": "LE dynamic counter-exit — DYNAMIC_SCORE_COUNTER_EXIT_ENABLED.",
        "le_dynamic_tradier_v2": "LE dynamic v2 — refined version after first-pass winners.",
        "le_partial_exit_tradier": "LE + partial-exit (PE_REM_TFS, PE_FRAC).",
        "le_partial_exit_crypto": "LE + partial-exit on crypto.",
        "dc_low4_bypass_tradier": "DC_LOW4_BYPASS_NOLOSS_ENABLED — verdict was paper-cuts but tradier validate run.",
        "dc_low4_bypass_crypto": "Crypto equivalent of dc_low4_bypass_tradier.",
        "dc_low_tf_tradier": "DC_LOW timeframe sweep on tradier — 9 configs × 262 syms.",
        "dc_low_tf_crypto": "Crypto equivalent.",
        "dc_breakout_failed_tradier": "DC_BREAKOUT_FAILED entry path on tradier.",
        "dc_breakout_failed_crypto": "DC_BREAKOUT_FAILED on crypto.",
        "all_tf_brake_tradier": "ALL_TF_BRAKE_ENABLED — verdict: never fires (tradier: no W/M).",
        "all_tf_brake_crypto": "ALL_TF_BRAKE on crypto — verdict: redundant with WT_EXIT_MIN_TFS, do NOT enable.",
        "stock_60min_reentry": "60-minute reentry window for stocks.",
        "stock_wt_d_aug": "Stock WT-Daily augmentation entry.",
        "stock_wt_d_aug_pt": "Same + PT (profit-target) variant.",
        "crypto_wt_d_4h_aug": "Crypto WT-D + 4h augmentation.",
        "crypto_vel_sweep": "Crypto velocity-threshold sweep (DELTA_EXIT_VEL_MIN_DECAY).",
        "stock_exit_v1": "Stock exit primitives v1.",
        "crypto_exit_v1": "Crypto exit primitives v1.",
        "le_full_tradier": "Full LE configuration on tradier.",
        "le_k1h_rising": "LE with K_1h rising filter.",
        "crypto_validate_top": "Validate top-N crypto winners on full sym scope.",
        "exit_tuning": "Exit-side parameter tuning grid.",
        "exit_decision_tradier": "Tradier exit-decision sweep.",
        "exit_decision_crypto": "Crypto exit-decision sweep.",
        "entry_gates": "Entry-gate sweep — RSI / Stoch / MFI thresholds.",
        "tradier_core": "Core tradier knob set — small grid.",
        "v3_core": "V3 strategy core sweep.",
        "breakout_multi_lung": "BREAKOUT_MULTI_LUNG — multi-confirmation breakout entry on crypto.",
        "breakout_multi_lung_tradier": "BREAKOUT_MULTI_LUNG on tradier.",
        "sharpe3_tradier": "Push tradier above Sharpe 3 (legacy).",
        "reentry_sharpe_push": "Reentry-side knob sweep aiming Sharpe push.",
        "reentry_sharpe_push_wide": "Wider grid version.",
        "indicator_audit": "Indicator-by-indicator on/off audit.",
        "full": "Full grid (large).",
        "baseline255_ablation": "Ablate each switch from the 255-symbol baseline. 1-knob removal study.",
        "stock_phase2": "Stock phase-2 follow-up.",
        "stock_phase3": "Stock phase-3 follow-up.",
        "stock_phase4": "Stock phase-4 follow-up.",
        "stock_phase5": "Stock phase-5 follow-up.",
        "stock_phase6": "Stock phase-6 follow-up.",
        "stock_phase7": "Stock phase-7 follow-up.",
        "stock_sweep_v1": "Stock sweep v1.",
        "stock_v3": "Stock v3.",
        "stock_v4": "Stock v4.",
        "stock_v5": "Stock v5.",
        "stock_v6": "Stock v6.",
        "stock_v7": "Stock v7.",
        "stock_champion": "Stock champion config validation.",
        "stock_validate": "Stock validation pass.",
        "stock_validate2": "Stock validation pass 2.",
        "mega": "Original mega tier.",
        "mega_v2": "Mega v2.",
        "mega_v3": "Mega v3.",
        "mega_v4": "Mega v4.",
        "mega_v5": "Mega v5.",
        "mega_v6": "Mega v6.",
        "mega_v7": "Mega v7.",
        "mega_stock": "Mega stock variant.",
        "stock_mega": "Stock mega variant.",
        "le_dynamic_tradier_validate": "Validate LE dynamic winners on full universe.",
        "le_dynamic_tradier_v2_validate": "Validate LE v2 winners on full universe.",
        "le_dynamic_tradier_v2_validate_ea": "Validate LE v2 winners + EA early-abort knobs.",
        "le_partial_exit_tradier_validate": "Validate LE+PE tradier winners.",
        "le_partial_exit_crypto_validate": "Validate LE+PE crypto winners.",
    }

    def _opts(arr, sel="wt_dc_full"):
        return "".join(f'<option value="{t}" title="{html.escape(TIER_DESCRIPTIONS.get(t, "(no description — see v8_quick_sweep.py docstring)"))}"' + (' selected' if t == sel else '') + f'>{t}</option>' for t in arr)

    msg_html = f'<div style="background:#3d2c00;border:2px solid #d4a000;padding:8px 12px;margin:10px 0;font-weight:600;color:#ffe082;">{html.escape(msg)}</div>' if msg else ""

    def _status_card(label, host, mode, st):
        ok = "✅" if st["nproc"] >= 3 and st["wrong_mode_n"] == 0 else "❌"
        bad_warn = f'<div style="color:#ff6b6b;font-weight:600;">⚠ WRONG-MODE procs detected: {st["wrong_mode_n"]}</div>' if st["wrong_mode_n"] > 0 else ""
        return f'''<div style="border:2px solid #4caf50;border-radius:6px;padding:12px;margin:8px;background:#1a2330;color:#e8e8e8;flex:1;min-width:340px;">
  <h3 style="margin-top:0;color:#4caf50;" title="Liveness state for {host} ({mode} mode). ✅ = ≥3 procs AND zero wrong-mode procs. ❌ = anything else.">{ok} {label} ({host} / --mode {mode})</h3>
  <div title="Number of v8_quick_sweep processes alive in this mode on this server. Includes parent + workers. Need ≥3 for a healthy sweep (1 parent + 2+ workers)."><b>{st["nproc"]}</b> sweep processes alive (need ≥3)</div>
  {bad_warn}
  <details><summary style="cursor:pointer;color:#aaa;">processes</summary><pre style="font-size:11px;background:#0d1117;padding:6px;overflow-x:auto;color:#9cdcfe;">{html.escape(st["procs"] or "(none)")}</pre></details>
  <div style="margin-top:6px;" title="Most recently modified v8_quick_*.csv result file in /home/niels/binance-sandbox/data/sweep_results/. This is where this server is currently writing results."><b>Newest CSV:</b><br><code style="font-size:11px;color:#9cdcfe;">{html.escape(st["csv"] or "(none)")}</code></div>
  <div title="Row count of the newest CSV. 0 = sweep started but hasn't completed any config yet (wait or investigate). Should grow steadily — refresh to see."><b>Rows:</b> <code style="color:#9cdcfe;">{html.escape(st["csv_rows"] or "(none)")}</code></div>
</div>'''

    crypto_status = _status_card("S1 Crypto", "s1-int", "crypto", s1)
    tradier_status = _status_card("S2 Tradier", "s2-int", "tradier", s2)

    body = f'''
<h2 style="margin-top:0;color:#bb86fc;" title="Vectorized backtests via v8_quick_engine. Each tier is a parameter grid defined in v8_quick_sweep.py. Hover any element for context.">🚀 Launch Sweep — One-click vectorized backtests</h2>
<p style="color:#aaa;font-size:13px;">
  Crypto sweeps run on <b>S1 only</b>. Tradier sweeps run on <b>S2 only</b>. Mode-mismatch is rejected at the launcher.<br>
  Launch button calls the canonical <code>start_{{crypto|tradier}}_sweeps.sh</code> launcher on the target server, which:
  ① kills any wrong-mode procs, ② nohup + disown launches the tier, ③ verifies T+30s liveness before returning.
</p>
{msg_html}

<div style="display:flex;flex-wrap:wrap;gap:8px;margin:16px 0;">
{crypto_status}
{tradier_status}
</div>

<div style="display:flex;flex-wrap:wrap;gap:24px;margin-top:24px;">

  <div style="flex:1;min-width:380px;border:2px solid #2a7;border-radius:8px;padding:18px;background:#0d1f12;color:#e8e8e8;">
    <h3 style="margin-top:0;color:#4caf50;" title="Crypto sweeps run on S1 (157.180.125.52). The launcher refuses if invoked on S2.">🟢 Launch CRYPTO sweep on S1</h3>
    <form method="POST" action="/sweeps/launch" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <input type="hidden" name="mode" value="crypto">
      <select name="tier" title="Hover any option to see what that tier sweeps. Default wt_dc_full = full WT_DC + EXIT_SCORER + DELTA_EXIT, 2304 configs." style="font-size:14px;padding:6px;flex:1;min-width:200px;background:#1a2330;color:#e8e8e8;border:1px solid #4caf50;">
        {_opts(crypto_tiers)}
      </select>
      <button type="submit" title="POST /sweeps/launch with mode=crypto. Routes to S1 via SSH, calls start_crypto_sweeps.sh, verifies liveness for 30s." style="background:#2a7;color:white;font-size:14px;font-weight:600;padding:8px 16px;border:none;border-radius:4px;cursor:pointer;">▶ Launch on S1</button>
    </form>
    <form method="POST" action="/sweeps/stop" style="margin-top:8px;">
      <input type="hidden" name="mode" value="crypto">
      <button type="submit" onclick="return confirm('Kill ALL crypto v8_quick_sweep procs on S1?');" title="pkill -f 'v8_quick_sweep.py.*--mode crypto' on S1. Use to clear stuck/duplicated runs before relaunching." style="background:#c33;color:white;font-size:12px;padding:6px 12px;border:none;border-radius:4px;cursor:pointer;">🛑 Stop all S1 crypto sweeps</button>
    </form>
  </div>

  <div style="flex:1;min-width:380px;border:2px solid #27a;border-radius:8px;padding:18px;background:#0d1424;color:#e8e8e8;">
    <h3 style="margin-top:0;color:#64b5f6;" title="Tradier sweeps run on S2 (204.168.181.211). The launcher refuses if invoked on S1.">🔵 Launch TRADIER sweep on S2</h3>
    <form method="POST" action="/sweeps/launch" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;">
      <input type="hidden" name="mode" value="tradier">
      <select name="tier" title="Hover any option to see what that tier sweeps." style="font-size:14px;padding:6px;flex:1;min-width:200px;background:#1a2330;color:#e8e8e8;border:1px solid #64b5f6;">
        {_opts(tradier_tiers)}
      </select>
      <button type="submit" title="POST /sweeps/launch with mode=tradier. Routes to S2 via SSH, calls start_tradier_sweeps.sh, verifies liveness for 30s." style="background:#27a;color:white;font-size:14px;font-weight:600;padding:8px 16px;border:none;border-radius:4px;cursor:pointer;">▶ Launch on S2</button>
    </form>
    <form method="POST" action="/sweeps/stop" style="margin-top:8px;">
      <input type="hidden" name="mode" value="tradier">
      <button type="submit" onclick="return confirm('Kill ALL tradier v8_quick_sweep procs on S2?');" title="pkill -f 'v8_quick_sweep.py.*--mode tradier' on S2." style="background:#c33;color:white;font-size:12px;padding:6px 12px;border:none;border-radius:4px;cursor:pointer;">🛑 Stop all S2 tradier sweeps</button>
    </form>
  </div>

</div>

<h3 style="margin-top:30px;color:#ffa726;" title="The v8_quick_sweep.py:TIER_MAP dict registers every available tier. Hover items in the dropdowns above for descriptions; click 'show full list' for a flat enumeration.">📋 All available tiers ({len(tiers)} total)</h3>
<details><summary style="cursor:pointer;color:#aaa;">show full list (defined in <code>v8_quick_sweep.py:TIER_MAP</code>)</summary>
<pre style="background:#0d1117;padding:8px;font-size:11px;max-height:200px;overflow:auto;color:#9cdcfe;">{html.escape(", ".join(tiers))}</pre>
</details>

<details style="margin-top:20px;"><summary style="cursor:pointer;color:#aaa;font-weight:600;">📖 Metric definitions (canonical, per CLAUDE.md)</summary>
<table style="background:#0d1117;color:#e8e8e8;font-size:12px;border-collapse:collapse;width:100%;margin-top:6px;">
<tr style="background:#1a2330;"><th style="padding:6px;text-align:left;">Name</th><th style="padding:6px;text-align:left;">Formula</th><th style="padding:6px;text-align:left;">What it tells you</th></tr>
<tr><td style="padding:6px;color:#4caf50;"><b>pool_sharpe</b></td><td style="padding:6px;"><code>mean(all_trade_returns) / std(all_trade_returns)</code> across ALL trades pooled</td><td style="padding:6px;">CANONICAL Sharpe. Per-trade quality. Compare configs by this. Threshold: ≥1.0 to be non-trash.</td></tr>
<tr><td style="padding:6px;color:#bb86fc;"><b>sym_sharpe</b> (aka <code>sharpe</code> in v8 CSV)</td><td style="padding:6px;"><code>mean(per-symbol Sharpes)</code>, capped ±20</td><td style="padding:6px;">DIAGNOSTIC ONLY. Lies when trade counts vary across syms. Use to check consistency, not to rank.</td></tr>
<tr><td style="padding:6px;color:#ff5252;"><b>sharpe_annual</b></td><td style="padding:6px;"><code>sharpe_per_trade × sqrt(trades_per_year)</code></td><td style="padding:6px;">⛔ BANNED. Frequency-gaming. The "frozen 2.52 baseline" lie was sharpe_annual.</td></tr>
<tr><td style="padding:6px;color:#ffa726;"><b>avg_gain_trade</b></td><td style="padding:6px;"><code>acc_gain_pct / trades</code></td><td style="padding:6px;">Per-trade % return. Trade-count neutral. Mandatory in every report.</td></tr>
<tr><td style="padding:6px;color:#ffa726;"><b>gain_per_yr</b></td><td style="padding:6px;"><code>acc_gain_pct / n_years</code></td><td style="padding:6px;">Annual return. Time-window neutral.</td></tr>
<tr><td style="padding:6px;color:#ffa726;"><b>gain_sym_yr</b></td><td style="padding:6px;"><code>acc_gain_pct / n_syms / n_years</code></td><td style="padding:6px;">Cross-machine comparable unit.</td></tr>
<tr><td style="padding:6px;"><b>max_dd_pct</b></td><td style="padding:6px;">Peak-to-trough equity DD as % of starting capital</td><td style="padding:6px;">Mandatory in every summary. Worst-the-account-ever-looked.</td></tr>
<tr><td style="padding:6px;"><b>min sample</b></td><td style="padding:6px;">≥48 syms (crypto) / ≥100 syms (tradier), >1yr, ≥30 trades/sym</td><td style="padding:6px;">Below this floor, results are NOT decision-material — diagnostic only.</td></tr>
</table></details>

<p style="color:#888;font-size:12px;margin-top:24px;">
  Page auto-refreshes every 30s. Equivalent shell commands:<br>
  <code style="color:#9cdcfe;">ssh s1-int 'bash /home/niels/binance-sandbox/start_crypto_sweeps.sh status'</code><br>
  <code style="color:#9cdcfe;">ssh s2-int 'bash /home/niels/binance-sandbox/start_tradier_sweeps.sh status'</code>
</p>
<meta http-equiv="refresh" content="30">
'''
    return render_page(body, title="Launch Sweep", active_nav="🚀 Sweeps")


@app.route("/sweeps/launch", methods=["POST"])
def sweeps_launch():
    tier = (request.form.get("tier", "") or "").strip()
    mode = (request.form.get("mode", "") or "").strip()
    if not tier:
        return redirect("/sweeps?msg=Error:+tier+is+required")
    if mode not in ("crypto", "tradier"):
        return redirect("/sweeps?msg=Error:+mode+must+be+crypto+or+tradier")
    if mode == "crypto":
        host = "s1-int"
        cmd = f"bash /home/niels/binance-sandbox/start_crypto_sweeps.sh {tier}"
    else:
        host = "s2-int"
        cmd = f"bash /home/niels/binance-sandbox/start_tradier_sweeps.sh {tier}"
    try:
        out = _ssh_run(host, None, cmd, timeout=90) or ""
    except subprocess.TimeoutExpired:
        return redirect(f"/sweeps?msg=Timeout+launching+{tier}+on+{host}+(launcher+may+still+be+verifying;+check+status)")
    snippet = out.splitlines()[-3:] if out else ["(no output)"]
    msg = f"Launched {tier} on {host}: " + " | ".join(snippet)[:200]
    return redirect("/sweeps?msg=" + msg.replace(" ", "+").replace("&", "%26"))


@app.route("/sweeps/stop", methods=["POST"])
def sweeps_stop():
    mode = (request.form.get("mode", "") or "").strip()
    if mode == "crypto":
        host = "s1-int"
    elif mode == "tradier":
        host = "s2-int"
    else:
        return redirect("/sweeps?msg=Error:+invalid+mode")
    cmd = f"pkill -f 'v8_quick_sweep.py.*--mode {mode}' ; sleep 1 ; pgrep -afc 'v8_quick_sweep.*--mode {mode}'"
    out = _ssh_run(host, None, cmd, timeout=10) or "0"
    return redirect(f"/sweeps?msg=Stopped+{mode}+sweeps+on+{host}+(remaining+procs:+{out[:5]})")


# ---------------------------------------------------------------------------
# /autonomous — autonomous_search.csv viewer with CANONICAL_METRICS only.
# ---------------------------------------------------------------------------
@app.route("/autonomous")
def autonomous_page():
    """Show every autonomous_search CSV with canonical metrics — only pool_sharpe + sym_sharpe.
    Cross-machine: globs MB local + S1/S2 swarm cache. Filterable by reliable=1."""
    import pandas as _pd

    show_unreliable = request.args.get("show_unreliable", "0") == "1"
    min_pool = float(request.args.get("min_pool", "0") or 0)

    # CANONICAL columns (per CANONICAL_METRICS.md). Anything else is dropped from view.
    KEEP = ["iter", "pool_sharpe", "sym_sharpe", "acc_gain_pct", "avg_gain_trade",
            "gain_per_yr", "gain_sym_yr", "max_dd_pct", "trades", "wins", "losses",
            "wr", "n_syms", "n_years", "start_date", "gain_vs_bh", "elapsed_s",
            "overrides_count", "reliable", "useless"]

    SOURCES = [
        ("Local (MacBook)", os.path.join(BASE_DIR, "data", "autonomous", "**", "autonomous_*.csv")),
        ("S1 cache (crypto)", os.path.join(BASE_DIR, "data", "swarm_cache", "s1", "autonomous", "**", "autonomous_*.csv")),
        ("S2 cache (tradier)", os.path.join(BASE_DIR, "data", "swarm_cache", "s2", "autonomous", "**", "autonomous_*.csv")),
    ]

    sections = []
    overall_best_pool = 0.0
    overall_best_loc = ""

    for label, pattern in SOURCES:
        files = sorted(glob.glob(pattern, recursive=True))
        if not files:
            continue
        rows = []
        for f in files:
            try:
                df = _pd.read_csv(f)
            except Exception:
                continue
            # Skip files lacking the canonical columns — they're old-format (had deflated_sharpe etc).
            if "pool_sharpe" not in df.columns:
                continue
            df["_run"] = os.path.relpath(f, BASE_DIR)
            try:
                df["_age_min"] = int((time.time() - os.path.getmtime(f)) / 60)
            except Exception:
                df["_age_min"] = -1
            rows.append(df)
        if not rows:
            continue
        combined = _pd.concat(rows, ignore_index=True)
        combined["pool_sharpe"] = _pd.to_numeric(combined["pool_sharpe"], errors="coerce").fillna(0)
        combined = combined[combined["pool_sharpe"] >= min_pool]
        if not show_unreliable and "reliable" in combined.columns:
            combined["reliable"] = _pd.to_numeric(combined["reliable"], errors="coerce").fillna(0).astype(int)
            combined = combined[combined["reliable"] == 1]
        if combined.empty:
            continue
        # Top 10 by pool_sharpe (canonical ranking)
        top = combined.nlargest(10, "pool_sharpe").copy()
        for c in KEEP:
            if c not in top.columns:
                top[c] = "—"
        top_b = combined["pool_sharpe"].max()
        if top_b > overall_best_pool:
            overall_best_pool = top_b
            overall_best_loc = label

        def _sf(v, default=0.0):
            try: return float(v)
            except (ValueError, TypeError): return default
        def _si(v, default=0):
            try: return int(float(v))
            except (ValueError, TypeError): return default
        rows_html = ""
        for _, r in top.iterrows():
            run_short = str(r.get("_run", ""))[-60:]
            useless_tag = ' <span style="color:#ff9800;" title="useless=1: reliable but pool_sharpe below floor">⚠</span>' if r.get("useless") in (1, "1", 1.0) else ""
            rows_html += (
                f'<tr style="border-bottom:1px solid #2a2a2a;">'
                f'<td title="pool_sharpe — canonical Sharpe per CANONICAL_METRICS.md" style="color:#4caf50;font-weight:600;padding:4px 6px;">{_sf(r["pool_sharpe"]):.4f}{useless_tag}</td>'
                f'<td title="sym_sharpe — diagnostic per-sym avg, capped ±20" style="color:#bb86fc;padding:4px 6px;">{_sf(r.get("sym_sharpe",0)):.3f}</td>'
                f'<td title="acc_gain_pct — sum of all per-trade %-returns" style="padding:4px 6px;">{_sf(r.get("acc_gain_pct",0)):.0f}%</td>'
                f'<td title="avg_gain_trade — acc_gain_pct/trades" style="padding:4px 6px;">{_sf(r.get("avg_gain_trade",0)):.3f}%</td>'
                f'<td title="gain_per_yr — annual return" style="padding:4px 6px;">{_sf(r.get("gain_per_yr",0)):.0f}%</td>'
                f'<td title="gain_sym_yr — cross-machine comparable" style="padding:4px 6px;">{_sf(r.get("gain_sym_yr",0)):.2f}%</td>'
                f'<td title="max_dd_pct — peak-to-trough" style="padding:4px 6px;">{_sf(r.get("max_dd_pct",0)):.1f}%</td>'
                f'<td title="wr — win rate" style="padding:4px 6px;">{_sf(r.get("wr",0)):.1f}%</td>'
                f'<td title="trades count" style="padding:4px 6px;">{_si(r.get("trades",0))}</td>'
                f'<td title="n_syms — distinct syms with trades" style="padding:4px 6px;">{_si(r.get("n_syms",0))}</td>'
                f'<td title="n_years — test window" style="padding:4px 6px;">{_sf(r.get("n_years",0)):.1f}y</td>'
                f'<td title="iteration index" style="padding:4px 6px;color:#888;">i={_si(r.get("iter",0))}</td>'
                f'<td style="font-size:10px;color:#888;padding:4px 6px;" title="{run_short}">{r.get("_age_min",-1)}m</td>'
                f'</tr>'
            )
        n_total = len(combined)
        n_files = len(files)
        n_with_canonical = sum(1 for r in rows if not r.empty)
        sections.append(
            f'<div style="margin-bottom:18px;">'
            f'<h3 style="color:#64b5f6;margin-bottom:4px;">{label} — {n_total} configs (top 10 by pool_sharpe), {n_files} CSV files</h3>'
            f'<div style="font-size:11px;color:#888;margin-bottom:6px;">Files with canonical columns: {n_with_canonical}/{n_files}. Older files lacking pool_sharpe column are skipped (regen with patched autonomous_search.py to populate).</div>'
            f'<table style="background:#0d1117;color:#e8e8e8;font-size:11px;border-collapse:collapse;width:100%;">'
            f'<tr style="background:#1a2330;color:#fff;">'
            f'<th style="padding:6px;text-align:left;" title="pool_sharpe = mean(all trade returns) / std. Canonical Sharpe.">pool</th>'
            f'<th style="padding:6px;text-align:left;" title="sym_sharpe = mean(per-sym Sharpes), capped ±20. Diagnostic only.">sym</th>'
            f'<th style="padding:6px;text-align:left;">Gain%</th>'
            f'<th style="padding:6px;text-align:left;">/trade%</th>'
            f'<th style="padding:6px;text-align:left;">/yr%</th>'
            f'<th style="padding:6px;text-align:left;">/sym/yr%</th>'
            f'<th style="padding:6px;text-align:left;">DD%</th>'
            f'<th style="padding:6px;text-align:left;">WR</th>'
            f'<th style="padding:6px;text-align:left;">Trades</th>'
            f'<th style="padding:6px;text-align:left;">Syms</th>'
            f'<th style="padding:6px;text-align:left;">Span</th>'
            f'<th style="padding:6px;text-align:left;">Iter</th>'
            f'<th style="padding:6px;text-align:left;">Age</th>'
            f'</tr>'
            f'{rows_html}</table></div>'
        )

    if not sections:
        body_inner = '<p style="color:#aaa;">No autonomous_search CSVs found with canonical columns. Old-format CSVs (with deflated_sharpe/psr columns) are skipped — they need to be regenerated by autonomous_search.py with the new column set, or you can re-launch the autonomous search and the new run will populate canonical columns from iter=-1 onward.</p>'
    else:
        body_inner = "".join(sections)

    filter_form = f'''<form method="GET" action="/autonomous" style="background:#1a2330;padding:10px;margin-bottom:16px;border-radius:6px;display:flex;gap:12px;align-items:center;flex-wrap:wrap;">
  <label title="Hide reliable=0 rows (configs that didn't meet the per-sym trade-floor)"><input type="checkbox" name="show_unreliable" value="1" {'checked' if show_unreliable else ''}> show unreliable</label>
  <label title="Filter to pool_sharpe >= this. Default 0 = show all.">min pool_sharpe: <input type="number" step="0.1" name="min_pool" value="{min_pool}" style="background:#0d1117;color:#e8e8e8;border:1px solid #444;padding:4px;width:80px;"></label>
  <button type="submit" style="background:#27a;color:white;padding:6px 12px;border:none;border-radius:4px;cursor:pointer;">apply</button>
</form>'''

    body = f'''
<h2 style="margin-top:0;color:#bb86fc;" title="Autonomous search results — random+perturbation walk over QuickConfig knobs. Top configs by canonical pool_sharpe.">🤖 Autonomous Search Results</h2>
<p style="color:#aaa;font-size:13px;">
  Per-iteration vector A/B walk over QuickConfig knobs. Reranked by <b>pool_sharpe</b> (CANONICAL_METRICS.md).<br>
  <b style="color:#ff9800;">Reliable filter is ON by default</b> — configs with trades < floor are hidden. Toggle below to inspect noise.<br>
  Best pool_sharpe across all sources: <span style="color:#4caf50;font-weight:600;font-size:18px;">{overall_best_pool:.4f}</span> {f'<span style="color:#888;">in {overall_best_loc}</span>' if overall_best_loc else ''}
</p>
{filter_form}
{body_inner}

<details style="margin-top:30px;"><summary style="cursor:pointer;color:#aaa;">📖 What you're looking at</summary>
<p style="color:#ccc;font-size:12px;margin-top:6px;">
Each row is one autonomous-search iteration. The engine applied a random subset of QuickConfig overrides and ran a vectorized v8_quick simulate.
The CSV columns shown are the CANONICAL set per <code>CANONICAL_METRICS.md</code> — only the 2 canonical Sharpes (pool + sym).
Banned values (deflated_sharpe, psr, sharpe_annual, sharpe_weekly) have been removed from the writer.
Click the column headers' tooltip-icons to see formulas.
</p>
</details>
<meta http-equiv="refresh" content="60">
'''
    return render_page(body, title="Autonomous Search", active_nav="🤖 Autonomous")


@app.route("/swarm")
def swarm_page():
    """Live view of autonomous swarm + funnel results from all 3 machines."""
    import pandas as _pd

    # S1 local cache dir — populated by periodic rsync (server is SSH-session-limited when busy).
    # Falls back to live SSH if cache doesn't exist.
    S1_CACHE = os.path.join(BASE_DIR, "data", "swarm_cache", "s1")
    S2_CACHE = os.path.join(BASE_DIR, "data", "swarm_cache", "s2")
    MACHINES = [
        {"name": "Local (MacBook)", "host": None, "base": BASE_DIR},
        {"name": "S1 (Crypto)", "host": "s1-int",
         "base": S1_CACHE if os.path.isdir(S1_CACHE) else None,
         "remote_base": "/home/niels/binance-sandbox"},
        {"name": "S2 (Tradier)", "host": "s2-int",
         "base": S2_CACHE if os.path.isdir(S2_CACHE) else None,
         "remote_base": "/home/niels/binance-sandbox"},
    ]

    def _read_csvs_local(pattern):
        rows = []
        for f in sorted(glob.glob(pattern)):
            try:
                age_s = int(time.time() - os.path.getmtime(f))
                df = _pd.read_csv(f)
                df["_source_file"] = os.path.basename(os.path.dirname(f)) + "/" + os.path.basename(f)
                df["_age_s"] = age_s
                rows.append(df)
            except Exception:
                pass
        return _pd.concat(rows, ignore_index=True) if rows else _pd.DataFrame()

    def _read_csvs_remote(host, pattern):
        try:
            import io as _io, tempfile as _tf, os as _os
            script = "\n".join([
                "import glob,pandas as pd",
                "COLS=['pool_sharpe','acc_gain_pct','max_dd_pct','trades','symbols_used','stage1_sharpe']",
                "pieces=[]",
                f"for f in sorted(glob.glob('{pattern}'))[:50]:",
                "    try:",
                "        df=pd.read_csv(f,usecols=lambda c:c in COLS)",
                "        df['_source_file']='/'.join(f.split('/')[-2:])",
                "        pieces.append(df)",
                "    except: pass",
                "if pieces:",
                "    print(pd.concat(pieces,ignore_index=True).to_csv(index=False))",
            ])
            script_file = f"/tmp/_swarm_fetch_{host}.py"
            _ssh_run(host, None, f"cat > {script_file} << 'PYEOF'\n{script}\nPYEOF", timeout=5)
            raw = _ssh_run(host, None, f"python3 {script_file} 2>/dev/null", timeout=20)
            if raw.strip():
                return _pd.read_csv(_io.StringIO(raw))
        except Exception:
            pass
        return _pd.DataFrame()

    sections = []
    all_autonomous = []
    all_funnel = []

    for m in MACHINES:
        host = m["host"]
        base = m["base"]

        # If base is a local cache dir (for busy servers), read locally.
        # If base is None (remote, no cache), fall back to SSH.
        use_local = (host is None) or (base is not None and os.path.isdir(base))
        eff_base = base if use_local else m.get("remote_base", base)

        auto_pat = f"{eff_base}/data/autonomous/*/w*/autonomous_*.csv"
        funnel_pat = f"{eff_base}/data/funnel_validated/**/*.csv"
        stage2_pat = f"{eff_base}/data/stage2_validated/**/*.csv"

        if use_local:
            auto_df = _read_csvs_local(auto_pat)
            funnel_df = _read_csvs_local(funnel_pat)
            s2_df = _read_csvs_local(stage2_pat)
        else:
            auto_df = _cached(f"swarm_auto_{host}", lambda h=host, p=auto_pat: _read_csvs_remote(h, p))
            funnel_df = _cached(f"swarm_funnel_{host}", lambda h=host, p=funnel_pat: _read_csvs_remote(h, p))
            s2_df = _cached(f"swarm_s2_{host}", lambda h=host, p=stage2_pat: _read_csvs_remote(h, p))
            if isinstance(auto_df, dict): auto_df = _pd.DataFrame()
            if isinstance(funnel_df, dict): funnel_df = _pd.DataFrame()
            if isinstance(s2_df, dict): s2_df = _pd.DataFrame()

        def _fmt_df(df, label, valid_floor_syms=None):
            if df is None or (isinstance(df, _pd.DataFrame) and df.empty):
                return f'<div style="color:#888">{label}: no data</div>'
            df = df.copy()
            for col in ["pool_sharpe", "acc_gain_pct", "max_dd_pct", "trades", "symbols_used"]:
                if col in df.columns:
                    df[col] = _pd.to_numeric(df[col], errors="coerce")
            if "pool_sharpe" not in df.columns:
                return f'<div style="color:#888">{label}: no pool_sharpe column</div>'
            df = df.dropna(subset=["pool_sharpe"])
            df = df[df["pool_sharpe"] > 0]
            if valid_floor_syms and "symbols_used" in df.columns:
                valid = df[df["symbols_used"] >= valid_floor_syms]
                invalid = df[df["symbols_used"] < valid_floor_syms]
                valid_note = f' ({len(valid)} valid ≥{valid_floor_syms} syms, {len(invalid)} stage-1 screening only)'
                df_show = valid if not valid.empty else df
            else:
                valid_note = ""
                df_show = df
            if df_show.empty:
                return f'<div style="color:#888">{label}: 0 qualifying rows{valid_note}</div>'
            df_show = df_show.sort_values("pool_sharpe", ascending=False)
            top = df_show.head(10)
            age_s = int(df["_age_s"].min()) if "_age_s" in df.columns else -1
            age_str = f"{age_s//60}m{age_s%60}s ago" if age_s >= 0 else "?"
            color = "#4caf50" if age_s < 300 else ("#ffaa00" if age_s < 1800 else "#f44336")
            rows_html = ""
            for _, r in top.iterrows():
                sharpe = r.get("pool_sharpe", 0)
                gain = r.get("acc_gain_pct", r.get("stage1_gain", 0)) or 0
                dd = r.get("max_dd_pct", 0) or 0
                tr = int(r.get("trades", 0) or 0)
                syms = int(r.get("symbols_used", r.get("symbols", 0)) or 0)
                src = r.get("_source_file", "")
                s1s = r.get("stage1_sharpe", "")
                s1_str = f' (s1={float(s1s):.3f})' if s1s and str(s1s) != "nan" else ""
                sc = "#4caf50" if sharpe >= 0.5 else ("#ffaa00" if sharpe >= 0.25 else "#e0e0e0")
                rows_html += (f'<tr><td style="color:{sc}">{sharpe:.4f}{s1_str}</td>'
                              f'<td>{gain:.0f}%</td><td>{dd:.1f}%</td><td>{tr}</td>'
                              f'<td>{syms}</td><td style="font-size:10px;color:#888">{src}</td></tr>')
            return (f'<div style="margin-bottom:12px">'
                    f'<b>{label}</b> — {len(df_show)} rows, best={df_show["pool_sharpe"].max():.4f} '
                    f'<span style="color:{color}">updated {age_str}</span>{valid_note}'
                    f'<table style="width:100%;font-size:12px;margin-top:4px">'
                    f'<tr><th>Sharpe</th><th>Gain%</th><th>DD%</th><th>Trades</th><th>Syms</th><th>Source</th></tr>'
                    f'{rows_html}</table></div>')

        auto_section = _fmt_df(auto_df, "Stage-1 autonomous")
        funnel_section = _fmt_df(funnel_df, "Funnel validated", valid_floor_syms=12)
        s2_section = _fmt_df(s2_df, "Stage-2 validated", valid_floor_syms=48)

        sections.append(f'<div class="card"><div class="card-header"><span class="card-title">{m["name"]}</span></div>'
                        f'{auto_section}{funnel_section}{s2_section}</div>')

        if not auto_df.empty and "pool_sharpe" in auto_df.columns:
            auto_df["_machine"] = m["name"]
            all_autonomous.append(auto_df)
        if not funnel_df.empty and "pool_sharpe" in funnel_df.columns:
            funnel_df["_machine"] = m["name"]
            all_funnel.append(funnel_df)

    # Global best across all machines
    summary_html = ""
    for label, frames, floor in [("Stage-1 (all machines)", all_autonomous, None),
                                   ("Funnel validated (all machines)", all_funnel, 12)]:
        if not frames:
            continue
        combined = _pd.concat(frames, ignore_index=True)
        combined["pool_sharpe"] = _pd.to_numeric(combined["pool_sharpe"], errors="coerce")
        combined = combined.dropna(subset=["pool_sharpe"])
        if floor and "symbols_used" in combined.columns:
            combined["symbols_used"] = _pd.to_numeric(combined["symbols_used"], errors="coerce")
            combined = combined[combined["symbols_used"] >= floor]
        combined = combined.sort_values("pool_sharpe", ascending=False)
        if combined.empty:
            continue
        top5 = combined.head(5)
        rows_h = ""
        for _, r in top5.iterrows():
            sharpe = r.get("pool_sharpe", 0)
            gain = r.get("acc_gain_pct", 0) or 0
            dd = r.get("max_dd_pct", 0) or 0
            tr = int(r.get("trades", 0) or 0)
            syms = int(r.get("symbols_used", 0) or 0)
            mch = r.get("_machine", "?")
            sc = "#4caf50" if sharpe >= 0.5 else ("#ffaa00" if sharpe >= 0.25 else "#e0e0e0")
            rows_h += (f'<tr><td style="color:{sc}">{sharpe:.4f}</td><td>{gain:.0f}%</td>'
                       f'<td>{dd:.1f}%</td><td>{tr}</td><td>{syms}</td><td>{mch}</td></tr>')
        summary_html += (f'<h3>{label} — Top 5</h3>'
                         f'<table style="width:100%;font-size:12px">'
                         f'<tr><th>Sharpe</th><th>Gain%</th><th>DD%</th><th>Trades</th><th>Syms</th><th>Machine</th></tr>'
                         f'{rows_h}</table>')

    body = f"""
<h2>🔬 Swarm + Funnel — Live Results</h2>
<p style="color:#888">Reads <code>data/autonomous/*/w*/autonomous_*.csv</code> + <code>data/funnel_validated/**/*.csv</code> + <code>data/stage2_validated/**/*.csv</code> from all machines. Auto-refresh 30s.</p>
<p style="color:#ff9800">⚠️ Stage-1 results (small sym count) are screening only — NOT valid per floor rules. Funnel/Stage-2 ≥48 crypto / ≥100 tradier are the real numbers.</p>
{summary_html}
<div class="grid">{''.join(sections)}</div>
"""
    return render_page(body, title="Swarm Results", active_nav="🔬 Swarm")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import subprocess, os as _os, time as _time
    def _kill_port(port):
        try:
            result = subprocess.run(["lsof", "-t", f"-i:{port}"], capture_output=True, text=True)
            for pid_str in result.stdout.strip().split("\n"):
                pid_str = pid_str.strip()
                if pid_str and pid_str.isdigit() and int(pid_str) != _os.getpid():
                    _os.kill(int(pid_str), 9)
            _time.sleep(0.5)
        except Exception:
            pass
    _kill_port(5051)
    from werkzeug.serving import BaseWSGIServer
    BaseWSGIServer.allow_reuse_address = True
    print("Sweep Cockpit starting on http://localhost:5051")
    app.run(host="0.0.0.0", port=5051, debug=False)
