"""Autonomous config search: keep sampling until accumulated_gain_pct > 10 * B&H.

Writes winners immediately to winners JSONL, all results to CSV, runs forever
until killed or N_MAX reached.
"""
import argparse, csv, json, os, random, sys, time, gc
from dataclasses import fields, is_dataclass
from pathlib import Path


FORBIDDEN_FLIPS = {
    # Quick-engine profit-target artifacts NOT in real live code — cause 99%+ WR lie.
    # PROFIT_TARGET_PCT/ENABLED were the fake fixed-% exit (now purged from v8_quick_engine.py).
    # AUGMENT_PT is separate TP logic also not in live code.
    "AUGMENT_PT_ENABLED", "AUGMENT_PT_PCT",
    # CYCLE_TP is also a profit target not in live.
    "CYCLE_TP_TIERED_ENABLED", "CYCLE_TP_PCT", "CYCLE_TP_TIERED_FRAC",
    "CYCLE_TP_CONDITIONAL_EXIT",
    # Any flag with PT / TAKE_PROFIT / TP_PCT semantics
    "ACCOUNT_TP_PCT",
    # AUGMENT_PT ride-along
    "AUGMENT_WT_D_AUTO_CLOSE_ENABLED", "AUGMENT_WT_4H_AUTO_CLOSE_ENABLED",
    # NOTE: PARTIAL_PROFIT_LOCK_* and PARTIAL_EXIT_* are NOT forbidden.
    # PPL is a REAL live mechanism (50% close at +0.5% via maker/webhook_url_2).
    # It must be searchable — it is the only TP mechanism after PROFIT_TARGET removal.
}

def _sample_cfg(base_cfg, bool_flip_prob=0.15, numeric_perturb_prob=0.10):
    """Return a dict of overrides sampled randomly from knob space, skipping FORBIDDEN_FLIPS."""
    ovr = {}
    for fld in fields(base_cfg):
        nm = fld.name
        if nm in ("MODE", "LTF", "_loaded_from"): continue
        if nm in FORBIDDEN_FLIPS: continue
        val = getattr(base_cfg, nm)
        if isinstance(val, bool):
            if random.random() < bool_flip_prob:
                ovr[nm] = not val
        elif isinstance(val, int) and not isinstance(val, bool):
            if random.random() < numeric_perturb_prob:
                # multiplicative perturbation within ±50%, min 1
                mult = random.choice([0.5, 0.75, 1.25, 1.5, 2.0])
                new = max(1, int(val * mult))
                if new != val: ovr[nm] = new
        elif isinstance(val, float):
            if random.random() < numeric_perturb_prob:
                mult = random.choice([0.25, 0.5, 0.75, 1.25, 1.5, 2.0, 3.0])
                new = val * mult
                if abs(new - val) > 1e-9: ovr[nm] = new
        elif isinstance(val, str):
            # skip strings by default (unknown domain)
            pass
    return ovr


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", required=True, choices=["crypto", "tradier"])
    ap.add_argument("--symbols", type=int, default=24)
    ap.add_argument("--start", required=True)
    ap.add_argument("--npz-dir", required=True)
    ap.add_argument("--bh-accumulated-gain-pct", type=float, required=True,
                    help="Buy-and-hold accumulated gain (reported as reference).")
    ap.add_argument("--target-gain-abs", type=float, required=True,
                    help="Absolute accumulated_gain_pct to call a config a WINNER.")
    ap.add_argument("--target-sharpe-min", type=float, default=1.0,
                    help="Minimum pool_sharpe to qualify as WINNER.")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--n-max", type=int, default=100000)
    ap.add_argument("--seed", type=int, default=None)
    ap.add_argument("--bool-flip-prob", type=float, default=0.15)
    ap.add_argument("--numeric-perturb-prob", type=float, default=0.10)
    ap.add_argument("--workers", type=int, default=4,
                    help="How many configs to run in parallel (multiprocessing).")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    if args.seed is not None:
        random.seed(args.seed)
    target_gain = args.target_gain_abs
    target_sharpe = args.target_sharpe_min
    Path(args.out_dir).mkdir(parents=True, exist_ok=True)
    csv_path = Path(args.out_dir) / f"autonomous_{args.mode}.csv"
    winners_path = Path(args.out_dir) / f"autonomous_{args.mode}_winners.jsonl"

    # Pre-select symbols to avoid loading the full corpus into RAM (OOM on small servers)
    from pathlib import Path as _P
    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    all_files = sorted(_P(args.npz_dir).glob("*.npz"))
    candidates = []
    for p in all_files:
        sym = p.stem
        is_crypto = any(sym.endswith(s) for s in CRYPTO_SUFFIXES)
        if args.mode == "crypto" and not is_crypto: continue
        if args.mode == "tradier" and is_crypto: continue
        candidates.append(sym)
    syms = candidates[:args.symbols]
    subset = load_npz(args.mode, syms, args.start, args.npz_dir)
    gc.collect()

    base = QuickConfig()
    base.MODE = args.mode
    base.LTF = "3m" if args.mode == "crypto" else "5m"
    # Force FORBIDDEN_FLIPS defaults to safe-off on the base config so no sampled config accidentally carries them on
    for nm in FORBIDDEN_FLIPS:
        if hasattr(base, nm):
            val = getattr(base, nm)
            if isinstance(val, bool):
                setattr(base, nm, False)

    print(f"[AUTO_SEARCH] mode={args.mode} syms={len(subset)} start={args.start} "
          f"BH_gain={args.bh_accumulated_gain_pct:.1f}% TARGET>{target_gain:.1f}% "
          f"out={args.out_dir}", flush=True)

    csv_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as csv_f:
        w = csv.writer(csv_f)
        if not csv_exists:
            w.writerow(["iter", "pool_sharpe", "acc_gain_pct", "max_dd_pct",
                        "trades", "gain_vs_bh", "elapsed_s", "overrides_count",
                        "overrides_json"])
        best_gain = -1e9
        best_sharpe = -1e9
        for i in range(args.n_max):
            cfg = QuickConfig()
            cfg.MODE = args.mode
            cfg.LTF = base.LTF
            ovr = _sample_cfg(base, args.bool_flip_prob, args.numeric_perturb_prob)
            for k, v in ovr.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            t0 = time.time()
            try:
                r = simulate(subset, cfg)
            except Exception as e:
                print(f"[AUTO_SEARCH] iter={i} ERR: {e}", flush=True)
                continue
            el = time.time() - t0
            gain = r.get("accumulated_gain_pct", 0.0)
            sharpe = r.get("pool_sharpe", 0.0)
            dd = r.get("max_dd_pct", 0.0)
            tr = r.get("trades", 0)
            gvb = gain / args.bh_accumulated_gain_pct if args.bh_accumulated_gain_pct != 0 else 0.0
            w.writerow([i, round(sharpe, 4), round(gain, 2), round(dd, 2), tr,
                        round(gvb, 3), round(el, 1), len(ovr), json.dumps(ovr)])
            csv_f.flush()
            if gain > best_gain:
                best_gain = gain
                best_sharpe = sharpe
                print(f"[AUTO_SEARCH] iter={i} NEW_BEST_GAIN sharpe={sharpe:.3f} "
                      f"gain={gain:.1f}% ({gvb:.2f}x BH) dd={dd:.1f}% tr={tr} "
                      f"ovr={len(ovr)} el={el:.1f}s", flush=True)
            if gain >= target_gain and sharpe >= target_sharpe:
                win = {"iter": i, "pool_sharpe": round(sharpe, 4),
                       "acc_gain_pct": round(gain, 2), "max_dd_pct": round(dd, 2),
                       "trades": tr, "gain_vs_bh": round(gvb, 3),
                       "overrides": ovr}
                with open(winners_path, "a") as wf:
                    wf.write(json.dumps(win) + "\n")
                print(f"[AUTO_SEARCH] *** WINNER iter={i} gain={gain:.0f}% "
                      f"({gvb:.1f}x BH) sharpe={sharpe:.3f} dd={dd:.1f}% tr={tr} ***",
                      flush=True)

if __name__ == "__main__":
    main()
