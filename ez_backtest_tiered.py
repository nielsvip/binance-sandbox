#!/usr/bin/env python3
"""
TIERED ALIGNMENT BACKTEST
==========================
Tests a risk-tiered entry system:

  Tier 1 (4/4): D+4h+1h aligned + 15m cross (clean) → 100% size, full TP
  Tier 2 (3/4): D+4h aligned + 15m cross           → 50% size, 70% TP
  Tier 3 (2/4): D only aligned + 15m cross          → 25% size, 50% TP

"Clean cross" detonator (simulating 3m/1m entry quality on 15m):
  - K/D crossover while K was oversold (<40 for long, >60 for short)
  - OR: higher low on current bar vs previous (1m higher low proxy)
  - AND: bar volume >= relative volume threshold

Relative volume filter:
  - rel_vol = bar_volume / avg_volume(last N bars)
  - Tests: 0 (none), 1.0, 1.3, 1.5, 2.0

Output: per-tier P&L + combined weighted P&L (by size)
        shows whether riskier tiers add value to the portfolio

Usage:
  python3 ez_backtest_tiered.py [--workers N] [--tp 1.5] [--report-only]
"""
import sys, os, json, math, time, sqlite3, argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import numpy as np

BASE_PATH = Path(os.environ.get('BASE_PATH', '/home/niels/binance-sandbox'))
KLINES_DIR = BASE_PATH / 'klines_cache'
RESULTS_DIR = BASE_PATH / 'backtest_framework' / 'results'
DB_PATH = RESULTS_DIR / 'tiered.db'
REPORT_PATH = RESULTS_DIR / 'tiered_report.md'

FEE = 0.0004
WARMUP = 300
STOCH_K = 9       # best from tournament
STOCH_SK = 5
STOCH_SD = 5
TP_BASE = 1.5     # tier 1 TP (%)
ANNUAL_BARS_15M = 35040

# Tier definitions: (htf_required, size_weight, tp_multiplier, label)
TIERS = [
    (3, 1.00, 1.00, "T1_D+4h+1h+15m"),   # all 3 HTFs required
    (2, 0.50, 0.70, "T2_D+4h+15m"),       # only D+4h required
    (1, 0.25, 0.50, "T3_D+15m"),          # only D required
]
HTF_LIST = ['D', '4h', '1h']

# Relative volume filter thresholds to test
REL_VOL_THRESHOLDS = [0.0, 1.0, 1.3, 1.5, 2.0]
VOL_LOOKBACK = 20   # bars for avg volume


# ═══════════════════════════════════════════════════════════════════════
# INDICATOR ENGINE
# ═══════════════════════════════════════════════════════════════════════

def load_tf(sym, tf):
    p = KLINES_DIR / f'{sym}_{tf}.json'
    if not p.exists():
        return None
    try:
        with open(p) as f:
            data = json.load(f)
        if not data:
            return None
        if isinstance(data[0], dict):
            arr = np.array([[d['open'], d['high'], d['low'], d['close'], d.get('volume', 0)]
                            for d in data], dtype=np.float64)
        else:
            raw = np.array(data, dtype=np.float64)
            arr = raw[:, 1:6] if raw.ndim == 2 and raw.shape[1] >= 6 else raw[:, :5]
        return arr  # (n, 5): o, h, l, c, v
    except Exception:
        return None


def stoch_kd(h, l, c, k_period=9, k_smooth=5, d_smooth=5):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = h[i - k_period + 1:i + 1].max()
        ll = l[i - k_period + 1:i + 1].min()
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
    k = np.convolve(raw_k, np.ones(k_smooth) / k_smooth, mode='same')
    d = np.convolve(k, np.ones(d_smooth) / d_smooth, mode='same')
    return k.astype(np.float32), d.astype(np.float32)


def heikin_ashi_bull(o, h, l, c):
    hac = (o + h + l + c) / 4
    hao = np.empty(len(o))
    hao[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2
    return (hac > hao).astype(np.int8)


def rel_vol(v, lookback=20):
    """Relative volume: v[i] / mean(v[i-lookback:i])"""
    n = len(v)
    rv = np.ones(n)
    cs = np.cumsum(v)
    for i in range(lookback, n):
        avg = (cs[i] - cs[i - lookback]) / lookback
        rv[i] = v[i] / avg if avg > 1e-9 else 1.0
    return rv.astype(np.float32)


def make_idx(n_p, n_t):
    return np.clip((np.arange(n_p) * n_t / n_p).astype(np.int32), 0, n_t - 1)


def precompute(sym):
    """Load and compute all indicators for a symbol."""
    primary = load_tf(sym, '15m')
    if primary is None or len(primary) < WARMUP + 200:
        return None
    n = len(primary)
    o, h, l, c, v = primary[:, 0], primary[:, 1], primary[:, 2], primary[:, 3], primary[:, 4]
    k, d = stoch_kd(h, l, c, STOCH_K, STOCH_SK, STOCH_SD)
    ha = heikin_ashi_bull(o, h, l, c)
    k_prev = np.roll(k, 1); d_prev = np.roll(d, 1)
    co = (k > d) & (k_prev <= d_prev)   # bullish cross
    cu = (k < d) & (k_prev >= d_prev)   # bearish cross
    co[0] = False; cu[0] = False
    rv = rel_vol(v, VOL_LOOKBACK)
    # "Clean" detonator quality: cross while previously oversold/overbought
    # OR: higher low on primary TF (l[i] > l[i-1], c[i] > c[i-1])
    l_prev = np.roll(l, 1); c_prev = np.roll(c, 1)
    k_prev2 = np.roll(k, 2)  # two bars ago
    clean_long = co & ((k_prev <= 40) | (l > l_prev))   # cross from oversold OR higher low
    clean_short = cu & ((k_prev >= 60) | (l < l_prev))  # cross from overbought OR lower high
    clean_long[0] = False; clean_short[0] = False
    ind = {
        'n': n, 'c': c, 'h': h, 'l': l,
        'k': k, 'd': d, 'ha': ha, 'co': co, 'cu': cu,
        'rv': rv, 'clean_long': clean_long, 'clean_short': clean_short,
        'htf': {}, 'htf_missing': set(),
    }
    for htf in HTF_LIST:
        data = load_tf(sym, htf)
        if data is None or len(data) < 50:
            ind['htf_missing'].add(htf)
            continue
        nt = len(data)
        idx = make_idx(n, nt)
        o2, h2, l2, c2 = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
        k2, d2 = stoch_kd(h2, l2, c2, STOCH_K, STOCH_SK, STOCH_SD)
        ha2 = heikin_ashi_bull(o2, h2, l2, c2)
        ind['htf'][htf] = {
            'k': k2[idx], 'd': d2[idx], 'ha': ha2[idx],
        }
    return ind


def count_htf_aligned(ind, bar, is_long):
    """Count how many HTFs are K>D aligned for this bar."""
    count = 0
    for htf in HTF_LIST:
        hd = ind['htf'].get(htf)
        if hd is None:
            continue
        kv = float(hd['k'][bar])
        dv = float(hd['d'][bar])
        hav = int(hd['ha'][bar])
        if is_long and kv > dv and hav == 1:
            count += 1
        elif not is_long and kv < dv and hav == 0:
            count += 1
    return count


# ═══════════════════════════════════════════════════════════════════════
# SIMULATION (one per rel_vol threshold)
# ═══════════════════════════════════════════════════════════════════════

def simulate_tiered(ind, is_long, rv_thresh):
    """
    Walk 15m bars. For each bar:
    - Count HTF alignment (0-3)
    - Check detonator quality (clean cross)
    - Check relative volume
    - Open trade at the appropriate tier or skip
    - Size = tier weight; TP = tier tp_mult × TP_BASE
    Returns: list of (tier_label, size_weight, return%)
    """
    if len(ind['htf_missing']) > 0:
        return []
    n = ind['n']
    c = ind['c']
    co = ind['clean_long'] if is_long else ind['clean_short']
    rv = ind['rv']
    max_bars = 250  # 15m bars = ~62.5 hours
    trades = []
    in_trade = False
    ep = 0.0
    eb = 0
    tier_label = None
    tier_size = 0.0
    tier_tp = 0.0
    for bar in range(WARMUP, n - 1):
        price = c[bar]
        if price <= 0:
            continue
        if in_trade:
            gain = (price - ep) / ep * 100.0 if is_long else (ep - price) / ep * 100.0
            if gain >= tier_tp or (bar - eb) >= max_bars:
                ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
                trades.append((tier_label, tier_size, ret * 100.0))
                in_trade = False
        else:
            # Must have a detonator trigger
            if not co[bar]:
                continue
            # Primary TF K>D alignment
            kv = float(ind['k'][bar])
            dv = float(ind['d'][bar])
            if is_long and kv <= dv:
                continue
            if not is_long and kv >= dv:
                continue
            # Relative volume filter
            rv_val = float(rv[bar])
            if rv_thresh > 0 and rv_val < rv_thresh:
                continue
            # Count HTF alignment
            n_aligned = count_htf_aligned(ind, bar, is_long)
            # Find which tier applies (best tier that matches)
            matched_tier = None
            for htf_req, size, tp_mult, label in TIERS:
                if n_aligned >= htf_req:
                    matched_tier = (htf_req, size, tp_mult, label)
                    break
            if matched_tier is None:
                continue  # no tier matched (0/4 aligned)
            _, tier_size, tp_mult, tier_label = matched_tier
            tier_tp = TP_BASE * tp_mult
            in_trade = True
            ep = price
            eb = bar
    if in_trade:
        price = c[n - 1]
        ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
        trades.append((tier_label, tier_size, ret * 100.0))
    return trades


# ═══════════════════════════════════════════════════════════════════════
# METRICS
# ═══════════════════════════════════════════════════════════════════════

def compute_tier_metrics(trades_all):
    """
    Compute per-tier metrics AND combined weighted Sharpe.
    trades_all: list of (tier_label, size_weight, return%)
    """
    if not trades_all:
        return {}
    # Per-tier breakdown
    tier_trades = {}
    for label, size, ret in trades_all:
        if label not in tier_trades:
            tier_trades[label] = []
        tier_trades[label].append(ret)
    result = {}
    for label, rets in tier_trades.items():
        r = np.array(rets)
        wins = r[r > 0]; losses = r[r <= 0]
        pf = wins.sum() / max(abs(losses.sum()), 1e-9) if len(wins) and len(losses) else (99.0 if not len(losses) else 0.0)
        mean_r = r.mean(); std_r = r.std()
        sharpe = mean_r / std_r * math.sqrt(ANNUAL_BARS_15M) if std_r > 1e-9 else 0.0
        cum = np.cumsum(r); peak = np.maximum.accumulate(cum)
        max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
        result[label] = {
            'sharpe': round(float(sharpe), 3),
            'win_rate': round(float((r > 0).mean()), 3),
            'pf': round(float(pf), 3),
            'max_dd': round(float(max_dd), 3),
            'total_return': round(float(r.sum()), 3),
            'n_trades': len(rets),
        }
    # Combined: weight each trade return by its size, compute portfolio Sharpe
    if trades_all:
        weighted_rets = np.array([size * ret for _, size, ret in trades_all])
        mean_w = weighted_rets.mean(); std_w = weighted_rets.std()
        sharpe_w = mean_w / std_w * math.sqrt(ANNUAL_BARS_15M) if std_w > 1e-9 else 0.0
        cum_w = np.cumsum(weighted_rets); peak_w = np.maximum.accumulate(cum_w)
        max_dd_w = float((peak_w - cum_w).max()) if len(cum_w) > 0 else 0.0
        wins_w = weighted_rets[weighted_rets > 0]
        losses_w = weighted_rets[weighted_rets <= 0]
        pf_w = wins_w.sum() / max(abs(losses_w.sum()), 1e-9) if len(wins_w) and len(losses_w) else (99.0 if not len(losses_w) else 0.0)
        result['COMBINED'] = {
            'sharpe': round(float(sharpe_w), 3),
            'win_rate': round(float((weighted_rets > 0).mean()), 3),
            'pf': round(float(pf_w), 3),
            'max_dd': round(float(max_dd_w), 3),
            'total_return': round(float(weighted_rets.sum()), 3),
            'n_trades': len(trades_all),
        }
    return result


# ═══════════════════════════════════════════════════════════════════════
# WORKER
# ═══════════════════════════════════════════════════════════════════════

def worker(args):
    sym, is_long = args
    ind = precompute(sym)
    if ind is None:
        return sym, None
    results = {}
    for rv_thresh in REL_VOL_THRESHOLDS:
        trades = simulate_tiered(ind, is_long, rv_thresh)
        metrics = compute_tier_metrics(trades)
        results[rv_thresh] = metrics
    return sym, results


# ═══════════════════════════════════════════════════════════════════════
# SYMBOLS
# ═══════════════════════════════════════════════════════════════════════

def get_symbols():
    long_syms, short_syms = [], []
    for fname in ['symbols_ang_long.json', 'symbols_inf_long.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f: long_syms = json.load(f)
            break
    for fname in ['symbols_ang_short.json', 'symbols_inf_short.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f: short_syms = json.load(f)
            break
    return long_syms, short_syms


# ═══════════════════════════════════════════════════════════════════════
# DB + REPORT
# ═══════════════════════════════════════════════════════════════════════

def init_db():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS tiered_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, side TEXT, rv_thresh REAL, tier TEXT,
        sharpe REAL, win_rate REAL, pf REAL, max_dd REAL,
        total_return REAL, n_trades INTEGER, ts TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tier ON tiered_results(tier)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rv ON tiered_results(rv_thresh)")
    conn.commit()
    return conn


def store(conn, sym, is_long, rv_thresh, metrics):
    now = datetime.now(timezone.utc).isoformat()
    side = 'LONG' if is_long else 'SHORT'
    rows = []
    for tier, m in metrics.items():
        rows.append((sym, side, rv_thresh, tier, m['sharpe'], m['win_rate'],
                     m['pf'], m['max_dd'], m['total_return'], m['n_trades'], now))
    conn.executemany("""INSERT INTO tiered_results
        (symbol, side, rv_thresh, tier, sharpe, win_rate, pf, max_dd, total_return, n_trades, ts)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()


def generate_report(conn):
    cur = conn.cursor()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    lines = [
        "# TIERED ALIGNMENT BACKTEST REPORT",
        f"Generated: {now}",
        f"Stoch: K={STOCH_K} SK={STOCH_SK} SD={STOCH_SD} | Base TP={TP_BASE}%",
        f"Tier 1 (4/4 D+4h+1h aligned): 100% size, {TP_BASE*1.0:.1f}% TP",
        f"Tier 2 (3/4 D+4h aligned):     50% size, {TP_BASE*0.7:.2f}% TP",
        f"Tier 3 (2/4 D only):            25% size, {TP_BASE*0.5:.2f}% TP",
        "",
    ]

    # ── Aggregate by tier × rv_thresh ──
    lines.append("## Aggregate: Tier Performance by RelVol Filter")
    lines.append("")
    lines.append(f"| {'RV Filter':>9} | {'Tier':<22} | {'Sharpe':>8} | {'WR%':>6} | {'PF':>6} | {'MaxDD%':>7} | {'Ret%':>7} | {'Trades':>7} | {'Syms':>5} |")
    lines.append(f"|{'-'*11}|{'-'*24}|{'-'*10}|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*9}|{'-'*9}|{'-'*7}|")
    MIN_T = 5
    for rv_thresh in REL_VOL_THRESHOLDS:
        for tier in ['T1_D+4h+1h+15m', 'T2_D+4h+15m', 'T3_D+15m', 'COMBINED']:
            row = cur.execute("""SELECT AVG(sharpe), AVG(win_rate), AVG(pf), AVG(max_dd),
                AVG(total_return), AVG(n_trades), COUNT(DISTINCT symbol)
                FROM tiered_results WHERE rv_thresh=? AND tier=? AND n_trades>=?""",
                (rv_thresh, tier, MIN_T)).fetchone()
            if row and row[0] is not None:
                rv_label = f"RV≥{rv_thresh:.1f}" if rv_thresh > 0 else "no filter"
                lines.append(f"| {rv_label:>9} | {tier:<22} | {row[0]:>8.3f} | {row[1]*100:>5.1f}% | {row[2]:>6.2f} | {row[3]:>6.2f}% | {row[4]:>6.2f}% | {row[5]:>7.0f} | {row[6]:>5} |")
        lines.append(f"|{' ':>11}|{' ':>24}|{' ':>10}|{' ':>8}|{' ':>8}|{' ':>9}|{' ':>9}|{' ':>9}|{' ':>7}|")
    lines.append("")

    # ── Best RV threshold by combined Sharpe ──
    lines.append("## Best RelVol Threshold (by combined weighted Sharpe)")
    lines.append("")
    best_rows = cur.execute("""SELECT rv_thresh, AVG(sharpe) as s FROM tiered_results
        WHERE tier='COMBINED' AND n_trades>=?
        GROUP BY rv_thresh ORDER BY s DESC""", (MIN_T,)).fetchall()
    lines.append("| RV Threshold | Avg Combined Sharpe |")
    lines.append("|-------------|---------------------|")
    for r in best_rows:
        lines.append(f"| {'≥'+str(r[0]) if r[0]>0 else 'none':>12} | {r[1]:>19.3f} |")
    lines.append("")

    # ── Value added by each tier (no RV filter) ──
    lines.append("## Value of Each Tier (no RV filter, delta vs T1 only)")
    lines.append("")
    t1_sharpe = cur.execute("""SELECT AVG(sharpe) FROM tiered_results
        WHERE rv_thresh=0 AND tier='T1_D+4h+1h+15m' AND n_trades>=?""", (MIN_T,)).fetchone()
    t1_s = t1_sharpe[0] if t1_sharpe and t1_sharpe[0] else 0
    combined_sharpe = cur.execute("""SELECT AVG(sharpe) FROM tiered_results
        WHERE rv_thresh=0 AND tier='COMBINED' AND n_trades>=?""", (MIN_T,)).fetchone()
    comb_s = combined_sharpe[0] if combined_sharpe and combined_sharpe[0] else 0
    lines.append(f"- T1 only avg Sharpe:  {t1_s:.3f}")
    lines.append(f"- All tiers combined:  {comb_s:.3f}  (delta: {comb_s-t1_s:+.3f})")
    lines.append("")
    lines.append("> **If combined > T1 alone**: tiered approach (allowing riskier trades with smaller size) adds portfolio value.")
    lines.append("> **If combined < T1 alone**: better to only take 4/4 setups; lower tiers drag performance.")
    lines.append("")

    # ── Per-symbol T1 vs Combined ──
    lines.append("## Per-Symbol T1 vs Combined (no RV filter, top 20 by T1 Sharpe)")
    lines.append("")
    lines.append("| Symbol | Side | T1 Sharpe | T1 Trades | Combined Sharpe | Combined Trades | +Delta |")
    lines.append("|--------|------|-----------|-----------|-----------------|-----------------|--------|")
    syms = cur.execute("""SELECT symbol, side, sharpe, n_trades FROM tiered_results
        WHERE rv_thresh=0 AND tier='T1_D+4h+1h+15m' AND n_trades>=?
        ORDER BY sharpe DESC LIMIT 20""", (MIN_T,)).fetchall()
    for sym, side, t1_sh, t1_n in syms:
        comb_row = cur.execute("""SELECT sharpe, n_trades FROM tiered_results
            WHERE symbol=? AND side=? AND rv_thresh=0 AND tier='COMBINED' AND n_trades>=?""",
            (sym, side, MIN_T)).fetchone()
        if comb_row:
            delta = comb_row[0] - t1_sh
            lines.append(f"| {sym:<14} | {side} | {t1_sh:>9.3f} | {t1_n:>9} | {comb_row[0]:>15.3f} | {comb_row[1]:>15} | {delta:>+6.3f} |")
    lines.append("")

    report = "\n".join(lines)
    with open(REPORT_PATH, 'w') as f:
        f.write(report)
    print(f"Report: {REPORT_PATH}")
    return report


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--tp', type=float, default=1.5)
    parser.add_argument('--long-only', action='store_true')
    parser.add_argument('--short-only', action='store_true')
    parser.add_argument('--report-only', action='store_true')
    args = parser.parse_args()
    global TP_BASE
    TP_BASE = args.tp
    if args.workers <= 0:
        import multiprocessing
        load = os.getloadavg()[0]
        args.workers = max(2, min(8, int(multiprocessing.cpu_count() / 2 - load)))
    conn = init_db()
    if args.report_only:
        report = generate_report(conn)
        print(report)
        conn.close()
        return
    long_syms, short_syms = get_symbols()
    tasks = []
    if not args.short_only:
        tasks += [(s, True) for s in long_syms]
    if not args.long_only:
        tasks += [(s, False) for s in short_syms]
    print("=" * 70)
    print(f"TIERED ALIGNMENT BACKTEST  stoch={STOCH_K}/{STOCH_SK}/{STOCH_SD}  TP_BASE={TP_BASE}%")
    print(f"Symbols: {len(tasks)} | RV thresholds: {REL_VOL_THRESHOLDS} | Workers: {args.workers}")
    print("=" * 70)
    # Clear old results
    conn.execute("DELETE FROM tiered_results")
    conn.commit()
    t0 = time.time()
    done = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t for t in tasks}
        for future in as_completed(futures):
            sym, is_long = futures[future]
            done += 1
            try:
                _, results = future.result()
                if results:
                    for rv_thresh, metrics in results.items():
                        if metrics:
                            store(conn, sym, is_long, rv_thresh, metrics)
                elapsed = time.time() - t0
                print(f"  [{done:>3}/{len(tasks)}] {sym:<16} ({'L' if is_long else 'S'})  {elapsed:.0f}s", flush=True)
            except Exception as e:
                print(f"  ERROR {sym}: {e}", flush=True)
    elapsed = time.time() - t0
    print(f"\nComplete in {elapsed:.1f}s")
    report = generate_report(conn)
    print(report)
    conn.close()


if __name__ == '__main__':
    main()
