#!/usr/bin/env python3
import argparse
import fcntl
import json
import os
import select
import shlex
import subprocess
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from closed_bar_feed import receive_closed_15m


def receive(base):
    for line in sys.stdin:
        result = {'ok': [], 'errors': []}
        try:
            for event in json.loads(line):
                try:
                    receipt = receive_closed_15m(base, event)
                    result['ok'].append([event['symbol'], event['bar_start_ts'], receipt['received_at']])
                except Exception as exc:
                    result['errors'].append([event.get('symbol'), str(exc)])
        except Exception as exc:
            result['errors'].append(['payload', str(exc)])
        print(json.dumps(result, separators=(',', ':')), flush=True)


def acknowledge_unchanged(path, sent_bytes):
    lock_path = path.with_suffix('.lock')
    with lock_path.open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            if path.read_bytes() != sent_bytes:
                return False
            path.unlink()
            return True
        except FileNotFoundError:
            return False


def send_lane(base, mode, host, remote):
    connection = None
    outbox = Path(base) / 'data' / 'closed_15m_outbox' / mode
    while True:
        try:
            if connection is None or connection.poll() is not None:
                command = f'cd {shlex.quote(remote)} && python3 -u tools/closed_15m_feed.py receive --base {shlex.quote(remote)}'
                connection = subprocess.Popen(['ssh', '-T', '-S', 'none', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=8', '-o', 'ServerAliveInterval=5', '-o', 'ServerAliveCountMax=2', host, command], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, bufsize=1)
            # 2026-10-27: newest bar first, capped batch. One batch of the whole 2,890-file backlog could not be acknowledged inside
            # the 15 s timeout, so the lane killed and resent it forever and every NEW close queued behind it.
            paths = sorted(outbox.glob('*.json'), key=lambda path: (-int(path.stem.rsplit('_', 1)[-1]) if path.stem.rsplit('_', 1)[-1].isdigit() else 0, path.name))[:int(os.environ.get('CLOSED_15M_BATCH', '250'))]
            if not paths:
                time.sleep(0.1)
                continue
            snapshots = [path.read_bytes() for path in paths]
            events = [json.loads(snapshot) for snapshot in snapshots]
            connection.stdin.write(json.dumps(events, separators=(',', ':')) + '\n')
            connection.stdin.flush()
            if not select.select([connection.stdout], [], [], 15)[0]:
                raise TimeoutError('receiver acknowledgement timed out')
            response = json.loads(connection.stdout.readline())
            accepted = {(symbol, int(start)) for symbol, start, _ in response['ok']}
            for path, event, snapshot in zip(paths, events, snapshots):
                if (event['symbol'], int(event['bar_start_ts'])) in accepted:
                    acknowledge_unchanged(path, snapshot)
            print(json.dumps({'mode': mode, 'host': host, 'sent_at': time.time(), **response}), flush=True)
            # 2026-10-27: 16 LOW bars refused on S5 ("missing history") rode in EVERY batch, and this sleep then held every other
            # stock bar ~2 s. Sleep only when nothing was accepted; a bar refused for > 6 h is parked in rejected/ (kept, never deleted).
            refused = {symbol for symbol, _ in response['errors']}
            for path, event in zip(paths, events):
                if event['symbol'] in refused and (event['symbol'], int(event['bar_start_ts'])) not in accepted and time.time() - int(event['bar_close_ts']) > 6 * 3600:
                    (outbox / 'rejected').mkdir(exist_ok=True)
                    os.replace(path, outbox / 'rejected' / path.name)
            if response['errors'] and not response['ok']:
                time.sleep(1)
        except Exception as exc:
            # 2026-10-27: an ENOSPC raised by THIS print killed the crypto lane thread at 14:37Z while the tradier thread kept the
            # process (and launchd KeepAlive) satisfied — S1 got no Mac bars for 3.5 h. Logging must never end a lane.
            try:
                print(json.dumps({'mode': mode, 'host': host, 'error': str(exc), 'at': time.time()}), flush=True)
            except Exception:
                pass
            if connection is not None:
                try:
                    connection.kill()
                    connection.wait()
                except OSError:
                    pass
                connection = None
            time.sleep(1)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=('send', 'receive'))
    parser.add_argument('--base', default=str(Path(__file__).resolve().parents[1]))
    args = parser.parse_args()
    if args.action == 'receive':
        receive(args.base)
        return
    lock = Path(args.base) / 'data' / '.closed_15m_sender.lock'
    lock.parent.mkdir(parents=True, exist_ok=True)
    with lock.open('a') as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        threads = []
        for mode, host in [('crypto', os.environ.get('CLOSED_15M_CRYPTO_HOST', 's1-pub')), ('tradier', os.environ.get('CLOSED_15M_STOCK_HOST', 's5'))]:
            worker = threading.Thread(target=send_lane, args=(args.base, mode, host, '/home/niels/binance-sandbox'))
            worker.start()
            threads.append(worker)
        # a lane that dies for ANY reason ends the process, so launchd (KeepAlive, ThrottleInterval 5) restarts every lane
        while all(worker.is_alive() for worker in threads):
            time.sleep(1)
        os._exit(1)


if __name__ == '__main__':
    main()
