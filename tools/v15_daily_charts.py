#!/usr/bin/env python3
"""Daily 30D chart refresh of every published V15 board (USER 2026-10-30: 20:00 UTC daily, overwrite SPREADSHEETS/charts).

Per sym_side, the LATEST published 30D board in SPREADSHEETS/V15_V16_CELL_BY_CELL (max manifest published_utc) is the source:
  set   = the board HTML's own "SET THAT PRODUCED THIS CHART" block, else an S1/S5 store row (published_selection.json or a
          run progress JSON) — accepted ONLY when md5(set) == the board manifest's overrides_md5. Nothing else is substituted.
  calc  = tools/v15_daily_chart_worker.py render on S1/S5 (their NPZs, their engine) -> tools.opt.hires_chart.generate_hires.
  then  = a compact ORIGINAL-vs-TODAY block (same chart parser on both files) + original cell calc time (manifest cells_vintage)
          and its age, inserted under the heading; chart_truth.verify_html re-checks the final file before it is written.
Output: SPREADSHEETS/charts/<SYM_SIDE>_30d_daily.html (overwritten), index.html, daily_charts_manifest.json, plus
data/reports/daily_charts_history.jsonl. A sym_side whose calc or provenance failed gets a FAIL page, never an old chart.

    python tools/v15_daily_charts.py --now            # run immediately
    python tools/v15_daily_charts.py --scheduled      # launchd hourly tick: runs once per 20:00 UTC slot (catch-up safe)
    python tools/v15_daily_charts.py --now --symside SAP_LONG
"""
import argparse
import bisect
import datetime
import fcntl
import glob
import hashlib
import html as _html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from v15_daily_chart_worker import parse_chart_metrics, set_md5  # noqa: E402

CELL = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
CHARTS = ROOT / "SPREADSHEETS" / "charts"
STAGE = ROOT / "data" / "reports" / "daily_charts_staging"
HISTORY = ROOT / "data" / "reports" / "daily_charts_history.jsonl"
STATE = ROOT / "data" / "reports" / "daily_charts_state.json"
LOCK = ROOT / "data" / "reports" / "daily_charts.lock"
HOSTS = {"s1": "~/binance-sandbox", "s5": "~/binance-sandbox"}
WORKER_PY = "~/.conda/envs/binance_env_mac/bin/python"
SLOT_HOUR_UTC = 20
SSH = ["ssh", "-o", "ConnectTimeout=10", "-o", "ServerAliveInterval=30", "-o", "BatchMode=yes"]


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.timezone.utc)


def iso(dt: datetime.datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(text):
    try:
        return datetime.datetime.strptime(str(text)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=datetime.timezone.utc)
    except Exception:
        return None


def age_text(then, now) -> str:
    if then is None:
        return "UNAVAILABLE"
    secs = max(0, int((now - then).total_seconds()))
    days, rem = divmod(secs, 86400)
    return f"{days}d {rem // 3600}h {rem % 3600 // 60}m" if days else f"{rem // 3600}h {rem % 3600 // 60}m"


def discover(only=None) -> dict:
    """sym_side -> latest published 30D board (by manifest published_utc), with its set when provable from the HTML."""
    best = {}
    for mp in glob.glob(str(CELL / "*_30d_matrix_manifest.json")):
        try:
            man = json.loads(Path(mp).read_text())
        except Exception:
            continue
        symside = str(man.get("symside") or "").upper()
        if not symside or (only and symside not in only):
            continue
        key = man.get("published_utc") or ""
        if symside not in best or key > best[symside]["published_utc"]:
            best[symside] = {"symside": symside, "published_utc": key, "manifest": mp, "man": man}
    for symside, board in best.items():
        man = board.pop("man")
        board["board_html"] = board["manifest"].replace("_manifest.json", ".html")
        board["overrides_md5"] = man.get("overrides_md5")
        board["cells_vintage"] = man.get("cells_vintage")
        board["board_host"] = man.get("host")
        board["publish_class"] = man.get("publish_class")
        board["manifest_metrics"] = man.get("metrics") or {}
        board["board_npz_md5"] = man.get("npz_md5")
        board["window_days"] = man.get("window_days")
        if not board["published_utc"]:
            board["error"] = "manifest has no published_utc"
            continue
        if not board["overrides_md5"]:
            board["error"] = "manifest has no overrides_md5 — set cannot be proven"
            continue
        if board["window_days"] not in (None, 30):
            board["error"] = f"manifest window_days={board['window_days']} is not 30"
            continue
        if not os.path.exists(board["board_html"]):
            board["error"] = "board HTML missing"
            continue
        text = Path(board["board_html"]).read_text(encoding="utf-8", errors="replace")
        board["orig_metrics"] = parse_chart_metrics(text)
        gen = re.search(r"generated (\d{4}-\d\d-\d\d \d\d:\d\dZ) on ([\w.-]+)", text)
        board["board_chart_generated"] = f"{gen.group(1)} on {gen.group(2)}" if gen else None
        block = re.search(r"set_md5 <code>([0-9a-f]+)</code>.*?<pre[^>]*>(.*?)</pre>", text, re.S)
        if block:
            try:
                ov = json.loads(_html.unescape(block.group(2)))
            except Exception:
                board["error"] = "board HTML set block unparseable"
                continue
            if set_md5(ov) != block.group(1) or set_md5(ov) != board["overrides_md5"]:
                board["error"] = f"board HTML set md5 {set_md5(ov)} != manifest {board['overrides_md5']}"
                continue
            board["overrides"] = ov
            board["set_source"] = "board HTML set block (md5 = manifest overrides_md5)"
    return best


def ssh(host: str, cmd: str, timeout: int = 600, stdin=None, tries: int = 3) -> subprocess.CompletedProcess:
    """Same mesh path every attempt (CLAUDE.md connection rule); the last error is raised verbatim."""
    err = None
    for attempt in range(tries):
        try:
            r = subprocess.run(SSH + [host, cmd], capture_output=True, text=True, timeout=timeout, input=stdin)
            if r.returncode != 255:
                return r
            err = RuntimeError(f"host {host} did not answer: {r.stderr.strip()[-300:]}")
        except subprocess.TimeoutExpired as exc:
            err = RuntimeError(f"host {host} did not answer within {timeout}s: {exc}")
        time.sleep(5 * (attempt + 1))
    raise err


def fetch(host: str, remote: str, local: Path, timeout: int = 1800, tries: int = 3) -> None:
    for attempt in range(tries):
        r = subprocess.run(["rsync", "-az", f"{host}:{remote}", str(local)], capture_output=True, text=True, timeout=timeout)
        if r.returncode == 0:
            return
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"host {host} did not answer (rsync {remote}): {r.stderr.strip()[-300:]}")


def resolve_remote(boards: dict) -> None:
    need = {s: b["overrides_md5"] for s, b in boards.items() if "overrides" not in b and "error" not in b}
    if not need:
        return
    for host, root in HOSTS.items():
        need = {s: m for s, m in need.items() if "overrides" not in boards[s]}
        if not need:
            break
        try:
            ssh(host, "cat > /tmp/dc_need.json", 300, json.dumps(need))
            ssh(host, f"cd {root} && python3 tools/v15_daily_chart_worker.py resolve /tmp/dc_need.json > /tmp/dc_found.json", 900)
            local = STAGE / f"found_{host}.json"
            local.parent.mkdir(parents=True, exist_ok=True)
            fetch(host, "/tmp/dc_found.json", local, 600)
            found = json.loads(local.read_text() or "{}")
        except Exception as exc:
            print(f"[daily-charts] resolve on {host} failed: {exc}", flush=True)
            continue
        for symside, row in found.items():
            if set_md5(row["overrides"]) == boards[symside]["overrides_md5"]:
                boards[symside]["overrides"] = row["overrides"]
                boards[symside]["set_source"] = row["source"] + " (md5 = manifest overrides_md5)"
    for symside, board in boards.items():
        if "overrides" not in board and "error" not in board:
            board["error"] = f"set md5 {board['overrides_md5']} not found in board HTML, S1/S5 published_selection or progress JSONs"


def npz_inventory(host: str, root: str) -> set:
    try:
        r = ssh(host, f"ls {root}/backtest_v8/indicators/", 300)
    except Exception as exc:
        print(f"[daily-charts] NPZ inventory: {exc}", flush=True)
        return set()
    return {n[:-4] for n in r.stdout.split() if n.endswith(".npz")} if r.returncode == 0 else set()


def deploy_worker() -> None:
    """Every host must run the exact worker this Mac holds; a host that cannot be verified gets no jobs."""
    src = ROOT / "tools" / "v15_daily_chart_worker.py"
    want = hashlib.md5(src.read_bytes()).hexdigest()
    for host in list(HOSTS):
        got = ""
        for _ in range(3):
            try:
                ssh(host, f"cat > {HOSTS[host]}/tools/v15_daily_chart_worker.py.tmp && mv {HOSTS[host]}/tools/v15_daily_chart_worker.py.tmp {HOSTS[host]}/tools/v15_daily_chart_worker.py", 300, src.read_text())
                got = ssh(host, f"md5sum {HOSTS[host]}/tools/v15_daily_chart_worker.py", 60).stdout[:32]
            except Exception as exc:
                print(f"[daily-charts] deploy to {host} failed: {exc}", flush=True)
            if got == want:
                break
        if got != want:
            print(f"[daily-charts] {host} worker md5 {got or 'unreachable'} != {want} — host excluded this run", flush=True)
            HOSTS.pop(host)


def dispatch(boards: dict, run_id: str, procs: int) -> list:
    jobs = {h: [] for h in HOSTS}
    inv = {h: npz_inventory(h, r) for h, r in HOSTS.items()}
    print(f"[daily-charts] NPZ inventory: " + ", ".join(f"{h}={len(v)}" for h, v in inv.items()), flush=True)
    for symside in sorted(boards):
        board = boards[symside]
        if "error" in board:
            continue
        symbol = symside.rsplit("_", 1)[0]
        home = "s1" if symbol.endswith(("USDT", "USDC", "USD1")) else "s5"  # USER 2026-10-30: crypto NPZ on S1, stock NPZ on S5
        hosts = [h for h in HOSTS if h == home and symbol in inv[h]]
        if not hosts:
            board["error"] = f"NPZ {symbol}.npz on no verified worker ({', '.join(HOSTS) or 'none reachable'})"
            continue
        host = min(hosts, key=lambda h: len(jobs[h]))
        jobs[host].append({"symside": symside, "overrides": board["overrides"], "out_name": f"{symside}_30d_daily.html", "heading": f"{symside} — daily 30D recalculation of the published V15 set"})
    results, threads = [], []
    lock = threading.Lock()

    def run(host: str, host_jobs: list) -> None:
        remote = f"/tmp/daily_charts_{run_id}"
        local = STAGE / run_id / host
        local.mkdir(parents=True, exist_ok=True)
        try:
            (local / "jobs.json").write_text(json.dumps(host_jobs))
            ssh(host, f"mkdir -p {remote}", 300)
            for attempt in range(3):
                r = subprocess.run(["rsync", "-az", "--timeout=300", str(local / "jobs.json"), f"{host}:{remote}/jobs.json"], capture_output=True, text=True, timeout=1200)
                if r.returncode == 0:
                    break
                time.sleep(10)
            else:
                raise RuntimeError(f"host {host} did not answer (rsync jobs.json): {r.stderr.strip()[-300:]}")
            ssh(host, f"cd {HOSTS[host]} && setsid nohup nice -n 15 {WORKER_PY} -u tools/v15_daily_chart_worker.py render {remote}/jobs.json {remote}/out {procs} > {remote}/worker.log 2>&1 < /dev/null &", 300)
            deadline, last = time.time() + 6 * 3600, ""
            while time.time() < deadline:
                time.sleep(30)
                try:
                    last = ssh(host, f"grep -c . {remote}/out/results.jsonl 2>/dev/null; grep -q '] DONE ' {remote}/worker.log && echo DONE; pgrep -f '{remote}/jobs.json' >/dev/null || echo GONE", 300).stdout.split()
                except Exception as exc:
                    print(f"[daily-charts] {host} poll: {exc}", flush=True)
                    continue
                if "DONE" in last or "GONE" in last:
                    break
            print(f"[daily-charts] {host}: worker finished state {last} ({len(host_jobs)} jobs)", flush=True)
            fetch(host, f"{remote}/out/", local)
            try:
                fetch(host, f"{remote}/worker.log", local / "worker.log", 300)
            except Exception as exc:
                print(f"[daily-charts] {exc}", flush=True)
            rows = [json.loads(line) for line in (local / "results.jsonl").read_text().splitlines() if line.strip()]
            ssh(host, f"rm -rf {remote}", 300)
        except Exception as exc:
            rows = []
            print(f"[daily-charts] {host} dispatch failed: {exc}", flush=True)
        done = {row["symside"] for row in rows}
        for job in host_jobs:
            if job["symside"] not in done:
                rows.append({"symside": job["symside"], "host": host, "status": "FAIL", "error": "worker produced no result (dispatch/sync failure)"})
        for row in rows:
            row["local_html"] = str(local / f"{row['symside']}_30d_daily.html")
        with lock:
            results.extend(rows)

    for host, host_jobs in jobs.items():
        if host_jobs:
            print(f"[daily-charts] {host}: {len(host_jobs)} jobs x {procs} procs", flush=True)
            t = threading.Thread(target=run, args=(host, host_jobs))
            t.start()
            threads.append(t)
    for t in threads:
        t.join()
    return results


def _fmt(v, nd=2, signed=False):
    if v is None:
        return "n/a"
    return f"{float(v):+.{nd}f}" if signed else f"{float(v):.{nd}f}"


def comparison_block(board: dict, res: dict) -> str:
    o, n = board.get("orig_metrics") or {}, res["metrics"]
    rows = [("gain %", "gain_pct", 2), ("B&H %", "bh_pct", 2), ("gain − B&H %", "delta_vs_bh", 2), ("trades", "trades", 0), ("TIM %", "tim_pct", 2), ("max DD %", "max_dd_pct", 2), ("DDmtm %", "dd_mtm_pct", 2), ("WR %", "wr_pct", 2), ("peak $", "peak_capital", 0), ("$ P&L", "pnl_dollars", 2), ("window bars", "window_bars", 0)]
    cells = []
    for label, key, nd in rows:
        a, b = o.get(key), n.get(key)
        d = (float(b) - float(a)) if a is not None and b is not None else None
        colour = "#a3a3a3" if d is None or abs(d) < 1e-9 else ("#22c55e" if (d > 0) != (key in ("max_dd_pct", "dd_mtm_pct")) else "#ef4444")
        if key in ("trades", "window_bars", "peak_capital", "bh_pct", "tim_pct"):
            colour = "#a3a3a3" if d is None or abs(d) < 1e-9 else "#e5e5e5"
        cells.append(f"<tr><td>{label}</td><td>{_fmt(a, nd)}</td><td>{_fmt(b, nd)}</td><td style='color:{colour}'>{_fmt(d, nd, True)}</td></tr>")
    calc = parse_iso(res.get("calculated_utc"))
    vintage = parse_iso(board.get("cells_vintage"))
    vint_txt = f"{board['cells_vintage']} ({age_text(vintage, calc)} before today's recalculation)" if vintage else "<span style='color:#ef4444'>UNAVAILABLE — manifest has no cells_vintage; original calculation time unproven</span>"
    mg = (board.get("manifest_metrics") or {}).get("gain_pct")
    mg_note = f" · manifest gain {_fmt(mg)}% differs from the board page" if mg is not None and o.get("gain_pct") is not None and abs(float(mg) - float(o["gain_pct"])) > 0.01 else ""
    win = res.get("window") or {}
    return (
        "<div class=\"metrics\" style='margin-bottom:6px;border-color:#2563eb'>"
        f"<b>TODAY vs ORIGINAL BOARD</b> &nbsp;|&nbsp; same set md5 <code>{res['set_md5']}</code> &nbsp;|&nbsp; set source: {_html.escape(board.get('set_source') or '')}"
        "<table style='width:auto;margin:4px 0'><thead><tr><th>metric</th><th>original board</th><th>today</th><th>Δ</th></tr></thead><tbody>"
        + "".join(cells)
        + "</tbody></table>"
        f"original: <b>{_html.escape(os.path.basename(board['board_html']))}</b> · cells calculated {vint_txt} · board chart generated {_html.escape(board.get('board_chart_generated') or 'UNAVAILABLE')} · published {_html.escape(board.get('published_utc') or '?')} ({_html.escape(str(board.get('publish_class')))}) · window {_html.escape(str(o.get('window_first_bar')))} → {_html.escape(str(o.get('window_last_bar')))} UTC{mg_note}"
        f"<br>today: calculated <b>{_html.escape(res.get('calculated_utc') or '?')}</b> on {_html.escape(res.get('host') or '?')} · NPZ {_html.escape(res['npz']['name'])} md5 {res['npz']['md5'][:12]} (mtime {res['npz']['mtime_utc']}){' = board NPZ' if res['npz']['md5'] == board.get('board_npz_md5') else ' ≠ board NPZ ' + str(board.get('board_npz_md5'))[:12]} · engine {res.get('engine_md5', '')[:8]} · window {_html.escape(str(n.get('window_first_bar')))} → {_html.escape(str(n.get('window_last_bar')))} UTC ({win.get('bars')} bars)"
        "</div>"
    )


def confirmed_order_rows(host: str, symsides: set[str], since: float) -> list[dict]:
    """Read the trader journal on its owner host; an accepted stock order is not a fill."""
    script = """import glob,json,pathlib
symsides = %r
since = %r
venue = %r
root = pathlib.Path.home() / 'binance/data/vector_direct'
try: fills = json.loads((root / ('fills_' + venue + '.json')).read_text())
except (OSError, ValueError): fills = {}
coverage_start = None
for path in sorted(glob.glob(str(pathlib.Path.home() / 'binance/data/vector_direct' / ('ledger_' + venue + '_*.jsonl')))):
    for line in open(path):
        try: row = json.loads(line)
        except (ValueError, UnicodeError): continue
        if row.get('event') == 'ORDER' and row.get('ts'):
            coverage_start = min(coverage_start, float(row['ts'])) if coverage_start is not None else float(row['ts'])
        if row.get('event') in ('ORDER', 'STOP') and row.get('ss') in symsides and float(row.get('ts') or 0) >= since:
            response = row.get('resp') or {}
            oid = response.get('orderId') or response.get('id')
            if oid: row['broker_fill'] = fills.get('oid:' + str(row.get('acct')) + ':' + str(oid))
            print(json.dumps(row, separators=(',', ':')))
print(json.dumps({'_coverage_start': coverage_start}))
""" % (sorted(symsides), since, "crypto" if host == "s1" else "stocks")
    response = ssh(host, "python3 - <<'PY'\n" + script + "PY", 180)
    if response.returncode != 0:
        raise RuntimeError(f"{host} live order read failed: {response.stderr.strip()[-300:]}")
    return [json.loads(line) for line in response.stdout.splitlines() if line.startswith("{")]


def broker_equity_snapshot(host: str) -> dict:
    """Read actual account equity from the venue's authenticated account endpoint."""
    script = """import json,sys,time
from pathlib import Path
root=Path.home()/'binance'
sys.path.insert(0,str(root))
from tools.vector_direct_fill_export import BinanceRO,TradierRO,load_env,trader_local_ip
env=load_env(root)
out={}
if %r == 's1':
    broker=BinanceRO(env,root/'data/vector_direct',trader_local_ip())
    for acct in ('flz','men','ang','inf','fin'):
        try:
            row=broker._get(acct,'/fapi/v2/account',{})
            out[acct]={'equity_usd':row.get('totalMarginBalance'),'wallet_usd':row.get('totalWalletBalance'),'unrealized_usd':row.get('totalUnrealizedProfit'),'available_usd':row.get('availableBalance'),'source':'Binance GET /fapi/v2/account'}
        except Exception as exc: out[acct]={'error':type(exc).__name__+': '+str(exc)[:100]}
else:
    broker=TradierRO(env)
    for acct in ('tra','trb','trc'):
        try:
            aid,*_=broker.account(acct)
            row=(broker._get(acct,f'/accounts/{aid}/balances') or {}).get('balances') or {}
            out[acct]={'equity_usd':row.get('total_equity'),'cash_usd':row.get('total_cash'),'open_pl_usd':row.get('open_pl'),'closed_today_pl_usd':row.get('close_pl'),'source':'Tradier GET /accounts/{id}/balances'}
        except Exception as exc: out[acct]={'error':type(exc).__name__+': '+str(exc)[:100]}
print(json.dumps({'asof_utc':time.strftime('%%FT%%TZ',time.gmtime()),'accounts':out}))
""" % host
    response = ssh(host, "python3 - <<'PY'\n" + script + "PY", 120)
    if response.returncode != 0:
        raise RuntimeError(f"{host} broker equity read failed: {response.stderr.strip()[-200:]}")
    return json.loads(response.stdout.splitlines()[-1])


def _chart_array(page: str, name: str) -> list:
    match = re.search(r"const " + name + r" = ([^\n]+);", page)
    if not match:
        raise ValueError(f"chart has no {name} array")
    return json.loads(match.group(1))


def discrepancy_overlay(page: str, board: dict, rows: list[dict], equity: dict | None = None) -> tuple[str, dict]:
    """Compare action and exact bar after publication; draw full-height discrepancy markers."""
    labels = _chart_array(page, "LABELS")
    trades = _chart_array(page, "TRADES")
    events = _chart_array(page, "EVENTS")
    published = parse_iso(board["published_utc"])
    if published is None:
        raise ValueError("original board publication time is not parseable")
    first = published.timestamp()
    coverage = next((float(row["_coverage_start"]) for row in rows if row.get("_coverage_start") is not None), None)
    bar_times = []
    for label in labels:
        parsed = parse_iso(str(label).replace(" ", "T") + ":00")
        bar_times.append(int(parsed.timestamp()) if parsed else 0)
    vector = set()
    for trade in trades:
        for action, field in (("OPEN", "bar_entry"), ("CLOSE", "bar_exit")):
            if action == "CLOSE" and trade.get("unrealised"):
                continue
            index = trade.get(field)
            if isinstance(index, int) and 0 <= index < len(bar_times) and bar_times[index] >= first:
                vector.add((bar_times[index], action))
    for event in events:
        action = str(event.get("type") or "").upper()
        index = event.get("bar")
        if action in ("AUGMENT", "REDUCE") and isinstance(index, int) and 0 <= index < len(bar_times) and bar_times[index] >= first:
            vector.add((bar_times[index], action))
    live, accepted = set(), set()
    accounts = {}
    broker_events, stop_events, order_outcomes = [], [], []
    for row in rows:
        if row.get("ss") != board["symside"] or row.get("dry_run"):
            continue
        event_time = float(row.get("ts") or 0)
        display_index = bisect.bisect_right(bar_times, event_time) - 1
        if event_time >= bar_times[-1] + 900:
            continue
        if row.get("event") == "STOP":
            stop_status = row.get("status") or row.get("act")
            if stop_status in ("PLACED", "PREARM_VERIFIED", "CANCEL_ON_CLOSE", "STOP_RETAINED_POSITION_HELD"):
                stop_fill = row.get("broker_fill") or {}
                if stop_status == "PLACED" and stop_fill.get("found") and float(stop_fill.get("exec_qty") or 0) > 0 and float(stop_fill.get("avg_price") or 0) > 0:
                    fill_time = float(stop_fill.get("fill_ms") or 0) / 1000 or event_time
                    fill_index = bisect.bisect_right(bar_times, fill_time) - 1
                    if 0 <= fill_index < len(bar_times) and fill_time < bar_times[-1] + 900:
                        broker_events.append({"i": fill_index, "action": "CLOSE (protective stop)", "qty": float(stop_fill["exec_qty"]), "price": float(stop_fill["avg_price"]), "acct": row.get("acct"), "time": iso(datetime.datetime.fromtimestamp(fill_time, datetime.timezone.utc)), "reason": f"PROTECTIVE_DC15_STOP level={row.get('level')}", "order_id": str(stop_fill.get("order_id") or ""), "before_board": fill_time < first})
                        if bar_times[fill_index] >= first:
                            stop_key = (bar_times[fill_index], "CLOSE")
                            live.add(stop_key)
                            accounts.setdefault(stop_key, set()).add(str(row.get("acct") or "?"))
                    stop_status = "FILLED"
                if 0 <= display_index < len(bar_times):
                    stop_events.append({"i": display_index, "level": float(row.get("level") or 0), "status": stop_status, "acct": row.get("acct"), "time": _html.escape(str(row.get("iso") or "")), "order_id": str(stop_fill.get("order_id") or (row.get("resp") or {}).get("id") or "")})
            continue
        if row.get("event") != "ORDER" or row.get("status") not in ("SENT", "PROTECTED_OTO_SUBMITTED"):
            continue
        parts = str(row.get("key") or "").split("|")
        if len(parts) < 4 or not parts[2].isdigit():
            continue
        key = (int(parts[2]), str(row.get("act") or "").upper())
        response = row.get("resp") or {}
        status = str(response.get("status") or "").upper()
        broker_fill = row.get("broker_fill") or {}
        crypto = board["symside"].rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1"))
        filled = (broker_fill.get("found") and float(broker_fill.get("exec_qty") or 0) > 0
                  and str(broker_fill.get("status") or "").lower() in ("filled", "partially_filled")) or (crypto and (status in ("FILLED", "PARTIALLY_FILLED") or float(response.get("executedQty") or 0) > 0))
        terminal_unfilled = str(broker_fill.get("status") or "").lower() in ("rejected", "canceled", "expired") and float(broker_fill.get("exec_qty") or 0) <= 0
        if 0 <= display_index < len(bar_times):
            order_outcomes.append({"time": iso(datetime.datetime.fromtimestamp(event_time, datetime.timezone.utc)), "acct": str(row.get("acct") or ""), "action": key[1], "qty": float(row.get("qty") or 0), "status": str(broker_fill.get("status") or response.get("status") or "UNVERIFIED"), "order_id": str(broker_fill.get("order_id") or response.get("orderId") or response.get("id") or ""), "reason": str(row.get("why") or "")[:160]})
        if filled:
            if key[0] >= first and key[0] in bar_times:
                live.add(key)
            fill_price = float(broker_fill.get("avg_price") or response.get("avgPrice") or 0)
            fill_time = float(broker_fill.get("fill_ms") or 0) / 1000 or event_time
            fill_index = bisect.bisect_right(bar_times, fill_time) - 1
            if fill_price > 0 and 0 <= fill_index < len(bar_times) and fill_time < bar_times[-1] + 900:
                broker_events.append({"i": fill_index, "action": key[1], "qty": float(broker_fill.get("exec_qty") or response.get("executedQty") or row.get("qty") or 0), "price": fill_price, "acct": row.get("acct"), "time": iso(datetime.datetime.fromtimestamp(fill_time, datetime.timezone.utc)), "reason": str(row.get("why") or "")[:160], "order_id": str(broker_fill.get("order_id") or response.get("orderId") or response.get("id") or ""), "before_board": fill_time < first})
        elif not terminal_unfilled:
            if key[0] >= first and key[0] in bar_times:
                accepted.add(key)
        accounts.setdefault(key, set()).add(str(row.get("acct") or "?"))
    bar_index = {bar: i for i, bar in enumerate(bar_times)}
    blue = [{"i": bar_index[bar], "action": act, "accounts": sorted(accounts.get((bar, act), []))} for bar, act in sorted(live - vector)]
    orange = [{"i": bar_index[bar], "action": act} for bar, act in sorted(vector - live - accepted) if coverage is not None and bar >= int(coverage // 900) * 900]
    pending = [{"i": bar_index[bar], "action": act} for bar, act in sorted(accepted - live)]
    data = json.dumps({"blue": blue, "orange": orange, "accepted_unverified": pending}, separators=(",", ":"))
    fills_json = json.dumps(broker_events, separators=(",", ":"))
    stops_json = json.dumps([r for r in stop_events if r["level"] > 0 and r["status"] in ("PLACED", "FILLED", "PREARM_VERIFIED")], separators=(",", ":"))
    plugin = ("const DISCREPANCIES = " + data + ";\nconst BROKER_FILLS = " + fills_json + ";\nconst BROKER_STOPS = " + stops_json + ";\n"
              "DATASETS.push({label:'CONFIRMED broker fill',data:BROKER_FILLS.map(r=>({x:LABELS[r.i],y:r.price,qty:r.qty,price:r.price,reason:r.action+' '+r.acct+' '+r.reason+' order '+r.order_id})),type:'scatter',backgroundColor:'#38bdf8',borderColor:'#ffffff',borderWidth:2.5,pointStyle:'star',pointRadius:11,pointHoverRadius:15,order:30});\n"
              "DATASETS.push({label:'broker protective stop placed',data:BROKER_STOPS.map(r=>({x:LABELS[r.i],y:r.level,qty:'stop',price:r.level,reason:r.status+' '+r.acct+' order '+r.order_id})),type:'scatter',backgroundColor:'#e879f9',borderColor:'#ffffff',borderWidth:2,pointStyle:'rectRot',pointRadius:8,pointHoverRadius:12,order:29});\n"
              "Chart.register({id:'forwardDiscrepancyLines',afterDatasetsDraw(chart){const x=chart.scales.x,a=chart.chartArea,ctx=chart.ctx;for(const [name,color] of [['blue','rgba(6,182,212,0.10)'],['orange','rgba(249,115,22,0.10)']]){for(const row of DISCREPANCIES[name]){const px=x.getPixelForValue(row.i);if(px<a.left||px>a.right)continue;ctx.save();ctx.strokeStyle=color;ctx.lineWidth=2;ctx.beginPath();ctx.moveTo(px,a.top);ctx.lineTo(px,a.bottom);ctx.stroke();ctx.restore();}}}});\n")
    page = page.replace("pointRadius:5.5", "pointRadius:8")
    page = page.replace("new Chart(c,{", plugin + "new Chart(c,{", 1)
    rows_html = "".join(f"<tr><td>{_html.escape(r['time'])}</td><td>{_html.escape(str(r['acct']))}</td><td>{_html.escape(r['action'])}</td><td>{r['qty']:g}</td><td>${r['price']:.4f}</td><td>{_html.escape(r['reason'])}</td><td>{_html.escape(r['order_id'])}</td></tr>" for r in sorted(broker_events, key=lambda r: r["time"]))
    stop_html = "".join(f"<tr><td>{r['time']}</td><td>{_html.escape(str(r['acct']))}</td><td>{_html.escape(r['status'])}</td><td>{'$' + format(r['level'], '.4f') if r['level'] else '—'}</td><td>{_html.escape(r['order_id'])}</td></tr>" for r in sorted(stop_events, key=lambda r: r["time"]))
    order_html = "".join(f"<tr><td>{_html.escape(r['time'])}</td><td>{_html.escape(r['acct'])}</td><td>{_html.escape(r['action'])}</td><td>{r['qty']:g}</td><td>{_html.escape(r['status'])}</td><td>{_html.escape(r['order_id'])}</td><td>{_html.escape(r['reason'])}</td></tr>" for r in sorted(order_outcomes, key=lambda r: r["time"]))
    summary = (f"<div class='metrics'><b>AFTER ORIGINAL BOARD PUBLICATION — VECTOR VS CONFIRMED BROKER FILLS (ACTION-BAR DIAGNOSTIC)</b> "
               f"<span style='color:#60a5fa'>blue live only {len(blue)}</span> · "
               f"<span style='color:#fb923c'>orange vector only {len(orange)}</span> · "
               f"""accepted, fill unverified {len(pending)}. Diagnostic matches action and 15 minute bar only; it ignores account allocation and quantity, so it does NOT certify exact live parity. Confirmed fill markers use broker fill time; regular-order action matching uses the submitted decision bar, protective-stop matching uses its fill bar. """
               f"Journal coverage starts {_html.escape(iso(datetime.datetime.fromtimestamp(coverage, datetime.timezone.utc)) if coverage else 'UNAVAILABLE')}; earlier vector events are unassessed. "
               f"Stock 'ok' responses are order acceptance, not actual fills. No broker fill history means orange marks require investigation.</div>"
               f"<details open><summary>{len(broker_events)} confirmed broker fills (blue stars) · {len(stop_events)} protective stop records (purple diamonds at placed level) within this chart window</summary><table><tr><th>UTC fill</th><th>account</th><th>action</th><th>qty</th><th>fill price</th><th>trader reason</th><th>broker order</th></tr>{rows_html}</table><table><tr><th>UTC</th><th>account</th><th>stop status</th><th>level</th><th>order ID</th></tr>{stop_html}</table><table><tr><th>UTC order</th><th>account</th><th>action</th><th>qty</th><th>broker status</th><th>order ID</th><th>trader reason</th></tr>{order_html}</table></details>")
    if equity:
        stock = not board["symside"].rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1"))
        selected = equity.get("accounts", {})
        figures = " · ".join(f"{_html.escape(acct)} equity ${float(row['equity_usd']):,.2f} (wallet ${float(row.get('wallet_usd') or 0):,.2f}; available ${float(row.get('available_usd') or 0):,.2f})" if row and row.get("equity_usd") is not None and not stock else f"{_html.escape(acct)} equity ${float(row['equity_usd']):,.2f} (cash ${float(row.get('cash_usd') or 0):,.2f}; open P/L ${float(row.get('open_pl_usd') or 0):+,.2f}; closed today ${float(row.get('closed_today_pl_usd') or 0):+,.2f})" if row and row.get("equity_usd") is not None else f"{_html.escape(acct)} UNAVAILABLE: {_html.escape(str((row or {}).get('error') or 'missing broker value'))}" for acct, row in selected.items())
        summary += f"<div class='metrics'><b>BROKER ACCOUNT EQUITY</b> · as of {_html.escape(str(equity.get('asof_utc') or '?'))} · {figures} · direct authenticated venue API; current snapshot, not historical chart P/L.</div>"
    page = page.replace("<div id='wrap'>", summary + "<div id='wrap'>", 1)
    return page, {"comparison_scope": "ACTION_BAR_DIAGNOSTIC_ACCOUNT_AND_QUANTITY_IGNORED", "live_only": len(blue), "vector_only": len(orange), "accepted_unverified": len(pending), "confirmed_broker_fills": len(broker_events), "protective_stop_records": len(stop_events)}


def fail_page(symside: str, board: dict, error: str, now: str) -> str:
    source = Path(board.get("board_html") or "")
    historical = source.is_file() and source.parent == CELL
    original = board.get("orig_metrics") or (parse_chart_metrics(source.read_text(encoding="utf-8", errors="replace")) if historical else {})
    title = f"{symside} — {'ORIGINAL board' if historical else 'source chart unavailable'}"
    if historical:
        title += f" · B&H {_fmt(original.get('bh_pct'))}% · {original.get('trades') or '?'} vector trades · gain {_fmt(original.get('gain_pct'))}%"
    source_url = "../V15_V16_CELL_BY_CELL/" + source.name if historical else ""
    viewer = (f"<p><a href='{_html.escape(source_url)}'>Open original board directly</a></p>"
              f"<iframe src='{_html.escape(source_url)}' title='Historical original board' style='width:100%;height:78vh;border:1px solid #555;background:#0a0a0a'></iframe>") if historical else ""
    return (
        f"<!doctype html><html><head><meta charset=utf-8><title>{_html.escape(title)}</title>"
        "<style>body{font-family:system-ui,monospace;margin:12px;background:#0a0a0a;color:#e5e5e5}</style></head><body>"
        f"<h2>{_html.escape(title)}</h2>"
        f"<div style='background:#5a1a1a;border:1px solid #a33;padding:8px;font-size:12px'>CURRENT 30D RECALCULATION FAILED: {_html.escape(error)}<br>attempted {now} · "
        f"original board published {_html.escape(str(board.get('published_utc')))}; cells {_html.escape(str(board.get('cells_vintage') or 'UNAVAILABLE'))}. Historical chart below; its metrics and trades are from that publication.</div>{viewer}</body></html>\n"
    )


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def metric_chart_name(symside: str, metrics: dict, historical: bool = False, gain_delta: float | None = None) -> str | None:
    try:
        bh = float(metrics["bh_pct"])
        gain = float(metrics["gain_pct"])
        trades = int(metrics["trades"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(bh) or not math.isfinite(gain) or trades < 0:
        return None
    def token(value):
        return ("m" if value < 0 else "") + f"{abs(value):.2f}".replace(".", "p")
    if historical:
        suffix = "_historical"
    elif gain_delta is not None and math.isfinite(float(gain_delta)):
        suffix = "_up" + token(gain_delta) + "pp" if gain_delta > 1e-9 else "_down" + token(-gain_delta) + "pp" if gain_delta < -1e-9 else "_flat"
    else:
        suffix = "_uncompared"
    return f"{symside}_bh{token(bh)}_gain{token(gain)}_trades{trades}{suffix}_30d_daily.html"


def metric_chart_alias(canonical: Path, symside: str, metrics: dict, historical: bool = False, gain_delta: float | None = None) -> str:
    name = metric_chart_name(symside, metrics, historical, gain_delta)
    if name is None:
        return canonical.name
    alias = CHARTS / name
    temp = alias.with_suffix(".html.tmp")
    if temp.exists() or temp.is_symlink():
        temp.unlink()
    temp.symlink_to(canonical.name)
    os.replace(temp, alias)
    for old in CHARTS.glob(f"{symside}_bh*_gain*_trades*_30d_daily.html"):
        if old != alias and old.is_symlink() and old.resolve() == canonical.resolve():
            old.unlink()
    return name


def publish(boards: dict, results: list, run_id: str, started: str, partial: bool = False) -> dict:
    from tools.opt.chart_truth import verify_html

    CHARTS.mkdir(parents=True, exist_ok=True)
    by_sym = {r["symside"]: r for r in results}
    live_rows = {}
    live_errors = {}
    broker_equity = {}
    equity_errors = {}
    for host in HOSTS:
        sides = {s for s, r in by_sym.items() if r.get("status") == "OK" and ("s1" if s.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1")) else "s5") == host}
        stamps = [parse_iso(boards[s].get("published_utc")) for s in sides]
        if not sides or not any(stamps):
            continue
        try:
            live_rows[host] = confirmed_order_rows(host, sides, min(t.timestamp() for t in stamps if t) - 35 * 86400)
        except Exception as exc:
            live_errors[host] = str(exc)[:180]
        try:
            broker_equity[host] = broker_equity_snapshot(host)
        except Exception as exc:
            equity_errors[host] = str(exc)[:180]
    entries, now = [], iso(utcnow())
    for symside in sorted(boards):
        board, res = boards[symside], by_sym.get(symside)
        out = CHARTS / f"{symside}_30d_daily.html"
        entry = {"symside": symside, "chart": out.name, "board": os.path.basename(board.get("board_html") or ""), "board_published_utc": board.get("published_utc"), "board_cells_vintage": board.get("cells_vintage"), "board_publish_class": board.get("publish_class"), "set_md5": board.get("overrides_md5"), "set_source": board.get("set_source"), "orig_metrics": board.get("orig_metrics")}
        error = None
        if board.get("error"):
            error = board["error"]
        elif not res or res.get("status") != "OK":
            error = (res or {}).get("error") or "no worker result"
        if error is None:
            try:
                text = Path(res["local_html"]).read_text(encoding="utf-8")
                if res["set_md5"] != board["overrides_md5"]:
                    raise AssertionError(f"worker set md5 {res['set_md5']} != board {board['overrides_md5']}")
                text = re.sub(r"(<h2>.*?</h2>)", lambda m: m.group(1) + comparison_block(board, res), text, count=1, flags=re.S)
                title = f"{symside} — B&H {_fmt(res['metrics'].get('bh_pct'))}% · {res['metrics'].get('trades')} vector trades · gain {_fmt(res['metrics'].get('gain_pct'))}%"
                text = re.sub(r"<title>.*?</title>", "<title>" + _html.escape(title) + "</title>", text, count=1, flags=re.S)
                text = re.sub(r"<h2>.*?</h2>", "<h2>" + _html.escape(title) + "</h2>", text, count=1, flags=re.S)
                owner = "s1" if symside.rsplit("_", 1)[0].endswith(("USDT", "USDC", "USD1")) else "s5"
                if owner in live_errors:
                    text = text.replace("<div id='wrap'>", "<div class='metrics' style='color:#ef4444'>LIVE FILL COMPARISON UNAVAILABLE: " + _html.escape(live_errors[owner]) + "</div><div id='wrap'>", 1)
                    entry["discrepancy_error"] = live_errors[owner]
                else:
                    text, entry["discrepancies"] = discrepancy_overlay(text, board, live_rows.get(owner, []), broker_equity.get(owner))
                if owner in equity_errors:
                    text = text.replace("<div id='wrap'>", "<div class='metrics' style='color:#ef4444'>BROKER EQUITY UNAVAILABLE: " + _html.escape(equity_errors[owner]) + "</div><div id='wrap'>", 1)
                    entry["broker_equity_error"] = equity_errors[owner]
                verify_html(text, symside, require_table=True)
                if parse_chart_metrics(text).get("gain_pct") != res["metrics"]["gain_pct"]:
                    raise AssertionError("final file gain differs from worker result")
                atomic_write(out, text)
                original_gain = (board.get("orig_metrics") or {}).get("gain_pct")
                gain_delta = float(res["metrics"]["gain_pct"]) - float(original_gain) if original_gain is not None else None
                entry["chart"] = metric_chart_alias(out, symside, res["metrics"], gain_delta=gain_delta)
                o = board.get("orig_metrics") or {}
                deltas = {k: (float(res["metrics"][k]) - float(o[k])) if res["metrics"].get(k) is not None and o.get(k) is not None else None for k in ("gain_pct", "bh_pct", "delta_vs_bh", "trades", "tim_pct", "max_dd_pct", "wr_pct")}
                entry.update({"status": "OK", "host": res["host"], "calculated_utc": res["calculated_utc"], "board_age_at_calc": age_text(parse_iso(board.get("cells_vintage")), parse_iso(res["calculated_utc"])), "metrics": res["metrics"], "deltas": deltas, "npz": res["npz"], "window": res.get("window"), "engine_md5": res.get("engine_md5")})
            except Exception as exc:
                error = f"publish check failed: {type(exc).__name__}: {str(exc)[:300]}"
        if error is not None:
            failure_text = fail_page(symside, board, error, now)
            atomic_write(out, failure_text)
            original_path = Path(board.get("board_html") or "")
            original_metrics = board.get("orig_metrics") or (parse_chart_metrics(original_path.read_text(encoding="utf-8", errors="replace")) if original_path.is_file() else {})
            entry["chart"] = metric_chart_alias(out, symside, original_metrics, historical=True)
            entry.update({"status": "FAIL", "error": error})
        entries.append(entry)
    ok = sum(1 for e in entries if e["status"] == "OK")
    manifest = {"run_id": run_id, "started_utc": started, "finished_utc": iso(utcnow()), "sym_sides": len(entries), "ok": ok, "fail": len(entries) - ok, "complete": ok == len(entries), "entries": entries}
    if partial:
        atomic_write(CHARTS / "daily_charts_manifest_partial.json", json.dumps(manifest, indent=1, default=str))
        return manifest
    atomic_write(CHARTS / "daily_charts_manifest.json", json.dumps(manifest, indent=1, default=str))
    atomic_write(CHARTS / "index.html", index_page(manifest))
    with open(HISTORY, "a", encoding="utf-8") as fh:
        fh.write(json.dumps({k: v for k, v in manifest.items() if k != "entries"} | {"fails": {e["symside"]: e["error"][:160] for e in entries if e["status"] != "OK"}}, default=str) + "\n")
    return manifest


def index_page(manifest: dict) -> str:
    rows = []
    active = {r["symside"]: r["drop_pp"] for r in catchup_rows(manifest)}
    catchup_n = len(active)
    severe_n = sum(drop >= 15.0 for drop in active.values())
    for e in sorted(manifest["entries"], key=lambda e: (e["status"] != "OK", -(e.get("metrics") or {}).get("gain_pct", -1e9) if e["status"] == "OK" else 0, e["symside"])):
        m, d, o = e.get("metrics") or {}, e.get("deltas") or {}, e.get("orig_metrics") or {}
        chart_path = CHARTS / e["chart"]
        chart_head = chart_path.open(encoding="utf-8", errors="replace").read(1200) if chart_path.is_file() else ""
        title_match = re.search(r"<title>(.*?)</title>", chart_head, re.S | re.I)
        chart_title = _html.unescape(title_match.group(1)) if title_match else e["symside"]
        gain_delta = d.get("gain_pct") if e["status"] == "OK" else None
        if gain_delta is None:
            direction, title_color = "uncompared", "#a3a3a3"
        elif float(gain_delta) > 1e-9:
            direction, title_color = f"↑ +{float(gain_delta):.2f} pp since board", "#4ade80"
        elif float(gain_delta) < -1e-9:
            direction, title_color = f"↓ {float(gain_delta):.2f} pp since board", "#fb7185"
        else:
            direction, title_color = "= unchanged since board", "#d1d5db"
        link = f"<a href='{_html.escape(e['chart'])}' title='{_html.escape(chart_title + ' · ' + direction, quote=True)}' style='color:{title_color}'>{_html.escape(chart_title)}</a> <span style='color:{title_color}'>{_html.escape(direction)}</span>"
        if e["status"] == "OK":
            drop = d.get("gain_pct")
            queue_status = "HALTED · CATCH-UP" if active.get(e["symside"], 0) >= 15.0 else "CATCH-UP · TRADING" if e["symside"] in active else "SUPERSEDED" if drop is not None and float(drop) < -1e-9 else "OK"
            rows.append(f"<tr><td>{link}</td><td>{queue_status}</td><td>{_fmt(m.get('gain_pct'))}</td><td>{_fmt(o.get('gain_pct'))}</td><td>{_fmt(d.get('gain_pct'), 2, True)}</td><td>{_fmt(m.get('bh_pct'))}</td><td>{m.get('trades')}</td><td>{_fmt(m.get('max_dd_pct'))}</td><td>{_fmt(m.get('tim_pct'))}</td><td>{e.get('board_cells_vintage') or 'UNAVAILABLE'}</td><td>{e.get('board_age_at_calc')}</td><td>{e.get('calculated_utc')}</td></tr>")
        else:
            rows.append(f"<tr style='color:#ef4444'><td>{link}</td><td>FAIL</td><td colspan=10>{_html.escape(e.get('error', '')[:200])}</td></tr>")
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>V15 daily 30D charts</title>"
        "<style>body{font-family:system-ui,monospace;margin:12px;background:#0a0a0a;color:#e5e5e5}a{color:#93c5fd}table{border-collapse:collapse;font-size:11px}th{background:#1f2937;padding:4px}td{border:1px solid #333;padding:3px}</style></head><body>"
        f"<h2>V15 daily 30D charts — run {manifest['run_id']}</h2><p>started {manifest['started_utc']} · finished {manifest['finished_utc']} · {manifest['ok']}/{manifest['sym_sides']} OK · {manifest['fail']} FAIL{' · COMPLETE' if manifest['complete'] else ' · INCOMPLETE'} · {catchup_n} negative gain changes queued for catch-up, {severe_n} at least 15 pp lower and halted. Smaller drops keep trading.</p>"
        "<p><span style='color:#4ade80'>↑ higher gain</span> · <span style='color:#fb7185'>↓ lower gain</span> · <span style='color:#d1d5db'>= unchanged</span> · <span style='color:#a3a3a3'>uncompared</span>. Percentage-point change from the original published board using the same settings on the latest rolling 30-day window.</p>"
        "<table><thead><tr><th>sym_side</th><th>status</th><th>gain % today</th><th>gain % board</th><th>Δ gain</th><th>B&H %</th><th>trades</th><th>max DD %</th><th>TIM %</th><th>board cells calculated</th><th>board age at recalc</th><th>recalculated</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>\n"
    )


# A same-settings 30D gain drop of at least 15 pp halts entries while all negative gain drops enter catch-up.
DETERIORATION_RULES = (("gain_pct", "drop", 15.0),)
HALT_FILE = ROOT / "data" / "deterioration_halt.json"
HALT_TARGETS = (("s1-int", "~/binance/data"), ("s5", "~/binance/data"), ("s5", "~/trc7d_root/data"))
PRIORITY_HOSTS = ("s1-int", "s2")
PRIORITY_REMOTE = "~/binance-sandbox/data/fleet_priority.json"
PRIORITY_MINE = ROOT / "data" / "reports" / "daily_charts_priority_added.json"
CATCHUP_FILE = ROOT / "data" / "fleet_catchup.json"
CATCHUP_REMOTE = "~/binance-sandbox/data/fleet_catchup.json"


def deterioration(entry: dict) -> list:
    o, n = entry.get("orig_metrics") or {}, entry.get("metrics") or {}
    out = []
    for key, way, limit in DETERIORATION_RULES:
        if o.get(key) is None or n.get(key) is None:
            continue
        d = float(n[key]) - float(o[key])
        if (way == "drop" and d <= -limit) or (way == "rise" and d >= limit):
            out.append(f"{key} {float(o[key]):.2f} -> {float(n[key]):.2f} ({d:+.2f}, limit {'-' if way == 'drop' else '+'}{limit})")
    if o.get("gain_pct") is not None and n.get("gain_pct") is not None and float(o["gain_pct"]) > 0 >= float(n["gain_pct"]):
        out.append(f"gain_pct turned non-positive {float(o['gain_pct']):.2f} -> {float(n['gain_pct']):.2f}")
    return out


def load_halts() -> dict:
    try:
        return dict((json.loads(HALT_FILE.read_text()) or {}).get("halts") or {})
    except Exception:
        return {}


def latest_publish(symside: str) -> str:
    best = ""
    for mp in glob.glob(str(CELL / f"{symside}_*_manifest.json")):
        try:
            man = json.loads(Path(mp).read_text())
        except Exception:
            continue
        if str(man.get("symside") or "").upper() == symside and (man.get("published_utc") or "") > best:
            best = man["published_utc"]
    return best


def update_halts(manifest=None) -> dict:
    """Daily run: deteriorated sides halt (fresh halted_utc); a side recalculated OK today without deterioration is released.
    Every call: a side whose board was re-published after its halt is released (the fleet re-ran it on the latest NPZ)."""
    halts, now, released = load_halts(), iso(utcnow()), {}
    if manifest:
        for e in manifest["entries"]:
            if e["status"] != "OK":
                continue
            reasons = deterioration(e)
            e["deterioration"] = reasons
            if reasons:
                keep = halts.get(e["symside"]) or {}
                halts[e["symside"]] = {"halted_utc": keep.get("halted_utc") or now, "rechecked_utc": e["calculated_utc"], "reasons": reasons, "board": e["board"], "board_published_utc": e["board_published_utc"], "set_md5": e["set_md5"], "today_gain_pct": e["metrics"].get("gain_pct"), "board_gain_pct": (e.get("orig_metrics") or {}).get("gain_pct")}
            elif e["symside"] in halts:
                released[e["symside"]] = f"recalculated {e['calculated_utc']} without significant deterioration"
                halts.pop(e["symside"])
    for symside in list(halts):
        pub = latest_publish(symside)
        if pub and pub > halts[symside]["halted_utc"]:
            released[symside] = f"new board published {pub} after halt {halts[symside]['halted_utc']}"
            halts.pop(symside)
    doc = {"updated_utc": now, "rules": [list(r) for r in DETERIORATION_RULES], "semantics": "no OPEN / AUGMENT for these sym_sides on every account; CLOSE / REDUCE follow the board", "halts": halts}
    atomic_write(HALT_FILE, json.dumps(doc, indent=1, sort_keys=True))
    for symside, why in released.items():
        print(f"[daily-charts] HALT RELEASED {symside}: {why}", flush=True)
    with open(ROOT / "data" / "reports" / "deterioration_halt_events.jsonl", "a", encoding="utf-8") as fh:
        for symside, h in halts.items():
            if h["halted_utc"] == now:
                fh.write(json.dumps({"event": "HALT", "symside": symside, **h}) + "\n")
        for symside, why in released.items():
            fh.write(json.dumps({"event": "RELEASE", "symside": symside, "utc": now, "why": why}) + "\n")
    return doc


def push_halts(doc: dict) -> dict:
    """Same bytes on every trader root, md5-verified. A host that did not answer is reported, never assumed."""
    body = json.dumps(doc, indent=1, sort_keys=True)
    want = hashlib.md5(body.encode()).hexdigest()
    status = {}
    for host, ddir in HALT_TARGETS:
        try:
            r = ssh(host, f"test -d {ddir} || exit 3; cat > {ddir}/deterioration_halt.json.tmp && mv {ddir}/deterioration_halt.json.tmp {ddir}/deterioration_halt.json && md5sum {ddir}/deterioration_halt.json", 300, body)
            status[f"{host}:{ddir}"] = "OK" if r.stdout[:32] == want else f"MISMATCH rc={r.returncode} {r.stdout[:32]} {r.stderr.strip()[-160:]}"
        except Exception as exc:
            status[f"{host}:{ddir}"] = f"FAIL {exc}"
    for target, st in status.items():
        print(f"[daily-charts] halt file -> {target}: {st} ({len(doc['halts'])} halted)", flush=True)
    return status


def sync_priority(halted: set) -> dict:
    """Merge halted sides into fleet_dispatch's priority file on S1+S2; remove sides this tool added that are no longer halted.
    Other lanes' sides in the file are kept untouched."""
    try:
        mine_before = set(json.loads(PRIORITY_MINE.read_text()))
    except Exception:
        mine_before = set()
    now, status = time.time(), {}
    for host in PRIORITY_HOSTS:
        try:
            cur = json.loads(ssh(host, f"cat {PRIORITY_REMOTE} 2>/dev/null || echo '{{}}'", 300).stdout or "{}") or {}
            live = float(cur.get("until") or 0) > now
            sides = [s for s in (cur.get("sym_sides") or []) if live and s not in (mine_before - halted)]
            sides += [s for s in sorted(halted) if s not in sides]
            why = [w for w in str(cur.get("why") or "").split(" | ") if w and live and not w.startswith("DAILY_DETERIORATION")]
            if halted:
                why.append(f"DAILY_DETERIORATION {len(halted)} sides (tools/v15_daily_charts.py)")
            until = max(float(cur.get("until") or 0) if live else 0.0, now + 86400 if halted else 0.0)
            doc = {"sym_sides": sides, "until": until, "why": " | ".join(why)}
            r = ssh(host, f"cat > {PRIORITY_REMOTE}.tmp && mv {PRIORITY_REMOTE}.tmp {PRIORITY_REMOTE} && cat {PRIORITY_REMOTE}", 300, json.dumps(doc))
            status[host] = "OK" if json.loads(r.stdout or "{}") == doc else f"MISMATCH {r.stderr.strip()[-160:]}"
        except Exception as exc:
            status[host] = f"FAIL {exc}"
        print(f"[daily-charts] fleet_priority -> {host}: {status[host]} ({len(halted)} deterioration sides)", flush=True)
    if all(v == "OK" for v in status.values()):
        atomic_write(PRIORITY_MINE, json.dumps(sorted(halted)))
    return status


def catchup_rows(manifest: dict) -> list[dict]:
    rows = []
    for entry in manifest.get("entries") or []:
        drop = (entry.get("deltas") or {}).get("gain_pct")
        if entry.get("status") != "OK" or drop is None or not math.isfinite(float(drop)) or float(drop) >= -1e-9:
            continue
        if latest_publish(entry["symside"]) > entry["calculated_utc"]:
            continue
        rows.append({"symside": entry["symside"], "drop_pp": round(-float(drop), 6), "calculated_utc": entry["calculated_utc"], "set_md5": entry["set_md5"], "board_published_utc": entry["board_published_utc"]})
    return sorted(rows, key=lambda row: (-row["drop_pp"], row["symside"]))


def sync_catchup_queue(doc: dict) -> dict:
    body = json.dumps(doc, indent=1, sort_keys=True)
    want = hashlib.sha256(body.encode()).hexdigest()
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    status = {}
    for host in PRIORITY_HOSTS:
        try:
            remote = CATCHUP_REMOTE
            cmd = f"mkdir -p ~/binance-sandbox/backups; if [ -f {remote} ]; then cp {remote} ~/binance-sandbox/backups/before_fleet_catchup_{stamp}.json; fi; cat > {remote}.tmp && mv {remote}.tmp {remote} && sha256sum {remote}"
            result = ssh(host, cmd, 300, body)
            status[host] = "OK" if result.returncode == 0 and result.stdout[:64] == want else f"MISMATCH rc={result.returncode} {result.stderr.strip()[-120:]}"
        except Exception as exc:
            status[host] = f"FAIL {exc}"
    return status


def enforce(manifest=None, partial=False) -> dict:
    """Queue negative gain deltas; halt only drops of at least 15 pp."""
    if partial or not manifest:
        return {"catchup": "partial chart run: fleet queue unchanged", "halt_push": "DISABLED"}
    rows = catchup_rows(manifest)
    doc = {"schema": "daily-chart-catchup-v1", "run_id": manifest["run_id"], "generated_utc": iso(utcnow()), "until": time.time() + 36 * 3600, "rows": rows, "threshold_halt_pp": 15.0, "mode": "V18_CATCHUP=1"}
    atomic_write(CATCHUP_FILE, json.dumps(doc, indent=1, sort_keys=True))
    halts = {r["symside"]: {"halted_utc": iso(utcnow()), "rechecked_utc": r["calculated_utc"], "drop_pp": r["drop_pp"], "board_published_utc": r["board_published_utc"], "set_md5": r["set_md5"], "reason": "same-settings 30D gain fell by at least 15 pp"} for r in rows if r["drop_pp"] >= 15.0}
    halt_doc = {"updated_utc": iso(utcnow()), "semantics": "no OPEN or AUGMENT for drops >=15 pp; CLOSE and REDUCE allowed", "halts": halts}
    atomic_write(HALT_FILE, json.dumps(halt_doc, indent=1, sort_keys=True))
    status = sync_catchup_queue(doc)
    halt_status = push_halts(halt_doc)
    print(f"[daily-charts] catch-up queue {len(rows)} negative sides, {sum(r['drop_pp'] >= 15 for r in rows)} at least 15 pp; S1/S2 {status}", flush=True)
    return {"catchup": len(rows), "severe_drop_15pp": len(halts), "queue_sync": status, "halt_push": halt_status}


def slot_due(now: datetime.datetime) -> str:
    slot = now.replace(hour=SLOT_HOUR_UTC, minute=0, second=0, microsecond=0)
    if now < slot:
        slot -= datetime.timedelta(days=1)
    try:
        last = json.loads(STATE.read_text()).get("last_slot_done")
    except Exception:
        last = None
    return "" if last == iso(slot) else iso(slot)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--now", action="store_true")
    ap.add_argument("--scheduled", action="store_true")
    ap.add_argument("--symside", action="append")
    ap.add_argument("--procs", type=int, default=4)
    a = ap.parse_args()
    now = utcnow()
    slot = slot_due(now) if (a.scheduled or not a.symside) else None
    if a.scheduled and not slot:
        return 0
    LOCK.parent.mkdir(parents=True, exist_ok=True)
    lock_fh = open(LOCK, "w")
    try:
        fcntl.flock(lock_fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[daily-charts] another run holds the lock — exiting", flush=True)
        return 0
    started = iso(now)
    run_id = now.strftime("%Y%m%dT%H%M%SZ")
    only = {s.upper() for s in a.symside} if a.symside else None
    print(f"[daily-charts] run {run_id} start (slot {slot or 'manual'})", flush=True)
    deploy_worker()
    boards = discover(only)
    resolve_remote(boards)
    print(f"[daily-charts] {len(boards)} sym_sides; set proven for {sum(1 for b in boards.values() if 'overrides' in b and 'error' not in b)}", flush=True)
    if not HOSTS:
        print("[daily-charts] no verified worker host — every sym_side reported FAIL", flush=True)
    results = dispatch(boards, run_id, a.procs)
    manifest = publish(boards, results, run_id, started, partial=bool(only))
    manifest["enforcement"] = enforce(manifest, partial=bool(only))
    atomic_write(CHARTS / ("daily_charts_manifest_partial.json" if only else "daily_charts_manifest.json"), json.dumps(manifest, indent=1, default=str))
    if not only:
        atomic_write(CHARTS / "index.html", index_page(manifest))
    if slot and not only:
        atomic_write(STATE, json.dumps({"last_slot_done": slot, "run_id": run_id, "ok": manifest["ok"], "fail": manifest["fail"]}))
    shutil.rmtree(STAGE / run_id, ignore_errors=True)
    print(f"[daily-charts] run {run_id} done: {manifest['ok']}/{manifest['sym_sides']} OK, {manifest['fail']} FAIL, complete={manifest['complete']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
