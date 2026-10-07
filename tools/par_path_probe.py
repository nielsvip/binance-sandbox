#!/usr/bin/env python3
"""par_path_probe — vector (v12_quick, include_ledger) vs scalar live-faithful (backtest_v12_engine.run_one) per-path trade counts at DEFAULT overrides on the same frozen 30D NPZ slice.
Output JSON per sym_side in OUT dir: counts by (action, reason family) for vec and scalar, totals, gain. Run on a worker (S1/S2/S5) with .venv/bin/python, niced.
  python tools/par_path_probe.py OUTDIR SYM_SIDE [SYM_SIDE...]
"""
import os, sys, json, re, glob, time, collections
ROOT = os.path.expanduser('~/binance-sandbox')
os.chdir(ROOT); sys.path.insert(0, '.'); sys.path.insert(0, 'tools')
os.environ['BASE_PATH'] = ROOT; os.environ['V12_NPZ_CACHE'] = '4'
# PAR 2026-10-01: scalar stock shorts exited SystemExit(2) on 0 trades (rate guard) and traded 0 (LS_RATIO_ENFORCE_TRADIER portfolio gate, harness-only artifact for a single-sym_side replay)
os.environ.setdefault('V8_RATE_GUARD_DISABLED', '1'); os.environ.setdefault('TEST_RATE_GUARD_MIN_PER_DAY', '0')


def fam(reason):
    r = str(reason or '').split('|')[0].strip()
    toks = []
    for t in re.split(r'[_ ]', r):
        if not t or re.search(r'\d', t) or t.startswith(('o', 'p', 'g')) and re.search(r'\d', t):
            break
        toks.append(t)
        if len(toks) >= 4:
            break
    return '_'.join(toks) or (r[:20] or 'EMPTY')


def vec_counts(led):
    c = collections.Counter()
    for e in led or []:
        t = str(e.get('type', '')).upper()
        c[(t, fam(e.get('reason') or e.get('exit_reason') or e.get('entry_reason')))] += 1
    return c


def scalar_counts(path):
    c = collections.Counter()
    for l in open(path):
        try:
            e = json.loads(l)
        except Exception:
            continue
        c[(str(e.get('action', '')).upper(), fam(e.get('reason')))] += 1
    return c


def main():
    out = sys.argv[1]; os.makedirs(out, exist_ok=True)
    from tools.opt.v12_pilot import evaluate_sanitized
    import backtest_v12_engine as B
    for ss in sys.argv[2:]:
        res = {'sym_side': ss}
        try:
            v = evaluate_sanitized(ss, {}, window_days=30, include_ledger=True)
            res['vec'] = {k: v.get(k) for k in ('valid', 'invalid_reason', 'gain_pct', 'trades', 'tim_pct')}
            res['vec_counts'] = {f'{a}|{b}': n for (a, b), n in vec_counts(v.get('execution_ledger') or v.get('ledger')).items()}
        except Exception as e:
            res['vec_error'] = str(e)[:200]
        if os.environ.get('PAR_VEC_ONLY') == '1':
            json.dump(res, open(os.path.join(out, ss + '.json'), 'w'), indent=1, default=str)
            print('DONE-VEC', ss, flush=True)
            continue
        t0 = time.time()
        before = set(glob.glob(ROOT + '/backtest_v8/logs/v8_*.jsonl'))
        try:
            _ov = {'LS_RATIO_ENFORCE_TRADIER': False} if (ss.endswith('_SHORT') and not ss.split('_')[0].endswith(('USDT', 'USDC'))) else {}
            l = B.run_one(ss, _ov, window_days=30)
            res['scalar'] = {k: l.get(k) for k in ('valid', 'invalid_reason', 'gain_pct', 'trades', 'tim_pct')}
            new = sorted(set(glob.glob(ROOT + '/backtest_v8/logs/v8_*.jsonl')) - before, key=os.path.getmtime)
            if new:
                res['scalar_counts'] = {f'{a}|{b}': n for (a, b), n in scalar_counts(new[-1]).items()}
        except BaseException as e:
            res['scalar_error'] = ('%s %s' % (type(e).__name__, e))[:200]
            new = sorted(set(glob.glob(ROOT + '/backtest_v8/logs/v8_*.jsonl')) - before, key=os.path.getmtime)
            if new:
                res['scalar_counts'] = {f'{a}|{b}': n for (a, b), n in scalar_counts(new[-1]).items()}
        res['scalar_sec'] = round(time.time() - t0, 1)
        json.dump(res, open(os.path.join(out, ss + '.json'), 'w'), indent=1, default=str)
        print('DONE', ss, flush=True)


main()
