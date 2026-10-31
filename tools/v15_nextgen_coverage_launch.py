"""Bounded isolated coverage launcher; signals no other process and never changes gate budgets."""
import argparse
import datetime
import hashlib
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

MIB = 1 << 20
RAM_RESERVE = 3 << 30
DISK_RESERVE = 2 << 30
OUTPUT_CAP = 512 << 20
OUTPUT_STOP = 480 << 20
AS_CAP = 8 << 30
THREAD_ENV = ('OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS', 'NUMEXPR_NUM_THREADS', 'VECLIB_MAXIMUM_THREADS', 'BLIS_NUM_THREADS')


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def memory_available():
    return next(int(line.split()[1]) * 1024 for line in Path('/proc/meminfo').read_text().splitlines() if line.startswith('MemAvailable:'))


def disk_free(path):
    stat = os.statvfs(path)
    return stat.f_bavail * stat.f_frsize


def tree_bytes(path):
    if not path.exists():
        return 0
    total = 0
    for item in path.rglob('*'):
        if item.is_symlink():
            raise ValueError('coverage output contains a symlink')
        try:
            if item.is_file():
                total += item.stat().st_size
        except FileNotFoundError:
            pass
    return total


def group_rss(pgid):
    total, members = 0, []
    for path in Path('/proc').iterdir():
        if not path.name.isdigit():
            continue
        try:
            if os.getpgid(int(path.name)) != pgid:
                continue
            rss = next(int(line.split()[1]) * 1024 for line in (path / 'status').read_text().splitlines() if line.startswith('VmRSS:'))
            total += rss
            members.append(int(path.name))
        except (OSError, StopIteration, ProcessLookupError):
            pass
    return total, members


def write_new(path, value):
    with path.open('x') as stream:
        json.dump(value, stream, indent=2, sort_keys=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--runner', required=True)
    parser.add_argument('--runner-sha256', required=True)
    parser.add_argument('--side', required=True)
    parser.add_argument('--launch-dir', required=True)
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    runner, side, launch = Path(args.runner), Path(args.side), Path(args.launch_dir)
    if hashlib.sha256(runner.read_bytes()).hexdigest() != args.runner_sha256 or str(runner) not in command:
        raise ValueError('staged coverage runner hash/command mismatch')
    if side.exists() or launch.exists() or side.is_symlink() or launch.is_symlink():
        raise ValueError('fresh own side and launch receipt directories required')
    if '--side' not in command or command[command.index('--side') + 1] != str(side):
        raise ValueError('monitored side differs from actual command')
    if '--limit' not in command or not 0 < int(command[command.index('--limit') + 1]) <= 200:
        raise ValueError('explicit bounded batch of at most200 logical cells required')
    initial_memory, initial_disk = memory_available(), disk_free(side.parent)
    if initial_memory < RAM_RESERVE or initial_disk < DISK_RESERVE + OUTPUT_CAP:
        raise ValueError('isolated coverage launch lacks3GiB RAM or2GiB disk reserve plus512MiB bounded output')
    launch.mkdir(parents=True)
    stop_file = launch / 'cooperative_stop.json'
    def stop(reason, sample=None):
        if not stop_file.exists():
            write_new(stop_file, {'at_utc': utc(), 'reason': reason, 'sample': sample, 'action': 'stop new coverage scheduling; finish and persist inflight actual cells; no external process signals'})
    def requested_stop(signum, frame):
        stop('launcher signal requested cooperative checkpoint', {'signal': signum})
    signal.signal(signal.SIGTERM, requested_stop)
    signal.signal(signal.SIGINT, requested_stop)
    env = dict(os.environ)
    env.update({key: '1' for key in THREAD_ENV})
    env['V15_COVERAGE_STOP_FILE'] = str(stop_file)
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (AS_CAP, AS_CAP))
        os.nice(19)
    with (launch / 'driver.log').open('x') as log:
        process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True, preexec_fn=limits)
    pgid = os.getpgid(process.pid)
    budget = initial_memory - RAM_RESERVE
    receipt = {'at_utc': utc(), 'status': 'ISOLATED_COVERAGE_LAUNCHED', 'launcher_pid': os.getpid(), 'driver_pid': process.pid, 'driver_pgid': pgid, 'runner_sha256': args.runner_sha256, 'command': command, 'side': str(side), 'initial_memory_available_bytes': initial_memory, 'initial_disk_free_bytes': initial_disk, 'own_group_rss_budget_bytes': budget, 'ram_reserve_bytes': RAM_RESERVE, 'disk_reserve_bytes': DISK_RESERVE, 'new_output_cap_bytes': OUTPUT_CAP, 'cooperative_output_stop_bytes': OUTPUT_STOP, 'per_process_as_cap_bytes': AS_CAP, 'threads': 1, 'scope': 'New diagnostic coverage only; official gate7GB/20GB unchanged; no publication or promotion'}
    write_new(launch / 'launch_receipt.json', receipt)
    print(json.dumps(receipt), flush=True)
    peaks = {'group_rss_bytes': 0, 'new_output_bytes': 0}
    with (launch / 'resource_samples.jsonl').open('x') as journal:
        while True:
            rss, members = group_rss(pgid)
            output = tree_bytes(side) + tree_bytes(launch)
            sample = {'at_utc': utc(), 'memory_available_bytes': memory_available(), 'disk_free_bytes': disk_free(side.parent), 'own_group_rss_bytes': rss, 'own_members': members, 'new_output_bytes': output}
            peaks['group_rss_bytes'] = max(peaks['group_rss_bytes'], rss)
            peaks['new_output_bytes'] = max(peaks['new_output_bytes'], output)
            journal.write(json.dumps(sample) + '\n')
            journal.flush()
            if sample['memory_available_bytes'] < RAM_RESERVE or rss > budget:
                stop('memory reserve/budget reached', sample)
            if sample['disk_free_bytes'] < DISK_RESERVE + (32 << 20) or output >= OUTPUT_STOP:
                stop('disk/output checkpoint threshold reached', sample)
            if process.poll() is not None and not members:
                break
            time.sleep(2)
    final = dict(at_utc=utc(), status='DIAGNOSTIC_BATCH_TERMINAL', returncode=process.returncode, peaks=peaks, cooperative_stop_requested=stop_file.exists(), output_cap_exceeded=peaks['new_output_bytes'] > OUTPUT_CAP, scope='Partial completed cells only count when their actual worker proof persisted; no process count is coverage')
    write_new(launch / 'terminal_receipt.json', final)
    print(json.dumps(final), flush=True)
    return process.returncode or (1 if final['output_cap_exceeded'] else 0)


if __name__ == '__main__':
    raise SystemExit(main())
