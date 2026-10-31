import numpy as np
from vec_decisions.ema20_hard_open import all_ema20_against, hard_open_live


def test_shared_gate_matches_original_vector_scalar_conditions():
    rng = np.random.default_rng(19)
    prices = rng.uniform(80, 120, 1000)
    levels = rng.uniform(80, 120, (3, 1000))
    levels[rng.random(levels.shape) < .3] = 0
    for is_long in (True, False):
        expected = []
        for i, px in enumerate(prices):
            valid = [v for v in levels[:, i] if v > 0]
            expected.append(len(valid) >= 2 and all(px < v if is_long else px > v for v in valid))
            indicators = {f'ema_20_{tf}': levels[j, i] for j, tf in enumerate(('15m','1h','4h'))}
            assert hard_open_live(indicators, px, is_long) == expected[-1]
        np.testing.assert_array_equal(all_ema20_against(prices, levels, is_long), expected)


def test_equality_and_one_known_timeframe_do_not_block():
    assert not hard_open_live({'ema_20_15m': 101}, 100, True)
    assert not hard_open_live({'ema_20_15m': 100, 'ema_20_1h': 101}, 100, True)
    assert hard_open_live({'ema_20_15m': 101, 'ema_20_1h': 102}, 100, True)
    assert hard_open_live({'ema_20_15m': 99, 'ema_20_1h': 98}, 100, False)
