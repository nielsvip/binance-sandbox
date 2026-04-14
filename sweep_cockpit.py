# pylint: disable=W,C,R,I
#!/usr/bin/env python3
"""Sweep Cockpit — Real-time V8 backtest sweep dashboard on port 5051."""
import csv
import glob
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
        "host": "157.180.125.52",
        "user": "niels",
        "sweep_dir": "/home/niels/binance-sandbox/backtest_v8/sweeps",
        "log_path": "/tmp/v8_t25.log",
        "is_local": False,
    },
    {
        "name": "S2",
        "host": "204.168.181.211",
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
    full = ["ssh", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=no", f"{user}@{host}", cmd]
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
                r = subprocess.run(["bash", "-c", "ps aux | grep backtest_v8_sweep | grep -v grep | wc -l"], capture_output=True, text=True, timeout=5)
                return int(r.stdout.strip()) > 0
            except Exception:
                return False
        else:
            try:
                out = _ssh_run(server["host"], server["user"], "ps aux | grep backtest_v8_sweep | grep -v grep | wc -l")
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
    ("Live Stocks", "/live"),
    ("Live Crypto", "/live/crypto"),
    ("Symbols Stocks", "/symbols"),
    ("Symbols Crypto", "/symbols/crypto"),
    ("Params Stocks", "/params"),
    ("Params Crypto", "/params/crypto"),
    ("History", "/history"),
    ("Trades", "http://localhost:5050/feed"),
    ("Monitor", "/monitor"),
]


def render_page(body, title="Sweep Cockpit", active_nav="Home"):
    nav_html = ""
    for label, href in NAV_ITEMS:
        cls = ' class="active"' if label == active_nav else ""
        nav_html += f'<a href="{href}"{cls}>{label}</a>'
    return f"""<!DOCTYPE html>
<html><head>
<meta charset="utf-8">
<meta http-equiv="refresh" content="30">
<title>{title}</title>
<style>{STYLE}</style>
</head><body>
<div class="refresh-bar"></div>
<div class="navbar">{nav_html}</div>
<h1>V8 Sweep Cockpit</h1>
<div class="subtitle">Auto-refresh 30s | {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}</div>
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

    # If no sweep data, show notice with links to active pages
    no_sweep_notice = ""
    if total_done == 0:
        no_sweep_notice = """
        <div style="background:#1a3a5c; border:1px solid #00d4ff; border-radius:8px; padding:20px; margin-bottom:20px; text-align:center">
            <h3 style="color:#00d4ff; margin:0 0 10px 0">No active sweeps — running single confirmation runs</h3>
            <p style="color:#aaa">Use the tabs above to monitor current runs:</p>
            <p>
                <a href="/live" style="font-size:16px; margin:0 10px">Live Stocks</a>
                <a href="/live/crypto" style="font-size:16px; margin:0 10px">Live Crypto</a>
                <a href="/symbols" style="font-size:16px; margin:0 10px">Per-Symbol Results</a>
                <a href="/history" style="font-size:16px; margin:0 10px">History</a>
                <a href="/monitor" style="font-size:16px; margin:0 10px; color:#ff9800">Monitor Agent</a>
            </p>
        </div>
        """

    body = f"""
    {no_sweep_notice}
    <div class="grid">
        {''.join(server_sections)}
    </div>

    <h2>Aggregate ({total_done} configs total)</h2>
    <div style="margin-bottom:15px">
        <span class="metric"><span class="metric-label">Total Configs</span><br><span class="metric-value">{total_done}</span></span>
        <span class="metric"><span class="metric-label">Sharpe Range</span><br><span class="metric-value">{s_min:.3f} — {s_max:.3f}</span></span>
        <span class="metric"><span class="metric-label">Sharpe Mean</span><br><span class="metric-value">{s_mean:.3f}</span></span>
        <span class="metric"><span class="metric-label">Sharpe StDev</span><br><span class="metric-value">{s_stdev:.3f}</span></span>
        <span class="metric"><span class="metric-label">Differentiation</span><br><span class="metric-value" style="color:{diff_color}">{diff_verdict}</span></span>
    </div>

    <h3>TOP 10 by Sharpe</h3>
    {render_ranked_table(top10, "top")}

    <h3>BOTTOM 5 by Sharpe</h3>
    {render_ranked_table(bottom5, "bottom")}

    <h3>Sharpe Distribution</h3>
    <div class="hist-box">{histogram}</div>
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
    {"name": "S1", "host": "157.180.125.52", "user": "niels"},
    {"name": "S2", "host": "204.168.181.211", "user": "niels"},
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
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    print("Sweep Cockpit starting on http://localhost:5051")
    app.run(host="0.0.0.0", port=5051, debug=False)
