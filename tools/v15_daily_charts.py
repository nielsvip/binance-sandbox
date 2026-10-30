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
import datetime
import fcntl
import glob
import hashlib
import html as _html
import json
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
            ssh(host, "cat > /tmp/dc_need.json", 120, json.dumps(need))
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
        r = ssh(host, f"ls {root}/backtest_v8/indicators/", 120)
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
                ssh(host, f"cat > {HOSTS[host]}/tools/v15_daily_chart_worker.py.tmp && mv {HOSTS[host]}/tools/v15_daily_chart_worker.py.tmp {HOSTS[host]}/tools/v15_daily_chart_worker.py", 120, src.read_text())
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
            ssh(host, f"mkdir -p {remote} && cat > {remote}/jobs.json", 120, json.dumps(host_jobs))
            ssh(host, f"cd {HOSTS[host]} && setsid nohup nice -n 15 {WORKER_PY} -u tools/v15_daily_chart_worker.py render {remote}/jobs.json {remote}/out {procs} > {remote}/worker.log 2>&1 < /dev/null &", 120)
            deadline, last = time.time() + 6 * 3600, ""
            while time.time() < deadline:
                time.sleep(30)
                try:
                    last = ssh(host, f"grep -c . {remote}/out/results.jsonl 2>/dev/null; grep -q '] DONE ' {remote}/worker.log && echo DONE; pgrep -f '{remote}/jobs.json' >/dev/null || echo GONE", 120).stdout.split()
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
            ssh(host, f"rm -rf {remote}", 120)
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


def fail_page(symside: str, board: dict, error: str, now: str) -> str:
    return (
        f"<!doctype html><html><head><meta charset=utf-8><title>{_html.escape(symside)} daily FAIL</title>"
        "<style>body{font-family:system-ui,monospace;margin:12px;background:#0a0a0a;color:#e5e5e5}</style></head><body>"
        f"<h2>{_html.escape(symside)} — daily 30D recalculation FAILED</h2>"
        f"<div style='background:#5a1a1a;border:1px solid #a33;padding:8px;font-size:12px'>No current chart: {_html.escape(error)}<br>attempted {now} · "
        f"source board {_html.escape(os.path.basename(board.get('board_html') or '?'))} (published {_html.escape(str(board.get('published_utc')))}, cells {_html.escape(str(board.get('cells_vintage') or 'UNAVAILABLE'))})</div></body></html>\n"
    )


def atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def publish(boards: dict, results: list, run_id: str, started: str, partial: bool = False) -> dict:
    from tools.opt.chart_truth import verify_html

    CHARTS.mkdir(parents=True, exist_ok=True)
    by_sym = {r["symside"]: r for r in results}
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
                verify_html(text, symside, require_table=True)
                if parse_chart_metrics(text).get("gain_pct") != res["metrics"]["gain_pct"]:
                    raise AssertionError("final file gain differs from worker result")
                atomic_write(out, text)
                o = board.get("orig_metrics") or {}
                deltas = {k: (float(res["metrics"][k]) - float(o[k])) if res["metrics"].get(k) is not None and o.get(k) is not None else None for k in ("gain_pct", "bh_pct", "delta_vs_bh", "trades", "tim_pct", "max_dd_pct", "wr_pct")}
                entry.update({"status": "OK", "host": res["host"], "calculated_utc": res["calculated_utc"], "board_age_at_calc": age_text(parse_iso(board.get("cells_vintage")), parse_iso(res["calculated_utc"])), "metrics": res["metrics"], "deltas": deltas, "npz": res["npz"], "window": res.get("window"), "engine_md5": res.get("engine_md5")})
            except Exception as exc:
                error = f"publish check failed: {type(exc).__name__}: {str(exc)[:300]}"
        if error is not None:
            atomic_write(out, fail_page(symside, board, error, now))
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
    for e in sorted(manifest["entries"], key=lambda e: (e["status"] != "OK", -(e.get("metrics") or {}).get("gain_pct", -1e9) if e["status"] == "OK" else 0, e["symside"])):
        m, d, o = e.get("metrics") or {}, e.get("deltas") or {}, e.get("orig_metrics") or {}
        if e["status"] == "OK":
            halt = e.get("deterioration")
            rows.append(f"<tr><td><a href='{e['chart']}'>{e['symside']}</a></td><td{' style=color:#ef4444 title=' + chr(34) + _html.escape('; '.join(halt)) + chr(34) if halt else ''}>{'HALTED' if halt else 'OK'}</td><td>{_fmt(m.get('gain_pct'))}</td><td>{_fmt(o.get('gain_pct'))}</td><td>{_fmt(d.get('gain_pct'), 2, True)}</td><td>{_fmt(m.get('bh_pct'))}</td><td>{m.get('trades')}</td><td>{_fmt(m.get('max_dd_pct'))}</td><td>{_fmt(m.get('tim_pct'))}</td><td>{e.get('board_cells_vintage') or 'UNAVAILABLE'}</td><td>{e.get('board_age_at_calc')}</td><td>{e.get('calculated_utc')}</td></tr>")
        else:
            rows.append(f"<tr style='color:#ef4444'><td><a href='{e['chart']}'>{e['symside']}</a></td><td>FAIL</td><td colspan=10>{_html.escape(e.get('error', '')[:200])}</td></tr>")
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>V15 daily 30D charts</title>"
        "<style>body{font-family:system-ui,monospace;margin:12px;background:#0a0a0a;color:#e5e5e5}a{color:#93c5fd}table{border-collapse:collapse;font-size:11px}th{background:#1f2937;padding:4px}td{border:1px solid #333;padding:3px}</style></head><body>"
        f"<h2>V15 daily 30D charts — run {manifest['run_id']}</h2><p>started {manifest['started_utc']} · finished {manifest['finished_utc']} · {manifest['ok']}/{manifest['sym_sides']} OK · {manifest['fail']} FAIL{' · COMPLETE' if manifest['complete'] else ' · INCOMPLETE'} · {len((manifest.get('enforcement') or {}).get('halted') or [])} sym_sides HALTED (no open/augment; exits follow the board) — rules: " + _html.escape(", ".join(f"{k} {w} >= {v}" for k, w, v in DETERIORATION_RULES)) + ", gain turning non-positive</p>"
        "<table><thead><tr><th>sym_side</th><th>status</th><th>gain % today</th><th>gain % board</th><th>Δ gain</th><th>B&H %</th><th>trades</th><th>max DD %</th><th>TIM %</th><th>board cells calculated</th><th>board age at recalc</th><th>recalculated</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>\n"
    )


# USER 2026-10-30: "ANY number that deteriorates significantly gets lined up with priority in v15_fleet_scheduler and stops
# trading immediately". The board launcher is tools/fleet_dispatch.py since 2026-10-27 (v15_fleet_scheduler's S1 cron is
# disabled), so priority = data/fleet_priority.json on S1+S2; the stop = data/deterioration_halt.json in every
# vector_direct_trader root (no OPEN / AUGMENT; exits never blocked). Today vs the published board, same set:
DETERIORATION_RULES = (("gain_pct", "drop", 5.0), ("delta_vs_bh", "drop", 5.0), ("max_dd_pct", "rise", 2.0), ("wr_pct", "drop", 15.0))
HALT_FILE = ROOT / "data" / "deterioration_halt.json"
HALT_TARGETS = (("s1", "~/binance/data"), ("s5", "~/binance/data"), ("s5", "~/trc7d_root/data"))
PRIORITY_HOSTS = ("s1", "s2")
PRIORITY_REMOTE = "~/binance-sandbox/data/fleet_priority.json"
PRIORITY_MINE = ROOT / "data" / "reports" / "daily_charts_priority_added.json"


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
            r = ssh(host, f"test -d {ddir} || exit 3; cat > {ddir}/deterioration_halt.json.tmp && mv {ddir}/deterioration_halt.json.tmp {ddir}/deterioration_halt.json && md5sum {ddir}/deterioration_halt.json", 120, body)
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
            cur = json.loads(ssh(host, f"cat {PRIORITY_REMOTE} 2>/dev/null || echo '{{}}'", 120).stdout or "{}") or {}
            live = float(cur.get("until") or 0) > now
            sides = [s for s in (cur.get("sym_sides") or []) if live and s not in (mine_before - halted)]
            sides += [s for s in sorted(halted) if s not in sides]
            why = [w for w in str(cur.get("why") or "").split(" | ") if w and live and not w.startswith("DAILY_DETERIORATION")]
            if halted:
                why.append(f"DAILY_DETERIORATION {len(halted)} sides (tools/v15_daily_charts.py)")
            until = max(float(cur.get("until") or 0) if live else 0.0, now + 86400 if halted else 0.0)
            doc = {"sym_sides": sides, "until": until, "why": " | ".join(why)}
            r = ssh(host, f"cat > {PRIORITY_REMOTE}.tmp && mv {PRIORITY_REMOTE}.tmp {PRIORITY_REMOTE} && cat {PRIORITY_REMOTE}", 120, json.dumps(doc))
            status[host] = "OK" if json.loads(r.stdout or "{}") == doc else f"MISMATCH {r.stderr.strip()[-160:]}"
        except Exception as exc:
            status[host] = f"FAIL {exc}"
        print(f"[daily-charts] fleet_priority -> {host}: {status[host]} ({len(halted)} deterioration sides)", flush=True)
    if all(v == "OK" for v in status.values()):
        atomic_write(PRIORITY_MINE, json.dumps(sorted(halted)))
    return status


def enforce(manifest=None) -> dict:
    """USER 2026-10-30 "KEEP OUT OF THE TRADERS": deterioration is REPORTED only (manifest, index, events log); nothing is
    pushed to any trader root and nothing is written to the fleet priority file."""
    doc = update_halts(manifest)
    return {"deteriorated": sorted(doc["halts"]), "halt_push": "DISABLED (user: keep out of the traders)", "priority": "DISABLED"}


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
    manifest["enforcement"] = enforce(manifest)
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
