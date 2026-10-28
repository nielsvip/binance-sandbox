#!/usr/bin/env python3
"""Copy the currently traded active rows into per_sym_store.db, retaining its history."""
import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def rows(connection):
    columns = [row[1] for row in connection.execute('PRAGMA table_info(per_sym_active)')]
    if not columns or columns[0] != 'sym_side':
        raise ValueError('invalid per_sym_active schema')
    return columns, {row[0]: row for row in connection.execute('SELECT * FROM per_sym_active')}


def migrate(source_path, target_path, apply=False):
    source_path, target_path = Path(source_path).resolve(), Path(target_path).resolve()
    if source_path == target_path or not source_path.is_file() or not target_path.is_file():
        raise ValueError('source and target must be distinct existing databases')
    source = sqlite3.connect(f'file:{source_path}?mode=ro', uri=True, timeout=30)
    target = sqlite3.connect(str(target_path), timeout=60)
    try:
        source_columns, active = rows(source)
        target_columns, current = rows(target)
        if source_columns != target_columns:
            raise ValueError('per_sym_active schemas differ')
        if not active or any(not row[source_columns.index('full_config_json')] for row in active.values()):
            raise ValueError('source has missing active settings')
        newer = [symbol for symbol, row in active.items() if symbol in current and str(current[symbol][2] or '') > str(row[2] or '')]
        if newer:
            raise ValueError(f'target contains {len(newer)} newer rows: {newer[:5]}')
        changed = [symbol for symbol, row in active.items() if current.get(symbol) != row]
        history_before = target.execute('SELECT COUNT(*) FROM per_sym_history').fetchone()[0]
        report = {'source': str(source_path), 'target': str(target_path), 'source_active': len(active), 'target_active_before': len(current), 'changed': len(changed), 'history_before': history_before, 'applied': False}
        if not apply:
            return report
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
        backup = target_path.parent.parent.parent / 'backups' / f'before_forward_master_merge_{stamp}_{target_path.name}'
        backup.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(str(backup)) as saved:
            target.backup(saved)
        report['backup'] = str(backup)
        quoted = ','.join('"' + column + '"' for column in source_columns)
        placeholders = ','.join('?' for _ in source_columns)
        updates = ','.join('"' + column + '"=excluded."' + column + '"' for column in source_columns if column != 'sym_side')
        target.execute('BEGIN IMMEDIATE')
        try:
            for symbol in changed:
                target.execute(f'INSERT INTO per_sym_active ({quoted}) VALUES ({placeholders}) ON CONFLICT(sym_side) DO UPDATE SET {updates}', active[symbol])
            if target.execute('SELECT COUNT(*) FROM per_sym_history').fetchone()[0] != history_before:
                raise ValueError('history count changed')
            after_columns, after = rows(target)
            if after_columns != source_columns or any(after.get(symbol) != row for symbol, row in active.items()):
                raise ValueError('active row verification failed')
            target.commit()
        except Exception:
            target.rollback()
            raise
        report.update(applied=True, target_active_after=len(after), history_after=history_before)
        return report
    finally:
        source.close()
        target.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', required=True)
    parser.add_argument('--target', required=True)
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    print(json.dumps(migrate(args.source, args.target, args.apply), sort_keys=True), flush=True)


if __name__ == '__main__':
    main()
