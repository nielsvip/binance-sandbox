# === TRADIER_V8Q_SWEEP_WINNERS auto-emitted 2026-04-20 03:15 UTC ===
# Source: apply_sweep_winners.py, from 8-sector × 2yr × pick-3 vectorized sweep
# DO NOT apply to live tradier_manage.py without manual review + user approval

TRADIER_V8Q_SWEEP_WINNERS = [
    { # sec=mix_12 src=sample robust=1.166 avg=1.441 pool=1.166 wr=97.6% n=326 h=8
        'side': 'L',
        'horizon_bars': 8,
        'conditions': ['bb15_lt10', 'dcx_1h', 'k1h_lt60'],
        'combo_str': 'bb15_lt10+dcx_1h+k1h_lt60',
        'source': 'sample',
        'sector': 'mix_12',
        'metrics': {'robust': 1.1657, 'avg': 1.4409, 'pool': 1.1657, 'wr': 97.55, 'n': 326},
    },
    { # sec=mix_12 src=sample robust=1.148 avg=1.417 pool=1.148 wr=97.4% n=278 h=8
        'side': 'L',
        'horizon_bars': 8,
        'conditions': ['bb15_lt10', 'dcx_1h', 'k1h_lt50'],
        'combo_str': 'bb15_lt10+dcx_1h+k1h_lt50',
        'source': 'sample',
        'sector': 'mix_12',
        'metrics': {'robust': 1.1484, 'avg': 1.4168, 'pool': 1.1484, 'wr': 97.45, 'n': 278},
    },
    { # sec=mix_12 src=sample robust=1.094 avg=1.251 pool=1.094 wr=96.9% n=289 h=8
        'side': 'L',
        'horizon_bars': 8,
        'conditions': ['bb15_lt10', 'dcx_1h', 'wt_D'],
        'combo_str': 'bb15_lt10+dcx_1h+wt_D',
        'source': 'sample',
        'sector': 'mix_12',
        'metrics': {'robust': 1.0938, 'avg': 1.2510, 'pool': 1.0938, 'wr': 96.92, 'n': 289},
    },
    { # sec=mix_12 src=sample robust=1.083 avg=1.201 pool=1.083 wr=94.4% n=349 h=8
        'side': 'L',
        'horizon_bars': 8,
        'conditions': ['bb15_lt10', 'dcx_1h', 'k5_lt40'],
        'combo_str': 'bb15_lt10+dcx_1h+k5_lt40',
        'source': 'sample',
        'sector': 'mix_12',
        'metrics': {'robust': 1.0830, 'avg': 1.2012, 'pool': 1.0830, 'wr': 94.43, 'n': 349},
    },
    { # sec=mix_12 src=sample robust=1.083 avg=1.281 pool=1.083 wr=95.5% n=336 h=8
        'side': 'L',
        'horizon_bars': 8,
        'conditions': ['above_sma5pct', 'bb15_lt10', 'dcx_1h'],
        'combo_str': 'above_sma5pct+bb15_lt10+dcx_1h',
        'source': 'sample',
        'sector': 'mix_12',
        'metrics': {'robust': 1.0826, 'avg': 1.2809, 'pool': 1.0826, 'wr': 95.50, 'n': 336},
    },
]

# Enforcement switch — MUST be set False until user reviews and enables
TRADIER_V8Q_SWEEP_ENFORCE = False
TRADIER_V8Q_SWEEP_MIN_ROBUST = 0.5  # only enforce combos above this at runtime