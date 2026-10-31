import numpy as np
from types import SimpleNamespace

from vec_decisions.ez_missing_hold_bars_exit import (
    optimal_hold_bars_exit_core,
    satoshit_standard_exits_skipped,
)


def _hold_cfg(gain_threshold):
    return SimpleNamespace(
        OPTIMAL_HOLD_BARS_EXIT_ENABLED=True,
        TF_FOCUS="3m",
        GAIN_THRESHOLD_LOW=gain_threshold,
        NOLOSS_MIN_PROFIT_PCT=0.0,
        OPTIMAL_HOLD_BARS_3M=1,
    )


def test_gain_threshold_options_each_have_a_real_predicate_boundary():
    elapsed = np.array([181.0, 181.0])
    eligible = np.ones(2, dtype=bool)
    gain = np.array([0.3, 0.75])
    results = {}
    for threshold in (0.15, 0.5, 1.0):
        results[threshold] = optimal_hold_bars_exit_core(
            _hold_cfg(threshold), gain, elapsed, eligible
        ).tolist()
    assert results == {
        0.15: [True, True],
        0.5: [False, True],
        1.0: [False, False],
    }


def test_satoshit_protection_bool_options_have_distinct_decisions():
    cfg = SimpleNamespace(SATOSHIT_PROTECT_TRADES=True, SATOSHIT_ENABLED=True)
    assert satoshit_standard_exits_skipped(cfg, "SATOSHIT_ENTRY", []) is True
    cfg.SATOSHIT_PROTECT_TRADES = False
    assert satoshit_standard_exits_skipped(cfg, "SATOSHIT_ENTRY", []) is False
