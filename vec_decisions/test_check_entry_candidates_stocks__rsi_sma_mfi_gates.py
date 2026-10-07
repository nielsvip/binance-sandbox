"""Parity: scalar cores == vec masks for rsi/sma/sma200_dist/mfi gates over >=10000 samples each."""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.check_entry_candidates_stocks__rsi_sma_mfi_gates import (
    _rsi_gate_blocks, _sma_gate_blocks, _sma200_dist_blocks_long, _mfi_gate_blocks_long,
    check_rsi_gate_vec, check_sma_gate_vec, check_sma200_dist_long_vec, check_mfi_gate_long_vec,
    _rsi_params, _sma200_dist_thr, _mfi_thr,
)


class _Cfg:
    RSI_ENTRY_LONG_TRADIER = 42.0
    RSI_ENTRY_SHORT_TRADIER = 58.0
    SMA200_DIST_LONG_THRESHOLD_4H = -10.0
    MFI_LONG_THRESHOLD_D = 80.0


def run(n=20000, seed=44):
    rng = np.random.default_rng(seed)
    cfg = _Cfg()
    total = 0
    lt, st = _rsi_params(cfg)
    for is_long in (True, False):
        rsi = rng.uniform(0, 100, n); rsi[:100] = lt; rsi[100:200] = st
        sc = np.array([_rsi_gate_blocks(float(rsi[i]), is_long, lt, st) for i in range(n)], dtype=bool)
        vc = check_rsi_gate_vec(cfg, rsi, is_long)
        m = int(np.sum(sc != vc)); total += m
        print(f"RSI is_long={is_long}: blocks={int(vc.sum())} mismatches={m}")
        price = rng.uniform(0, 1000, n); sma = rng.uniform(0, 1000, n)
        price[:100] = 0.0; sma[100:200] = 0.0; sma[200:300] = price[200:300]
        scs = np.array([_sma_gate_blocks(float(price[i]), float(sma[i]), is_long) for i in range(n)], dtype=bool)
        vcs = check_sma_gate_vec(cfg, price, sma, is_long)
        ms = int(np.sum(scs != vcs)); total += ms
        print(f"SMA is_long={is_long}: blocks={int(vcs.sum())} mismatches={ms}")
    # LONG-only gates
    price = rng.uniform(0, 1000, n); sma4 = rng.uniform(0, 1000, n)
    price[:100] = 0.0; sma4[100:200] = 0.0
    thr = _sma200_dist_thr(cfg)
    scd = np.array([_sma200_dist_blocks_long(float(price[i]), float(sma4[i]), thr) for i in range(n)], dtype=bool)
    vcd = check_sma200_dist_long_vec(cfg, price, sma4)
    md = int(np.sum(scd != vcd)); total += md
    print(f"SMA200_DIST long: blocks={int(vcd.sum())} mismatches={md}")
    mfi = rng.uniform(0, 100, n); mfi[:100] = _mfi_thr(cfg)
    scm = np.array([_mfi_gate_blocks_long(float(mfi[i]), _mfi_thr(cfg)) for i in range(n)], dtype=bool)
    vcm = check_mfi_gate_long_vec(cfg, mfi)
    mm = int(np.sum(scm != vcm)); total += mm
    print(f"MFI long: blocks={int(vcm.sum())} mismatches={mm}")
    print(f"TOTAL_MISMATCHES={total}")
    assert total == 0, f"PARITY FAIL: {total}"
    print("PARITY_OK")
    return total


if __name__ == "__main__":
    run()
