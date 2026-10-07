"""Parity: scalar cores == vec masks for ema/alignment/trend/htf/wt15m gates over >=10000 samples each."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__ema_alignment_trend_htf_gates import (
    _ema_9_21_blocks, _alignment_blocks, _trend_gate_blocks, _htf_conf_blocks,
    _wt_crossunder_15m_blocks_short,
    check_ema_9_21_vec, check_alignment_gate_vec, check_trend_gate_vec,
    check_htf1_conf_vec, check_htf4_conf_vec, check_wt_crossunder_15m_short_vec,
    _alignment_min,
)


class _Cfg:
    ALIGNMENT_GATE_MIN = 4


def run(n=20000, seed=55):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for is_long in (True, False):
        ema = rng.choice([0.0, 1.0, -1.0], n).astype(float)
        sc = np.array([_ema_9_21_blocks(float(ema[i]), is_long) for i in range(n)], dtype=bool)
        vc = check_ema_9_21_vec(cfg, ema, is_long)
        m = int(np.sum(sc != vc)); total += m
        print(f"EMA is_long={is_long}: blocks={int(vc.sum())} mismatches={m}")
        k1h = rng.uniform(0, 100, n); d1h = rng.uniform(0, 100, n)
        k4h = rng.uniform(0, 100, n); d4h = rng.uniform(0, 100, n)
        k15 = rng.uniform(0, 100, n); d15 = rng.uniform(0, 100, n)
        lr = rng.uniform(-2, 2, n)
        lr[:200] = 0.0; k1h[200:300] = d1h[200:300]
        for amin in (1, 2, 3, 4):
            cfg.ALIGNMENT_GATE_MIN = amin
            sca = np.array([_alignment_blocks(float(k1h[i]), float(d1h[i]), float(k4h[i]), float(d4h[i]),
                                              float(k15[i]), float(d15[i]), float(lr[i]), is_long, amin) for i in range(n)], dtype=bool)
            vca = check_alignment_gate_vec(cfg, k1h, d1h, k4h, d4h, k15, d15, lr, is_long)
            ma = int(np.sum(sca != vca)); total += ma
            print(f"ALIGN amin={amin} is_long={is_long}: blocks={int(vca.sum())} mismatches={ma}")
        sct = np.array([_trend_gate_blocks(float(lr[i]), is_long) for i in range(n)], dtype=bool)
        vct = check_trend_gate_vec(cfg, lr, is_long)
        mt = int(np.sum(sct != vct)); total += mt
        print(f"TREND is_long={is_long}: blocks={int(vct.sum())} mismatches={mt}")
        sc1 = np.array([_htf_conf_blocks(float(k1h[i]), float(d1h[i]), is_long) for i in range(n)], dtype=bool)
        vc1 = check_htf1_conf_vec(cfg, k1h, d1h, is_long)
        m1 = int(np.sum(sc1 != vc1)); total += m1
        print(f"HTF1 is_long={is_long}: blocks={int(vc1.sum())} mismatches={m1}")
        sc4 = np.array([_htf_conf_blocks(float(k4h[i]), float(d4h[i]), is_long) for i in range(n)], dtype=bool)
        vc4 = check_htf4_conf_vec(cfg, k4h, d4h, is_long)
        m4 = int(np.sum(sc4 != vc4)); total += m4
        print(f"HTF4 is_long={is_long}: blocks={int(vc4.sum())} mismatches={m4}")
    # WT crossunder 15m (short only)
    w1 = rng.uniform(-50, 50, n); w2 = rng.uniform(-50, 50, n); w1[:200] = w2[:200]
    scw = np.array([_wt_crossunder_15m_blocks_short(float(w1[i]), float(w2[i])) for i in range(n)], dtype=bool)
    vcw = check_wt_crossunder_15m_short_vec(cfg, w1, w2)
    mw = int(np.sum(scw != vcw)); total += mw
    print(f"WT_CROSSUNDER_15M short: blocks={int(vcw.sum())} mismatches={mw}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
