#!/usr/bin/env python3
"""
PROGRESSIVE TIMEFRAME COMPARISON BACKTEST
==========================================
Tests 4 progressive TF combos to quantify the value each TF adds:
  Level 0: D only              → enter on D stoch cross, D aligned
  Level 1: D + 4h              → enter on 4h stoch cross, D+4h aligned
  Level 2: D + 4h + 1h        → enter on 1h stoch cross, D+4h+1h aligned
  Level 3: D + 4h + 1h + 15m  → enter on 15m stoch cross, all TFs aligned

Output: per-symbol and aggregate table showing Sharpe/WR/PF/MaxDD
        delta at each level vs prior level.

Usage:
  python3 ez_backtest_tf_compare.py [--workers N] [--tp 1.0] [--min-bars 30]
"""
import sys, os, json, math, time, sqlite3, argparse
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import numpy as np

BASE_PATH = Path(os.environ.get('BASE_PATH', '/home/niels/binance-sandbox'))
KLINES_DIR = BASE_PATH / 'klines_cache'
RESULTS_DIR = BASE_PATH / 'backtest_framework' / 'results'
DB_PATH = RESULTS_DIR / 'tf_compare.db'
REPORT_PATH = RESULTS_DIR / 'tf_compare_report.md'
XLS_PATH = RESULTS_DIR / 'tf_compare.xlsx'

FEE = 0.0004       # 0.04% per side (taker)
WARMUP = 300       # bars to skip before entering (warm up indicators)
MIN_TRADES = 8     # minimum trades for a result to be included in aggregates
STOCH_K = 14
STOCH_SK = 3
STOCH_SD = 3

# TF combos: (primary_tf, [htf_alignment_gates...])
TF_COMBOS = [
    ('D',    []),
    ('4h',   ['D']),
    ('1h',   ['D', '4h']),
    ('15m',  ['D', '4h', '1h']),
]
COMBO_LABELS = ['D', 'D+4h', 'D+4h+1h', 'D+4h+1h+15m']

# Minimum bars of primary TF data required per combo
MIN_BARS = {'D': 100, '4h': 300, '1h': 500, '15m': 1000}

# Max bars per trade (timeout) per primary TF
MAX_TRADE_BARS = {'D': 30, '4h': 60, '1h': 120, '15m': 250}


# ═══════════════════════════════════════════════════════════════════════
# INDICATOR ENGINE (consolidated, single source)
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
            arr = np.array([[d['open'], d['high'], d['low'], d['close']] for d in data], dtype=np.float64)
        else:
            raw = np.array(data, dtype=np.float64)
            arr = raw[:, 1:5] if raw.ndim == 2 and raw.shape[1] >= 5 else raw[:, :4]
        return arr  # (n, 4): o, h, l, c
    except Exception:
        return None


def stoch_kd(h, l, c, k_period=14, k_smooth=3, d_smooth=3):
    n = len(c)
    raw_k = np.full(n, 50.0)
    for i in range(k_period - 1, n):
        hh = h[i - k_period + 1:i + 1].max()
        ll = l[i - k_period + 1:i + 1].min()
        raw_k[i] = (c[i] - ll) / (hh - ll) * 100.0 if hh > ll else 50.0
    k = np.convolve(raw_k, np.ones(k_smooth) / k_smooth, mode='same')
    d = np.convolve(k, np.ones(d_smooth) / d_smooth, mode='same')
    return k.astype(np.float32), d.astype(np.float32)


def heikin_ashi_color(o, h, l, c):
    hac = (o + h + l + c) / 4
    hao = np.empty(len(o))
    hao[0] = (o[0] + c[0]) / 2
    for i in range(1, len(o)):
        hao[i] = (hao[i - 1] + hac[i - 1]) / 2
    return (hac > hao).astype(np.int8)  # 1=bull, 0=bear


def make_idx_map(n_primary, n_tf):
    return np.clip(
        (np.arange(n_primary) * n_tf / n_primary).astype(np.int32),
        0, n_tf - 1
    )


def precompute(sym, primary_tf, htf_list):
    """
    Build indicator arrays for primary TF + all HTF alignment gates.
    All arrays are aligned to primary TF length via make_idx_map.
    Returns dict or None.
    """
    primary = load_tf(sym, primary_tf)
    min_b = MIN_BARS.get(primary_tf, 300)
    if primary is None or len(primary) < min_b + WARMUP:
        return None
    n = len(primary)
    o, h, l, c = primary[:, 0], primary[:, 1], primary[:, 2], primary[:, 3]
    k, d = stoch_kd(h, l, c, STOCH_K, STOCH_SK, STOCH_SD)
    ha = heikin_ashi_color(o, h, l, c)
    k_prev = np.roll(k, 1)
    d_prev = np.roll(d, 1)
    # Stoch crossover signals (only valid after bar 1)
    co = (k > d) & (k_prev <= d_prev)   # crossover up
    cu = (k < d) & (k_prev >= d_prev)   # crossover down
    co[0] = False; cu[0] = False
    ind = {
        'n': n, 'c': c, 'h': h, 'l': l,
        'k': k, 'd': d, 'ha': ha, 'co': co, 'cu': cu,
        'htf': {},
        'htf_missing': set(),
    }
    for htf in htf_list:
        data = load_tf(sym, htf)
        if data is None or len(data) < 50:
            ind['htf_missing'].add(htf)
            continue
        nt = len(data)
        idx = make_idx_map(n, nt)
        o2, h2, l2, c2 = data[:, 0], data[:, 1], data[:, 2], data[:, 3]
        k2, d2 = stoch_kd(h2, l2, c2, STOCH_K, STOCH_SK, STOCH_SD)
        ha2 = heikin_ashi_color(o2, h2, l2, c2)
        ind['htf'][htf] = {
            'k': k2[idx].astype(np.float32),
            'd': d2[idx].astype(np.float32),
            'ha': ha2[idx],
        }
    return ind


def simulate(ind, htf_list, is_long, tp_pct, primary_tf):
    """
    Walk primary TF bars.
    Entry: stoch crossover on primary + K>D aligned on ALL htf gates + HA aligned.
    Exit: TP gain OR max bar timeout.
    """
    if ind['htf_missing']:
        return []   # skip if any required HTF missing
    n = ind['n']
    c = ind['c']
    co = ind['co']
    cu = ind['cu']
    k = ind['k']
    d = ind['d']
    ha = ind['ha']
    max_bars = MAX_TRADE_BARS.get(primary_tf, 250)
    trades = []
    in_trade = False
    ep = 0.0
    eb = 0
    for bar in range(WARMUP, n - 1):
        price = c[bar]
        if price <= 0:
            continue
        if in_trade:
            gain = (price - ep) / ep * 100.0 if is_long else (ep - price) / ep * 100.0
            if gain >= tp_pct or (bar - eb) >= max_bars:
                ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
                trades.append(ret * 100.0)
                in_trade = False
        else:
            # Entry trigger: stoch cross on primary TF
            if is_long:
                if not co[bar]:
                    continue
            else:
                if not cu[bar]:
                    continue
            # Primary TF must itself be K>D (long) or K<D (short)
            k_val = float(k[bar])
            d_val = float(d[bar])
            if is_long and k_val <= d_val:
                continue
            if not is_long and k_val >= d_val:
                continue
            # HA alignment on primary TF
            ha_val = int(ha[bar])
            if is_long and ha_val != 1:
                continue
            if not is_long and ha_val != 0:
                continue
            # HTF alignment gates
            aligned = True
            for htf in htf_list:
                htf_data = ind['htf'].get(htf)
                if htf_data is None:
                    aligned = False
                    break
                hk = float(htf_data['k'][bar])
                hd = float(htf_data['d'][bar])
                hha = int(htf_data['ha'][bar])
                if is_long and (hk <= hd or hha != 1):
                    aligned = False
                    break
                if not is_long and (hk >= hd or hha != 0):
                    aligned = False
                    break
            if not aligned:
                continue
            in_trade = True
            ep = price
            eb = bar
    if in_trade:
        price = c[n - 1]
        ret = ((price - ep) / ep if is_long else (ep - price) / ep) - 2 * FEE
        trades.append(ret * 100.0)
    return trades


# Bars per year per TF (crypto runs 24/7/365)
ANNUAL_BARS = {'D': 365, '4h': 2190, '1h': 8760, '15m': 35040}


def compute_metrics(trades, primary_tf):
    """Compute metrics with annualized Sharpe normalized to same scale across TFs."""
    if len(trades) < 1:
        return {'sharpe': 0.0, 'win_rate': 0.0, 'pf': 0.0, 'max_dd': 0.0,
                'total_return': 0.0, 'avg_return': 0.0, 'n_trades': 0}
    rets = np.array(trades, dtype=np.float64)
    wins = rets[rets > 0]
    losses = rets[rets <= 0]
    pf = (wins.sum() / max(abs(losses.sum()), 1e-9)) if (len(wins) > 0 and len(losses) > 0) else (99.0 if len(losses) == 0 else 0.0)
    mean_r = rets.mean()
    std_r = rets.std()
    # Annualized Sharpe: normalize by bars-per-year for the primary TF
    # This makes Sharpe comparable across D/4h/1h/15m strategies
    annual_factor = ANNUAL_BARS.get(primary_tf, 365)
    sharpe = mean_r / std_r * math.sqrt(annual_factor) if std_r > 1e-9 else 0.0
    cum = np.cumsum(rets)
    peak = np.maximum.accumulate(cum)
    max_dd = float((peak - cum).max()) if len(cum) > 0 else 0.0
    return {
        'sharpe': round(float(sharpe), 4),
        'win_rate': round(float((rets > 0).mean()), 4),
        'pf': round(float(pf), 4),
        'max_dd': round(float(max_dd), 4),
        'total_return': round(float(rets.sum()), 4),
        'avg_return': round(float(mean_r), 4),
        'n_trades': int(len(trades)),
    }


# ═══════════════════════════════════════════════════════════════════════
# WORKER (runs in subprocess)
# ═══════════════════════════════════════════════════════════════════════

def worker(args):
    sym, is_long, tp_pct = args
    results = []
    for level, (primary_tf, htf_list) in enumerate(TF_COMBOS):
        ind = precompute(sym, primary_tf, htf_list)
        if ind is None:
            continue
        trades = simulate(ind, htf_list, is_long, tp_pct, primary_tf)
        metrics = compute_metrics(trades, primary_tf)
        results.append({
            'symbol': sym,
            'side': 'LONG' if is_long else 'SHORT',
            'level': level,
            'combo': COMBO_LABELS[level],
            'primary_tf': primary_tf,
            'tp_pct': tp_pct,
            **metrics,
        })
    return sym, results


# ═══════════════════════════════════════════════════════════════════════
# SYMBOLS
# ═══════════════════════════════════════════════════════════════════════

def get_symbols():
    long_syms, short_syms = [], []
    for fname in ['symbols_ang_long.json', 'symbols_inf_long.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f:
                long_syms = json.load(f)
            break
    for fname in ['symbols_ang_short.json', 'symbols_inf_short.json']:
        fp = BASE_PATH / fname
        if fp.exists():
            with open(fp) as f:
                short_syms = json.load(f)
            break
    if not long_syms or not short_syms:
        sf = BASE_PATH / 'symbols.json'
        if sf.exists():
            with open(sf) as f:
                data = json.load(f)
            syms = data if isinstance(data, list) else list(data.keys())
            # Use symbols that have 15m data
            syms = [s for s in syms if (KLINES_DIR / f'{s}_15m.json').exists()][:100]
            long_syms = long_syms or syms[:50]
            short_syms = short_syms or syms[:50]
    return long_syms, short_syms


# ═══════════════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════════════

def init_db():
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("""CREATE TABLE IF NOT EXISTS tf_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT, side TEXT, level INTEGER, combo TEXT, primary_tf TEXT,
        tp_pct REAL, sharpe REAL, win_rate REAL, pf REAL, max_dd REAL,
        total_return REAL, avg_return REAL, n_trades INTEGER,
        timestamp TEXT
    )""")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_combo ON tf_results(combo)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sym ON tf_results(symbol)")
    conn.commit()
    return conn


def store_results(conn, results_list):
    now = datetime.now(timezone.utc).isoformat()
    rows = [(r['symbol'], r['side'], r['level'], r['combo'], r['primary_tf'],
             r['tp_pct'], r['sharpe'], r['win_rate'], r['pf'], r['max_dd'],
             r['total_return'], r['avg_return'], r['n_trades'], now)
            for r in results_list]
    conn.executemany("""INSERT INTO tf_results
        (symbol, side, level, combo, primary_tf, tp_pct, sharpe, win_rate, pf,
         max_dd, total_return, avg_return, n_trades, timestamp)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", rows)
    conn.commit()


# ═══════════════════════════════════════════════════════════════════════
# REPORTING
# ═══════════════════════════════════════════════════════════════════════

def generate_report(conn, tp_pct):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    cur = conn.cursor()
    lines = [
        f"# PROGRESSIVE TF BACKTEST REPORT",
        f"Generated: {now}",
        f"TP: {tp_pct}% | Fee: {FEE*100:.3f}% per side | Min trades for aggregate: {MIN_TRADES}",
        "",
    ]

    # ── Aggregate comparison table ──
    lines.append("## Aggregate TF Level Comparison (symbols with ≥{} trades, both sides)".format(MIN_TRADES))
    lines.append("")
    lines.append(f"| {'TF Combo':<16} | {'Symbols':>7} | {'Sharpe':>8} | {'Win%':>6} | {'PF':>6} | {'MaxDD%':>7} | {'TotRet%':>8} | {'Trades':>7} | {'ΔSharpe':>8} |")
    lines.append(f"|{'-'*18}|{'-'*9}|{'-'*10}|{'-'*8}|{'-'*8}|{'-'*9}|{'-'*10}|{'-'*9}|{'-'*10}|")
    prev_sharpe = None
    combo_stats = {}
    for label in COMBO_LABELS:
        rows = cur.execute("""
            SELECT AVG(sharpe), AVG(win_rate), AVG(pf), AVG(max_dd), AVG(total_return), AVG(n_trades), COUNT(DISTINCT symbol)
            FROM tf_results WHERE combo=? AND n_trades>=? AND tp_pct=?
        """, (label, MIN_TRADES, tp_pct)).fetchone()
        if rows and rows[0] is not None:
            avg_sharpe, avg_wr, avg_pf, avg_dd, avg_ret, avg_trades, n_syms = rows
            delta = (avg_sharpe - prev_sharpe) if prev_sharpe is not None else 0.0
            delta_str = f"{delta:+.4f}" if prev_sharpe is not None else "baseline"
            lines.append(f"| {label:<16} | {n_syms:>7} | {avg_sharpe:>8.4f} | {avg_wr*100:>5.1f}% | {avg_pf:>6.2f} | {avg_dd:>6.2f}% | {avg_ret:>7.2f}% | {avg_trades:>7.0f} | {delta_str:>8} |")
            prev_sharpe = avg_sharpe
            combo_stats[label] = (avg_sharpe, avg_wr, avg_pf, avg_dd, avg_ret)
        else:
            lines.append(f"| {label:<16} | {'N/A':>7} | {'N/A':>8} | {'N/A':>6} | {'N/A':>6} | {'N/A':>7} | {'N/A':>8} | {'N/A':>7} | {'N/A':>8} |")
    lines.append("")

    # ── Per-symbol comparison ──
    lines.append("## Per-Symbol TF Progression (top 30 by D+4h+1h+15m Sharpe, LONG side)")
    lines.append("")
    cols = "| {:<14} | {:>8} | {:>8} | {:>8} | {:>10} |".format(
        "Symbol", "D", "D+4h", "D+4h+1h", "D+4h+1h+15m")
    sep = "|" + "-" * 16 + "|" + ("-" * 10 + "|") * 4
    lines.append(cols)
    lines.append(sep)
    # Get symbols with all 4 levels
    syms_with_all = cur.execute("""
        SELECT symbol FROM tf_results WHERE side='LONG' AND n_trades>=? AND tp_pct=?
        GROUP BY symbol HAVING COUNT(DISTINCT combo)=4
        ORDER BY MAX(CASE WHEN combo='D+4h+1h+15m' THEN sharpe ELSE -99 END) DESC
        LIMIT 30
    """, (MIN_TRADES, tp_pct)).fetchall()
    for (sym,) in syms_with_all:
        row_vals = []
        for label in COMBO_LABELS:
            r = cur.execute("SELECT sharpe FROM tf_results WHERE symbol=? AND combo=? AND side='LONG' AND tp_pct=?",
                            (sym, label, tp_pct)).fetchone()
            row_vals.append(f"{r[0]:.3f}" if r else "N/A")
        lines.append(f"| {sym:<14} | {row_vals[0]:>8} | {row_vals[1]:>8} | {row_vals[2]:>8} | {row_vals[3]:>10} |")
    lines.append("")

    # ── Top 20 best combos overall ──
    lines.append("## Top 20 Individual Results (D+4h+1h+15m, Sharpe ranked)")
    lines.append("")
    lines.append("| Symbol | Side | Sharpe | WR% | PF | MaxDD% | Trades |")
    lines.append("|--------|------|--------|-----|----|--------|--------|")
    top = cur.execute("""SELECT symbol, side, sharpe, win_rate, pf, max_dd, n_trades
        FROM tf_results WHERE combo='D+4h+1h+15m' AND n_trades>=? AND tp_pct=?
        ORDER BY sharpe DESC LIMIT 20""", (MIN_TRADES, tp_pct)).fetchall()
    for row in top:
        lines.append(f"| {row[0]} | {row[1]} | {row[2]:.4f} | {row[3]*100:.1f}% | {row[4]:.2f} | {row[5]:.2f}% | {row[6]} |")
    lines.append("")

    report_text = "\n".join(lines)
    with open(REPORT_PATH, 'w') as f:
        f.write(report_text)
    print(f"Report written to {REPORT_PATH}")

    # XLS
    try:
        from openpyxl import Workbook
        from openpyxl.styles import PatternFill, Font
        wb = Workbook()
        ws = wb.active
        ws.title = "TF_COMPARISON"
        headers = ["Symbol", "Side", "Level", "Combo", "PrimaryTF", "TP%",
                   "Sharpe", "WinRate", "PF", "MaxDD%", "TotalRet%", "AvgRet%", "Trades"]
        ws.append(headers)
        # Header style
        header_fill = PatternFill("solid", fgColor="366092")
        header_font = Font(color="FFFFFF", bold=True)
        for cell in ws[1]:
            cell.fill = header_fill
            cell.font = header_font
        # All results
        all_rows = cur.execute("""SELECT symbol, side, level, combo, primary_tf, tp_pct,
            sharpe, win_rate, pf, max_dd, total_return, avg_return, n_trades
            FROM tf_results WHERE tp_pct=? ORDER BY symbol, side, level""", (tp_pct,)).fetchall()
        for row in all_rows:
            ws.append(list(row))
        # Aggregate sheet
        ws2 = wb.create_sheet("AGGREGATE")
        ws2.append(["TF_Combo", "N_Symbols", "Avg_Sharpe", "Avg_WinRate", "Avg_PF", "Avg_MaxDD", "Avg_Return", "Delta_Sharpe"])
        prev_s = None
        for label in COMBO_LABELS:
            r = cur.execute("""SELECT COUNT(DISTINCT symbol), AVG(sharpe), AVG(win_rate), AVG(pf), AVG(max_dd), AVG(total_return)
                FROM tf_results WHERE combo=? AND n_trades>=? AND tp_pct=?""", (label, MIN_TRADES, tp_pct)).fetchone()
            if r and r[0]:
                delta = (r[1] - prev_s) if prev_s is not None else None
                ws2.append([label, r[0], r[1], r[2], r[3], r[4], r[5], delta])
                prev_s = r[1]
        wb.save(str(XLS_PATH))
        print(f"XLS written to {XLS_PATH}")
    except ImportError:
        print("openpyxl not installed — skipping XLS")
    return report_text


# ═══════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--workers', type=int, default=0)
    parser.add_argument('--tp', type=float, default=1.0, help='Take profit %')
    parser.add_argument('--min-bars', type=int, default=0, help='Override min bars (0=use defaults)')
    parser.add_argument('--long-only', action='store_true')
    parser.add_argument('--short-only', action='store_true')
    parser.add_argument('--report-only', action='store_true', help='Skip sim, just regenerate report')
    args = parser.parse_args()

    if args.workers <= 0:
        import multiprocessing
        if Path('/home/niels/binance_scripts_paused').exists():
            args.workers = max(multiprocessing.cpu_count() - 1, 4)
        else:
            load = os.getloadavg()[0]
            args.workers = max(2, min(6, int(multiprocessing.cpu_count() / 2 - load)))
    if args.min_bars > 0:
        for tf in MIN_BARS:
            MIN_BARS[tf] = args.min_bars

    print("=" * 70)
    print(f"PROGRESSIVE TF COMPARISON BACKTEST")
    print(f"TP={args.tp}%  Workers={args.workers}  DB={DB_PATH.name}")
    print("=" * 70)
    for i, (primary_tf, htf_list) in enumerate(TF_COMBOS):
        print(f"  Level {i}: {COMBO_LABELS[i]:<16}  entry on {primary_tf}, gates: {htf_list or 'none'}")
    print()

    conn = init_db()

    if args.report_only:
        report = generate_report(conn, args.tp)
        print(report)
        conn.close()
        return

    long_syms, short_syms = get_symbols()
    tasks = []
    if not args.short_only:
        tasks += [(s, True, args.tp) for s in long_syms]
    if not args.long_only:
        tasks += [(s, False, args.tp) for s in short_syms]
    print(f"Symbols: {len(long_syms)} long + {len(short_syms)} short = {len(tasks)} tasks")
    print()

    # Clear existing results for this tp_pct
    conn.execute("DELETE FROM tf_results WHERE tp_pct=?", (args.tp,))
    conn.commit()

    t0 = time.time()
    done = 0
    total_results = 0
    with ProcessPoolExecutor(max_workers=args.workers) as pool:
        futures = {pool.submit(worker, t): t[0] for t in tasks}
        for future in as_completed(futures):
            sym = futures[future]
            done += 1
            try:
                _, results = future.result()
                if results:
                    store_results(conn, results)
                    total_results += len(results)
                elapsed = time.time() - t0
                print(f"  [{done:>3}/{len(tasks)}] {sym:<16} {len(results)} results  ({elapsed:.0f}s)", flush=True)
            except Exception as e:
                print(f"  ERROR {sym}: {e}", flush=True)

    elapsed = time.time() - t0
    print(f"\nComplete: {total_results} results in {elapsed:.1f}s")
    print()
    report = generate_report(conn, args.tp)
    print(report)
    conn.close()


if __name__ == '__main__':
    main()
