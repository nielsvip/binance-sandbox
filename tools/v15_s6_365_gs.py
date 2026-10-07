#!/usr/bin/env python3
"""s6 365D+GS pipeline (USER 2026-10-07): for every tradeable sym_side with a live per_sym book config,
prepare the 30D+365D NPZ slices ONCE (RAM-hot), run the 365D backtest of the book config, then immediately
run graph_search on the same hot preps. NPZs stream first-file-first (compute starts after 1 NPZ while S1's
sync_indicators cron backfills the rest). Every number is a real v12 engine eval (same functions the pilot
uses); candidates come from the pilot's own scan_template_cands (same space as in-pilot diagnose/GS).
"""
import argparse
import concurrent.futures as cf
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
STOP = {"v": False}


def _sig_stop(signum, frame):
    STOP["v"] = True


def mem_avail_mb():
    try:
        for l in open("/proc/meminfo"):
            if l.startswith("MemAvailable"):
                return int(l.split()[1]) // 1024
    except Exception:
        pass
    return 999999


def ensure_npz(sym, src_host="10.0.0.3"):
    p = ROOT / "backtest_v8" / "indicators" / f"{sym}.npz"
    if p.exists() and p.stat().st_size > 1000000:
        return str(p)
    r = subprocess.run(["rsync", "-az", "--timeout=180", f"niels@{src_host}:~/binance-sandbox/backtest_v8/indicators/{sym}.npz", str(p)], capture_output=True, timeout=200)
    if r.returncode == 0 and p.exists() and p.stat().st_size > 1000000:
        return str(p)
    return ""


def run_side(ss, outdir, gs_budget=600.0, workers=6, ckptdir=None):
    import v15_pilot as P
    import per_sym_store as pss
    from tools import v15_diagnose_repair as DR
    from tools import v15_graph_search as GS
    from tools.opt import v12_pilot as VP
    from tools import v15_trade_autopsy as TA
    t00 = time.time()
    logl = []
    def log(m):
        line = f"[{ss}] {m}"
        print(line, flush=True)
        logl.append(line)
    sym = ss.rsplit("_", 1)[0]
    is_long = ss.endswith("_LONG")
    outdir = Path(outdir)
    ckptdir = Path(ckptdir or (outdir / "ckpt"))
    ckptdir.mkdir(parents=True, exist_ok=True)
    ckf = ckptdir / f"{ss}.json"
    ck = {}
    try:
        if ckf.exists():
            ck = json.loads(ckf.read_text())
    except Exception:
        ck = {}
    npz = ensure_npz(sym)
    if not npz:
        return {"sym_side": ss, "error": "no NPZ"}
    con = __import__("sqlite3").connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True)
    row = con.execute("SELECT full_config_json, cat_side FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
    con.close()
    if not row or not row[0]:
        return {"sym_side": ss, "error": "no book config"}
    book = json.loads(pss._maybe_decompress(row[0]) or "{}")
    if not book:
        return {"sym_side": ss, "error": "empty book config"}
    cat_side = row[1] or P.map_key_for_symside(ss)
    defaults = P.get_defaults_for_symside(ss)
    prep30 = VP.prepare_batch(ss, 30)
    prep365 = VP.prepare_batch(ss, 365)
    npzp = (prep365 or {}).get("npz_prepared") or {}
    try:
        t = npzp.get("timestamps")
        t = [x / 1000 if x > 1e11 else x for x in (list(t[-1:]) + list(t[:1]))]
        span = float((t[0] - t[1]) / 86400)
    except Exception:
        span = 0.0
    ex = cf.ThreadPoolExecutor(max_workers=workers)
    shut = {"v": False}
    orig_term = signal.getsignal(signal.SIGTERM)

    def _w_term(signum, frame):
        shut["v"] = True
    try:
        signal.signal(signal.SIGTERM, _w_term)
    except Exception:
        pass

    def one(prep, ov, wd, to):
        f = ex.submit(VP.evaluate_prepared_sanitized, prep, dict(ov), wd)
        try:
            return f.result(timeout=to)
        except Exception as e:
            log(f"EVAL_{wd}D_RAISED {type(e).__name__}: {str(e)[:160]}")
            try:
                f.cancel()
            except Exception:
                pass
            return None

    def many365(items, deadline):
        out = []
        for it in items:
            ov = it[0] if isinstance(it, (tuple, list)) else it
            if shut["v"]:
                from tools.v15_shutdown import V15Shutdown
                raise V15Shutdown("s6 worker shutdown")
            if deadline and time.time() > deadline:  # USER 2026-10-07: honor deadline (pad None, keep alignment)
                out.append((None, span))
                continue
            out.append((one(prep365, ov, 365, 180.0), span))
        return out

    def many30(items, phase, cum, deadline):
        out = []
        for lab, ov, c in items:
            if shut["v"]:
                from tools.v15_shutdown import V15Shutdown
                raise V15Shutdown("s6 worker shutdown")
            if deadline and time.time() > deadline:  # USER 2026-10-07: honor deadline (pad None, keep alignment)
                out.append((None, "past deadline"))
                continue
            try:
                out.append((one(prep30, ov, 30, 60.0), None))
            except Exception as e:
                out.append((None, str(e)[:60]))
        return out

    def many_ledger(items, phase, cum, deadline):
        out = []
        for lab, ov, c in items:
            if shut["v"]:
                from tools.v15_shutdown import V15Shutdown
                raise V15Shutdown("s6 worker shutdown")
            if deadline and time.time() > deadline:  # USER 2026-10-07: honor deadline (pad None, keep alignment)
                out.append((None, "past deadline"))
                continue
            try:
                f = ex.submit(VP.evaluate_prepared_sanitized, prep30, dict(ov), 30, True)
                r = f.result(timeout=120.0)
                keep = {k: r.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "wr_pct", "valid", "invalid_reason", "bh_pct", "behavior_fingerprint")}
                keep["_rows"] = TA.scaled_rows(r)
                out.append((keep, None))
            except Exception as e:
                out.append((None, str(e)[:80]))
        return out
    r30 = one(prep30, book, 30, 120.0) or {}
    r365 = one(prep365, book, 365, 300.0) or {}
    q365, why365 = P._qualifies_365d(r365, span)
    verdict = {"sym_side": ss, "span_365": round(span, 1), "w30": {k: r30.get(k) for k in ("gain_pct", "trades", "valid", "tim_pct", "max_dd_pct", "bh_pct")}, "w365": {k: r365.get(k) for k in ("gain_pct", "trades", "valid", "tim_pct", "max_dd_pct", "bh_pct")}, "q365": q365, "why365": why365}
    (outdir / f"{ss}_365.json").write_text(json.dumps(verdict, indent=1, default=str))
    log(f"365D done 30D={r30.get('gain_pct')}/{r30.get('trades')}tr 365D={r365.get('gain_pct')}/{r365.get('trades')}tr q365={q365}")
    venue = "CRYPTO" if cat_side.startswith("CRYPTO") else "STOCKS"
    side = "LONG" if is_long else "SHORT"
    tpath = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / f"TEMPLATE_{venue}_{side}.xlsx"
    cands, stats = P.scan_template_cands(tpath, defaults, cat_side, zero_on=True)
    log(f"cands rows={stats['rows']} eval={stats['eval']} skip={stats['skip']} zero={stats['zero_skipped']} filters={stats['filters']}")
    last_ckpt = {"t": 0.0}

    def ckpt(delta):
        try:
            if delta.get("memo"):
                ck.setdefault("memo", {}).update(delta["memo"])
            if delta.get("m365"):
                ck.setdefault("m365", {}).update(delta["m365"])
            if delta.get("hall_prov"):
                ck.setdefault("hall_prov", {}).update(delta["hall_prov"])
            if delta.get("autopsy"):
                ck["autopsy"] = delta["autopsy"]
            if delta.get("gs"):
                ck["gs"] = delta["gs"]
            if time.time() - last_ckpt["t"] >= 30.0:
                last_ckpt["t"] = time.time()
                ckf.write_text(json.dumps(ck))
        except Exception:
            pass
    base_m = DR.metrics(r30, r30.get("bh_pct"))
    ctx = {"defaults": defaults, "sanitize": lambda ov: P.sanitize_overrides(ov, defaults)[0], "same_val": P.same_val, "candidates": cands, "base_overrides": dict(book), "base_res": r30, "bh": r30.get("bh_pct"), "deadline": time.time() + gs_budget, "cat_side": cat_side, "eval_many": many30, "eval_ledger": lambda ov: one(prep30, ov, 30, 120.0) or {}, "eval_365": lambda ov: (one(prep365, ov, 365, 180.0), span), "qualifies_365": lambda r, s: P._qualifies_365d(r, s if s else span), "eval_many_365": many365, "eval_many_ledger": many_ledger, "close": [float(x) for x in ((prep30 or {}).get("npz_prepared") or {}).get("close", [])] or None, "npz": (prep30 or {}).get("npz_prepared") or {}, "is_long": is_long, "graph": GS.load_graph(cat_side), "log": log, "touch": lambda m: None, "shutdown_requested": lambda: shut["v"], "checkpoint": ckpt, "resume_memo": ck.get("memo") or {}, "resume_m365": ck.get("m365") or {}, "resume_hall_prov": ck.get("hall_prov") or {}, "resume_gs": ck.get("gs")}
    gs = GS.run(ctx)
    gs_out = {"sym_side": ss, "accepted": gs.get("accepted"), "accept_reason": gs.get("accept_reason"), "before": gs.get("before"), "after": gs.get("after"), "after_365": gs.get("after_365"), "q365_after": gs.get("q365_after"), "changes": gs.get("changes"), "best_overrides": gs.get("best_overrides"), "n_evals": gs.get("n_evals"), "n_evals_365": gs.get("n_evals_365"), "secs": gs.get("secs"), "ablation_top": (gs.get("ablation") or [])[:20], "steps": gs.get("steps"), "finalists": gs.get("finalists"), "iterations": gs.get("iterations"), "diagnosis_after": gs.get("diagnosis_after"), "macro": gs.get("macro")}
    (outdir / f"{ss}_gs.json").write_text(json.dumps(gs_out, indent=1, default=str))
    try:
        from tools import v15_thought_rundown as TR
        TR.rundown_side(ss, outdir)
    except Exception as e:
        log(f"rundown skipped {ss}: {str(e)[:80]}")
    try:
        ckf.unlink()
    except Exception:
        pass
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    try:
        signal.signal(signal.SIGTERM, orig_term)
    except Exception:
        pass
    return {"sym_side": ss, "w365_gain": r365.get("gain_pct"), "w365_trades": r365.get("trades"), "q365": q365, "gs_accepted": gs.get("accepted"), "gs_gain": (gs.get("after") or {}).get("gain"), "secs": round(time.time() - t00, 1)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--max-sides", type=int, default=0)
    ap.add_argument("--symsides", default="")
    ap.add_argument("--budget-gs", type=float, default=600.0)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--out", default=str(ROOT / "data" / "s6_365"))
    ap.add_argument("--npz-source", default="10.0.0.3")
    a = ap.parse_args()
    signal.signal(signal.SIGTERM, _sig_stop)
    signal.signal(signal.SIGINT, _sig_stop)
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    keys = json.loads((ROOT / "tradeable_keys.json").read_text())
    want = [k.split(":", 1)[-1] for k in keys]
    if a.symsides:
        only = {s.strip() for s in a.symsides.split(",") if s.strip()}
        want = [s for s in want if s in only]
    import sqlite3
    con = sqlite3.connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True)
    have = {r[0] for r in con.execute("SELECT sym_side FROM per_sym_active WHERE full_config_json IS NOT NULL")}
    con.close()
    todo = [s for s in want if s in have]
    progf = outdir / "progress.json"
    done = set()
    try:
        if progf.exists():
            done = set(json.loads(progf.read_text()).get("done", []))
    except Exception:
        pass
    todo = [s for s in todo if s not in done and not (outdir / f"{s}_gs.json").exists()]
    if a.max_sides > 0:
        todo = todo[:a.max_sides]
    print(f"[s6] sides={len(todo)} jobs={a.jobs} workers={a.workers} gs_budget={a.budget_gs:.0f}s", flush=True)
    with ProcessPoolExecutor(max_workers=a.jobs) as pool:
        futs = {}
        idx = 0
        while idx < len(todo) or futs:
            if STOP["v"]:
                print("[s6] shutdown requested, draining", flush=True)
                break
            while idx < len(todo) and len(futs) < a.jobs:
                if mem_avail_mb() < 5120:
                    print(f"[s6] low mem ({mem_avail_mb()}MB), waiting for a slot to free", flush=True)
                    break
                ss = todo[idx]
                idx += 1
                futs[pool.submit(run_side, ss, str(outdir), a.budget_gs, a.workers)] = ss
            if not futs:
                time.sleep(10)
                continue
            done_f, _ = cf.wait(list(futs), timeout=30, return_when=cf.FIRST_COMPLETED)
            for f in done_f:
                ss = futs.pop(f)
                try:
                    r = f.result(timeout=1)
                    print(f"[s6] DONE {ss} 365D={r.get('w365_gain')}/{r.get('w365_trades')}tr q={r.get('q365')} gs={r.get('gs_accepted')} gs_gain={r.get('gs_gain')} {r.get('secs')}s", flush=True)
                except Exception as e:
                    print(f"[s6] FAIL {ss} {str(e)[:160]}", flush=True)
                done.add(ss)
                try:
                    progf.write_text(json.dumps({"done": sorted(done)}))
                except Exception:
                    pass
    try:
        from tools import v15_lever_inventory as LI
        _inv = LI.build(outdir)
        (outdir / "lever_inventory.json").write_text(json.dumps(_inv, indent=1, default=str))
        (outdir / "lever_inventory.md").write_text(LI.render_md(_inv))
        print(f"[s6] inventory sides={_inv['n_sides']}", flush=True)
    except Exception as e:
        print(f"[s6] inventory skipped: {str(e)[:100]}", flush=True)
    print(f"[s6] finished {len(done)} sides", flush=True)


if __name__ == "__main__":
    main()
