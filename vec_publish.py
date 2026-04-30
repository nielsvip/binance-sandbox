#!/usr/bin/env python3
"""vec_publish.py — export vec_sweep top configs as JSONL trades for the
chart visualizer at http://127.0.0.1:5077/.

chart_server.py reads from $V8_TRADES_OUT_DIR (default /tmp/v8_trades) with
file naming `{run_id}__{symbol}.jsonl`. Each line is one trade:
  {"entry_ts": int, "exit_ts": int, "side": "LONG"|"SHORT",
   "entry_price": float, "exit_price": float, "pnl_pct": float}

This script:
1. Pulls top-N configs from vec_sweep.db (any mode, sample-floor enforced).
2. For each (config, sym), re-evaluates from NPZ to recover entry/exit
   timestamps + prices alongside pnl.
3. Writes JSONL to TRADES_DIR.
4. After publishing, the visualizer's /runs endpoint lists each run_id and
   /backtest_trades?run=...&sym=... overlays trades on the price chart.

Usage:
  # Publish top 50 configs from streaming-pooled mode across local NPZs:
  python3 vec_publish.py --mode 'streamed:crypto48' --top-n 50

  # Publish from a remote DB (e.g. S1's running streamed_48):
  python3 vec_publish.py --remote s1-int --remote-db /home/niels/binance-sandbox/data/vec_sweep.db --top-n 50

  # Publish a single config by hash:
  python3 vec_publish.py --hash e2e6a0f741b6cb09df8c

  # Reset trades dir + re-publish everything:
  python3 vec_publish.py --reset --top-n 100
"""
from __future__ import annotations
import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

REPO = Path(__file__).resolve().parent
sys.path.insert(0, str(REPO))
import vec_sweep as vs
import metrics_guard as mg

TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))


def _trade_records(entry_mask: np.ndarray, exit_mask: np.ndarray,
                   close: np.ndarray, ts: np.ndarray, side: str,
                   sym: str, entry_reason: str, exit_reason: str) -> List[Dict]:
    """Same logic as vec_sweep._trade_returns but emits the rich JSONL schema
    chart_server expects (matches existing canon_crypto_1 file format)."""
    entry_bars = np.flatnonzero(entry_mask)
    if entry_bars.size == 0:
        return []
    exit_bars = np.flatnonzero(exit_mask)
    out: List[Dict] = []
    last_exit = -1
    n = len(close)
    direction = +1 if side == "LONG" else -1
    for e in entry_bars:
        if e <= last_exit:
            continue
        if exit_bars.size:
            idx = np.searchsorted(exit_bars, e + 1)
            xb = int(exit_bars[idx]) if idx < exit_bars.size else (n - 1)
        else:
            xb = n - 1
        ep = float(close[e]); xp = float(close[xb])
        if ep <= 0 or not np.isfinite(ep) or not np.isfinite(xp):
            continue
        r = (xp - ep) / ep
        if direction < 0:
            r = -r
        out.append({
            "symbol": sym,
            "side": side,
            "entry_type": entry_reason,
            "entry_reason": entry_reason,
            "exit_reason": exit_reason,
            "entry_bar": int(e),
            "entry_ts": int(ts[e]),
            "entry_price": round(ep, 6),
            "exit_bar": int(xb),
            "exit_ts": int(ts[xb]),
            "exit_price": round(xp, 6),
            "pnl_pct": round(r * 100.0, 6),
            "pnl_usd": 0.0,
            "duration_bars": int(xb - e),
            "stream": "vec_sweep",
        })
        last_exit = xb
        if xb >= n - 1:
            break
    return out


def evaluate_with_trades(npz: Dict[str, np.ndarray], cfg: vs.Config) -> List[Dict]:
    cache = vs.MaskCache(npz, limit=600)
    close = npz["close_3m"]
    ts = npz.get("timestamps")
    if ts is None:
        return []
    el = cache.and_many(cfg.entry_long) if cfg.entry_long else np.zeros(len(close), dtype=bool)
    xl = cache.and_many(cfg.exit_long)  if cfg.exit_long  else np.zeros(len(close), dtype=bool)
    es = cache.and_many(cfg.entry_short) if cfg.entry_short else np.zeros(len(close), dtype=bool)
    xs = cache.and_many(cfg.exit_short)  if cfg.exit_short  else np.zeros(len(close), dtype=bool)
    return _trade_records(el, xl, close, ts, "LONG") + _trade_records(es, xs, close, ts, "SHORT")


def fetch_remote_db(remote: str, remote_db: str) -> Path:
    """Lean dump: only configs + results tables (skip streaming_returns blobs
    which can be GBs). We re-evaluate trades from NPZ locally so we don't need
    the raw returns blobs. Dump size drops from ~2GB to ~5MB."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="vec_publish_"))
    local_db = tmp_dir / "vec_sweep_remote.db"
    print(f"[publish] dumping {remote}:{remote_db} (configs+results only) → {local_db}")
    dump_script = (
        f"import sqlite3,json,sys\n"
        f"con = sqlite3.connect({remote_db!r})\n"
        f"con.execute('PRAGMA wal_checkpoint(PASSIVE)')\n"
        f"out = sqlite3.connect(':memory:')\n"
        f"out.executescript('''\n"
        f"CREATE TABLE configs (id INTEGER PRIMARY KEY, config_hash TEXT UNIQUE, config_json TEXT, label TEXT, created_at TEXT);\n"
        f"CREATE TABLE results (id INTEGER PRIMARY KEY, config_id INTEGER, symbol TEXT, n_syms INTEGER, years REAL, pool_sharpe REAL, sym_sharpe REAL, trades INTEGER, trades_long INTEGER, trades_short INTEGER, acc_gain_pct REAL, avg_gain_trade REAL, gain_per_yr REAL, gain_sym_yr REAL, max_dd_pct REAL, win_rate_pct REAL, verdict TEXT, mode TEXT, created_at TEXT);\n"
        f"''')\n"
        f"# Bulk-copy rows\n"
        f"out.executemany('INSERT INTO configs(id,config_hash,config_json,label,created_at) VALUES(?,?,?,?,?)',\n"
        f"    [r for r in con.execute('SELECT id,config_hash,config_json,label,created_at FROM configs')])\n"
        f"out.executemany('INSERT INTO results(id,config_id,symbol,n_syms,years,pool_sharpe,sym_sharpe,trades,trades_long,trades_short,acc_gain_pct,avg_gain_trade,gain_per_yr,gain_sym_yr,max_dd_pct,win_rate_pct,verdict,mode,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',\n"
        f"    [r for r in con.execute('SELECT id,config_id,symbol,n_syms,years,pool_sharpe,sym_sharpe,trades,trades_long,trades_short,acc_gain_pct,avg_gain_trade,gain_per_yr,gain_sym_yr,max_dd_pct,win_rate_pct,verdict,mode,created_at FROM results')])\n"
        f"out.commit()\n"
        f"for line in out.iterdump():\n"
        f"    sys.stdout.write(line + '\\n')\n"
    )
    dump = subprocess.run(
        ["ssh", "-o", "BatchMode=yes", remote, "python3 -"],
        input=dump_script, capture_output=True, text=True, check=False)
    if dump.returncode != 0:
        print(f"[publish] remote dump failed (rc={dump.returncode}): {dump.stderr[:600]}")
        sys.exit(2)
    sql = dump.stdout
    print(f"[publish] received {len(sql):,} bytes of SQL; reimporting locally")
    import_con = sqlite3.connect(str(local_db))
    try:
        import_con.executescript(sql)
        import_con.commit()
    finally:
        import_con.close()
    return local_db


def select_configs(con: sqlite3.Connection, mode: str, top_n: int,
                   single_hash: Optional[str] = None) -> List[Tuple[str, vs.Config, dict]]:
    """Return list of (run_id, Config, meta_dict) sorted by pool_sharpe."""
    out = []
    if single_hash:
        rows = con.execute(
            "SELECT c.config_hash, c.config_json, c.label FROM configs c "
            "WHERE c.config_hash=?", (single_hash,)).fetchall()
        for h, j, lab in rows:
            cfg = vs.Config.from_json(j)
            out.append((f"vec_{h}", cfg, {"label": lab}))
        return out
    where = "AND r.mode=?" if mode else ""
    params: tuple = (mode,) if mode else ()
    # Only honest, sample-floor-passing rows
    rows = con.execute(
        f"""SELECT c.config_hash, c.config_json, c.label,
                   r.pool_sharpe, r.sym_sharpe, r.trades, r.acc_gain_pct,
                   r.max_dd_pct, r.n_syms, r.years, r.mode
            FROM results r JOIN configs c ON c.id=r.config_id
            WHERE r.trades >= 30*r.n_syms
              AND ABS(r.pool_sharpe) <= 5.0
              AND ABS(r.sym_sharpe) <= 5.0
              {where}
            ORDER BY r.pool_sharpe DESC
            LIMIT ?""", (*params, top_n)).fetchall()
    for h, j, lab, ps, ss, tr, ag, dd, ns, ys, m in rows:
        try:
            cfg = vs.Config.from_json(j)
        except Exception:
            continue
        run_id = f"vec_{ps:+.3f}_{h[:8]}".replace("+", "p").replace("-", "n").replace(".", "")
        out.append((run_id, cfg, {
            "label": lab, "pool_sharpe": ps, "sym_sharpe": ss,
            "trades": tr, "acc_gain_pct": ag, "max_dd_pct": dd,
            "n_syms": ns, "years": ys, "mode": m, "config_hash": h,
        }))
    return out


def publish(args) -> None:
    if args.reset and TRADES_DIR.exists():
        # Only blow away vec_* files; preserve other sweeps' outputs
        n_removed = 0
        for p in TRADES_DIR.glob("vec_*__*.jsonl"):
            p.unlink(); n_removed += 1
        print(f"[publish] reset: removed {n_removed} prior vec_* files")
    TRADES_DIR.mkdir(parents=True, exist_ok=True)

    if args.remote:
        db_path = fetch_remote_db(args.remote, args.remote_db)
    else:
        db_path = Path(args.db)
    con = sqlite3.connect(str(db_path))

    selected = select_configs(con, args.mode, args.top_n, args.hash)
    print(f"[publish] {len(selected)} configs selected (mode={args.mode or 'ALL'})")
    if not selected:
        print("[publish] nothing to publish — DB has no qualifying rows.")
        return

    # Resolve symbols. Priority: --syms > local NPZ basket discovery.
    # We don't query streaming_returns (skipped in lean dump); we re-evaluate
    # on every NPZ available locally. That's actually what we want — chart
    # views need ALL the trades on charts user can browse.
    if args.syms:
        sym_universe = [s.strip() for s in args.syms.split(",") if s.strip()]
    else:
        npz_files = sorted(Path(args.npz_dir).glob("*.npz"))
        sym_universe = [p.stem for p in npz_files
                        if p.stem.endswith("USDT") or p.stem.endswith("USDC")][:args.max_syms]
    print(f"[publish] sym universe: {len(sym_universe)} syms")

    npz_dir = Path(args.npz_dir)
    # Outer loop = sym (load NPZ once), inner = all configs. Avoids reloading
    # 600MB NPZ per (config, sym) pair. With N=30 configs × 19 syms that's
    # the difference between ~20 min and ~3 min.
    by_run: Dict[str, Dict[str, list]] = {r: {} for r, _, _ in selected}
    for sym_idx, sym in enumerate(sym_universe):
        try:
            npz = dict(np.load(npz_dir / f"{sym}.npz", allow_pickle=False))
        except FileNotFoundError:
            print(f"  [publish] skip {sym}: NPZ missing")
            continue
        cache = vs.MaskCache(npz, limit=600)
        close = npz["close_3m"]
        ts = npz.get("timestamps")
        if ts is None:
            print(f"  [publish] skip {sym}: no timestamps in NPZ")
            del npz; continue
        for run_id, cfg, meta in selected:
            el = cache.and_many(cfg.entry_long) if cfg.entry_long else np.zeros(len(close), dtype=bool)
            xl = cache.and_many(cfg.exit_long)  if cfg.exit_long  else np.zeros(len(close), dtype=bool)
            es = cache.and_many(cfg.entry_short) if cfg.entry_short else np.zeros(len(close), dtype=bool)
            xs = cache.and_many(cfg.exit_short)  if cfg.exit_short  else np.zeros(len(close), dtype=bool)
            # Reason strings are useful in chart_server tooltips
            el_reason = "+".join(p.field for p in cfg.entry_long)[:60] or "-"
            xl_reason = "+".join(p.field for p in cfg.exit_long)[:60] or "-"
            es_reason = "+".join(p.field for p in cfg.entry_short)[:60] or "-"
            xs_reason = "+".join(p.field for p in cfg.exit_short)[:60] or "-"
            trades = (_trade_records(el, xl, close, ts, "LONG", sym, el_reason, xl_reason) +
                      _trade_records(es, xs, close, ts, "SHORT", sym, es_reason, xs_reason))
            if trades:
                by_run[run_id][sym] = trades
        del npz, cache
        print(f"  [publish] {sym} ({sym_idx+1}/{len(sym_universe)}): "
              f"{sum(1 for r in by_run if sym in by_run[r])} runs had trades")
    # Flush JSONL files
    summary_rows = []
    for run_id, cfg, meta in selected:
        per_sym_rows = 0
        per_sym_trades = 0
        for sym, trades in by_run[run_id].items():
            out_path = TRADES_DIR / f"{run_id}__{sym}.jsonl"
            with out_path.open("w", encoding="utf-8") as f:
                for t in trades:
                    f.write(json.dumps(t) + "\n")
            per_sym_rows += 1
            per_sym_trades += len(trades)
        summary_rows.append((run_id, per_sym_rows, per_sym_trades, meta))
        print(f"  [publish] {run_id}: {per_sym_rows} sym files, {per_sym_trades:,} trades  "
              f"pool={meta.get('pool_sharpe', 0):+.4f}")

    # Drop a manifest for the visualizer to read (optional friendly index).
    manifest = TRADES_DIR / "vec_sweep_manifest.json"
    manifest.write_text(json.dumps([
        {"run_id": r, "syms_published": ns, "trades_total": nt, **m}
        for r, ns, nt, m in summary_rows], indent=2))
    print(f"\n[publish] manifest → {manifest}")
    print(f"[publish] visit http://127.0.0.1:5077/  (refresh /runs to see new run_ids)")
    print(f"[publish] runs published: {len(summary_rows)}, "
          f"total trade lines: {sum(nt for _,_,nt,_ in summary_rows):,}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(REPO / "data" / "vec_sweep.db"))
    ap.add_argument("--remote", default="", help="SSH host (e.g. s1-int) to dump DB from")
    ap.add_argument("--remote-db", default="/home/niels/binance-sandbox/data/vec_sweep.db")
    ap.add_argument("--mode", default="", help="filter results by mode (e.g. 'streamed:crypto48')")
    ap.add_argument("--top-n", type=int, default=50)
    ap.add_argument("--hash", default="", help="publish a single config_hash only")
    ap.add_argument("--syms", default="", help="comma-separated NPZ syms to publish; default = whatever's in DB")
    ap.add_argument("--npz-dir", default=str(REPO / "backtest_v8" / "indicators"))
    ap.add_argument("--reset", action="store_true", help="wipe prior vec_* files in TRADES_DIR before publishing")
    ap.add_argument("--max-syms", type=int, default=20, help="cap sym universe (NPZ load is heavy)")
    args = ap.parse_args()
    publish(args)


if __name__ == "__main__":
    main()
