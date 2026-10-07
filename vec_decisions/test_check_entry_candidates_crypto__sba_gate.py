"""PARITY test: scalar core == vectorized mask for SBA_GATE (stateful seam).
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__sba_gate.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__sba_gate import (  # noqa: E402
    _sba_gate_fires,
    check_sba_gate_vec,
)


class _Cfg:
    SBA_MIN_LOSS_PCT = -2.0
    SBA_MAX_LOSS_PCT = -15.0
    SBA_MAX_ADDS = 2
    SBA_COOLDOWN_POSITION_S = 3600.0
    SBA_COOLDOWN_GLOBAL_S = 300.0
    SBA_MAX_CONCURRENT = 3
    SBA_MAX_TOTAL_MULT = 2.5
    SBA_MIN_SCORE = 4.0


def main(n=15000, seed=8080):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    now = 1_700_000_000.0
    start_size = 18.0
    gain = rng.uniform(-20.0, 5.0, n)
    add_count = rng.integers(0, 4, n).astype(float)
    last = now - rng.uniform(0.0, 8000.0, n)
    glast = now - rng.uniform(0.0, 1000.0, n)
    active = rng.integers(0, 5, n).astype(float)
    pos_val = rng.uniform(0.0, 80.0, n)
    sba_score = rng.uniform(0.0, 14.0, n)
    vf = check_sba_gate_vec(cfg, gain, add_count, last, glast, active, pos_val,
                            sba_score, start_size, now)
    total = 0
    mism = 0
    first = None
    for i in range(n):
        sf = _sba_gate_fires(float(gain[i]), float(add_count[i]), float(last[i]),
                             float(glast[i]), float(active[i]), float(pos_val[i]),
                             float(sba_score[i]), start_size, now,
                             cfg.SBA_MIN_LOSS_PCT, cfg.SBA_MAX_LOSS_PCT, cfg.SBA_MAX_ADDS,
                             cfg.SBA_COOLDOWN_POSITION_S, cfg.SBA_COOLDOWN_GLOBAL_S,
                             cfg.SBA_MAX_CONCURRENT, cfg.SBA_MAX_TOTAL_MULT, cfg.SBA_MIN_SCORE)
        total += 1
        if bool(sf) != bool(vf[i]):
            mism += 1
            if first is None:
                first = (i, gain[i], add_count[i], active[i], pos_val[i], sba_score[i], sf, bool(vf[i]))
    print(f"samples={total} mismatches={mism}")
    if first is not None:
        print("FIRST MISMATCH:", first)
    if mism == 0:
        print("PARITY_OK scalar==vec")
        return 0
    print("PARITY_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
