"""Autonomous config search: keep sampling until accumulated_gain_pct > 10 * B&H.

Writes winners immediately to winners JSONL, all results to CSV, runs forever
until killed or N_MAX reached.

Timeout is enforced via multiprocessing.Process (fork) so numpy C code can
actually be killed — signal.SIGALRM cannot interrupt numpy's C extensions.
"""
import argparse, copy, csv, json, math, os, random, signal, sys, time, gc
from datetime import datetime
import multiprocessing as mp
from dataclasses import fields
from pathlib import Path

try:
    from scipy.stats import norm as _scipy_norm
    _HAVE_SCIPY = True
except Exception:
    _HAVE_SCIPY = False


def _norm_ppf(p):
    """Inverse normal CDF; uses scipy if available, else Acklam approximation."""
    if _HAVE_SCIPY:
        return float(_scipy_norm.ppf(p))
    # Peter Acklam's algorithm — accurate to ~1e-9
    if p <= 0.0 or p >= 1.0:
        if p <= 0.0: return -float("inf")
        return float("inf")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow = 0.02425; phigh = 1 - plow
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
               ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p <= phigh:
        q = p - 0.5; r = q * q
        return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / \
               (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)
    q = math.sqrt(-2 * math.log(1 - p))
    return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / \
            ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)


def _norm_cdf(x):
    if _HAVE_SCIPY:
        return float(_scipy_norm.cdf(x))
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def deflated_sharpe(observed_sharpe, n_trials, n_observations,
                    skew=0.0, kurt=3.0, sharpe_std=None):
    """Bailey & Lopez de Prado (2014) Deflated Sharpe Ratio.

    observed_sharpe: per-trade Sharpe (the user's pool_sharpe)
    n_trials: number of independent backtests run (~ iter count)
    n_observations: number of trades in the winning config
    skew, kurt: skew and kurtosis of trade returns (default normal: 0, 3)
    sharpe_std: stdev of Sharpes across trials (unused if None — null model used)
    Returns: (dsr, psr) where
        dsr = deflated SR in stdev units (sr - expected_max_under_null) / sigma_sr
        psr = probabilistic SR in [0,1] (cdf of dsr)
    """
    gamma = 0.5772156649015329  # Euler-Mascheroni
    n_t = max(int(n_trials), 2)
    z1 = _norm_ppf(1.0 - 1.0 / n_t)
    z2 = _norm_ppf(1.0 - 1.0 / (n_t * math.e))
    expected_max_sr_null = (1.0 - gamma) * z1 + gamma * z2
    sr = float(observed_sharpe)
    n_obs = max(int(n_observations) - 1, 1)
    var_sr = (1.0 - skew * sr + (kurt - 1.0) / 4.0 * sr * sr) / n_obs
    sigma_sr = math.sqrt(max(var_sr, 1e-12))
    dsr = (sr - expected_max_sr_null) / sigma_sr
    psr = _norm_cdf(dsr)
    return dsr, psr


# Default ranking metric. Per CLAUDE.md `feedback_chained_sharpe_is_overfit_lie`
# raw pool_sharpe overfits in large search spaces — DSR penalises by trial count.
RANKING_METRIC = os.environ.get("AUTO_SEARCH_RANKING_METRIC", "deflated_sharpe")  # "deflated_sharpe" | "pool_sharpe"
RANKING_TRIALS_THRESHOLD = 100  # below this, fall back to pool_sharpe (DSR unstable on tiny n_trials)


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
    # HEDGE_ENABLED=True in quick engine inflates trade count 177→25,610+ via micro-hedge churn.
    # Engine comment: "True inflated trades 1209→84K due to no min-hold." Lock False in all searches.
    "HEDGE_ENABLED",
    # ABLATION_DISABLE_QUICK_EXIT=True returns zeros from compute_exit_signals — disables ALL exits.
    # Positions never close; trade count and gain inflate arbitrarily. Not in live code. Lock False.
    "ABLATION_DISABLE_QUICK_EXIT",
    # NOTE: PARTIAL_PROFIT_LOCK_* and PARTIAL_EXIT_* are NOT forbidden.
    # PPL is a REAL live mechanism (50% close at +0.5% via maker/webhook_url_2).
    # It must be searchable — it is the only TP mechanism after PROFIT_TARGET removal.
}

# Per-mode caps for hold-bar parameters — prevents multi-session overnight holds that inflate
# Sharpe by forcing losers to recover before exit (gap risk not modeled in backtest).
# 2026-04-24: 100% of Sharpe>3.0 tradier winners used MIN_HOLD_BARS >= 2×baseline (124 bars =
# 620 min = overnight). Tradier cap = 78 bars (1 full session: 9:30am–4pm at 5m/bar).
# Crypto 24/7 — no session boundary, but cap at 240 bars (12h at 3m/bar) to prevent
# extreme cherry-picking from forcing all losers to be held until recovery.
HOLD_BAR_CAPS = {
    "MIN_HOLD_BARS":              {"tradier": 78, "crypto": 240},
    "MIN_HOLD_BARS_BEFORE_EXIT":  {"tradier": 78, "crypto": 240},
    "REGIME_TRENDING_MIN_HOLD_BARS": {"tradier": 78, "crypto": 240},
    "REGIME_RANGING_MIN_HOLD_BARS":  {"tradier": 78, "crypto": 240},
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


def _worker_with_setsid(cfg, child_conn):
    """Forked worker that creates its own process group so killpg kills it fully."""
    try:
        os.setsid()
    except Exception:
        pass
    _sim_worker_fn(cfg, child_conn)


def _run_simulate_timed(cfg, timeout_secs, mp_ctx):
    """Run simulate(cfg) in a child process; return (result, timed_out, error_str)."""
    parent_conn, child_conn = mp_ctx.Pipe(duplex=False)
    proc = mp_ctx.Process(target=_worker_with_setsid, args=(cfg, child_conn), daemon=False)
    proc.start()
    child_conn.close()
    proc.join(timeout=timeout_secs)
    if proc.is_alive():
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except Exception:
            proc.kill()
        proc.join(timeout=10)
        if proc.is_alive():
            proc.kill()
        parent_conn.close()
        return None, True, None
    # exitcode < 0 means killed by signal (e.g. SIGKILL from OOM killer)
    if proc.exitcode is not None and proc.exitcode < 0:
        parent_conn.close()
        return None, False, f"child killed by signal {-proc.exitcode} (OOM?)"
    try:
        r = parent_conn.recv()
    except EOFError:
        r = {}
    parent_conn.close()
    if "_err" in r:
        return None, False, r["_err"]
    return r, False, None


def _sample_cfg(base_cfg, bool_flip_prob=0.15, numeric_perturb_prob=0.10):
    """Return a dict of overrides sampled randomly from knob space, skipping FORBIDDEN_FLIPS.

    Sweep dimensions are auto-discovered via dataclasses.fields(QuickConfig). New switches added
    to v8_quick_engine.QuickConfig (VOL_TARGET_*, DD_KELLY_*, MINERVINI_*, CLENOW_*,
    PROXIMITY_TOP_*, SQUEEZE_FIRE_*, TSMOM_BOOK_SCALAR_*, etc.) are picked up automatically.
    No explicit dimension registry is needed — by design, follow the existing pattern.
    To LOCK a dimension OFF for safety, add it to FORBIDDEN_FLIPS above.
    """
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
                if nm in HOLD_BAR_CAPS:
                    mode = getattr(base_cfg, "MODE", "crypto")
                    cap = HOLD_BAR_CAPS[nm].get(mode, HOLD_BAR_CAPS[nm]["crypto"])
                    new = min(new, cap)
                # Stoch entry thresholds must stay in sensible trading ranges.
                # LONG entry gate: k_5m < threshold — cap at 60 so it stays a real
                # "not overbought" filter (90 = almost always true = useless/wrong).
                # SHORT entry gate: k_5m > threshold — floor at 40 so it stays a real
                # "not oversold" filter.
                if nm == "TRADIER_STOCH_ENTRY_LONG_TRADIER":
                    new = min(new, 60)
                elif nm == "TRADIER_STOCH_ENTRY_SHORT_TRADIER":
                    new = max(new, 40)
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
    ap.add_argument("--auto-restart-iters", type=int, default=50,
                    help="Exit cleanly after N successful iters so the watchdog can respawn a fresh worker. "
                         "Combats slow memory growth that OOMs the worker around iter 25-30. "
                         "Set to 0 to disable. CSV is append-mode, so progress persists across restarts.")
    args = ap.parse_args()

    sys.path.insert(0, str(Path(__file__).parent))
    from v8_quick_engine import QuickConfig, load_npz, simulate

    n_years = max((datetime.utcnow() - datetime.strptime(args.start, "%Y-%m-%d")).days / 365.25, 0.01)

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
            w.writerow(["iter", "pool_sharpe", "deflated_sharpe", "psr", "sym_sharpe",
                        "acc_gain_pct", "gain_sym_yr", "avg_gain_trade", "gain_per_yr",
                        "max_dd_pct", "trades", "gain_vs_bh",
                        "elapsed_s", "overrides_count", "reliable", "useless", "overrides_json"])

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
                sym_s0 = r0.get("sharpe", 0.0)
                gsy0 = round(g0 / len(subset) / n_years, 4)
                agt0 = round(g0 / tr0, 4) if tr0 else 0.0
                gpy0 = round(g0 / n_years, 4)
                # DSR for baseline uses n_trials=1 (no search yet) — informative but not used for ranking.
                dsr0, psr0 = deflated_sharpe(s0, n_trials=max(args.n_max, 2), n_observations=max(tr0, 1))
                w.writerow([-1, round(s0, 4), round(dsr0, 4), round(psr0, 4), round(sym_s0, 4),
                             round(g0, 2), gsy0, agt0, gpy0,
                             round(r0.get("max_dd_pct", 0.0), 2), tr0,
                             round(g0 / args.bh_accumulated_gain_pct, 3) if args.bh_accumulated_gain_pct else 0,
                             round(time.time() - t_bl, 1), 0, rel0, 0, json.dumps({})])
                csv_f.flush()
                print(f"[AUTO_SEARCH] BASELINE pool_sharpe={s0:.4f} dsr={dsr0:.4f} psr={psr0:.4f} "
                      f"sym_sharpe={sym_s0:.4f} gain={g0:.1f}% avg_gain_trade={agt0:.4f}%/trade "
                      f"gain_per_yr={gpy0:.2f}%/yr gain_sym_yr={gsy0:.4f}%/sym/yr trades={tr0} "
                      f"reliable={rel0}", flush=True)

        best_gain = -1e9
        best_rank_score = -1e9  # tracks best by RANKING_METRIC (DSR by default)
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
            sym_sharpe = r.get("sharpe", 0.0)
            dd = r.get("max_dd_pct", 0.0)
            tr = r.get("trades", 0)
            gvb = gain / args.bh_accumulated_gain_pct if args.bh_accumulated_gain_pct != 0 else 0.0
            n_syms = len(subset)
            gain_sym_yr = round(gain / n_syms / n_years, 4)
            avg_gain_trade = round(gain / tr, 4) if tr else 0.0
            gain_per_yr = round(gain / n_years, 4)
            floor_total = max(args.min_trades_for_record, n_syms * args.min_trades_per_sym)
            reliable = 1 if tr >= floor_total else 0
            useless = 1 if (reliable and sharpe < args.sharpe_useless_floor) else 0
            # Deflated Sharpe — n_trials = current iter count (i+1 trials including this one).
            tr_skew = float(r.get("trade_returns_skew", 0.0)) if isinstance(r, dict) else 0.0
            tr_kurt = float(r.get("trade_returns_kurt", 3.0)) if isinstance(r, dict) else 3.0
            dsr, psr = deflated_sharpe(sharpe, n_trials=max(i + 1, 2),
                                       n_observations=max(tr, 1),
                                       skew=tr_skew, kurt=tr_kurt)
            w.writerow([i, round(sharpe, 4), round(dsr, 4), round(psr, 4), round(sym_sharpe, 4),
                        round(gain, 2), gain_sym_yr, avg_gain_trade, gain_per_yr,
                        round(dd, 2), tr, round(gvb, 3), round(el, 1), len(ovr),
                        reliable, useless, json.dumps(ovr)])
            csv_f.flush()
            # Ranking: deflated_sharpe once n_trials > threshold, else pool_sharpe.
            if RANKING_METRIC == "deflated_sharpe" and (i + 1) > RANKING_TRIALS_THRESHOLD:
                rank_score = dsr
                rank_label = "dsr"
            else:
                rank_score = sharpe
                rank_label = "pool_sharpe"
            if reliable and rank_score > best_rank_score:
                best_rank_score = rank_score
                print(f"[AUTO_SEARCH] iter={i} NEW_BEST_{rank_label.upper()}={rank_score:.4f} "
                      f"pool_sharpe={sharpe:.3f} dsr={dsr:.3f} psr={psr:.3f} sym_sharpe={sym_sharpe:.3f} "
                      f"gain={gain:.1f}% avg_gain_trade={avg_gain_trade:.4f}%/trade gain_per_yr={gain_per_yr:.2f}%/yr "
                      f"gain_sym_yr={gain_sym_yr:.4f}%/sym/yr ({gvb:.2f}x BH) dd={dd:.1f}% tr={tr} "
                      f"ovr={len(ovr)} el={el:.1f}s", flush=True)
            if gain > best_gain:
                best_gain = gain
                print(f"[AUTO_SEARCH] iter={i} NEW_BEST_GAIN pool_sharpe={sharpe:.3f} dsr={dsr:.3f} psr={psr:.3f} "
                      f"sym_sharpe={sym_sharpe:.3f} gain={gain:.1f}% avg_gain_trade={avg_gain_trade:.4f}%/trade "
                      f"gain_per_yr={gain_per_yr:.2f}%/yr gain_sym_yr={gain_sym_yr:.4f}%/sym/yr "
                      f"({gvb:.2f}x BH) dd={dd:.1f}% tr={tr} ovr={len(ovr)} el={el:.1f}s", flush=True)
            if reliable and gain >= target_gain and sharpe >= target_sharpe:
                win = {"iter": i, "pool_sharpe": round(sharpe, 4),
                       "deflated_sharpe": round(dsr, 4), "psr": round(psr, 4),
                       "sym_sharpe": round(sym_sharpe, 4),
                       "acc_gain_pct": round(gain, 2), "gain_sym_yr": gain_sym_yr,
                       "avg_gain_trade": avg_gain_trade, "gain_per_yr": gain_per_yr,
                       "max_dd_pct": round(dd, 2), "trades": tr, "gain_vs_bh": round(gvb, 3),
                       "reliable": reliable, "n_trials_at_win": i + 1, "overrides": ovr}
                with open(winners_path, "a") as wf:
                    wf.write(json.dumps(win) + "\n")
                print(f"[AUTO_SEARCH] *** WINNER iter={i} pool_sharpe={sharpe:.3f} dsr={dsr:.3f} psr={psr:.3f} "
                      f"sym_sharpe={sym_sharpe:.3f} gain={gain:.0f}% avg_gain_trade={avg_gain_trade:.4f}%/trade "
                      f"gain_per_yr={gain_per_yr:.2f}%/yr gain_sym_yr={gain_sym_yr:.4f}%/sym/yr "
                      f"({gvb:.1f}x BH) dd={dd:.1f}% tr={tr} ***", flush=True)
            # ═══ 2026-04-26 MEM HYGIENE — combats slow growth that OOMs around iter 25-30 ═══
            # Drop per-iter refs explicitly + force gc. Pipe pickle of cfg+result leaves dangling
            # refs that Python's auto-GC doesn't reap aggressively enough on 30GB box with ~5GB
            # parent NPZ subset already pinned.
            del cfg, ovr, r
            try: del timed_out, err
            except Exception: pass
            if (i + 1) % 5 == 0:
                gc.collect()
            # Auto-restart: exit cleanly so watchdog respawns a fresh process. CSV is append-mode,
            # winners JSONL is append-mode, so no progress is lost.
            if args.auto_restart_iters > 0 and (i + 1) >= args.auto_restart_iters:
                gc.collect()
                try: import resource; ru = resource.getrusage(resource.RUSAGE_SELF); rss_mb = ru.ru_maxrss // 1024
                except Exception: rss_mb = 0
                print(f"[AUTO_SEARCH] AUTO_RESTART after {args.auto_restart_iters} iters (max_rss≈{rss_mb}MB). "
                      f"Watchdog will respawn — CSV preserved at {csv_path}", flush=True)
                csv_f.flush()
                sys.exit(0)


if __name__ == "__main__":
    mp.set_start_method("fork", force=True)
    main()
