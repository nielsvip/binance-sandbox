#!/usr/bin/env python3
"""flz_8sym_leaderboard — run 4 overrides × 8 flz syms, write canonical leaderboard via metrics_guard.

8 syms < 48 floor → results carry [DIAGNOSTIC] tag. But these ARE the actual flz strategy
universe, so they're the most relevant baseline regardless of floor.
"""
from __future__ import annotations
import json, os, sys, time
from pathlib import Path
ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))

import numpy as np
import re
import metrics_guard as mg
from v8_quick_engine import simulate, QuickConfig

NPZ_DIR = ROOT / "backtest_v8" / "indicators"
OUT_CSV = ROOT / "data" / "sweep_results" / "canonical_flz.csv"
SYMS = ("BTCUSDC", "ETHUSDC", "SOLUSDC", "BNBUSDC", "XRPUSDC", "DOGEUSDC", "ZECUSDC", "BTCDOMUSDT")
RUNS = (
    ("dedv3", "backtest_v8/btc_loop_results/override_btc_dedicated_v3.json"),
    ("BEST",  "backtest_v8/btc_loop_results/override_btc_BEST.json"),
    ("LOOSE", "backtest_v8/btc_loop_results/override_btc_LOOSE.json"),
    ("baseline", None),  # default QuickConfig, BTC dedicated forced ON across 8 syms
)

_IMPOSTER_RE = re.compile(
    r"trades=\d+\s+WR=[\d.]+%\s+gain=[+\-][\d.]+%|"
    r"\[UNVERIFIED\b|"
    r"_imposter_block|"
    r"override_per_sym_.*_BEST"
)


def load_safe(path: Path) -> dict:
    with path.open() as f:
        data = json.load(f)
    blob = json.dumps({k: v for k, v in data.items() if k.startswith("_")})
    if data.get("_imposter_block", {}).get("do_not_load") or _IMPOSTER_RE.search(blob) or "per_sym" in path.stem.lower():
        raise SystemExit(f"IMPOSTER_OVERRIDE_REFUSED: {path}")
    return data


def run_one(tag: str, override_path: str | None) -> dict | None:
    out_dir = ROOT / "data" / "canonical_trades" / f"flz8_{tag}_{int(time.time())}"
    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ["V8_TRADES_OUT_DIR"] = str(out_dir)
    os.environ["V8_TRADES_RUN_ID"] = f"flz8_{tag}"
    overrides = load_safe(Path(override_path)) if override_path else {}
    returns_by_sym: dict[str, list[float]] = {}
    years_max = 0.0
    t0 = time.time()
    for sym in SYMS:
        cfg = QuickConfig()
        for k, v in overrides.items():
            if k.startswith("_"):
                continue
            setattr(cfg, k, v)
        cfg.BTC_DEDICATED_SYMBOLS = SYMS
        cfg.BTC_DEDICATED_ENABLED = True
        z = np.load(str(NPZ_DIR / f"{sym}.npz"))
        try:
            simulate({sym: z}, cfg, capital=10000.0)
        except Exception as e:
            print(f"  [{tag}] {sym} ERR: {e}", flush=True)
            continue
        ts = z["timestamps"] if "timestamps" in z.files else z[z.files[0]]
        years_max = max(years_max, float(ts[-1] - ts[0]) / (365.25 * 86400))
        jp = out_dir / f"flz8_{tag}__{sym}.jsonl"
        rets: list[float] = []
        if jp.exists():
            with jp.open() as f:
                for ln in f:
                    try:
                        rets.append(float(json.loads(ln)["pnl_pct"]))
                    except Exception:
                        pass
        returns_by_sym[sym] = rets
        print(f"  [{tag}] {sym}: {len(rets)} trades  elapsed={time.time()-t0:.0f}s", flush=True)
    if not returns_by_sym:
        return None
    m = mg.standard_metric_set(returns_by_sym, years=years_max)
    all_rets = [r for v in returns_by_sym.values() for r in v]
    eq = peak = worst = 0.0
    for r in all_rets:
        eq += r
        if eq > peak:
            peak = eq
        if peak - eq > worst:
            worst = peak - eq
    m["max_dd_pct"] = worst
    m["override_path"] = Path(override_path).name if override_path else "default_QuickConfig"
    m["tag"] = f"flz8_{tag}"
    print()
    print(f"=== flz8_{tag}: {mg.format_standard_set(m, mode='crypto')}  tier={mg.tier_name(m['pool_sharpe'])}", flush=True)
    try:
        mg.write_sharpe_row(OUT_CSV, m, mode="crypto", append=True)
    except Exception as e:
        print(f"  REFUSED: {e}", flush=True)
    print(flush=True)
    return m


def main() -> int:
    print(f"[leaderboard] 4 configs × 8 flz syms, OUT={OUT_CSV}", flush=True)
    rows = []
    for tag, ovr in RUNS:
        m = run_one(tag, ovr)
        if m:
            rows.append((tag, m))
    print()
    print("=== flz-8sym LEADERBOARD (sorted by pool_sharpe) ===", flush=True)
    rows.sort(key=lambda r: -r[1]["pool_sharpe"])
    for tag, m in rows:
        print(f"  {tag:>10s}  pool={m['pool_sharpe']:+.4f} ({mg.tier_name(m['pool_sharpe'])})  "
              f"sym={m['sym_sharpe']:+.4f}  trades={int(m['trades']):,}  dd={m['max_dd_pct']:.1f}%  "
              f"avg={m['avg_gain_trade']:+.4f}%/tr", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
