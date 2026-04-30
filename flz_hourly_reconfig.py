#!/usr/bin/env python3
"""flz_hourly_reconfig — 7-day rolling reconfig with 2x/day weighting.

User directive 2026-04-30: "paper-test last 7 days on the real running scripts every
hour for every tradeable symbol and inject the best found settings for the next hour
of trading, and open/close positions if the script's opinion has changed."

This wrapper:
  1. Slices each tradeable symbol's NPZ to the last 7 days.
  2. Runs v8_quick_engine.simulate (the same engine the real backtest pipeline uses)
     across a small candidate config grid per symbol per side (LONG/SHORT).
  3. Reads per-trade JSONL output, computes WEIGHTED pool_sharpe with weight =
     2.0 ** (-day_ago) — today=1.0, yesterday=0.5, ..., 6 days ago=1/64.
  4. Picks the winning candidate per (sym, side); routes ALL Sharpe writes through
     metrics_guard.standard_metric_set + write_sharpe_row (the canonical CSV chokepoint).
  5. Publishes per-symbol opinion file: {LONG, SHORT, FLAT} + active config.
  6. Loops hourly (--daemon) or runs once (--once).

This wrapper does NOT place orders. It writes:
  data/hourly_reconfig/<account>/active_config.json   — winner per sym/side
  data/hourly_reconfig/<account>/opinions.json        — opinion + reasoning
  data/sweep_results/canonical_hourly_<account>.csv   — canonical row per cycle

A separate executor (out of scope today; needs explicit user OK before wiring into live
ez_manage/tradier_manage) reads opinions.json, compares vs live position, signals
execute_now via the sanctioned path. Per CLAUDE.md absolute-prohibitions list:
"Place orders outside execute_now()" — wrapper must NEVER directly place orders.

Usage:
  python3 flz_hourly_reconfig.py --account flz --once             # one cycle
  python3 flz_hourly_reconfig.py --account flz --daemon           # hourly loop
  python3 flz_hourly_reconfig.py --account inf --once --max-syms 6  # smoke test
"""
from __future__ import annotations

import argparse
import gc
import json
import math
import os
import re
import sys
import time
import traceback
from pathlib import Path
from typing import Dict, List, Tuple

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
OUT_BASE = ROOT / "data" / "hourly_reconfig"
SWEEP_CSV_DIR = ROOT / "data" / "sweep_results"

# Account → tradeable-syms file (long/short or pooled).
# For accounts with split long/short files, list both — wrapper unions them.
ACCOUNT_SYMS = {
    "flz": [ROOT / "symbols_flz.json"],
    "fin": [ROOT / "symbols_fin.json"],
    "inf": [ROOT / "symbols_inf_long.json", ROOT / "symbols_inf_short.json"],
    "trc": [ROOT / "symbols_trc_long.json", ROOT / "symbols_trc_short.json"],
    "trb": [ROOT / "symbols_trb_long.json", ROOT / "symbols_trb_short.json"],
}

# Trade thresholds (per CLAUDE.md sample-floor rule 5, but for hourly 7d window we
# cannot reasonably hit ≥30 trades/sym for low-frequency syms like BTCDOMUSDT).
# So: full opinion published if ≥30 trades, [LOW_SAMPLE] tag if 14–29, FLAT below 14.
OPINION_FULL_TRADES = 30
OPINION_LOW_SAMPLE_TRADES = 14
OPINION_WSHARPE_FLOOR = 0.3  # Directional tier minimum to publish non-FLAT

# Imposter-block regex (bypass chart_sweep import side effect; same pattern).
_IMPOSTER_RE = re.compile(
    r"trades=\d+\s+WR=[\d.]+%\s+gain=[+\-][\d.]+%|"
    r"\[UNVERIFIED\b|"
    r"_imposter_block|"
    r"override_per_sym_.*_BEST"
)


def load_safe_override(path: Path) -> Dict:
    if not path.exists():
        raise FileNotFoundError(str(path))
    with path.open() as f:
        data = json.load(f)
    blob = json.dumps({k: v for k, v in data.items() if k.startswith("_")})
    if (data.get("_imposter_block", {}).get("do_not_load")
            or _IMPOSTER_RE.search(blob)
            or "per_sym" in path.stem.lower()):
        raise SystemExit(f"IMPOSTER_OVERRIDE_REFUSED: {path}")
    return data


# Candidate set: known-good overrides + dynamic candidate pool from BTC settings search.
# Compact enough (4-6 candidates) for an hourly cycle to finish in <30 min for 8-sym universe.
EXTRA_CAND_DIR = ROOT / "data" / "hourly_reconfig" / "_candidates"


def candidate_configs(account: str) -> List[Tuple[str, Dict]]:
    """Return list of (tag, overrides_dict) candidates.

    Static: BEST + dedv3 + baseline. Plus any JSONs dropped into _candidates/
    by btc_settings_search (auto-pickup) — those persist across cycles.
    """
    base_dir = ROOT / "backtest_v8" / "btc_loop_results"
    cand: List[Tuple[str, Dict]] = []
    for tag, fname in (
        ("BEST", "override_btc_BEST.json"),
        ("dedv3", "override_btc_dedicated_v3.json"),
    ):
        p = base_dir / fname
        try:
            cand.append((tag, load_safe_override(p)))
        except Exception as e:
            print(f"  [candidates] skip {tag}: {e}", flush=True)
    cand.append(("baseline", {}))
    # Mutation focused on producing more trades (loosens entry gates) — addresses
    # BTCDOMUSDT and other low-frequency syms.
    if cand and cand[0][0] == "BEST":
        base = cand[0][1]
        more_trades = dict(base)
        more_trades.update({
            "BTC_BREAKOUT_MIN_HOLD_BARS": 1,
            "BTC_MIN_HOLD_BARS": 5,
            "MIN_HOLD_BARS": 5,
            "BTC_COOLDOWN_BARS": 1,
            "BTC_BREAKOUT_COOLDOWN_BARS": 1,
            "BTC_TECH_EXIT_WT_MIN_TFS": 2,
        })
        cand.append(("BEST_more_trades", more_trades))
    # Auto-pickup candidates dropped by btc_settings_search (extra optimizers may
    # publish JSONs here as they discover new winners).
    if EXTRA_CAND_DIR.exists():
        for p in sorted(EXTRA_CAND_DIR.glob("*.json")):
            try:
                cfg = load_safe_override(p)
                tag = f"extra_{p.stem}"
                cand.append((tag, cfg))
            except Exception as e:
                print(f"  [candidates] skip {p.name}: {e}", flush=True)
    return cand


def npz_window_seconds(z, days: float) -> Tuple[int, int, float]:
    """Return (window_start_ts, window_end_ts, actual_years) for filtering trades.

    Engine runs on FULL NPZ (preserves HTF lookback); we filter trade JSONL after.
    """
    if "timestamps" in z.files:
        ts = z["timestamps"]
    else:
        ts = z[z.files[0]]
    if len(ts) < 2:
        return 0, 0, 0.0
    end_ts = int(ts[-1])
    start_ts = end_ts - int(days * 86400)
    actual_years = float(days) / 365.25
    return start_ts, end_ts, actual_years


def time_weighted_pool_sharpe(returns_with_ts: List[Tuple[float, int]],
                              now_ts: int) -> Tuple[float, float, int]:
    """Compute weighted pool_sharpe with weight = 2 ** (-day_ago).

    returns_with_ts: list of (pnl_pct, exit_ts).
    Returns (weighted_pool_sharpe, weighted_trade_count_equiv, n_raw_trades).
    """
    if not returns_with_ts:
        return 0.0, 0.0, 0
    rs = np.array([r for r, _ in returns_with_ts], dtype=np.float64)
    ts = np.array([t for _, t in returns_with_ts], dtype=np.float64)
    days_ago = np.maximum(0.0, (now_ts - ts) / 86400.0)
    w = np.power(2.0, -days_ago)
    w_sum = w.sum()
    if w_sum <= 0 or len(rs) < 2:
        return 0.0, 0.0, len(rs)
    wmean = float((rs * w).sum() / w_sum)
    wvar = float((w * (rs - wmean) ** 2).sum() / w_sum)
    wstd = math.sqrt(max(wvar, 0.0))
    wsharpe = (wmean / wstd) if wstd > 1e-12 else 0.0
    eff_trades = float(w_sum)  # effective sample size proxy
    return wsharpe, eff_trades, len(rs)


def run_candidate(sym: str, side: str, tag: str, overrides: Dict,
                  z, run_dir: Path, now_ts: int, window_start_ts: int) -> Dict:
    """Drive v8_quick_engine on FULL NPZ (preserves HTF warmup); filter trades to 7d.

    Returns dict with weighted pool_sharpe over the trades exiting in [window_start_ts, now].
    """
    run_id = f"{sym}__{side}__{tag}"
    os.environ["V8_TRADES_OUT_DIR"] = str(run_dir)
    os.environ["V8_TRADES_RUN_ID"] = run_id
    cfg = QuickConfig()
    for k, v in overrides.items():
        if k.startswith("_"):
            continue
        try:
            setattr(cfg, k, v)
        except Exception:
            pass
    cfg.BTC_DEDICATED_SYMBOLS = (sym,)
    cfg.BTC_DEDICATED_ENABLED = True
    try:
        simulate({sym: z}, cfg, capital=10000.0)
    except SystemExit as e:
        return {"tag": tag, "error": f"SystemExit:{e}", "trades": 0, "wsharpe": 0.0}
    except Exception as e:
        return {"tag": tag, "error": str(e), "trades": 0, "wsharpe": 0.0}
    jp = run_dir / f"{run_id}__{sym}.jsonl"
    sided_rets: List[Tuple[float, int]] = []
    if jp.exists():
        with jp.open() as f:
            for line in f:
                try:
                    rec = json.loads(line)
                    rec_side = (rec.get("side") or "").upper()
                    if rec_side != side.upper():
                        continue
                    exit_ts = int(rec.get("exit_ts", 0))
                    # Only count trades exiting inside the rolling window.
                    if exit_ts < window_start_ts:
                        continue
                    pnl = float(rec.get("pnl_pct", 0.0))
                    sided_rets.append((pnl, exit_ts))
                except Exception:
                    pass
    wsharpe, eff_n, n_raw = time_weighted_pool_sharpe(sided_rets, now_ts)
    return {
        "tag": tag,
        "trades": n_raw,
        "weighted_eff_trades": eff_n,
        "wsharpe": wsharpe,
        "raw_returns": [r for r, _ in sided_rets],
        "overrides": overrides,
    }


def write_opinion(account: str, sym: str, side: str, winner: Dict,
                  prev_opinion: str, now_ts: int) -> Tuple[str, str]:
    """Decide LONG/SHORT/FLAT opinion + a quality tag.

    Returns (opinion, sample_tag).
      - opinion: "LONG" / "SHORT" / "FLAT" — what to actually do
      - sample_tag: "FULL" / "LOW_SAMPLE" / "INSUFFICIENT" — confidence

    Rules:
      - n >= 30 trades AND wsharpe >= 0.3 AND mean_pnl > 0  → side, FULL
      - n >= 14 trades AND wsharpe >= 0.3 AND mean_pnl > 0  → side, LOW_SAMPLE
      - else → FLAT, INSUFFICIENT (or DISCARD if wsharpe<0)
    """
    ws = float(winner.get("wsharpe", 0.0))
    n = int(winner.get("trades", 0))
    raw = winner.get("raw_returns", [])
    mean_pnl = float(np.mean(raw)) if raw else 0.0
    if n >= OPINION_FULL_TRADES and ws >= OPINION_WSHARPE_FLOOR and mean_pnl > 0:
        return side.upper(), "FULL"
    if n >= OPINION_LOW_SAMPLE_TRADES and ws >= OPINION_WSHARPE_FLOOR and mean_pnl > 0:
        return side.upper(), "LOW_SAMPLE"
    if ws < 0:
        return "FLAT", "DISCARD"
    return "FLAT", "INSUFFICIENT"


def reconfig_one_cycle(account: str, max_syms: int = 0) -> int:
    syms_paths = ACCOUNT_SYMS.get(account)
    if not syms_paths:
        print(f"[hourly] unknown account={account}", flush=True)
        return 1
    all_syms_set: List[str] = []
    seen = set()
    for sp in syms_paths:
        if not sp.exists():
            print(f"[hourly] symbol file missing: {sp}", flush=True)
            continue
        raw = sp.read_text()
        # Tolerate trailing commas in symbols_*.json (production format).
        raw_clean = re.sub(r",(\s*[\]}])", r"\1", raw)
        try:
            for s in json.loads(raw_clean):
                if isinstance(s, str) and s not in seen:
                    all_syms_set.append(s)
                    seen.add(s)
        except Exception as e:
            print(f"[hourly] parse error {sp}: {e}", flush=True)
    if not all_syms_set:
        print(f"[hourly] no symbols loaded for account={account}", flush=True)
        return 1
    all_syms = all_syms_set[:max_syms] if max_syms > 0 else all_syms_set
    sides = ("LONG", "SHORT")
    cands = candidate_configs(account)
    if not cands:
        print(f"[hourly] no candidates loadable", flush=True)
        return 2

    cycle_id = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    run_dir = ROOT / "data" / "hourly_reconfig" / account / "runs" / cycle_id
    run_dir.mkdir(parents=True, exist_ok=True)
    now_ts = int(time.time())

    out_dir = OUT_BASE / account
    out_dir.mkdir(parents=True, exist_ok=True)
    active_path = out_dir / "active_config.json"
    opinions_path = out_dir / "opinions.json"
    prev_opinions: Dict = {}
    if opinions_path.exists():
        try:
            prev_opinions = json.loads(opinions_path.read_text())
        except Exception:
            prev_opinions = {}

    print(f"[hourly] cycle={cycle_id} account={account} syms={len(all_syms)} cands={len(cands)}", flush=True)
    active: Dict = {}
    opinions: Dict = {"_cycle_id": cycle_id, "_now_ts": now_ts, "_account": account, "syms": {}}

    all_returns_by_sym: Dict[str, List[float]] = {}
    years_max = 0.0
    t0 = time.time()
    for i, sym in enumerate(all_syms, 1):
        npz_path = NPZ_DIR / f"{sym}.npz"
        if not npz_path.exists():
            print(f"  [{i}/{len(all_syms)}] {sym}: NO_NPZ", flush=True)
            continue
        z = None
        try:
            z = np.load(str(npz_path))
            window_start_ts, _, yr = npz_window_seconds(z, days=7.0)
            years_max = max(years_max, yr)
        except Exception as e:
            print(f"  [{i}/{len(all_syms)}] {sym}: NPZ_LOAD_ERR {e}", flush=True)
            if z is not None:
                try: z.close()
                except Exception: pass
            continue
        for side in sides:
            best = {"tag": None, "wsharpe": -1e9, "trades": 0}
            for tag, ovr in cands:
                r = run_candidate(sym, side, tag, ovr, z, run_dir, now_ts, window_start_ts)
                if r.get("wsharpe", -1e9) > best.get("wsharpe", -1e9):
                    best = r
            sym_key = f"{sym}_{side}"
            prev_op = prev_opinions.get("syms", {}).get(sym_key, {}).get("opinion", "FLAT")
            opinion, sample_tag = write_opinion(account, sym, side, best, prev_op, now_ts)
            active[sym_key] = {
                "winning_tag": best["tag"],
                "wsharpe": best["wsharpe"],
                "trades": best["trades"],
                "sample_tag": sample_tag,
                "overrides": best.get("overrides", {}),
                "cycle_id": cycle_id,
            }
            opinions["syms"][sym_key] = {
                "opinion": opinion,
                "sample_tag": sample_tag,
                "prev_opinion": prev_op,
                "changed": opinion != prev_op,
                "winning_tag": best["tag"],
                "wsharpe": round(best["wsharpe"], 4),
                "trades": best["trades"],
                "tier": mg.tier_name(best["wsharpe"]),
            }
            all_returns_by_sym[sym_key] = best.get("raw_returns", [])
        try: z.close()
        except Exception: pass
        del z
        gc.collect()
        if i % 4 == 0 or i == len(all_syms):
            print(f"  [{i}/{len(all_syms)}] {sym} cycle_elapsed={time.time()-t0:.0f}s", flush=True)

    # Canonical row via metrics_guard (the chokepoint)
    pooled = {k: v for k, v in all_returns_by_sym.items() if v}
    if pooled and years_max > 0:
        m = mg.standard_metric_set(pooled, years=max(years_max, 7.0 / 365.25))
        all_rets = [r for v in pooled.values() for r in v]
        eq = peak = worst = 0.0
        for r in all_rets:
            eq += r
            if eq > peak:
                peak = eq
            if peak - eq > worst:
                worst = peak - eq
        m["max_dd_pct"] = worst
        m["override_path"] = "candidate_grid"
        m["tag"] = f"hourly_{account}_{cycle_id}_w7d"  # _w7d marks rolling 7-day window
        try:
            csv_path = SWEEP_CSV_DIR / f"canonical_hourly_{account}.csv"
            mg.write_sharpe_row(csv_path, m, mode="crypto", append=True)
            print(f"[hourly] canonical row written: {csv_path}", flush=True)
            print("  " + mg.format_standard_set(m, mode="crypto"), flush=True)
            print(f"  tier: {mg.tier_name(m['pool_sharpe'])}", flush=True)
        except Exception as e:
            print(f"[hourly] write_sharpe_row REFUSED: {e}", flush=True)

    # Persist opinion + active config (atomic write)
    tmp1 = active_path.with_suffix(".tmp")
    tmp1.write_text(json.dumps(active, indent=2, default=str))
    tmp1.replace(active_path)
    tmp2 = opinions_path.with_suffix(".tmp")
    tmp2.write_text(json.dumps(opinions, indent=2, default=str))
    tmp2.replace(opinions_path)

    # Summarize
    changes = [k for k, v in opinions["syms"].items() if v.get("changed")]
    print(f"[hourly] opinion changes this cycle ({len(changes)}): {changes[:8]}", flush=True)
    print(f"[hourly] active_config: {active_path}", flush=True)
    print(f"[hourly] opinions:      {opinions_path}", flush=True)
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", required=True, choices=sorted(ACCOUNT_SYMS.keys()))
    ap.add_argument("--once", action="store_true", help="single cycle, then exit")
    ap.add_argument("--daemon", action="store_true", help="hourly loop")
    ap.add_argument("--max-syms", type=int, default=0)
    ap.add_argument("--cycle-secs", type=int, default=3600)
    args = ap.parse_args()
    if not (args.once or args.daemon):
        ap.error("specify --once or --daemon")

    if args.once:
        return reconfig_one_cycle(args.account, args.max_syms)
    while True:
        try:
            t0 = time.time()
            reconfig_one_cycle(args.account, args.max_syms)
            elapsed = time.time() - t0
            sleep_for = max(60, args.cycle_secs - int(elapsed))
            print(f"[hourly] cycle done in {elapsed:.0f}s; sleeping {sleep_for}s", flush=True)
            time.sleep(sleep_for)
        except KeyboardInterrupt:
            print("[hourly] interrupted", flush=True)
            return 0
        except Exception:
            traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
