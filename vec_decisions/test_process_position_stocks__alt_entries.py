"""Parity test: scalar == vec for alt-entry predicates (RZ / LR / BB_RSI_STOCH / BB_BREAKOUT)."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_stocks__alt_entries import (
    _rz_breakout_fires, check_rz_breakout_vec, _rz_params,
    _lr_pctb_d_long_fires, check_lr_pctb_d_long_vec,
    _bb_rsi_stoch_scalp_fires, check_bb_rsi_stoch_scalp_vec,
    _bb_breakout_fires, check_bb_breakout_vec,
)


class _Cfg:
    RZ_BREAKOUT_ENTRY_ENABLED = True
    RZ_TOP_BB_THRESHOLD = 0.85
    RZ_BOT_BB_THRESHOLD = 0.15
    RZ_BREAKOUT_BAND = 0.05
    LR_PCTB_D_LONG_ENTRY_ENABLED = True
    LR_PCTB_D_LONG_ENTRY_THRESHOLD = 0.20
    BB_RSI_STOCH_SCALP_ENABLED = True
    BB_BREAKOUT_ENABLED = True
    BB_BREAKOUT_TF = "1h"


def run(n=15000, seed=23):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for is_long in (True, False):
        # RZ
        bb1h = rng.uniform(-0.2, 1.2, n)
        rz_top, rz_bot, band = _rz_params(cfg)
        s = np.array([_rz_breakout_fires(float(bb1h[i]), is_long, rz_top, rz_bot, band) for i in range(n)], dtype=bool)
        v = check_rz_breakout_vec(cfg, bb1h, is_long)
        m = int(np.sum(s != np.asarray(v, dtype=bool))); total += m
        print(f"RZ is_long={is_long}: fires={int(np.sum(v))} mism={m}")
        # LR (long only fires)
        lr = rng.uniform(-0.1, 1.0, n)
        lr_nan = lr.copy(); lr_nan[:200] = np.nan
        sl = np.array([(_lr_pctb_d_long_fires(float(lr_nan[i]), is_long, 0.20) if not np.isnan(lr_nan[i]) else False) for i in range(n)], dtype=bool)
        vl = check_lr_pctb_d_long_vec(cfg, lr_nan, is_long)
        ml = int(np.sum(sl != np.asarray(vl, dtype=bool))); total += ml
        print(f"LR is_long={is_long}: fires={int(np.sum(vl))} mism={ml}")
        # BB_RSI_STOCH
        bb5 = rng.uniform(-0.1, 1.1, n); rsi = rng.uniform(0, 100, n); k = rng.uniform(0, 100, n)
        sb = np.array([_bb_rsi_stoch_scalp_fires(float(bb5[i]), float(rsi[i]), float(k[i]), is_long) for i in range(n)], dtype=bool)
        vb = check_bb_rsi_stoch_scalp_vec(cfg, bb5, rsi, k, is_long)
        mb = int(np.sum(sb != np.asarray(vb, dtype=bool))); total += mb
        print(f"BB_RSI_STOCH is_long={is_long}: fires={int(np.sum(vb))} mism={mb}")
        # BB_BREAKOUT
        bbk = rng.uniform(-0.3, 1.3, n)
        sk = np.array([_bb_breakout_fires(float(bbk[i]), is_long) for i in range(n)], dtype=bool)
        vk = check_bb_breakout_vec(cfg, bbk, is_long)
        mk = int(np.sum(sk != np.asarray(vk, dtype=bool))); total += mk
        print(f"BB_BREAKOUT is_long={is_long}: fires={int(np.sum(vk))} mism={mk}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
