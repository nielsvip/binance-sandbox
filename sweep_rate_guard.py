#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""sweep_rate_guard.py — external wrapper that kills sweeps producing <50 trades/day/acct at 5s.

Usage:
  python sweep_rate_guard.py --min-rate-per-acct-per-day 50 --window-sec 5 --n-accts 50 \
      --regex 'trades?[=:\s]+(\d+)' \
      -- python v8_quick_engine.py --config foo.py

Tails inner stdout+stderr. Counts max trade count seen. At t=window_sec, projects per-day-per-acct.
If below threshold → SIGTERM (then SIGKILL after 2s) and exit 2.

Engines that already self-abort via test_rate_guard.RateGuard don't need this wrapper, but it's
harmless to use both.
"""
import argparse
import re
import signal
import subprocess
import sys
import threading
import time

SECONDS_PER_DAY = 86400.0


def _stream_reader(stream, sink, regex, state, lock):
    pat = re.compile(regex) if regex else None
    for line in iter(stream.readline, b""):
        try:
            text = line.decode("utf-8", errors="replace")
        except Exception:
            continue
        sink.write(text)
        sink.flush()
        if pat:
            for m in pat.finditer(text):
                try:
                    n = int(m.group(1))
                except (ValueError, IndexError):
                    continue
                with lock:
                    if n > state["max_trades"]:
                        state["max_trades"] = n


def _kill_tree(proc):
    try:
        proc.terminate()
    except Exception:
        pass
    for _ in range(20):
        if proc.poll() is not None:
            return
        time.sleep(0.1)
    try:
        proc.kill()
    except Exception:
        pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-rate-per-acct-per-day", type=float, default=50.0)
    ap.add_argument("--window-sec", type=float, default=5.0)
    ap.add_argument("--n-accts", type=int, default=1)
    ap.add_argument("--regex", default=r"trades?[=:\s]+(\d+)")
    ap.add_argument("--label", default="sweep_guard")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    args = ap.parse_args()
    if not args.cmd:
        print("ERROR: pass `-- <command...>` to wrap", file=sys.stderr)
        sys.exit(64)
    cmd = args.cmd[1:] if args.cmd and args.cmd[0] == "--" else args.cmd
    state = {"max_trades": 0}
    lock = threading.Lock()
    t0 = time.time()
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, bufsize=0)
    threads = [
        threading.Thread(target=_stream_reader, args=(proc.stdout, sys.stdout, args.regex, state, lock), daemon=True),
        threading.Thread(target=_stream_reader, args=(proc.stderr, sys.stderr, args.regex, state, lock), daemon=True),
    ]
    for t in threads:
        t.start()
    aborted = False
    while True:
        rc = proc.poll()
        if rc is not None:
            for t in threads:
                t.join(timeout=1)
            sys.exit(rc)
        elapsed = time.time() - t0
        if elapsed >= args.window_sec and not aborted:
            with lock:
                trades = state["max_trades"]
            per_day_per_acct = (trades / max(elapsed, 1e-6)) * SECONDS_PER_DAY / max(1, args.n_accts)
            if per_day_per_acct < args.min_rate_per_acct_per_day:
                msg = (
                    f"EARLY_ABORT_LOW_RATE: label={args.label} elapsed={elapsed:.1f}s "
                    f"trades={trades} n_accts={args.n_accts} "
                    f"projected={per_day_per_acct:.1f}/acct/day target>={args.min_rate_per_acct_per_day:.0f}/acct/day "
                    f"-- killing inner pid={proc.pid}"
                )
                print(msg, file=sys.stderr, flush=True)
                _kill_tree(proc)
                aborted = True
                sys.exit(2)
        time.sleep(0.25)


if __name__ == "__main__":
    main()
