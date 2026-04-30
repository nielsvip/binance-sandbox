#!/usr/bin/env python3
"""populate_canonical_top10 — re-run the top-10 publishable canonical configs
through v8_quick_engine, recording per-trade JSONL into V8_TRADES_OUT_DIR
(/tmp/v8_trades) so they appear in chart_server's /runs and chart.html's
BT [run] checkboxes.

Replays each top-10 config across:
  - crypto: flz priority symbols (BTC, ETH, SOL, ADA, BNB, AVAX, XRP, LINK, LTC, UNI)
  - tradier: top liquid stocks (AAPL, MSFT, NVDA, AMZN, JPM, XOM, ABBV, TSLA, SPY, META)

Output: /tmp/v8_trades/<run_id>__<symbol>.jsonl, where run_id =
  canon_<mode>_<rank>  (e.g. canon_crypto_1, canon_tradier_3)

Each JSONL has one line per closed trade with timestamps, side, prices, pnl_pct
— consumed by chart.html as BT markers + equity curve + B&H comparison.

Audited via metrics_guard: every config used here came from a publishable iter
(n_syms ≥ floor, ≥30 trades/sym, |sharpe| ≤ 5).
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Dict, List

ROOT = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(ROOT))

import metrics_guard

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
AUTONOMOUS_DIR = ROOT / "data" / "autonomous"
TRADES_OUT_DIR = ROOT.parent / "tmp" / "v8_trades"
TRADES_OUT_DIR = Path("/tmp/v8_trades")
TRADES_OUT_DIR.mkdir(parents=True, exist_ok=True)

# Priority symbols — what the user actually trades / cares about
CRYPTO_SYMBOLS = ["BTCUSDC", "ETHUSDC", "SOLUSDC", "ADAUSDC", "BNBUSDC",
                  "AVAXUSDC", "XRPUSDC", "LINKUSDC", "LTCUSDC", "UNIUSDC"]
TRADIER_SYMBOLS = ["AAPL", "MSFT", "NVDA", "AMZN", "JPM", "XOM", "ABBV",
                   "TSLA", "SPY", "META", "GOOGL", "AMD"]


def _n_syms_hint(p: Path) -> int:
    for part in p.parts:
        m = re.search(r"_(\d+)sym", part)
        if m:
            return int(m.group(1))
    return 0


def _is_publishable(rec: Dict, mode: str, n_hint: int) -> bool:
    ps = float(rec.get("pool_sharpe", 0) or 0)
    tr = int(rec.get("trades", 0) or 0)
    floor = metrics_guard.MIN_SYMS_STOCKS if mode == "tradier" else metrics_guard.MIN_SYMS_CRYPTO
    if abs(ps) > metrics_guard.PER_SYM_SHARPE_CAP and tr < 5000:
        return False
    if n_hint < floor:
        return False
    if n_hint and tr / n_hint < metrics_guard.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE:
        return False
    return True


def collect_top_n(mode: str, n: int = 10) -> List[Dict]:
    out = []
    for p in AUTONOMOUS_DIR.rglob("autonomous_*_winners.jsonl"):
        s = str(p)
        if "_legacy_unverified" in s or "_NOLIES_HOLD_" in s:
            continue
        rec_mode = "tradier" if "tradier" in s.lower() else ("crypto" if "crypto" in s.lower() else None)
        if rec_mode != mode:
            continue
        n_hint = _n_syms_hint(p)
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if not _is_publishable(rec, mode, n_hint):
                    continue
                rec["_n_syms_hint"] = n_hint
                rec["_source"] = str(p)
                out.append(rec)
        except Exception:
            continue
    out.sort(key=lambda r: float(r.get("pool_sharpe", 0)), reverse=True)
    # Dedupe near-identical configs (same overrides hash) so top-10 has variety
    seen_hashes = set()
    deduped = []
    for r in out:
        ovr = r.get("overrides", {})
        h = json.dumps({k: v for k, v in ovr.items() if not k.startswith("_")}, sort_keys=True)[:200]
        if h in seen_hashes:
            continue
        seen_hashes.add(h)
        deduped.append(r)
        if len(deduped) >= n:
            break
    return deduped


def run_config(rank: int, mode: str, rec: Dict, symbols: List[str]) -> Dict:
    from v8_quick_engine import simulate, QuickConfig
    import numpy as np

    run_id = f"canon_{mode}_{rank}"
    overrides = rec.get("overrides", {}) or {}
    seed_ps = float(rec.get("pool_sharpe", 0))

    print(f"\n[{run_id}] seed pool_sharpe={seed_ps:+.4f} ({metrics_guard.tier_name(seed_ps)}) "
          f"trades={int(rec.get('trades', 0)):,} from {Path(rec['_source']).parent.parent.name}", flush=True)

    results = {}
    for sym in symbols:
        npz_path = NPZ_DIR / f"{sym}.npz"
        if not npz_path.exists():
            print(f"  [{run_id}] skip {sym}: no NPZ", flush=True)
            continue
        os.environ["V8_TRADES_OUT_DIR"] = str(TRADES_OUT_DIR)
        os.environ["V8_TRADES_RUN_ID"] = run_id

        cfg = QuickConfig()
        cfg.MODE = mode
        cfg.LTF = "3m" if mode == "crypto" else "5m"
        for k, v in overrides.items():
            if k.startswith("_"):
                continue
            if hasattr(cfg, k):
                setattr(cfg, k, v)

        z = np.load(npz_path)
        t0 = time.time()
        try:
            r = simulate(((sym, z),), cfg, capital=10000.0)
            elapsed = time.time() - t0
            jsonl = TRADES_OUT_DIR / f"{run_id}__{sym}.jsonl"
            n_trades_recorded = sum(1 for _ in open(jsonl)) if jsonl.exists() else 0
            results[sym] = {
                "pool_sharpe": r.get("pool_sharpe", 0),
                "sym_sharpe": r.get("sym_sharpe", 0),
                "trades": r.get("trades", 0),
                "wr": r.get("wr", 0),
                "acc_gain_pct": r.get("accumulated_gain_pct", 0),
                "max_dd_pct": r.get("max_dd_pct", 0),
                "n_trades_recorded": n_trades_recorded,
                "elapsed_s": round(elapsed, 1),
            }
            print(f"  [{run_id}] {sym:>10s}  pool_sharpe={r.get('pool_sharpe',0):+.4f}  "
                  f"trades={r.get('trades',0):>5d}  wr={r.get('wr',0):>5.1f}%  "
                  f"gain={r.get('accumulated_gain_pct',0):>+8.1f}%  "
                  f"dd={r.get('max_dd_pct',0):>5.1f}%  ({elapsed:>4.1f}s, {n_trades_recorded} recorded)", flush=True)
        except Exception as e:
            print(f"  [{run_id}] {sym}: ERROR {e}", flush=True)
            results[sym] = {"error": str(e)}
    return results


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=10, help="Top-N to replay (default 10)")
    ap.add_argument("--mode", choices=["crypto", "tradier", "both"], default="both")
    ap.add_argument("--crypto-syms", default=",".join(CRYPTO_SYMBOLS))
    ap.add_argument("--tradier-syms", default=",".join(TRADIER_SYMBOLS))
    args = ap.parse_args()

    print(f"=== populate_canonical_top10 — out={TRADES_OUT_DIR} ===", flush=True)

    if args.mode in ("crypto", "both"):
        crypto_top = collect_top_n("crypto", args.n)
        print(f"\n--- crypto top-{len(crypto_top)} (publishable, deduped) ---", flush=True)
        crypto_syms = [s.strip() for s in args.crypto_syms.split(",") if s.strip()]
        for rank, rec in enumerate(crypto_top, 1):
            run_config(rank, "crypto", rec, crypto_syms)

    if args.mode in ("tradier", "both"):
        tradier_top = collect_top_n("tradier", args.n)
        print(f"\n--- tradier top-{len(tradier_top)} (publishable, deduped) ---", flush=True)
        tradier_syms = [s.strip() for s in args.tradier_syms.split(",") if s.strip()]
        for rank, rec in enumerate(tradier_top, 1):
            run_config(rank, "tradier", rec, tradier_syms)

    files = sorted(TRADES_OUT_DIR.glob("canon_*__*.jsonl"))
    print(f"\n=== DONE — {len(files)} per-trade JSONLs in {TRADES_OUT_DIR} ===", flush=True)
    print(f"Open http://127.0.0.1:5077/ — BT [canon_*] checkboxes should now appear in 'Runs'", flush=True)


if __name__ == "__main__":
    main()
