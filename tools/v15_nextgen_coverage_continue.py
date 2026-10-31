"""Continue source-pinned200-cell diagnostic batches until owned deadline or resource checkpoint."""
import argparse
import datetime
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import subprocess
import time
from v15_nextgen_coverage_launch import RAM_RESERVE, OUTPUT_STOP, disk_free, memory_available, tree_bytes


def utc():
    return datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')


def idle_cores():
    def sample():
        values = list(map(int, Path('/proc/stat').read_text().splitlines()[0].split()[1:]))
        return sum(values), values[3] + values[4]
    before = sample()
    time.sleep(1)
    after = sample()
    return os.cpu_count() * (after[1] - before[1]) / max(1, after[0] - before[0])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--parent', required=True)
    parser.add_argument('--host', choices=('s1', 's2', 's5', 's6'), required=True)
    parser.add_argument('--until', required=True)
    args = parser.parse_args()
    parent, host = Path(args.parent), args.host
    deadline = datetime.datetime.fromisoformat(args.until.replace('Z', '+00:00')).timestamp()
    control = parent / ('fullcell_controller_' + host)
    control.mkdir(exist_ok=True)
    lock = (control / 'controller.lock').open('a')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    database = sqlite3.connect(control / 'unique_actual_trials.sqlite')
    database.execute('CREATE TABLE IF NOT EXISTS trials(id TEXT PRIMARY KEY,cell TEXT,source TEXT)')
    indexed = set()
    with (control / 'events.jsonl').open('a') as journal:
        def event(status, **values):
            journal.write(json.dumps(dict(at_utc=utc(), host=host, status=status, **values)) + '\n')
            journal.flush()
        while time.time() < deadline:
            entries = sorted(parent.glob('fullcell_entry_' + host + '_[0-9][0-9].json'))
            if not entries:
                raise ValueError('requires an independently launched source-pinned initial batch')
            records = [json.loads(path.read_text()) for path in entries]
            total_output = tree_bytes(control) + sum(tree_bytes(Path(item['side'])) + tree_bytes(Path(item['launch'])) + path.stat().st_size + (Path(item['log']).stat().st_size if Path(item['log']).exists() else 0) for path, item in zip(entries, records))
            latest, latest_path = records[-1], entries[-1]
            launch, side = Path(latest['launch']), Path(latest['side'])
            stop = control / 'stop_requested.json'
            if stop.exists() or total_output >= OUTPUT_STOP:
                request = launch / 'cooperative_stop.json'
                if not request.exists():
                    with request.open('x') as stream:
                        json.dump(dict(at_utc=utc(), reason='owned campaign stop or aggregate per-host output budget', total_output_bytes=total_output), stream)
                event('STOP_NEW_SCHEDULING_REQUESTED', output_bytes=total_output)
                break
            for item in records:
                attempts = Path(item['side']) / 'coverage/cell_attempts.jsonl'
                terminal = Path(item['launch']) / 'terminal_receipt.json'
                if str(attempts) in indexed or not terminal.exists() or not attempts.exists():
                    continue
                for line in attempts.read_text().splitlines():
                    value = json.loads(line)
                    if value.get('proven_actual_result'):
                        database.execute('INSERT OR IGNORE INTO trials VALUES(?,?,?)', (value['trial_id'], value['category_cell_key'], str(attempts)))
                database.commit()
                indexed.add(str(attempts))
            terminal = launch / 'terminal_receipt.json'
            if not terminal.exists():
                time.sleep(2)
                continue
            result = json.loads(terminal.read_text())
            if result['returncode'] or result['cooperative_stop_requested'] or result['output_cap_exceeded']:
                event('TERMINAL_REQUIRES_REVIEW', batch=str(latest_path), terminal=result)
                break
            progress = json.loads((side / 'coverage/progress.json').read_text())['counts']
            if progress.get('deferred_budget_boundary_not_executed'):
                event('PARTIAL_BOUNDARY_REQUIRES_EXPLICIT_RESUME_NOT_REPLAY', batch=str(latest_path))
                break
            memory = memory_available()
            prior_peak = result['peaks']['group_rss_bytes']
            if memory < RAM_RESERVE + max(512 << 20, math.ceil(prior_peak * 1.25)) or disk_free(parent) < (2 << 30) + (512 << 20):
                event('WAIT_RESOURCE_RESERVE', memory_available_bytes=memory, output_bytes=total_output)
                time.sleep(5)
                continue
            idle = idle_cores()
            workers = max(1, min({'s1': 5, 's2': 9, 's5': 10, 's6': 8}[host], math.floor(idle) - 1, math.floor((memory - RAM_RESERVE) / (200 << 20)) - 1))
            number = int(latest_path.stem.rsplit('_', 1)[1]) + 1
            if number > 99 or deadline - time.time() < 15:
                break
            label = '%02d' % number
            new_side = parent / ('fullcell_side_' + host + '_' + label)
            new_launch = parent / ('fullcell_launch_' + host + '_' + label)
            new_entry = parent / ('fullcell_entry_' + host + '_' + label + '.json')
            log = parent / ('fullcell_entry_' + host + '_' + label + '.log')
            if any(path.exists() for path in (new_side, new_launch, new_entry, log)):
                raise ValueError('refusing duplicate campaign path')
            command = [value.replace(str(side), str(new_side)).replace(str(launch), str(new_launch)) for value in latest['command']]
            command[command.index('--workers') + 1] = str(workers)
            command[command.index('--start-index') + 1] = str(progress['last_catalog_ordinal'] + 1)
            command[command.index('--max-seconds') + 1] = str(min(600, int(deadline - time.time())))
            with log.open('x') as stream:
                process = subprocess.Popen(command, stdout=stream, stderr=subprocess.STDOUT, start_new_session=True, stdin=subprocess.DEVNULL, close_fds=True)
            receipt = dict(at_utc=utc(), host=host, wrapper_pid=process.pid, wrapper_pgid=os.getpgid(process.pid), workers=workers, start_index=progress['last_catalog_ordinal'] + 1, side=str(new_side), launch=str(new_launch), log=str(log), command=command, status='CONTINUATION_STARTED_NOT_COMPLETED_CELL_CLAIM', controller_source_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
            with new_entry.open('x') as stream:
                json.dump(receipt, stream, indent=2)
            event('NEXT_BOUNDED_BATCH_LAUNCHED', entry=str(new_entry), workers=workers, idle_cores_sample=idle, distinct_proven_trials=database.execute('SELECT count(*) FROM trials').fetchone()[0], output_bytes=total_output)
        event('CONTROLLER_EXIT_NO_EXTERNAL_PROCESS_SIGNAL', distinct_proven_trials=database.execute('SELECT count(*) FROM trials').fetchone()[0])
    database.close()


if __name__ == '__main__':
    main()
