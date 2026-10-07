"""PARITY test: scalar core == vectorized mask for BB_SQUEEZE_GATE.
Run: /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_check_entry_candidates_crypto__bb_squeeze_gate.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_crypto__bb_squeeze_gate import (  # noqa: E402
    _bb_squeeze_gate_fires,
    check_bb_squeeze_gate_vec,
)


class _Cfg:
    BB_SQUEEZE_MIN_ALIGNMENT = 10.0


def main(n=12000, seed=5151):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    sig = rng.integers(0, 2, n).astype(bool)
    align = rng.uniform(0.0, 25.0, n)
    k3m = rng.uniform(0.0, 100.0, n)
    total = 0
    mism = 0
    first = None
    for is_long in (True, False):
        vf = check_bb_squeeze_gate_vec(cfg, sig, align, k3m, is_long)
        for i in range(n):
            sf = _bb_squeeze_gate_fires(bool(sig[i]), float(align[i]), float(k3m[i]),
                                        is_long, cfg.BB_SQUEEZE_MIN_ALIGNMENT)
            total += 1
            if bool(sf) != bool(vf[i]):
                mism += 1
                if first is None:
                    first = (i, is_long, bool(sig[i]), align[i], k3m[i], sf, bool(vf[i]))
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
