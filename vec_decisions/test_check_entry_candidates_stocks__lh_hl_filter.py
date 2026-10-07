"""Parity: scalar core == vec mask for lh_hl_filter over >=10000 random samples per config."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__lh_hl_filter import (
    _lh_hl_blocks, check_lh_hl_filter_vec, _lh_hl_params,
)


class _Cfg:
    LH_HL_FILTER_MODE = "STRICT_2BAR"
    LH_HL_FILTER_TF_REQ = 2
    LH_HL_FILTER_DC_THRESHOLD_PCT = 0.5
    LH_HL_FILTER_REQUIRE_BOTH = False


def run(n=15000, seed=66):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    for mode in ("STRICT_2BAR", "DC_REGRESS"):
        for tf_req in (1, 2):
            for req_both in (False, True):
                cfg.LH_HL_FILTER_MODE = mode
                cfg.LH_HL_FILTER_TF_REQ = tf_req
                cfg.LH_HL_FILTER_REQUIRE_BOTH = req_both
                mode_v, tf_v, dc_th, rb = _lh_hl_params(cfg)
                arrs = {k: rng.uniform(0, 100, n) for k in
                        ["h1h", "h1hp", "h4h", "h4hp", "l1h", "l1hp", "l4h", "l4hp",
                         "dch1h", "dch4h", "dcl1h", "dcl4h"]}
                # inject zeros to exercise the >0 guards
                arrs["h1h"][:200] = 0.0; arrs["l1h"][200:400] = 0.0
                arrs["dch1h"][400:600] = 0.0; arrs["dcl4h"][600:800] = 0.0
                for is_long in (True, False):
                    scalar = np.array([_lh_hl_blocks(
                        float(arrs["h1h"][i]), float(arrs["h1hp"][i]), float(arrs["h4h"][i]), float(arrs["h4hp"][i]),
                        float(arrs["l1h"][i]), float(arrs["l1hp"][i]), float(arrs["l4h"][i]), float(arrs["l4hp"][i]),
                        float(arrs["dch1h"][i]), float(arrs["dch4h"][i]), float(arrs["dcl1h"][i]), float(arrs["dcl4h"][i]),
                        is_long, mode_v, tf_v, dc_th, rb) for i in range(n)], dtype=bool)
                    vec = check_lh_hl_filter_vec(cfg, arrs["h1h"], arrs["h1hp"], arrs["h4h"], arrs["h4hp"],
                                                 arrs["l1h"], arrs["l1hp"], arrs["l4h"], arrs["l4hp"],
                                                 arrs["dch1h"], arrs["dch4h"], arrs["dcl1h"], arrs["dcl4h"], is_long)
                    mism = int(np.sum(scalar != vec)); total += mism
                    print(f"mode={mode} tf_req={tf_req} req_both={req_both} is_long={is_long}: blocks={int(vec.sum())} mismatches={mism}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
