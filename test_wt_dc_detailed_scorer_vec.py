"""Parity: vectorized detailed WT_DC scorer == scalar _score_long/_score_short.
User-requested WT_DC rewrite 2026-09-28 (slowdown/accel scorer). Proven 0.000000 diff."""
import glob
import numpy as np
import pytest
import wt_dc_entry_scorer as S
import wt_dc_entry_scorer_vec as VS

_FIELDS = ['wt_bull_alignment', 'wt_bear_alignment', 'wt_velocity_up_count',
    'wt_velocity_down_count', 'wt_structure_4h', 'wt_structure_D', 'wt_composite_bias',
    'wt_wave_phase_4h', 'wt_wave_phase_D', 'wt_cross_bull_1h', 'wt_cross_bull_15m',
    'wt_cross_bull_5m', 'wt_cross_bear_1h', 'wt_cross_bear_15m', 'wt_cross_bear_5m',
    'wt_velocity_1h', 'wt_momentum_state_1h', 'wt_cross_value_1h', 'wt_cross_rising_1h',
    'wt_cross_prev_value_1h', 'wt_divergence_1h', 'wt_divergence_strength_1h',
    'dc_position_1h', 'dc_position_4h', 'bb_pct_b_4h', 'bb_pct_b_D', 'relative_volume_1h',
    'mfi_1h', 'stoch_crossover_15m', 'stoch_crossunder_15m', 'stoch_k_1h', 'close_D',
    'ema_200_D', 'close_1h', 'ema_20_1h', 'ha_4h', 'ha_D']


def _load():
    files = sorted(glob.glob("backtest_v8/indicators/*.npz"))
    if not files:
        pytest.skip("no NPZ available")
    d = np.load(files[0], allow_pickle=True)
    n = min(2000, len(d['close']))
    ind = {k: (np.asarray(d[k])[:n] if k in d.files else np.zeros(n)) for k in _FIELDS}
    return ind, n


@pytest.mark.parametrize("is_long", [True, False])
def test_detailed_scorer_vec_equals_scalar(is_long):
    ind, n = _load()
    vec = VS.score_entry_detailed_vec(ind, is_long, n=n)
    for i in range(n):
        row = {k: ind[k][i] for k in _FIELDS}
        sc, _ = S.score_entry(row, is_long, detailed=True)
        assert abs(sc - vec[i]) < 1e-9, f"bar {i}: scalar {sc} vec {vec[i]}"
