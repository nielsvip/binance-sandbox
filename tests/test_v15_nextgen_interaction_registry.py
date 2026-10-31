import copy
import gzip
import json
import sqlite3

import pytest

from tools import v15_nextgen_interaction_registry as R
from tools import v18_experience as E


def native_fixture(tmp_path):
    path = tmp_path / 'experience.db'
    base = {'A': False, 'B': 0}
    base_sha = E.Canon(base).set_id
    with sqlite3.connect(path) as con:
        con.execute('CREATE TABLE sets(set_id TEXT,gz BLOB)')
        con.execute('CREATE TABLE evals(eval_id TEXT,provenance TEXT,base_set_id TEXT,set_id TEXT,delta_rule TEXT,delta TEXT,extra TEXT,valid INT,gain REAL,invalid_reason TEXT,sym_side TEXT,cat_side TEXT,window_days REAL,offset_days REAL,npz_md5 TEXT)')
        con.execute('INSERT INTO sets VALUES (?,?)', (base_sha, gzip.compress(json.dumps(base).encode())))
        for name, mods, gain in [('B', {}, 1), ('X', {'A': True}, 2), ('Y', {'B': 5}, 3), ('Z', {'A': True, 'B': 5}, 8)]:
            config = dict(base, **mods)
            extra = {'engine_sha256': 'a' * 64, 'market_data_end': '2026-10-01T00:00:00.000000Z'}
            con.execute('INSERT INTO evals VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)', (name, 'native', base_sha, E.Canon(config).set_id, 'explicit', json.dumps({'set': mods}), json.dumps(extra), 1, gain, None, 'ABC_LONG', 'STOCKS_LONG', 30, 0, 'b' * 32))
    return path


def test_real_sqlite_components_and_future_queue_preserve_baseline(tmp_path):
    path = native_fixture(tmp_path)
    components = R.read_native_components(path, R.file_sha(path), {key: key for key in ('B', 'X', 'Y', 'Z')})
    x, y = {'kind': 'switch', 'field': 'A', 'value': True}, {'kind': 'filter', 'field': 'B', 'value': 5}
    proof = R.verify_quartet(components, x, y)
    assert proof['status'] == 'verified_measurement'
    assert proof['interaction_delta'] == 4
    assert proof['kind'] == 'SWITCH_FILTER'
    baseline = {'A': False, 'B': 0, 'CURRENT': 7}
    before = copy.deepcopy(baseline)
    result = R.postbaseline_queue([proof], baseline, lambda a, b, c: True, 1)
    assert baseline == before
    assert result['runtime_hooked'] is False
    assert result['queue'][0]['measurements_required'][0]['config'] == before
    assert result['queue'][0]['current_interaction_verified'] is False
    assert len(result['queue'][0]['measurements_required']) == 4
    assert not R.postbaseline_queue([proof], baseline, lambda *args: False, 1)['queue']


def test_engine_context_hash_and_missing_components_fail_closed(tmp_path):
    path = native_fixture(tmp_path)
    with pytest.raises(ValueError, match='snapshot hash'):
        R.read_native_components(path, '0' * 64, {})
    rows = R.read_native_components(path, R.file_sha(path), {key: key for key in ('B', 'X', 'Y', 'Z')})
    x, y = {'kind': 'filter', 'field': 'A', 'value': True}, {'kind': 'filter', 'field': 'B', 'value': 5}
    assert R.verify_quartet(rows, x, y)['kind'] == 'FILTER_FILTER'
    x['kind'] = y['kind'] = 'switch'
    assert R.verify_quartet(rows, x, y)['kind'] == 'SWITCH_SWITCH'
    rows['Z']['engine_sha256'] = None
    rows['Z']['gain'] = float('nan')
    result = R.verify_quartet(rows, x, y)
    assert result['status'] == 'unverified'
    assert 'unknown_engine_era' in result['reasons']
    assert 'invalid_or_nonfinite_component' in result['reasons']
    assert not R.postbaseline_queue([result], {}, lambda *args: True, 99)['queue']
    assert R.verify_quartet({}, x, y)['status'] == 'unverified'
