#!/usr/bin/env python3
"""Worker side of the daily V15 30D chart refresh (tools/v15_daily_charts.py drives it from the Mac).

Runs on S1/S5 where the NPZs live. Two modes, both read-only on every shared store:
  resolve <need.json>                 -> {sym_side: {"source", "overrides"}} for sets whose md5 equals the board manifest's
                                         overrides_md5 (published_selection.json, then run progress JSONs). Exact md5 or nothing.
  render <jobs.json> <out_dir> <procs> -> one <SYM_SIDE>_30d_daily.html per job via tools.opt.hires_chart.generate_hires (the real
                                         evaluate_month / v12_quick_engine path on this host's NPZ) + results.jsonl.
A job whose NPZ changed while it was evaluated is retried once and then reported FAIL; nothing is ever substituted.
"""
import datetime
import glob
import hashlib
import json
import os
import re
import socket
import sys
import time
import traceback
from multiprocessing import Pool
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
HOME = os.path.expanduser("~")


def set_md5(overrides) -> str:
    return hashlib.md5(json.dumps(overrides or {}, sort_keys=True, default=str).encode()).hexdigest()


def file_md5(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _num(pattern: str, text: str):
    m = re.search(pattern, text)
    return float(m.group(1)) if m else None


def parse_chart_metrics(html_text: str) -> dict:
    """Metrics exactly as printed by a hires chart (same parser for the original board and the new chart)."""
    out = {}
    m = re.search(r"const HEADER_VERIFIED = (\{.*?\});", html_text)
    if m:
        hv = json.loads(m.group(1))
        out.update({k: hv.get(k) for k in ("gain_pct", "trades", "max_dd_pct", "peak_capital", "pnl_dollars")})
    i = html_text.find("— FINAL</b>")
    head = re.sub(r"<[^>]+>", " ", html_text[i : i + 4000] if i >= 0 else html_text[:20000]).replace("&nbsp;", " ")
    if "gain_pct" not in out:
        out["gain_pct"] = _num(r"(?:ledger )?gain (-?[\d.]+)%", head)
        out["trades"] = _num(r"trades (\d+)", head)
        out["max_dd_pct"] = _num(r"DD (-?[\d.]+)%", head)
        out["peak_capital"] = _num(r"peak \$(-?[\d.]+)", head)
    out["bh_pct"] = _num(r"BH (-?[\d.]+)%", head)
    out["delta_vs_bh"] = _num(r"Δ (-?[\d.]+)%", head)
    out["tim_pct"] = _num(r"TIM (-?[\d.]+)%", head)
    out["wr_pct"] = _num(r"WR (-?[\d.]+)%", head)
    out["dd_mtm_pct"] = _num(r"DDmtm (-?[\d.]+)%", head)
    out["pool_sharpe"] = _num(r"pool_sharpe=([+-]?[\d.]+)", head)
    out["window_bars"] = _num(r"window (\d+) bars", head)
    lab = re.search(r"const LABELS = \[\"([^\"]*)\"", html_text)
    lab_end = re.search(r"\"([^\"]*)\"\];\s*const TRADES", html_text)
    out["window_first_bar"] = lab.group(1) if lab else None
    out["window_last_bar"] = lab_end.group(1) if lab_end else None
    if out.get("trades") is not None:
        out["trades"] = int(out["trades"])
    return out


def resolve(need_path: str) -> None:
    need = json.loads(Path(need_path).read_text())
    found = {}
    try:
        sel = json.loads((ROOT / "data" / "published_selection.json").read_text()).get("sym_sides") or {}
        for symside, row in sel.items():
            ov = row.get("overrides")
            if symside in need and isinstance(ov, dict) and set_md5(ov) == need[symside]:
                found[symside] = {"source": f"{socket.gethostname()}:data/published_selection.json history_id={row.get('history_id')}", "overrides": ov}
    except Exception as exc:
        print(f"[resolve] published_selection unreadable: {exc}", file=sys.stderr)
    patterns = [HOME + "/v15_*/progress/%s_v14_progress.json", str(ROOT) + "/data/reports/lifecycle_pilot*/%s_v14_progress.json", HOME + "/v15_*/*/%s_v14_progress.json"]
    for symside, want in need.items():
        if symside in found:
            continue
        for pattern in patterns:
            for path in glob.glob(pattern % symside):
                try:
                    doc = json.loads(Path(path).read_text())
                except Exception:
                    continue
                for key in ("cumulative_overrides", "published_overrides", "final_overrides"):
                    ov = doc.get(key)
                    if isinstance(ov, dict) and ov and set_md5(ov) == want:
                        found[symside] = {"source": f"{socket.gethostname()}:{path}:{key}", "overrides": ov}
                        break
                if symside in found:
                    break
            if symside in found:
                break
    sys.stdout.write(json.dumps(found, default=str))


def _render_one(job: dict) -> dict:
    os.chdir(ROOT)
    symside = job["symside"]
    out_dir = Path(job["out_dir"])
    res = {"symside": symside, "host": socket.gethostname(), "status": "FAIL", "set_md5": set_md5(job["overrides"])}
    npz_path = ROOT / "backtest_v8" / "indicators" / f"{symside.rsplit('_', 1)[0]}.npz"
    t0 = time.time()
    for attempt in (1, 2):
        try:
            if not npz_path.exists():
                res["error"] = f"NPZ missing on {res['host']}: {npz_path.name}"
                return res
            md5_before = file_md5(npz_path)
            from tools.opt.hires_chart import generate_hires
            from tools.opt.lifecycle_pilot import _config_and_month_npz

            out = generate_hires(symside, dict(job["overrides"]), 30, out_name=job["out_name"], heading=job["heading"], out_dir=str(out_dir))
            window = _config_and_month_npz(symside, dict(job["overrides"]), window_days=30)[6]
            md5_after = file_md5(npz_path)
            if md5_before != md5_after:
                res["error"] = f"NPZ changed during eval (attempt {attempt})"
                continue
            text = Path(out).read_text(encoding="utf-8")
            res.update(
                {
                    "status": "OK",
                    "html": str(out),
                    "metrics": parse_chart_metrics(text),
                    "npz": {"name": npz_path.name, "md5": md5_after, "mtime_utc": datetime.datetime.fromtimestamp(npz_path.stat().st_mtime, datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")},
                    "window": {k: window.get(k) for k in ("start_timestamp", "end_timestamp", "bars", "execution_bars", "npz_provenance") if k in window},
                    "engine_md5": file_md5(ROOT / "v12_quick_engine.py"),
                    "calculated_utc": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "seconds": round(time.time() - t0, 1),
                }
            )
            res.pop("error", None)
            return res
        except Exception as exc:
            res["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
            res["trace"] = traceback.format_exc()[-1200:]
            return res
    return res


def render(jobs_path: str, out_dir: str, procs: int) -> None:
    jobs = json.loads(Path(jobs_path).read_text())
    Path(out_dir).mkdir(parents=True, exist_ok=True)
    for job in jobs:
        job["out_dir"] = out_dir
    results = Path(out_dir) / "results.jsonl"
    with Pool(procs, maxtasksperchild=4) as pool, open(results, "a", encoding="utf-8") as fh:
        for res in pool.imap_unordered(_render_one, jobs):
            fh.write(json.dumps(res, default=str) + "\n")
            fh.flush()
            print(f"[daily-chart-worker] {res['symside']} {res['status']} {res.get('error', '')[:160]}", flush=True)
    print(f"[daily-chart-worker] DONE {len(jobs)} jobs -> {results}", flush=True)


if __name__ == "__main__":
    if sys.argv[1] == "resolve":
        resolve(sys.argv[2])
    elif sys.argv[1] == "render":
        render(sys.argv[2], sys.argv[3], int(sys.argv[4]))
    else:
        raise SystemExit("usage: resolve <need.json> | render <jobs.json> <out_dir> <procs>")
