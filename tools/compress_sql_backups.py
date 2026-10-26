#!/usr/bin/env python3
import argparse
import gzip
import hashlib
import json
import os
import shutil
import time
from pathlib import Path


def compress_verified(path, manifest):
    before = path.stat()
    target = path.with_name(path.name + '.gz')
    if target.exists():
        return 0
    temporary = target.with_name(f'.{target.name}.{os.getpid()}.tmp')
    source_digest = hashlib.sha256()
    try:
        with path.open('rb') as source, temporary.open('xb') as raw:
            with gzip.GzipFile(filename=path.name, mode='wb', compresslevel=1, fileobj=raw, mtime=int(before.st_mtime)) as archive:
                while True:
                    block = source.read(1024 * 1024)
                    if not block:
                        break
                    source_digest.update(block)
                    archive.write(block)
            raw.flush()
            os.fsync(raw.fileno())
        recovered = hashlib.sha256()
        with gzip.open(temporary, 'rb') as archive:
            while True:
                block = archive.read(1024 * 1024)
                if not block:
                    break
                recovered.update(block)
        after = path.stat()
        if (after.st_size, after.st_mtime_ns, after.st_ino) != (before.st_size, before.st_mtime_ns, before.st_ino) or recovered.digest() != source_digest.digest():
            raise RuntimeError('backup changed or compressed copy failed verification')
        os.chmod(temporary, before.st_mode & 0o777)
        os.utime(temporary, ns=(before.st_atime_ns, before.st_mtime_ns))
        os.replace(temporary, target)
        record = {'at': time.time(), 'original': str(path), 'archive': str(target), 'original_bytes': before.st_size, 'archive_bytes': target.stat().st_size, 'sha256': source_digest.hexdigest(), 'verified_byte_identical': True}
        with manifest.open('a') as stream:
            stream.write(json.dumps(record) + '\n')
            stream.flush()
            os.fsync(stream.fileno())
        path.unlink()
        return before.st_size - target.stat().st_size
    finally:
        temporary.unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', required=True)
    parser.add_argument('--target-free-gib', type=float, default=16)
    parser.add_argument('--min-age-hours', type=float, default=24)
    args = parser.parse_args()
    os.nice(10)
    base = Path(args.base)
    manifest = base / 'data/reports/sql_backup_compression.jsonl'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    candidates = []
    for path in (base / 'backups').rglob('*.db'):
        if path.is_symlink():
            continue
        stat = path.stat()
        if stat.st_size >= 100_000_000 and time.time() - stat.st_mtime >= args.min_age_hours * 3600:
            candidates.append((stat.st_size, path))
    saved = 0
    for _, path in sorted(candidates, key=lambda item: item[0], reverse=True):
        if shutil.disk_usage(base).free >= args.target_free_gib * 1024 ** 3:
            break
        try:
            saved += compress_verified(path, manifest)
            print(json.dumps({'file': str(path), 'saved_bytes': saved, 'free_bytes': shutil.disk_usage(base).free}), flush=True)
        except OSError as exc:
            print(json.dumps({'file': str(path), 'error': str(exc)}), flush=True)
            break


if __name__ == '__main__':
    main()
