import ast
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd


def actual_functions():
    tree = ast.parse(Path('ez_klines.py').read_text())
    names = {'native_kline_row', '_normalize_df'}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {'datetime': datetime, 'timezone': timezone, 'time': time, 'pd': pd, 'expected_columns': ['timestamp', 'open', 'high', 'low', 'close', 'volume']}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'ez_klines.py', 'exec'), namespace)
    return namespace


def test_actual_native_parser_proves_closed_time_and_rejects_forming_volume():
    function = actual_functions()['native_kline_row']
    candle = [1000000, '10', '12', '9', '11', '0', 1899999]
    forming = function(candle, 1800)
    assert forming['_provisional'] and not forming['_closed']
    assert '_closed_verified_at' not in forming
    closed = function(candle, 1900)
    assert not closed['_provisional'] and closed['_closed']
    assert closed['_closed_verified_at'] == 1900 and closed['volume'] == 0


def test_existing_closed_and_observed_provenance_survives_normalization():
    function = actual_functions()['_normalize_df']
    row = {'timestamp': '2026-10-26T20:30:00Z', 'open': 10, 'high': 12, 'low': 9, 'close': 11, 'volume': 0, '_provisional': False, '_closed_verified_at': 1793047501, '_producer': 'independent_crypto_15m_clock'}
    result = function(pd.DataFrame([row])).to_dict('records')[0]
    assert result['_closed_verified_at'] == row['_closed_verified_at']
    assert result['_provisional'] == False
    assert result['_producer'] == row['_producer']
