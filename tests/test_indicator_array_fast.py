import ast
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from indicator_array_fast import confirmed_pivots, weighted_moving_average


def original(name):
    path = Path('backups/before_npz_array_speed_202610261855/ez_indicators.py')
    tree = ast.parse(path.read_text())
    node = next(item for item in tree.body if isinstance(item, ast.FunctionDef) and item.name == name)
    node.returns = None
    for argument in node.args.args:
        argument.annotation = None
    namespace = {'np': np, 'pd': pd}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), 'exec'), namespace)
    return namespace[name]


@pytest.mark.parametrize('length', [1, 3, 9, 21, 55, 200])
@pytest.mark.parametrize('missing', [False, True])
def test_wma_bitwise_matches_original_rolling_callback(length, missing):
    values = np.random.default_rng(871).normal(100, 20, 12000)
    if missing:
        values[[0, 31, 1045]] = np.nan
        values[502] = np.inf
        values[4008] = -np.inf
    series = pd.Series(values, name='close')
    expected = original('wma')(series, length)
    actual = weighted_moving_average(series, length)
    pd.testing.assert_series_equal(actual, expected, check_exact=True)


@pytest.mark.parametrize('lookback', [1, 3, 5, 20])
def test_confirmed_pivots_exactly_match_strict_original_and_nan_rules(lookback):
    values = np.random.default_rng(974).integers(-10, 10, 16000).astype(float)
    values[[0, 102, 1777]] = np.nan
    expected = original('find_pivots_arr')(values, lookback)
    actual = confirmed_pivots(values, lookback)
    for candidate, reference in zip(actual, expected):
        np.testing.assert_array_equal(candidate, reference)
