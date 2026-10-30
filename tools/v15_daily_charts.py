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


def ssh(host: str, cmd: str, timeout: int = 600, stdin=None) -> subprocess.CompletedProcess:
    return subprocess.run(SSH + [host, cmd], capture_output=True, text=True, timeout=timeout, input=stdin)


def resolve_remote(boards: dict) -> None:
    need = {s: b["overrides_md5"] for s, b in boards.items() if "overrides" not in b and "error" not in b}
    if not need:
        return
    for host, root in HOSTS.items():
        need = {s: m for s, m in need.items() if "overrides" not in boards[s]}
        if not need:
            break
        try:
            ssh(host, f"cat > /tmp/dc_need.json", 60, json.dumps(need))
            r = ssh(host, f"cd {root} && python3 tools/v15_daily_chart_worker.py resolve /tmp/dc_need.json", 900)
            found = json.loads(r.stdout or "{}")
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
    r = ssh(host, f"ls {root}/backtest_v8/indicators/", 60)
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
        hosts = [h for h in HOSTS if symbol in inv[h]]
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
            r = ssh(host, f"cd {HOSTS[host]} && nice -n 15 {WORKER_PY} -u tools/v15_daily_chart_worker.py render {remote}/jobs.json {remote}/out {procs} > {remote}/worker.log 2>&1; tail -2 {remote}/worker.log", 6 * 3600)
            print(f"[daily-charts] {host}: {r.stdout.strip()[-300:]} {r.stderr.strip()[-200:]}", flush=True)
            subprocess.run(["rsync", "-az", f"{host}:{remote}/out/", str(local) + "/"], check=True, timeout=1800)
            subprocess.run(["rsync", "-az", f"{host}:{remote}/worker.log", str(local) + "/"], timeout=300)
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
            rows.append(f"<tr><td><a href='{e['chart']}'>{e['symside']}</a></td><td>OK</td><td>{_fmt(m.get('gain_pct'))}</td><td>{_fmt(o.get('gain_pct'))}</td><td>{_fmt(d.get('gain_pct'), 2, True)}</td><td>{_fmt(m.get('bh_pct'))}</td><td>{m.get('trades')}</td><td>{_fmt(m.get('max_dd_pct'))}</td><td>{_fmt(m.get('tim_pct'))}</td><td>{e.get('board_cells_vintage') or 'UNAVAILABLE'}</td><td>{e.get('board_age_at_calc')}</td><td>{e.get('calculated_utc')}</td></tr>")
        else:
            rows.append(f"<tr style='color:#ef4444'><td><a href='{e['chart']}'>{e['symside']}</a></td><td>FAIL</td><td colspan=10>{_html.escape(e.get('error', '')[:200])}</td></tr>")
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>V15 daily 30D charts</title>"
        "<style>body{font-family:system-ui,monospace;margin:12px;background:#0a0a0a;color:#e5e5e5}a{color:#93c5fd}table{border-collapse:collapse;font-size:11px}th{background:#1f2937;padding:4px}td{border:1px solid #333;padding:3px}</style></head><body>"
        f"<h2>V15 daily 30D charts — run {manifest['run_id']}</h2><p>started {manifest['started_utc']} · finished {manifest['finished_utc']} · {manifest['ok']}/{manifest['sym_sides']} OK · {manifest['fail']} FAIL{' · COMPLETE' if manifest['complete'] else ' · INCOMPLETE'}</p>"
        "<table><thead><tr><th>sym_side</th><th>status</th><th>gain % today</th><th>gain % board</th><th>Δ gain</th><th>B&H %</th><th>trades</th><th>max DD %</th><th>TIM %</th><th>board cells calculated</th><th>board age at recalc</th><th>recalculated</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>\n"
    )


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
    if slot and not only:
        atomic_write(STATE, json.dumps({"last_slot_done": slot, "run_id": run_id, "ok": manifest["ok"], "fail": manifest["fail"]}))
    shutil.rmtree(STAGE / run_id, ignore_errors=True)
    print(f"[daily-charts] run {run_id} done: {manifest['ok']}/{manifest['sym_sides']} OK, {manifest['fail']} FAIL, complete={manifest['complete']}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
