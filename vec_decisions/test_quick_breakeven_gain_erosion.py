# Parity test: scalar core == vec mask over >=10000 random samples.
# Mirrors the strategy_enhancements.py pyramid parity test pattern.
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from vec_decisions.quick_breakeven_gain_erosion import (  # noqa: E402
    _quick_breakeven_gain_erosion_fires,
    check_quick_breakeven_gain_erosion,
    check_quick_breakeven_gain_erosion_vec,
)


class _Cfg:
    pass


def _make_cfg(rng):
    c = _Cfg()
    c.BREAKEVEN_GAIN_EROSION_ENABLED = True
    c.BREAKEVEN_GRACE_MINUTES = float(rng.choice([5.0, 15.0]))
    c.BREAKEVEN_GAIN_EROSION_REQUIRE_PROFIT = bool(rng.integers(0, 2))
    c.BREAKEVEN_GAIN_EROSION_MIN_GAIN = float(rng.choice([0.0, 0.10, 0.5]))
    c.COMMISSION_BUFFER_PCT = 0.10
    c.HARD_BREAKEVEN_FLOOR_ENABLED = bool(rng.integers(0, 2))
    c.HARD_BREAKEVEN_MIN_PEAK_PCT = float(rng.choice([0.5, 1.0]))
    return c


def main():
    rng = np.random.default_rng(20260530)
    N = 20000
    mismatches = 0
    examples = []
    # Sweep multiple config draws so thresholds vary across the run.
    for _trial in range(20):
        cfg = _make_cfg(rng)
        n = N
        gain = rng.uniform(-3.0, 3.0, n)
        age = rng.uniform(0.0, 60.0, n)
        maxg = rng.uniform(-1.0, 5.0, n)
        htf = rng.integers(0, 2, n).astype(bool)
        trend = rng.integers(0, 2, n).astype(bool)
        # vec mask
        vec = check_quick_breakeven_gain_erosion_vec(cfg, gain, age, maxg, htf, trend)
        # scalar core, element by element
        from vec_decisions.quick_breakeven_gain_erosion import _be_erosion_thresholds
        grace, req, ming, hbf_en, hbf_pk = _be_erosion_thresholds(cfg)
        for i in range(n):
            scal = _quick_breakeven_gain_erosion_fires(
                gain[i], age[i], maxg[i], bool(htf[i]), bool(trend[i]),
                grace, req, ming, hbf_en, hbf_pk,
            )
            if bool(scal) != bool(vec[i]):
                mismatches += 1
                if len(examples) < 5:
                    examples.append((i, gain[i], age[i], maxg[i], bool(htf[i]), bool(trend[i]), scal, bool(vec[i])))
    # Also exercise the public scalar wrapper vs vec on a clean draw (veto=False default path).
    cfg = _make_cfg(rng)
    n = N
    gain = rng.uniform(-3.0, 3.0, n)
    age = rng.uniform(0.0, 60.0, n)
    maxg = rng.uniform(-1.0, 5.0, n)
    vec = check_quick_breakeven_gain_erosion_vec(cfg, gain, age, maxg)
    wrapper_mismatch = 0
    for i in range(n):
        fired, _ = check_quick_breakeven_gain_erosion(cfg, {}, gain[i], age[i], maxg[i])
        if bool(fired) != bool(vec[i]):
            wrapper_mismatch += 1
    total = mismatches + wrapper_mismatch
    samples = 20 * N + N
    print(f"samples={samples} core_vs_vec_mismatches={mismatches} wrapper_vs_vec_mismatches={wrapper_mismatch} total={total}")
    for e in examples:
        print("MISMATCH", e)
    assert total == 0, f"PARITY FAIL: {total} mismatches"
    print("PARITY OK")


if __name__ == "__main__":
    main()
