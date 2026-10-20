#!/usr/bin/env python3
"""v18_ledger_replay — one-off bootstrap of BACKTEST TRADE LEDGERS (doc 17 §1.4 / §3.1 "ledger replay", work item E3 input).

WHY. No corpus holds trade-level results of backtests (delta logs = metrics per eval; native cache rows `ledger: None`).
The exit-loss ranker (doc 17 §3.2) needs trades. This tool takes, for every sym_side of each cat_side, the LATEST PUBLISHED
FINAL SET and runs ONE real engine eval with the ledger on, on the host that holds the NPZs (S2), and writes compact ledger
rows. Every number is the engine's own output (`tools.opt.v12_pilot.evaluate_prepared_sanitized(prepared, set, 30,
include_ledger=True)`); MFE/MAE are read from the same prepared NPZ arrays (high_15m / low_15m) between entry and exit bars;
post-exit continuation and the autopsy classes come from tools/v15_trade_autopsy (reused, not reimplemented).

SET PRECEDENCE (the order tools/eval_cache_rebuild.py uses, with the publish table first):
  1. data/hourly_reconfig/per_sym_store.db  per_sym_history  newest row per sym_side (overrides_json = the published set)
  2. data/reports/lifecycle_pilot/<SS>_v14_progress.json  cumulative_overrides / hustler_overrides / overrides / full_config
  3. SPREADSHEETS/V15_V16_CELL_BY_CELL/<SS>_*matrix.xlsx  column C of every switch sheet
GLOBAL_ONLY keys are stripped exactly as the pilot strips them from per-sym sets. Tokenized stocks are skipped.

GAIN NORMALISATION (stated, never implied). The engine's gain_pct = net $ / max_invested_dollars x 100 (peak cost basis,
gain definition 2026-10-17; verified on S2: ETHUSDC_SHORT -25.18/360.0 = -6.99 = gain_pct). Each row carries
  pnl_pct     the engine's per-trade return on that trade's deployed $ (a trade-level %, NOT additive to gain)
  pnl_pp      pnl_dollars / max_invested_dollars x 100  = pct-points of the board's peak cost basis; sum(pnl_pp) == gain_pct
The ranker sums pnl_pp, never pnl_pct.

WINDOW TRUTH. The published set was fitted on an earlier NPZ; the replay measures it on TODAY's 30-day window, so the
replayed gain differs from the claimed one (board gain is window-bound). Both are written (`claimed_gain`, `gain_pct`).

usage
  Mac:  python3 tools/v18_ledger_replay.py sets [--since-days 14] [--out data/learn/replay_sets.jsonl.gz]
  S2:   nohup nice -n 10 python3 tools/v18_ledger_replay.py run --sets ~/v18_learn/replay_sets.jsonl.gz \
            --root ~/binance-sandbox --out-dir ~/v18_learn/trade_ledgers --workers 4 --apply > ~/logs/v18_ledger_replay.log 2>&1 &
  Mac:  rsync -a s2:v18_learn/trade_ledgers/ data/learn/trade_ledgers/
Dry-run is the default for `run` (lists what it would replay and the NPZ verdict, writes nothing).
"""
from __future__ import annotations

import argparse
import glob
import gzip
import hashlib
import json
import os
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

POST_BARS = 32
STORE_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
PROGRESS_DIR = ROOT / "data" / "reports" / "lifecycle_pilot"
XLSX_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"


def _utc(epoch=None):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() if epoch is None else epoch))


def _cat_side(ss):
    from tools.v18_learn_common import cat_side_of
    return cat_side_of(ss)


def set_sha(d):
    return hashlib.sha256(json.dumps(d, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


def history_sets(db=STORE_DB):
    """{sym_side: row} newest per_sym_history row per sym_side (by epoch), overrides_json parsed."""
    out = {}
    if not Path(db).exists():
        return out
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=120.0)
    try:
        q = ("SELECT h.sym_side, h.cat_side, h.updated_at, h.epoch, h.winning_tag, h.gain_pct, h.trades, h.overrides_json "
             "FROM per_sym_history h JOIN (SELECT sym_side, MAX(COALESCE(epoch,0)) AS e FROM per_sym_history "
             "WHERE overrides_json IS NOT NULL AND length(overrides_json) > 100 GROUP BY sym_side) m "
             "ON h.sym_side = m.sym_side AND COALESCE(h.epoch,0) = m.e")
        for ss, cs, upd, ep, tag, gain, trades, ovj in con.execute(q):
            try:
                ov = json.loads(ovj)
            except Exception:
                continue
            if isinstance(ov, dict) and ov and ss not in out:
                out[ss] = {"source": "per_sym_history", "cat_side_db": cs, "published_at": upd, "published_epoch": ep,
                           "winning_tag": tag, "claimed_gain": gain, "claimed_trades": trades, "set": ov}
    finally:
        con.close()
    return out


def fallback_set(ss):
    """progress JSON -> xlsx column C, exactly tools/eval_cache_rebuild.start_set_for."""
    from tools.eval_cache_rebuild import start_set_for
    d, src = start_set_for(ss, str(PROGRESS_DIR), str(XLSX_DIR))
    return (d, src) if d else (None, src)


def build_sets(a):
    from tools.v18_learn_common import excluded_key_reason, global_only_keys, is_tokenized
    since = time.time() - float(a.since_days) * 86400.0
    hist = history_sets(Path(a.db))
    universe = set(hist)
    for p in glob.glob(str(PROGRESS_DIR / "*_v14_progress.json")):
        universe.add(os.path.basename(p)[: -len("_v14_progress.json")])
    counts = {"history": 0, "progress_json": 0, "xlsx_col_c": 0, "none": 0, "tokenized": 0, "no_cat_side": 0, "older_than_window": 0}
    rows, skipped = [], []
    gok = global_only_keys()
    for ss in sorted(universe):
        cs = _cat_side(ss)
        if cs is None:
            counts["no_cat_side"] += 1
            skipped.append({"sym_side": ss, "why": "no_cat_side"})
            continue
        if is_tokenized(ss):
            counts["tokenized"] += 1
            skipped.append({"sym_side": ss, "why": "tokenized_stock"})
            continue
        h = hist.get(ss)
        if h is not None:
            if (h.get("published_epoch") or 0) < since:
                counts["older_than_window"] += 1
                skipped.append({"sym_side": ss, "why": f"latest publish {h.get('published_at')} older than {a.since_days} d"})
                continue
            rec = dict(h)
            counts["history"] += 1
        else:
            d, src = fallback_set(ss)
            if not d:
                counts["none"] += 1
                skipped.append({"sym_side": ss, "why": "no published set (history/progress/xlsx)"})
                continue
            counts[src] += 1
            rec = {"source": src, "published_at": None, "published_epoch": None, "winning_tag": None, "claimed_gain": None,
                   "claimed_trades": None, "set": d}
        stripped = sorted(k for k in rec["set"] if k in gok)
        for k in stripped:
            rec["set"].pop(k, None)
        rec.update({"sym_side": ss, "cat_side": cs, "global_only_stripped": stripped, "n_keys": len(rec["set"]),
                    "set_sha256": set_sha(rec["set"]),
                    "n_ablation_keys_true": sum(1 for k, v in rec["set"].items() if excluded_key_reason(k) == "ABLATION_lever" and v is True)})
        rows.append(rec)
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_name(out.name + ".tmp")
    with gzip.open(tmp, "wt") as fh:
        for r in rows:
            fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")
    os.replace(tmp, out)
    side = out.with_name(out.name.replace(".jsonl.gz", "") + "_skipped.json")
    side.write_text(json.dumps({"at": _utc(), "counts": counts, "skipped": skipped}, indent=1))
    per_cs = {}
    for r in rows:
        per_cs[r["cat_side"]] = per_cs.get(r["cat_side"], 0) + 1
    print(json.dumps({"out": str(out), "sets": len(rows), "per_cat_side": per_cs, "counts": counts, "skipped_file": str(side)}, indent=1))
    return 0


# ---------------------------------------------------------------------------------------------------------------- run


def npz_file(sym_side, root):
    from tools.opt.evaluate_v12 import split_symside, resolve_tokenised
    sym, _side = split_symside(sym_side)
    npz_sym, _t = resolve_tokenised(sym)
    for pfx in ("backtest_v8", "backtest_v7"):
        p = Path(root) / pfx / "indicators" / f"{npz_sym}.npz"
        if p.exists():
            return p
    return None


def _md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h.update(b)
    return h.hexdigest()


def _fav(p0, p1, is_long):
    return ((p1 - p0) / p0 * 100.0) * (1 if is_long else -1) if p0 else 0.0


def ledger_rows(res, npz, is_long, eval_id):
    """Compact rows from one engine result. Uses TA.realised/TA.classify for the trade set, post-exit and classes."""
    from tools import v15_trade_autopsy as TA
    from tools.v18_learn_common import entry_family, exit_family
    ledger = res.get("ledger") or []
    raw = [t for t in ledger if isinstance(t, dict) and "pnl_dollars" in t and not t.get("seeded_prewindow")]
    trades = TA.realised(ledger)
    close = [float(x) for x in npz["close"]]
    hi = npz.get("high_15m")
    lo = npz.get("low_15m")
    hi = [float(x) for x in hi] if hi is not None else close
    lo = [float(x) for x in lo] if lo is not None else close
    TA.classify(trades, close, is_long)
    n = len(close)
    peak = float(res.get("max_invested_dollars") or 0.0)
    open_ts = {}
    for e in ledger:
        if isinstance(e, dict) and e.get("type") == "OPEN" and e.get("bar") is not None:
            open_ts.setdefault(int(e["bar"]), e.get("ts"))
    out, mis = [], 0
    for i, (t, r) in enumerate(zip(trades, raw)):
        be, bx = t["be"], t["bx"]
        p_in = t["entry_price"] or (close[be] if 0 <= be < n else 0.0)
        if 0 <= be < n and p_in and abs(close[be] - p_in) / p_in > 0.02:
            mis += 1
        fav_hi, fav_lo = (hi, lo) if is_long else (lo, hi)
        seg = range(max(0, be + 1), min(n, bx + 1)) if be >= 0 else range(0)
        mfe = max([_fav(p_in, fav_hi[j], is_long) for j in seg] + [0.0])
        mae = min([_fav(p_in, fav_lo[j], is_long) for j in seg] + [0.0])
        unreal = bool(r.get("unrealised")) or str(r.get("exit_reason") or "") == "FINAL_MTM"
        out.append({"kind": "trade", "eval_id": eval_id, "trade_no": i, "type": t.get("type"), "bar_entry": be, "bar_exit": bx,
                    "bars_held": int(r.get("bars_held") if r.get("bars_held") is not None else max(0, bx - be)),
                    "ts_entry": open_ts.get(be), "ts_exit": r.get("ts"),
                    "entry_reason": t["entry_reason"], "exit_reason": t["exit_reason"],
                    "entry_family": entry_family(t["entry_reason"]), "exit_family": exit_family(t["exit_reason"]),
                    "pnl_pct": round(t["pnl_pct"], 6), "pnl_dollars": round(t["pnl"], 6),
                    "pnl_pp": round(t["pnl"] / peak * 100.0, 6) if peak > 0 else None,
                    "deployed": round(float(r.get("deployed") or 0.0), 6), "entry_price": p_in, "exit_price": t["exit_price"],
                    "mfe_pct": round(mfe, 4), "mae_pct": round(mae, 4), "post_exit_32b_pct": t.get("post_pct"),
                    "autopsy_cls": t.get("cls"), "n_aug": t.get("n_aug"), "unrealised_mark": unreal})
    return out, mis


def wait_for_load(max_load=None, poll=20.0, max_wait=1800.0):
    """S2 load law: never start an engine eval while the 1-min load is above V18_MAX_LOAD (default 14)."""
    lim = float(max_load if max_load is not None else os.environ.get("V18_MAX_LOAD", "14"))
    waited = 0.0
    while os.getloadavg()[0] > lim and waited < max_wait:
        time.sleep(poll)
        waited += poll
    return waited


def replay_one(job):
    """Worker: one sym_side -> (meta, rows) or (meta_with_skip, [])."""
    rec, root = job
    ss = rec["sym_side"]
    waited = wait_for_load()
    t0 = time.time()
    meta = {"kind": "meta", "sym_side": ss, "cat_side": rec["cat_side"], "set_source": rec.get("source"),
            "published_at": rec.get("published_at"), "winning_tag": rec.get("winning_tag"), "claimed_gain": rec.get("claimed_gain"),
            "claimed_trades": rec.get("claimed_trades"), "set_sha256": rec.get("set_sha256"), "n_keys": rec.get("n_keys"),
            "host": os.uname().nodename, "replayed_at": _utc(), "fn": "tools.opt.v12_pilot.evaluate_prepared_sanitized",
            "window_days": 30, "include_ledger": True, "load_wait_s": waited}
    try:
        from tools.opt import v12_pilot as VP
        p = npz_file(ss, root)
        if p is None:
            meta["skip"] = "NPZ_MISSING"
            return meta, []
        md5_a = _md5(p)
        prep = VP.prepare_batch(ss, 30)
        if prep is None:
            meta["skip"] = "PREPARE_FAILED"
            return meta, []
        if isinstance(prep, dict) and prep.get("_stale"):
            meta["skip"] = "NPZ_STALE"
            meta["skip_detail"] = prep["_stale"]
            return meta, []
        npz = prep.get("npz_prepared") or {}
        ts = npz.get("timestamps")
        res = VP.evaluate_prepared_sanitized(prep, dict(rec["set"]), 30, include_ledger=True)
        md5_b = _md5(p)
        eval_id = hashlib.sha1(f"{ss}|{md5_a}|{rec.get('set_sha256')}|30".encode()).hexdigest()[:20]
        rows, mis = ledger_rows(res, npz, bool(prep.get("is_long")), eval_id)
        meta.update({"eval_id": eval_id, "npz_path": str(p), "npz_md5": md5_a, "npz_md5_after": md5_b,
                     "npz_changed_during_replay": md5_a != md5_b, "bars": len(ts) if ts is not None else None,
                     "window_start": _utc(int(ts[0])) if ts is not None and len(ts) else None,
                     "window_end": _utc(int(ts[-1])) if ts is not None and len(ts) else None,
                     "gain_pct": res.get("gain_pct"), "trades": res.get("trades"), "valid": res.get("valid"),
                     "invalid_reason": res.get("invalid_reason"), "tim_pct": res.get("tim_pct"), "max_dd_pct": res.get("max_dd_pct"),
                     "wr_pct": res.get("wr_pct"), "pool_sharpe": res.get("pool_sharpe"), "bh_pct": res.get("bh_pct"),
                     "net_gain_dollars": res.get("net_gain_dollars"), "max_invested_dollars": res.get("max_invested_dollars"),
                     "behavior_fingerprint": res.get("behavior_fingerprint"), "n_rows": len(rows), "misaligned_entries": mis,
                     "sum_pnl_pp": round(sum(r["pnl_pp"] or 0.0 for r in rows), 6), "secs": round(time.time() - t0, 3)})
        return meta, rows
    except Exception as e:  # one bad sym_side never stops the replay
        meta["skip"] = "ERROR"
        meta["skip_detail"] = f"{type(e).__name__}: {e}"
        return meta, []


def run(a):
    root = Path(os.path.expanduser(a.root)).resolve()
    os.chdir(root)
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))
    with gzip.open(os.path.expanduser(a.sets), "rt") as fh:
        recs = [json.loads(l) for l in fh if l.strip()]
    if a.only:
        want = set(a.only.split(","))
        recs = [r for r in recs if r["sym_side"] in want]
    if a.limit:
        recs = recs[: a.limit]
    out_dir = Path(os.path.expanduser(a.out_dir))
    print(f"[replay] {len(recs)} sets root={root} out={out_dir} workers={a.workers} apply={a.apply} {_utc()}", flush=True)
    if not a.apply:
        miss = [r["sym_side"] for r in recs if npz_file(r["sym_side"], root) is None]
        print(json.dumps({"would_replay": len(recs) - len(miss), "npz_missing": miss[:200], "n_npz_missing": len(miss)}, indent=1))
        return 0
    done = set()
    if a.resume:
        for p in out_dir.glob("*/*.jsonl"):
            done.add(p.stem)
        recs = [r for r in recs if r["sym_side"] not in done]
        print(f"[replay] resume: {len(done)} already written, {len(recs)} to go", flush=True)
    summary = {"at": _utc(), "host": os.uname().nodename, "root": str(root), "replayed": {}, "skipped": {}}
    jobs = [(r, str(root)) for r in recs]
    t0 = time.time()
    if a.workers > 1:
        import multiprocessing as mp
        ctx = mp.get_context("fork")
        pool = ctx.Pool(a.workers, maxtasksperchild=25)
        it = pool.imap_unordered(replay_one, jobs, chunksize=1)
    else:
        pool = None
        it = map(replay_one, jobs)
    for i, (meta, rows) in enumerate(it, 1):
        cs, ss = meta["cat_side"], meta["sym_side"]
        if meta.get("skip"):
            summary["skipped"][ss] = {"cat_side": cs, "why": meta["skip"], "detail": meta.get("skip_detail")}
            print(f"[replay] {i}/{len(jobs)} SKIP {ss} {meta['skip']} {str(meta.get('skip_detail') or '')[:160]}", flush=True)
            continue
        d = out_dir / cs
        d.mkdir(parents=True, exist_ok=True)
        tmp = d / f".{ss}.jsonl.tmp"
        with open(tmp, "w") as fh:
            fh.write(json.dumps(meta, default=str) + "\n")
            for r in rows:
                fh.write(json.dumps(r, default=str, separators=(",", ":")) + "\n")
        os.replace(tmp, d / f"{ss}.jsonl")
        summary["replayed"][ss] = {"cat_side": cs, "trades": meta.get("trades"), "rows": len(rows), "gain_pct": meta.get("gain_pct"),
                                   "claimed_gain": meta.get("claimed_gain"), "npz_md5": meta.get("npz_md5"), "secs": meta.get("secs")}
        print(f"[replay] {i}/{len(jobs)} OK {ss} gain={meta.get('gain_pct')} trades={meta.get('trades')} rows={len(rows)} "
              f"sum_pp={meta.get('sum_pnl_pp')} {meta.get('secs')}s", flush=True)
    if pool is not None:
        pool.close()
        pool.join()
    summary["wall_s"] = round(time.time() - t0, 1)
    per = {}
    for v in summary["replayed"].values():
        per.setdefault(v["cat_side"], {"replayed": 0, "skipped": 0})["replayed"] += 1
    for v in summary["skipped"].values():
        per.setdefault(v["cat_side"], {"replayed": 0, "skipped": 0})["skipped"] += 1
    summary["per_cat_side"] = per
    out_dir.mkdir(parents=True, exist_ok=True)
    prev = out_dir / "_replay_summary.json"
    if prev.exists() and a.resume:
        try:
            old = json.loads(prev.read_text())
            for k in ("replayed", "skipped"):
                for ss, v in old.get(k, {}).items():
                    summary[k].setdefault(ss, v)
            for ss in summary["replayed"]:
                summary["skipped"].pop(ss, None)
        except Exception:
            pass
    prev.write_text(json.dumps(summary, indent=1, default=str))
    print(json.dumps({"wall_s": summary["wall_s"], "per_cat_side": per}, indent=1), flush=True)
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("sets", help="Mac: build the replay set file from the publish table / progress / xlsx")
    p.add_argument("--db", default=str(STORE_DB))
    p.add_argument("--since-days", type=float, default=14.0)
    p.add_argument("--out", default=str(ROOT / "data" / "learn" / "replay_sets.jsonl.gz"))
    p = sub.add_parser("run", help="S2: replay every set with the ledger on")
    p.add_argument("--sets", required=True)
    p.add_argument("--root", default=str(ROOT), help="repo/sandbox root whose engine + NPZs are used")
    p.add_argument("--out-dir", default=str(ROOT / "data" / "learn" / "trade_ledgers"))
    p.add_argument("--workers", type=int, default=4)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--only", default="")
    p.add_argument("--resume", action="store_true")
    p.add_argument("--apply", action="store_true")
    a = ap.parse_args(argv)
    if getattr(a, "workers", 0) > 4:
        print("[replay] workers capped at 4 (S2 load law)", flush=True)
        a.workers = 4
    return build_sets(a) if a.cmd == "sets" else run(a)


if __name__ == "__main__":
    sys.exit(main())
