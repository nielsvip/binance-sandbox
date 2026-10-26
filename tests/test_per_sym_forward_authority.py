import json
import os
import select
import sqlite3
import subprocess
import sys
import time

import pytest

from per_sym_forward_authority import digest, read_current


def test_settings_require_fresh_s1_publication_and_exact_full_set(tmp_path):
    root = tmp_path / 'data/per_sym_current'
    root.mkdir(parents=True)
    full = {'ENTRY_ENABLED': True, 'FILTERS': {'15m': 'UP'}, 'PARAMETER': 9}
    record = {'sym_side': 'TESTUSDT_LONG', 'updated_at': '2026-10-26T20:00:00Z', 'full_config': full, 'full_config_md5': digest(full)}
    (root / 'TESTUSDT_LONG.json').write_text(json.dumps(record))
    index = {'snapshot_md5': 'snapshot', 'rows': {'TESTUSDT_LONG': {key: record[key] for key in ('updated_at', 'full_config_md5')}}}
    (root / '_manifest.json').write_text(json.dumps(index))
    heartbeat = {'snapshot_md5': 'snapshot', 'generated_at': time.time()}
    (root / '_heartbeat.json').write_text(json.dumps(heartbeat))
    assert read_current('TESTUSDT_LONG', tmp_path)['full_config'] == full
    (root / '_heartbeat.json').write_text(json.dumps(dict(heartbeat, generated_at=time.time() - 30)))
    with pytest.raises(RuntimeError, match='heartbeat'):
        read_current('TESTUSDT_LONG', tmp_path)
    (root / '_heartbeat.json').write_text(json.dumps(heartbeat))
    changed = dict(index, rows={'TESTUSDT_LONG': {'updated_at': record['updated_at'], 'full_config_md5': 'newer-publication'}})
    (root / '_manifest.json').write_text(json.dumps(changed))
    with pytest.raises(RuntimeError, match='does not match S1'):
        read_current('TESTUSDT_LONG', tmp_path)


def test_s1_stream_announces_new_sql_switches_without_waiting_for_next_bar(tmp_path):
    path = tmp_path / 'source.db'
    connection = sqlite3.connect(path)
    connection.execute('CREATE TABLE per_sym_active(sym_side TEXT PRIMARY KEY,updated_at TEXT,full_config_json TEXT)')
    connection.execute('INSERT INTO per_sym_active VALUES(?,?,?)', ('TESTUSDT_LONG', 'first', json.dumps({'SWITCH': True})))
    connection.commit()
    process = subprocess.Popen([sys.executable, '-u', 'tools/per_sym_settings_feed.py', 'serve', '--db', str(path)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        process.stdin.write('{"known":{}}\n')
        process.stdin.flush()
        first = json.loads(process.stdout.readline())
        assert first['full_config'] == {'SWITCH': True}
        assert json.loads(process.stdout.readline())['kind'] == 'index'
        assert json.loads(process.stdout.readline())['kind'] == 'heartbeat'
        connection.execute('UPDATE per_sym_active SET updated_at=?,full_config_json=?', ('first', json.dumps({'SWITCH': False, 'NEW_FILTER': ['D', 'UP']})))
        connection.commit()
        deadline = time.monotonic() + 3
        found = None
        while time.monotonic() < deadline:
            if not select.select([process.stdout], [], [], 1)[0]:
                continue
            packet = json.loads(process.stdout.readline())
            if packet['kind'] == 'row':
                found = packet
                break
        assert found is not None
        assert found['updated_at'] == 'first'
        assert found['full_config'] == {'SWITCH': False, 'NEW_FILTER': ['D', 'UP']}
    finally:
        process.terminate()
        process.wait(timeout=3)
        connection.close()
