from types import SimpleNamespace
import numpy as np
import pytest
import classic_formations as formations
from vec_decisions import formation_entry_live, formation_exit_live

@pytest.mark.parametrize('action', ['ENTRY', 'EXIT'])
@pytest.mark.parametrize('is_long', [True, False])
@pytest.mark.parametrize('minimum', [.65, 0.0])
def test_vector_selection_matches_scalar_at_stored_score_boundary(action, is_long, minimum):
    direction = 'bull' if is_long == (action == 'ENTRY') else 'bear'
    values = np.array([0, .64, .65, .66, .9], dtype=np.float32)
    arrays = {f'formation_wedge_{direction}_score_15m': values}
    config = SimpleNamespace(FORMATION_TFS='15m', FORMATION_MIN_SCORE=minimum, **{f'FORMATION_WEDGE_{action}_ENABLED': True})
    expected = [formations.select_latest_formation({key: value[index] for key, value in arrays.items()}, is_long=is_long, action=action, config=config) is not None for index in range(len(values))]
    actual, _, _ = formations.formation_vector_mask(arrays, is_long=is_long, action=action, config=config, n=len(values))
    assert actual.tolist() == expected

@pytest.mark.parametrize('action', ['ENTRY', 'EXIT'])
def test_no_enabled_family_never_fires_even_at_zero_threshold(action):
    config = SimpleNamespace(FORMATION_TFS='15m', FORMATION_MIN_SCORE=0)
    assert formations.select_latest_formation({}, is_long=True, action=action, config=config) is None
    mask, _, _ = formations.formation_vector_mask({}, is_long=True, action=action, config=config, n=3)
    assert not mask.any()

@pytest.mark.parametrize('module,action', [(formation_entry_live, 'ENTRY'), (formation_exit_live, 'EXIT')])
def test_cached_fields_follow_timeframe_selection_and_retain_source_identity(monkeypatch, module, action):
    module._CACHE.clear()
    arrays = {'close_15m': np.ones(2)}
    calls = []
    direction = 'bull' if action == 'ENTRY' else 'bear'
    def derive(source, timeframes):
        calls.append(timeframes)
        return {f'formation_wedge_{direction}_score_{tf}': np.array([0, .8]) for tf in timeframes}
    monkeypatch.setattr(formations, 'ensure_npz_formation_fields', derive)
    config = SimpleNamespace(FORMATION_TFS='15m', FORMATION_MIN_SCORE=.65, **{f'FORMATION_WEDGE_{action}_ENABLED': True})
    predicate = getattr(module, f'formation_{action.lower()}_mask')
    assert predicate(arrays, 2, config, True).tolist() == [False, True]
    config.FORMATION_TFS = '1h'
    assert predicate(arrays, 2, config, True).tolist() == [False, True]
    assert calls == [('15m',), ('1h',)]
    assert next(iter(module._CACHE.values()))[0] is arrays
    module._CACHE.clear()

@pytest.mark.parametrize('is_long', [True, False])
def test_actual_engine_formation_open_matches_native_boundary_bar(is_long):
    import v12_quick_engine as engine
    n = 110
    timestamps = np.arange(n, dtype=np.int64) * 900 + 1800000000
    data = {'timestamps': timestamps, 'timestamp_15m': timestamps}
    for field in ('open', 'high', 'low', 'close'):
        data[f'{field}_15m'] = np.full(n, 100.)
    for timeframe in ('15m', '1h', '4h', 'D'):
        data[f'wt1_{timeframe}'] = np.zeros(n)
        data[f'wt2_{timeframe}'] = np.zeros(n)
        data[f'stoch_k_{timeframe}'] = np.full(n, 50.)
        data[f'dc_low_{timeframe}'] = np.full(n, 80.)
        data[f'dc_high_{timeframe}'] = np.full(n, 120.)
    direction = 'bull' if is_long else 'bear'
    scores = np.zeros(n, dtype=np.float32)
    scores[12:14] = [.65, .8]
    data["formation_direction_15m"] = np.sign(scores) * (1 if is_long else -1)
    data[f'formation_wedge_{direction}_score_15m'] = scores
    config = engine.QuickConfig()
    config.apply_cat_side_defaults('CRYPTO_LONG' if is_long else 'CRYPTO_SHORT')
    for name in vars(config):
        if name.endswith('_ENABLED'):
            setattr(config, name, False)
    config.MODE = 'crypto'
    config.BASE_TF = '15m'
    config.FORMATION_WEDGE_ENTRY_ENABLED = True
    config.FORMATION_TFS = '15m'
    config.FORMATION_MIN_SCORE = .65
    config.STRENGTH_FILTER_ENABLED = True
    config.STRENGTH_MIN_SCORE = 5.
    config.ENTRY_ISOLATE_FAMILY = 'FORMATION_ENTRY'
    config.START_POSITION_SIZE = 100.
    config.MAX_ORDER_VALUE = 500.
    config.COOLDOWN_BARS = 0
    native_bars = [i for i in range(n) if formations.select_latest_formation({key: value[i] for key, value in data.items()}, is_long=is_long, action='ENTRY', config=config)]
    assert native_bars == [13]
    result = engine.simulate_one(data, 'FORMATIONUSDC', is_long, config)
    open_bars = [row['bar'] for row in result['ledger'] if row.get('type') == 'OPEN']
    assert open_bars == native_bars
    config.FORMATION_WEDGE_ENTRY_ENABLED = False
    disabled = engine.simulate_one(data, 'FORMATIONUSDC', is_long, config)
    assert not any(row.get('type') == 'OPEN' for row in disabled['ledger'])
