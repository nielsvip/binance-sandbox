import hashlib
import json
import time
from pathlib import Path

_cache = {}


def digest(full_config):
    return hashlib.md5(json.dumps(full_config, sort_keys=True, default=str).encode()).hexdigest()


def read_current(sym_side, base_path, max_age_s=5.0):
    root = Path(base_path) / 'data' / 'per_sym_current'
    heartbeat = json.loads((root / '_heartbeat.json').read_text())
    age = time.time() - float(heartbeat['generated_at'])
    if age < -2 or age > max_age_s:
        raise RuntimeError(f'published settings heartbeat is {age:.1f}s old')
    manifest_path = root / '_manifest.json'
    signature = manifest_path.stat().st_mtime_ns
    key = str(manifest_path)
    if _cache.get(key, (None,))[0] != signature:
        _cache[key] = (signature, json.loads(manifest_path.read_text()))
    manifest = _cache[key][1]
    if manifest['snapshot_md5'] != heartbeat['snapshot_md5']:
        raise RuntimeError('settings snapshot is being updated')
    expected = manifest['rows'].get(str(sym_side).upper())
    if not expected:
        raise KeyError(f'no published full switch set for {sym_side}')
    path = root / f'{str(sym_side).upper()}.json'
    signature = path.stat().st_mtime_ns
    key = str(path)
    if _cache.get(key, (None,))[0] != signature:
        record = json.loads(path.read_text())
        if not isinstance(record.get('full_config'), dict) or not record['full_config']:
            raise RuntimeError('published switch set is empty')
        if digest(record['full_config']) != record['full_config_md5']:
            raise RuntimeError('published switch set checksum differs')
        _cache[key] = (signature, record)
    record = _cache[key][1]
    if record['full_config_md5'] != expected['full_config_md5'] or record['updated_at'] != expected['updated_at']:
        raise RuntimeError('local switch set does not match S1 publication')
    return record
