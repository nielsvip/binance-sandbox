"""Autonomous config search: keep sampling until accumulated_gain_pct > 10 * B&H.

Writes winners immediately to winners JSONL, all results to CSV, runs forever
until killed or N_MAX reached.

Timeout is enforced via multiprocessing.Process (fork) so numpy C code can
actually be killed — signal.SIGALRM cannot interrupt numpy's C extensions.
"""
import argparse, copy, csv, json, os, random, sys, time, gc
import multiprocessing as mp
from dataclasses import fields
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
    # EARLY_ABORT_* inflate Sharpe by cherry-picking good runs (found 2026-04-22).
    # Setting FLOOR high or ENABLED=True with few MIN_SYMBOLS causes abort after picking best N symbols.
    # TIME_LIMIT_SEC reduction also causes premature stop, same effect.
    "EARLY_ABORT_SHARPE_FLOOR", "EARLY_ABORT_ENABLED", "EARLY_ABORT_MIN_SYMBOLS",
    "EARLY_ABORT_TIME_LIMIT_SEC",
    # STOP_LOSS_ENABLED inflates backtest Sharpe by cutting losses while live system has it OFF.
    # Activating it in backtest but not live = false signal. Lock to False in all searches.
    # HARD_STOP_LOSS_MAX_PAIN caused $500+ losses on 2026-03-24 — NEVER re-enable in any search.
    "STOP_LOSS_ENABLED",
    # NOTE: PARTIAL_PROFIT_LOCK_* and PARTIAL_EXIT_* are NOT forbidden.
    # PPL is a REAL live mechanism (50% close at +0.5% via maker/webhook_url_2).
    # It must be searchable — it is the only TP mechanism after PROFIT_TARGET removal.
}

# Fork-inherited globals — set in main() before any Process.start()
_g_subset = None
_g_simulate = None


def _sim_worker_fn(cfg, conn):
    """Runs in forked child process. Inherits _g_subset/_g_simulate via COW fork."""
    try:
        r = _g_simulate(_g_subset, cfg)
        conn.send(r)
    except Exception as e:
        conn.send({"_err": str(e)})
    finally:
        conn.close()


def _run_simulate_timed(cfg, timeout_secs, mp_ctx):
    """Run simulate(cfg) in a child process; return (result, timed_out, error_str)."""
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(target=_sim_worker_fn, args=(cfg, child_conn), daemon=True)
    proc.start()
    child_conn.close()
    proc.join(timeout=timeout_secs)
    if proc.is_alive():
        proc.kill()
        proc.join()
        parent_conn.close()
        return None, True, None
    try:
        r = parent_conn.recv()
    except EOFError:
        r = {}
    parent_conn.close()
    if "_err" in r:
        return None, False, r["_err"]
    return r, False, None


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
                mult = random.choice([0.5, 0.75, 1.25, 1.5, 2.0])
                new = max(1, int(val * mult))
                if new != val: ovr[nm] = new
        elif isinstance(val, float):
            if random.random() < numeric_perturb_prob:
                mult = random.choice([0.25, 0.5, 0.75, 1.25, 1.5, 2.0, 3.0])
                new = val * mult
                if abs(new - val) > 1e-9: ovr[nm] = new
        elif isinstance(val, str):
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
    ap.add_argument("--base-overrides-json", default=None,
                    help="JSON file with overrides to apply to QuickConfig before perturbation starts.")
    ap.add_argument("--bool-flip-prob", type=float, default=0.02)
    ap.add_argument("--numeric-perturb-prob", type=float, default=0.02)
    ap.add_argument("--sharpe-useless-floor", type=float, default=2.0,
                    help="Tag reliable configs below this Sharpe as useless=1 in CSV.")
    ap.add_argument("--workers", type=int, default=4,
                    help="Unused — kept for CLI compatibility.")
    ap.add_argument("--min-trades-for-record", type=int, default=0,
                    help="Reject configs with trades < this from CSV output (garbage-filter).")
    ap.add_argument("--min-trades-per-sym", type=int, default=30,
                    help="Reject configs with trades/symbols < this (per-sym reliability floor).")
    ap.add_argument("--max-iter-seconds", type=int, default=120,
                    help="Kill any single iteration exceeding this (subprocess SIGKILL). Default 120s.")
    ap.add_argument("--symbol-list", default=None,
                    help="Comma-separated explicit symbol list (overrides alphabetical-first-N).")
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

    CRYPTO_SUFFIXES = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
    if args.symbol_list:
        syms = [s.strip() for s in args.symbol_list.split(",") if s.strip()]
    else:
        all_files = sorted(Path(args.npz_dir).glob("*.npz"))
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
    if args.base_overrides_json:
        with open(args.base_overrides_json) as _f:
            _base_ovr = json.load(_f)
        _base_ovr.pop("_meta", None)
        for k, v in _base_ovr.items():
            if k not in FORBIDDEN_FLIPS and hasattr(base, k):
                setattr(base, k, v)
        print(f"[AUTO_SEARCH] Base overrides applied from {args.base_overrides_json}: {len(_base_ovr)} keys", flush=True)
    for nm in FORBIDDEN_FLIPS:
        if hasattr(base, nm):
            val = getattr(base, nm)
            if isinstance(val, bool):
                setattr(base, nm, False)

    print(f"[AUTO_SEARCH] mode={args.mode} syms={len(subset)} start={args.start} "
          f"BH_gain={args.bh_accumulated_gain_pct:.1f}% TARGET>{target_gain:.1f}% "
          f"timeout={args.max_iter_seconds}s out={args.out_dir}", flush=True)

    # Expose subset/simulate to forked children via module globals (COW — no copy on read-only access)
    global _g_subset, _g_simulate
    _g_subset = subset
    _g_simulate = simulate
    _mp_ctx = mp.get_context("fork")

    csv_exists = csv_path.exists()
    with open(csv_path, "a", newline="") as csv_f:
        w = csv.writer(csv_f)
        if not csv_exists:
            w.writerow(["iter", "pool_sharpe", "acc_gain_pct", "max_dd_pct",
                        "trades", "gain_vs_bh", "elapsed_s", "overrides_count",
                        "reliable", "useless", "overrides_json"])

        # iter=-1: evaluate baseline (no perturbation) as reference floor
        if not csv_exists:
            t_bl = time.time()
            r0, timed_out_bl, err_bl = _run_simulate_timed(base, args.max_iter_seconds, _mp_ctx)
            if timed_out_bl:
                print(f"[AUTO_SEARCH] BASELINE TIMEOUT after {args.max_iter_seconds}s", flush=True)
            elif err_bl:
                print(f"[AUTO_SEARCH] BASELINE ERR: {err_bl}", flush=True)
            else:
                s0 = r0.get("pool_sharpe", 0.0)
                g0 = r0.get("accumulated_gain_pct", 0.0)
                tr0 = r0.get("trades", 0)
                floor_total = len(subset) * args.min_trades_per_sym
                rel0 = 1 if tr0 >= floor_total else 0
                w.writerow([-1, round(s0, 4), round(g0, 2), round(r0.get("max_dd_pct", 0.0), 2),
                             tr0, round(g0 / args.bh_accumulated_gain_pct, 3) if args.bh_accumulated_gain_pct else 0,
                             round(time.time() - t_bl, 1), 0, rel0, 0, json.dumps({})])
                csv_f.flush()
                print(f"[AUTO_SEARCH] BASELINE sharpe={s0:.4f} gain={g0:.1f}% trades={tr0} reliable={rel0}", flush=True)

        best_gain = -1e9
        for i in range(args.n_max):
            cfg = copy.deepcopy(base)
            ovr = _sample_cfg(base, args.bool_flip_prob, args.numeric_perturb_prob)
            for k, v in ovr.items():
                if hasattr(cfg, k):
                    setattr(cfg, k, v)
            t0 = time.time()
            r, timed_out, err = _run_simulate_timed(cfg, args.max_iter_seconds, _mp_ctx)
            el = time.time() - t0
            if timed_out:
                print(f"[AUTO_SEARCH] iter={i} TIMEOUT ({el:.0f}s > {args.max_iter_seconds}s), skipping", flush=True)
                continue
            if err:
                print(f"[AUTO_SEARCH] iter={i} ERR: {err}", flush=True)
                continue
            gain = r.get("accumulated_gain_pct", 0.0)
            sharpe = r.get("pool_sharpe", 0.0)
            dd = r.get("max_dd_pct", 0.0)
            tr = r.get("trades", 0)
            gvb = gain / args.bh_accumulated_gain_pct if args.bh_accumulated_gain_pct != 0 else 0.0
            n_syms = len(subset)
            floor_total = max(args.min_trades_for_record, n_syms * args.min_trades_per_sym)
            reliable = 1 if tr >= floor_total else 0
            useless = 1 if (reliable and sharpe < args.sharpe_useless_floor) else 0
            w.writerow([i, round(sharpe, 4), round(gain, 2), round(dd, 2), tr,
                        round(gvb, 3), round(el, 1), len(ovr), reliable, useless, json.dumps(ovr)])
            csv_f.flush()
            if gain > best_gain:
                best_gain = gain
                print(f"[AUTO_SEARCH] iter={i} NEW_BEST_GAIN sharpe={sharpe:.3f} "
                      f"gain={gain:.1f}% ({gvb:.2f}x BH) dd={dd:.1f}% tr={tr} "
                      f"ovr={len(ovr)} el={el:.1f}s", flush=True)
            if reliable and gain >= target_gain and sharpe >= target_sharpe:
                win = {"iter": i, "pool_sharpe": round(sharpe, 4),
                       "acc_gain_pct": round(gain, 2), "max_dd_pct": round(dd, 2),
                       "trades": tr, "gain_vs_bh": round(gvb, 3),
                       "reliable": reliable, "overrides": ovr}
                with open(winners_path, "a") as wf:
                    wf.write(json.dumps(win) + "\n")
                print(f"[AUTO_SEARCH] *** WINNER iter={i} gain={gain:.0f}% "
                      f"({gvb:.1f}x BH) sharpe={sharpe:.3f} dd={dd:.1f}% tr={tr} ***",
                      flush=True)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
