"""Random-input PARITY test: scalar core == vectorized mask for QUICK_OPEN_STRONG.

Mirrors the pyramid parity test pattern (strategy_enhancements.py
check_pyramid_signal / check_pyramid_signal_vec): generate >=10000 random per-bar
samples, run the pure scalar predicate element-by-element, run the vectorized
mask, assert ZERO mismatches. Proves the live scalar path and the backtest vec
path cannot drift because they share _composite_score().

Run:
    /opt/anaconda3/envs/binance_env/bin/python vec_decisions/test_quick_open_strong.py
"""
import os
import sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from vec_decisions.quick_open_strong import (  # noqa: E402
    _quick_open_strong_fires,
    check_quick_open_strong_vec,
    _composite_score,
    _risk_level_from_drawdown,
)


class _Cfg:
    QUICK_OPEN_STRONG_SCORE_THRESHOLD = 80.0


def main(n=20000, seed=12345):
    rng = np.random.default_rng(seed)
    # momentum: gain-mean, plausibly large +/- to span the >=80 boundary.
    momentum = rng.uniform(-120.0, 120.0, n)
    # risk_level drawn from the real ladder values the live code produces.
    drawdown = rng.uniform(-1.0, 15.0, n)
    risk_level = np.array([_risk_level_from_drawdown(d) for d in drawdown], dtype=float)
    high_vol = rng.random(n) > 0.5
    cfg = _Cfg()
    mismatches = 0
    first_bad = None
    for have_momentum in (True, False):
        for have_risk in (True, False):
            vec_mask = check_quick_open_strong_vec(
                cfg, momentum, risk_level, high_vol,
                have_momentum=have_momentum, have_risk=have_risk,
            )
            for i in range(n):
                scalar = _quick_open_strong_fires(
                    float(momentum[i]), float(risk_level[i]), bool(high_vol[i]),
                    have_momentum, have_risk, 80.0,
                )
                if scalar != bool(vec_mask[i]):
                    mismatches += 1
                    if first_bad is None:
                        score = _composite_score(
                            float(momentum[i]), float(risk_level[i]), bool(high_vol[i]),
                            have_momentum, have_risk,
                        )
                        first_bad = (i, have_momentum, have_risk,
                                     float(momentum[i]), float(risk_level[i]),
                                     bool(high_vol[i]), score, scalar, bool(vec_mask[i]))
    total = n * 4
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
