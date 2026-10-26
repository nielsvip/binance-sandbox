#!/usr/bin/env python3
import argparse
import fcntl
import hashlib
import json
import os
import re
import select
import shlex
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from per_sym_forward_authority import digest


def atomic(path, document):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f'.{path.name}.{os.getpid()}.tmp')
    with temporary.open('w') as stream:
        json.dump(document, stream, separators=(',', ':'))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def row_record(symbol_side, updated, full_json):
    if not re.fullmatch(r'[A-Za-z0-9_.-]+_(LONG|SHORT)', symbol_side):
        raise ValueError('invalid symbol-side')
    full = json.loads(full_json)
    if not isinstance(full, dict) or not full:
        raise ValueError('empty full switch set')
    return {'kind': 'row', 'sym_side': symbol_side, 'updated_at': updated, 'full_config': full, 'full_config_md5': digest(full)}


def serve(db):
    known = json.loads(sys.stdin.readline()).get('known', {})
    connection = sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro', uri=True, timeout=1)
    rows, versions, previous_version, snapshot = {}, {}, None, ''
    try:
        while True:
            version = connection.execute('PRAGMA data_version').fetchone()[0]
            if version != previous_version:
                current = []
                for symbol_side, updated, full_json in connection.execute('SELECT sym_side,updated_at,full_config_json FROM per_sym_active'):
                    current.append(symbol_side)
                    if not full_json:
                        continue
                    row_version = (updated, hashlib.md5(full_json.encode()).hexdigest())
                    if versions.get(symbol_side) == row_version:
                        continue
                    record = row_record(symbol_side, updated, full_json)
                    rows[symbol_side] = {'updated_at': updated, 'full_config_md5': record['full_config_md5']}
                    if known.get(symbol_side) != record['full_config_md5']:
                        print(json.dumps(record, separators=(',', ':')), flush=True)
                        known[symbol_side] = record['full_config_md5']
                    versions[symbol_side] = row_version
                current_sides = set(current)
                rows = {key: value for key, value in rows.items() if key in current_sides}
                snapshot = hashlib.md5(json.dumps(rows, sort_keys=True).encode()).hexdigest()
                print(json.dumps({'kind': 'index', 'source_db': str(Path(db).resolve()), 'rows': rows, 'snapshot_md5': snapshot}, separators=(',', ':')), flush=True)
                previous_version = version
            print(json.dumps({'kind': 'heartbeat', 'generated_at': time.time(), 'snapshot_md5': snapshot}, separators=(',', ':')), flush=True)
            time.sleep(0.25)
    finally:
        connection.close()


def seed(root, db):
    if not db or not Path(db).is_file():
        return
    connection = sqlite3.connect(f'file:{Path(db).resolve()}?mode=ro', uri=True, timeout=1)
    try:
        for symbol_side, updated, full_json in connection.execute('SELECT sym_side,updated_at,full_config_json FROM per_sym_active'):
            if not full_json:
                continue
            path = root / f'{symbol_side}.json'
            if not path.exists():
                atomic(path, row_record(symbol_side, updated, full_json))
    finally:
        connection.close()


def follow(base, host, db, seed_db):
    root = Path(base) / 'data' / 'per_sym_current'
    root.mkdir(parents=True, exist_ok=True)
    lock = (root / '.feed.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return
    atomic(root / '_required.json', {'source_db': db, 'installed_at': time.time()})
    seed(root, seed_db)
    while True:
        process = None
        try:
            known = {}
            for path in root.glob('*.json'):
                if path.name.startswith('_'):
                    continue
                record = json.loads(path.read_text())
                known[record['sym_side']] = record['full_config_md5']
            command = ['/home/niels/.conda/envs/binance_env_mac/bin/python', '-u', '/home/niels/binance-sandbox/tools/per_sym_settings_feed.py', 'serve', '--db', db]
            if host == 'local':
                process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            else:
                remote = ' '.join(shlex.quote(part) for part in command)
                process = subprocess.Popen(['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5', '-o', 'StrictHostKeyChecking=accept-new', host, remote], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            process.stdin.write(json.dumps({'known': known}, separators=(',', ':')) + '\n')
            process.stdin.flush()
            while True:
                if not select.select([process.stdout], [], [], 5)[0]:
                    raise TimeoutError('S1 settings heartbeat did not arrive')
                line = process.stdout.readline()
                if not line:
                    raise EOFError('S1 settings feed disconnected')
                record = json.loads(line)
                kind = record.get('kind')
                if kind == 'row':
                    if digest(record['full_config']) != record['full_config_md5']:
                        raise ValueError('S1 settings checksum differs')
                    atomic(root / f'{record["sym_side"]}.json', record)
                elif kind == 'index':
                    atomic(root / '_manifest.json', record)
                elif kind == 'heartbeat':
                    atomic(root / '_heartbeat.json', record)
        except Exception as exc:
            print(json.dumps({'at': time.time(), 'settings_feed_error': str(exc)}), flush=True)
        finally:
            if process is not None:
                try:
                    process.kill()
                    process.wait()
                except OSError:
                    pass
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('serve', 'follow'))
    parser.add_argument('--db', default='/home/niels/binance/data/hourly_reconfig/per_sym_store.db')
    parser.add_argument('--base', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--host', default='s1-pub')
    parser.add_argument('--seed-db')
    args = parser.parse_args()
    if args.action == 'serve':
        serve(args.db)
    else:
        follow(args.base, args.host, args.db, args.seed_db)


if __name__ == '__main__':
    main()
