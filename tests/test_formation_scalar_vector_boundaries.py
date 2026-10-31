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
