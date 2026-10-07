"""Random-input PARITY test for E_3_STRUCTURE_EXIT.
Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_process_position_crypto__e3_structure_exit.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.process_position_crypto__e3_structure_exit import (  # noqa: E402
    _e3_structure_fires,
    check_e3_structure_exit_vec,
    _TFS,
)

_TOKENS = ["LH", "LL", "HH", "HL", "lh", "", "NEUTRAL"]


class _Cfg:
    pass


def main(n=20000, seed=66):
    rng = np.random.default_rng(seed)
    struct_arrs = {tf: np.array([_TOKENS[i] for i in rng.integers(0, len(_TOKENS), n)], dtype=object) for tf in _TFS}
    cfg = _Cfg()
    mismatches = 0
    first_bad = None
    for is_long in (True, False):
        vec = check_e3_structure_exit_vec(cfg, struct_arrs, is_long)
        for idx in range(n):
            structs = {tf: str(struct_arrs[tf][idx]) for tf in _TFS}
            scalar = _e3_structure_fires(structs, is_long)
            if scalar != bool(vec[idx]):
                mismatches += 1
                if first_bad is None:
                    first_bad = (idx, is_long, scalar, bool(vec[idx]))
    total = n * 2
    print(f"samples={total} mismatches={mismatches}")
    if first_bad is not None:
        print("FIRST MISMATCH:", first_bad)
    if mismatches == 0:
        print("PARITY_OK scalar==vec")
        return 0
    print("PARITY_FAIL")
    return 1


if __name__ == "__main__":
    sys.exit(main())
