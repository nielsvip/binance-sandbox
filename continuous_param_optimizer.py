#!/usr/bin/env python3
"""
Continuous Parameter Optimizer — runs until killed.
Tests ALL indicator combinations across ALL timeframes on ALL klines.
Workers load data locally — no large array pickling over process boundary.
Uses all available CPUs. Saves ranked results every 5 minutes.
"""
import os, sys, json, time, random, logging, signal, gc
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime
from multiprocessing import Pool, cpu_count

logging.basicConfig(level=logging.INFO, format='%(asctime)s %(levelname)s %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

KLINES_DIR   = Path('/home/niels/binance/klines_cache')
RESULTS_FILE = Path('/home/niels/binance/data/param_optimizer_results.jsonl')
SUMMARY_FILE = Path('/home/niels/binance/data/param_optimizer_top.json')
N_WORKERS      = max(1, cpu_count() - 2)
FORWARD_BARS   = 16      # bars forward to measure return
FEE_PCT        = 0.10    # round-trip fee %
MIN_TRADES     = 40
COMBOS_PER_SYM = 8000    # combos to test per (symbol, tf)
SAVE_INTERVAL  = 300     # seconds between saves
TOP_N          = 1000    # keep top N in summary
TIMEFRAMES     = ['1m', '3m', '15m', '1h', '4h', 'D']
TF_MINUTES     = {'1m': 1, '3m': 3, '15m': 15, '1h': 60, '4h': 240, 'D': 1440}
# HTF pairs: {primary_tf: [(htf, ltf_bars_per_htf_bar), ...]}
HTF_FOR_LTF    = {
    '1m':  [('1h', 60), ('4h', 240)],
    '3m':  [('1h', 20), ('4h', 80)],
    '15m': [('1h', 4),  ('4h', 16)],
    '1h':  [('4h', 4),  ('D', 24)],
    '4h':  [('D', 6)],
}


# ── indicator computation (all vectorized) ───────────────────────────────────

def _ema(s, span):
    return s.ewm(span=span, min_periods=max(1, span // 2), adjust=False).mean()

def ind_stoch(h, l, c, kp=14, sk=3, sd=3):
    ll = l.rolling(kp).min(); hh = h.rolling(kp).max()
    raw = 100.0 * (c - ll) / (hh - ll + 1e-9)
    k = raw.rolling(sk).mean(); d = k.rolling(sd).mean()
    return k.values, d.values

def ind_rsi(c, p=14):
    dlt = c.diff(); g = dlt.clip(lower=0); ls = (-dlt).clip(lower=0)
    ag = _ema(g, p); al = _ema(ls, p)
    return (100 - 100 / (1 + ag / (al + 1e-9))).values

def ind_mfi(h, l, c, v, p=14):
    tp = (h + l + c) / 3; mf = tp * v
    pos = mf.where(tp > tp.shift(1), 0.0); neg = mf.where(tp <= tp.shift(1), 0.0)
    pmf = pos.rolling(p).sum(); nmf = neg.rolling(p).sum()
    return (100 - 100 / (1 + pmf / (nmf + 1e-9))).values

def ind_atr(h, l, c, p=14):
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    return _ema(tr, p).values

def ind_dc(h, l, p=20):
    dch = h.rolling(p).max().values; dcl = l.rolling(p).min().values
    return dch, dcl, (dch + dcl) / 2.0

def ind_wt(h, l, c, n1=10, n2=21):
    hlc3 = (h + l + c) / 3.0
    esa = _ema(hlc3, n1); d = _ema((hlc3 - esa).abs(), n1)
    ci = (hlc3 - esa) / (0.015 * d + 1e-9)
    wt1 = _ema(ci, n2); wt2 = wt1.rolling(4).mean()
    return wt1.values, wt2.values

def ind_bb(c, p=20, m=2.0):
    sma = c.rolling(p).mean(); std = c.rolling(p).std()
    return ((c - (sma - m * std)) / (2 * m * std + 1e-9)).values

def ind_ha(o, h, l, c):
    hac = (o + h + l + c) / 4.0
    hao = pd.Series(0.0, index=c.index)
    hao.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(c)):
        hao.iloc[i] = (hao.iloc[i - 1] + hac.iloc[i - 1]) / 2.0
    return (hac > hao).astype(np.int8).values

def ind_lr(c, p=50):
    cv = c.values if hasattr(c, 'values') else np.asarray(c, float)
    n = len(cv)
    x = np.arange(p, dtype=float) - (p - 1) / 2.0
    sx2 = float((x ** 2).sum())
    wins = np.lib.stride_tricks.sliding_window_view(cv, p)
    slope = (wins @ x) / sx2
    my = wins.mean(axis=1)
    fl = my + slope * x[-1]
    fa = my[:, None] + slope[:, None] * x[None, :]
    sr = (wins - fa).std(axis=1)
    pct = (cv[p - 1:] - (fl - 2 * sr)) / (4 * sr + 1e-9)
    res = np.full(n, 0.5); res[p - 1:] = pct
    return res


def build_htf_mfi_filters(symbol, tf):
    """Load HTF klines and return MFI arrays aligned (via repeat) to LTF bar count."""
    htf_pairs = HTF_FOR_LTF.get(tf, [])
    result = {}
    n_ltf = 1800
    for htf, ratio in htf_pairs:
        path = KLINES_DIR / f'{symbol}_{htf}.json'
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text())
            n_need = n_ltf // max(1, ratio) + 20
            df = pd.DataFrame(raw).tail(n_need).reset_index(drop=True)
            for col in ('open', 'high', 'low', 'close', 'volume'):
                df[col] = df[col].astype(float)
            if len(df) < 30:
                continue
            mfi_vals = ind_mfi(df['high'], df['low'], df['close'], df['volume'])
            aligned = np.repeat(mfi_vals, ratio)
            if len(aligned) >= n_ltf:
                aligned = aligned[-n_ltf:]
            else:
                pad = np.full(n_ltf - len(aligned), 50.0)
                aligned = np.concatenate([pad, aligned])
            result[f'mfi_{htf}'] = aligned
        except Exception:
            pass
    return result


def build_all(df):
    """Build all indicator arrays + forward return. Returns dict of np.arrays."""
    if len(df) < 250:
        return None
    o, h, l, c, v = df['open'], df['high'], df['low'], df['close'], df['volume']
    cv = c.values; n = len(cv)
    F = {}
    # Stochastic
    k, d = ind_stoch(h, l, c)
    F['k'] = k; F['d'] = d
    kp = np.roll(k, 1); kp[0] = k[0]; F['kp'] = kp
    dp = np.roll(d, 1); dp[0] = d[0]
    F['stoch_co'] = ((k > d) & (kp <= dp)).astype(np.int8)
    F['stoch_cu'] = ((k < d) & (kp >= dp)).astype(np.int8)
    # RSI
    rsi = ind_rsi(c); F['rsi'] = rsi
    rsip = np.roll(rsi, 1); rsip[0] = rsi[0]; F['rsip'] = rsip
    # MFI
    F['mfi'] = ind_mfi(h, l, c, v)
    # ATR
    atr = ind_atr(h, l, c)
    atr_m = pd.Series(atr).rolling(50).mean().values
    F['atr_ratio'] = atr / (atr_m + 1e-9)
    # Donchian
    dch, dcl, dcb = ind_dc(h, l)
    F['dc_pct'] = (cv - dcl) / (dch - dcl + 1e-9)
    dcbp = np.roll(dcb, 1); cvp = np.roll(cv, 1)
    F['dc_co'] = ((cv > dcb) & (cvp <= dcbp)).astype(np.int8)
    F['dc_cu'] = ((cv < dcb) & (cvp >= dcbp)).astype(np.int8)
    # WaveTrend
    wt1, wt2 = ind_wt(h, l, c)
    F['wt1'] = wt1; F['wt2'] = wt2
    wt1p = np.roll(wt1, 1); wt1p[0] = wt1[0]
    wt2p = np.roll(wt2, 1); wt2p[0] = wt2[0]
    F['wt_co'] = ((wt1 > wt2) & (wt1p <= wt2p)).astype(np.int8)
    F['wt_cu'] = ((wt1 < wt2) & (wt1p >= wt2p)).astype(np.int8)
    # BB
    F['bb'] = ind_bb(c)
    # LR channel
    F['lr'] = ind_lr(c)
    # EMA20 / SMA200
    ema20 = _ema(c, 20).values
    sma200 = c.rolling(200).mean().values
    sma500 = c.rolling(500).mean().values
    F['p_vs_ema'] = (cv / (ema20 + 1e-9) - 1.0) * 100.0
    F['p_vs_s200'] = (cv / (sma200 + 1e-9) - 1.0) * 100.0
    F['p_vs_s500'] = (cv / (sma500 + 1e-9) - 1.0) * 100.0
    ema3 = np.roll(ema20, 3); ema3[:3] = ema20[:3]
    F['ema_slope'] = (ema20 / (ema3 + 1e-9) - 1.0) * 100.0
    # Heiken-Ashi
    ha = ind_ha(o, h, l, c); F['ha'] = ha
    hap2 = np.roll(ha, 1); hap2[0] = ha[0]; F['hap'] = hap2
    # Relative volume
    vmean = pd.Series(v.values).rolling(20).mean().values
    F['rv'] = v.values / (vmean + 1e-9)
    # t_up
    dcbr2 = np.roll(dcb, 2); dcbr2[:2] = dcb[:2]
    F['t_up'] = ((cv > dcb) & (cvp > np.roll(dcb, 1)) & (np.roll(cv, 2) > dcbr2)).astype(np.int8)
    # Forward return
    fwd = np.roll(cv, -FORWARD_BARS) / (cv + 1e-9) - 1.0
    fwd[-FORWARD_BARS:] = np.nan
    F['fwd'] = fwd * 100.0
    return F


def make_signals(F):
    """Build all named atomic boolean signals as np.bool_ arrays."""
    k = F['k']; d = F['d']; kp = F['kp']
    rsi = F['rsi']; rsip = F['rsip']; mfi = F['mfi']
    wt1 = F['wt1']; wt2 = F['wt2']
    bb = F['bb']; lr = F['lr']; dc = F['dc_pct']
    atr = F['atr_ratio']; rv = F['rv']
    ha = F['ha']; hap = F['hap']
    ema = F['p_vs_ema']; s200 = F['p_vs_s200']; s500 = F['p_vs_s500']
    ems = F['ema_slope']

    L, S = {}, {}

    # ── Stochastic
    for t in [15, 20, 25, 30, 40]:
        L[f'k<{t}']  = k < t
    for t in [60, 70, 75, 80, 85]:
        S[f'k>{t}']  = k > t
    L['k>d']      = k > d;        S['k<d']      = k < d
    L['stoch_co'] = F['stoch_co'].astype(bool)
    S['stoch_cu'] = F['stoch_cu'].astype(bool)
    L['k<50+k>d'] = (k < 50) & (k > d)
    S['k>50+k<d'] = (k > 50) & (k < d)
    L['k<=32+k>d']= (k <= 32) & (k > d)
    S['k>=68+k<d']= (k >= 68) & (k < d)
    L['k_rising'] = (k > kp) & (k < 50)
    S['k_falling']= (k < kp) & (k > 50)
    for t in [20, 30, 40]:
        L[f'k_rise_from<{t}'] = (k > kp) & (k < t + 15)
    for t in [60, 70, 80]:
        S[f'k_fall_from>{t}'] = (k < kp) & (k > t - 15)

    # ── RSI
    for t in [25, 30, 35, 40, 45, 50]:
        L[f'rsi<{t}'] = rsi < t
    for t in [50, 55, 60, 65, 70, 75]:
        S[f'rsi>{t}'] = rsi > t
    L['rsi_rising'] = (rsi > rsip) & (rsi < 50)
    S['rsi_falling']= (rsi < rsip) & (rsi > 50)
    L['rsi<40+k>d'] = (rsi < 40) & (k > d)
    S['rsi>60+k<d'] = (rsi > 60) & (k < d)
    L['rsi<30+co']  = (rsi < 30) & F['stoch_co'].astype(bool)
    S['rsi>70+cu']  = (rsi > 70) & F['stoch_cu'].astype(bool)

    # ── MFI (same TF)
    for t in [20, 30, 40, 50]:
        L[f'mfi<{t}'] = mfi < t
    for t in [50, 60, 70, 80]:
        S[f'mfi>{t}'] = mfi > t
    L['mfi<40+k>d'] = (mfi < 40) & (k > d)
    S['mfi>60+k<d'] = (mfi > 60) & (k < d)

    # ── Cross-TF MFI (HTF approval filter applied to LTF entries)
    for htf_key in ('mfi_1h', 'mfi_4h', 'mfi_D'):
        htf_mfi = F.get(htf_key)
        if htf_mfi is None:
            continue
        tag = htf_key
        for t in [30, 40, 50]:
            L[f'{tag}<{t}'] = htf_mfi < t
        for t in [50, 60, 70]:
            S[f'{tag}>{t}'] = htf_mfi > t
        L[f'{tag}<40+k>d']   = (htf_mfi < 40) & (k > d)
        S[f'{tag}>60+k<d']   = (htf_mfi > 60) & (k < d)
        L[f'{tag}<40+stco']  = (htf_mfi < 40) & F['stoch_co'].astype(bool)
        S[f'{tag}>60+stcu']  = (htf_mfi > 60) & F['stoch_cu'].astype(bool)
        L[f'{tag}<40+rsi<40']= (htf_mfi < 40) & (rsi < 40)
        S[f'{tag}>60+rsi>60']= (htf_mfi > 60) & (rsi > 60)

    # ── WaveTrend
    for t in [-80, -60, -40, -20, 0]:
        L[f'wt1<{t}'] = wt1 < t
    for t in [0, 20, 40, 60, 80]:
        S[f'wt1>{t}'] = wt1 > t
    L['wt1<wt2']  = wt1 < wt2;   S['wt1>wt2']  = wt1 > wt2
    L['wt_co']    = F['wt_co'].astype(bool)
    S['wt_cu']    = F['wt_cu'].astype(bool)
    L['wt_co+<0'] = (wt1 < 0) & F['wt_co'].astype(bool)
    S['wt_cu+>0'] = (wt1 > 0) & F['wt_cu'].astype(bool)
    L['wt1<wt2+k>d']= (wt1 < wt2) & (k > d)
    S['wt1>wt2+k<d']= (wt1 > wt2) & (k < d)

    # ── Bollinger Bands
    for t in [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3]:
        L[f'bb<{t:.1f}'] = bb < t
    for t in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2]:
        S[f'bb>{t:.1f}'] = bb > t
    L['bb<0+k>d']   = (bb < 0)   & (k > d)
    S['bb>1+k<d']   = (bb > 1.0) & (k < d)

    # ── Linear Regression channel
    for t in [-0.2, -0.1, 0.0, 0.1, 0.2, 0.3]:
        L[f'lr<{t:.1f}'] = lr < t
    for t in [0.7, 0.8, 0.9, 1.0, 1.1, 1.2]:
        S[f'lr>{t:.1f}'] = lr > t
    L['lr<0+k>d']   = (lr < 0)   & (k > d)
    S['lr>1+k<d']   = (lr > 1.0) & (k < d)

    # ── Donchian channel position
    for t in [0.10, 0.15, 0.20, 0.25, 0.30]:
        L[f'dc<{t}'] = dc < t
    for t in [0.70, 0.75, 0.80, 0.85, 0.90]:
        S[f'dc>{t}'] = dc > t
    L['dc_co']   = F['dc_co'].astype(bool)
    S['dc_cu']   = F['dc_cu'].astype(bool)
    L['dc<0.2+co']  = (dc < 0.2) & F['dc_co'].astype(bool)
    S['dc>0.8+cu']  = (dc > 0.8) & F['dc_cu'].astype(bool)

    # ── EMA / SMA structure
    L['above_ema20']  = ema > 0;  S['below_ema20']  = ema < 0
    L['below_ema20']  = ema < 0;  S['above_ema20']  = ema > 0
    L['above_s200']   = s200 > 0; S['below_s200']   = s200 < 0
    L['above_s500']   = s500 > 0; S['below_s500']   = s500 < 0
    for pct in [-2, -1, -0.5]:
        L[f'ema_near{pct}']= (ema >= pct) & (ema < 1.0)
    for pct in [0.5, 1, 2]:
        S[f'ema_near+{pct}']= (ema <= pct) & (ema > -1.0)
    L['ema_rising']   = ems > 0;  S['ema_falling']  = ems < 0
    L['ema_rising+k>d'] = (ems > 0) & (k > d)
    S['ema_falling+k<d']= (ems < 0) & (k < d)

    # ── Heiken-Ashi
    L['ha_green']     = ha.astype(bool)
    S['ha_red']       = (ha == 0)
    L['ha_2green']    = ha.astype(bool) & hap.astype(bool)
    S['ha_2red']      = (ha == 0) & (hap == 0)
    L['ha_flip_g']    = ha.astype(bool) & (~hap.astype(bool))
    S['ha_flip_r']    = (ha == 0) & (hap == 1)
    L['ha_green+k>d'] = ha.astype(bool) & (k > d)
    S['ha_red+k<d']   = (ha == 0) & (k < d)

    # ── ATR volatility regime
    L['atr_hi']  = atr > 1.5; S['atr_hi']  = atr > 1.5
    L['atr_lo']  = atr < 0.7; S['atr_lo']  = atr < 0.7
    L['atr_exp'] = atr > 1.2; S['atr_exp'] = atr > 1.2
    L['atr_hi+k>d'] = (atr > 1.5) & (k > d)
    S['atr_hi+k<d'] = (atr > 1.5) & (k < d)

    # ── Relative volume
    for t in [1.2, 1.5, 2.0, 2.5, 3.0]:
        L[f'rv>{t}'] = rv > t
        S[f'rv>{t}'] = rv > t
    L['rv>1.5+k>d'] = (rv > 1.5) & (k > d)
    S['rv>1.5+k<d'] = (rv > 1.5) & (k < d)

    # ── t_up (trend)
    L['t_up']      = F['t_up'].astype(bool)
    S['t_dn']      = (~F['t_up'].astype(bool))
    L['t_up+k>d']  = F['t_up'].astype(bool) & (k > d)
    S['t_dn+k<d']  = (~F['t_up'].astype(bool)) & (k < d)

    return L, S


def score_combo(fwd, mask, direction):
    valid = mask & ~np.isnan(fwd)
    n = int(valid.sum())
    if n < MIN_TRADES:
        return None
    ret = fwd[valid]
    if direction == 'short':
        ret = -ret
    mu = float(ret.mean()); sigma = float(ret.std())
    if sigma < 1e-9 or mu <= 0:
        return None
    wr = float((ret > FEE_PCT).mean())
    sharpe = mu / sigma * np.sqrt(n)
    score = mu * wr * np.log1p(n) * (1.0 + max(0.0, sharpe))
    if mu <= FEE_PCT:
        score *= 0.01
    return {'mean_pct': round(mu, 4), 'win_rate': round(wr, 4), 'n': n,
            'std': round(sigma, 4), 'sharpe': round(sharpe, 4), 'score': round(score, 6)}


# ── worker: self-contained, loads own data ───────────────────────────────────

def worker(task):
    """task = (symbol, tf, seed, n_combos). Loads own data, tests combos."""
    symbol, tf, seed, n_combos = task
    random.seed(seed); np.random.seed(seed % (2**32))
    path = KLINES_DIR / f'{symbol}_{tf}.json'
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text())
        if len(raw) < 250:
            return []
        df = pd.DataFrame(raw).tail(1800).reset_index(drop=True)
        for col in ('open', 'high', 'low', 'close', 'volume'):
            df[col] = df[col].astype(float)
    except Exception:
        return []
    F = build_all(df)
    if F is None:
        return []
    F.update(build_htf_mfi_filters(symbol, tf))
    fwd = F['fwd']
    L, S = make_signals(F)
    L_names = list(L.keys()); S_names = list(S.keys())
    results = []
    combo_sizes = [2, 3, 4]
    size_weights = [0.25, 0.55, 0.20]
    for _ in range(n_combos):
        direction = random.choice(('long', 'short'))
        pool_n = L_names if direction == 'long' else S_names
        pool_s = L if direction == 'long' else S
        sz = random.choices(combo_sizes, weights=size_weights)[0]
        chosen = random.sample(pool_n, sz)
        mask = pool_s[chosen[0]]
        for nm in chosen[1:]:
            mask = mask & pool_s[nm]
        stats = score_combo(fwd, mask, direction)
        if stats and stats['score'] > 0:
            results.append({'direction': direction, 'signals': sorted(chosen),
                            'symbol': symbol, 'tf': tf, **stats})
    del df, F, L, S, fwd
    return results


# ── result management ─────────────────────────────────────────────────────────

def load_results():
    if not RESULTS_FILE.exists():
        return {}, []
    best = {}; ordered = []
    try:
        for line in RESULTS_FILE.read_text().splitlines():
            if not line.strip():
                continue
            r = json.loads(line)
            key = (r['direction'], tuple(sorted(r['signals'])))
            if key not in best or r['score'] > best[key]['score']:
                best[key] = r
    except Exception:
        pass
    ordered = list(best.values())
    return best, ordered


def save_results(best, new_results):
    if new_results:
        with RESULTS_FILE.open('a') as f:
            for r in new_results:
                f.write(json.dumps(r) + '\n')
    top = sorted(best.values(), key=lambda x: x['score'], reverse=True)[:TOP_N]
    SUMMARY_FILE.write_text(json.dumps({
        'generated_at': datetime.utcnow().isoformat(),
        'total_unique': len(best),
        'top': top,
    }, indent=2))


# ── main ─────────────────────────────────────────────────────────────────────

def main():
    logger.info(f"Optimizer start | workers={N_WORKERS} | pid={os.getpid()}")
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    best, _ = load_results()
    logger.info(f"Loaded {len(best)} existing results")

    all_syms = [f.name.replace('_15m.json', '') for f in KLINES_DIR.glob('*_15m.json')]
    logger.info(f"Found {len(all_syms)} symbols")

    total_tasks = 0; new_results = []; last_save = time.time()
    iteration = 0

    with Pool(processes=N_WORKERS, maxtasksperchild=50) as pool:
        while True:
            iteration += 1
            syms = random.sample(all_syms, min(80, len(all_syms)))
            tfs  = random.choices(TIMEFRAMES, k=min(4, len(TIMEFRAMES)))
            tasks = [(s, tf, random.randint(0, 2**31), COMBOS_PER_SYM)
                     for s in syms for tf in tfs]
            random.shuffle(tasks)
            logger.info(f"[iter={iteration}] {len(tasks)} tasks | unique_combos={len(best)}")

            for batch_results in pool.imap_unordered(worker, tasks, chunksize=4):
                total_tasks += 1
                for r in batch_results:
                    key = (r['direction'], tuple(sorted(r['signals'])))
                    if key not in best or r['score'] > best[key]['score']:
                        best[key] = r
                        new_results.append(r)

                if time.time() - last_save > SAVE_INTERVAL:
                    logger.info(f"  Saving | tasks={total_tasks} unique={len(best)} new={len(new_results)}")
                    save_results(best, new_results)
                    new_results = []; last_save = time.time()
                    top5 = sorted(best.values(), key=lambda x: x['score'], reverse=True)[:5]
                    for i, r in enumerate(top5, 1):
                        logger.info(f"  #{i} {r['direction'].upper():5s} {r['signals']} score={r['score']:.4f} mean={r['mean_pct']:.3f}% wr={r['win_rate']:.2f} n={r['n']} tf={r['tf']}")

            logger.info(f"[iter={iteration}] done | total_tasks={total_tasks} unique={len(best)}")
            if new_results:
                save_results(best, new_results)
                new_results = []; last_save = time.time()


if __name__ == '__main__':
    def _stop(sig, frame):
        logger.info("Stopping...")
        sys.exit(0)
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    main()
