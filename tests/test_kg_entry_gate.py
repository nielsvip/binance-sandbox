import numpy as np, sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from types import SimpleNamespace as NS
import vec_decisions.kg_entry_gate as K
import vec_decisions.live_kindergarten_gate as G


def _safe(npz, k, n, d):
    return npz[k] if k in npz else np.full(n, d)


def _rng(n=400, seed=1):
    r = np.random.default_rng(seed)
    npz = {}
    for tf in ("15m", "1h", "4h", "D"):
        a = r.integers(0, 2, n).astype(float)
        a[r.random(n) < 0.1] = np.nan
        npz[f"ema_9_above_21_{tf}"] = a
        npz[f"ema_200_{tf}"] = 100 + r.normal(0, 3, n)
        npz[f"sma_200_{tf}"] = 100 + r.normal(0, 3, n)
    return npz, 100 + r.normal(0, 4, n), n


def _ind(npz, i):
    d = {}
    for k, v in npz.items():
        if not np.isnan(v[i]):
            d[k] = float(v[i])
    return d


def test_ema921_scalar_vec_parity():
    npz, close, n = _rng()
    for is_long in (True, False):
        for cfgd in ({"EMA_9_21_FILTER_TFS": "1h"}, {"EMA_9_21_FILTER_TFS": "1h,4h,D", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 0, "EMA_9_21_FILTER_MIN_TFS": 2},
                     {"EMA_9_21_FILTER_TFS": "1h,4h,D", "KINDERGARTEN_CUMULATIVE_MIN_TFS": 3}, {"EMA_9_21_FILTER_TFS": "1h,D", "KINDERGARTEN_STRICT_TFS": "D"}):
            cfg = NS(EMA_9_21_FILTER_ENABLED=True, **cfgd)
            v = K.ema921_pass_vec(npz, n, is_long, cfg, _safe)
            get = lambda k, d=None: getattr(cfg, k, d)
            for i in range(n):
                s = K.ema921_pass(get, _ind(npz, i), is_long)[0]
                assert bool(v[i]) == s, (cfgd, is_long, i)


def test_kg_scan_tf_scalar_vec_parity():
    import ez_manage as E
    npz, close, n = _rng(200, 2)
    for tf in ("OFF", "D", "4h", "1h", "15m"):
        for is_long in (True, False):
            cfg = NS(KINDERGARTEN_FILTER_TF=tf)
            blk = G.kg_block_mask(npz, n, is_long, close, _safe, cfg)
            for i in range(n):
                ok, _ = E._kindergarten_ema_gate(_ind(npz, i), is_long, float(close[i]), enabled=True, scan_tf=tf)
                assert (not ok) == bool(blk[i]), (tf, is_long, i)
