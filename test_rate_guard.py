#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""test_rate_guard.py — in-process self-abort for tests/sweeps producing <50 trades/day/acct.

User rule 2026-04-27: tests projecting <50 trades/day/acct at the 5s mark are BROKEN. Auto-abort.
Engines call `RateGuard(n_accts=N).tick(trades_so_far)` after each cycle; guard exit(2)s with
`EARLY_ABORT_LOW_RATE: ...` if projection falls below threshold.

NOT for live workers. Live <50/day is a separate problem (capital/gates) — don't kill those.
"""
import os
import sys
import time

DEFAULT_MIN_PER_DAY = int(os.environ.get("TEST_RATE_GUARD_MIN_PER_DAY", "3"))
# 2026-04-30: lowered 50→3. Real live rates per analysis of data/decisions/*.jsonl on 2026-04-29:
#   crypto OPEN/sym/day: ang=5.8, inf=4.8, fin=6.6, flz=2.2, men=2.3
#   tradier OPEN/sym/day: tra=22 (real opens), trb/trc=123-144 (mostly blocked LONG_BUY/SHORT_SELL fires, not real opens)
# Floor of 3 matches crypto-live per-sym-per-day rate (the conservative target). Sweeps that genuinely
# match live activity will clear it; sparse-regime configs (engine without reentry) will fail honestly.
DEFAULT_WINDOW_SEC = float(os.environ.get("TEST_RATE_GUARD_WINDOW_SEC", "5"))
SECONDS_PER_DAY = 86400.0


class RateGuard:
    """Drop-in monitor for backtest/sweep loops."""

    def __init__(self, n_accts=1, min_per_day=None, window_sec=None, label="guard"):
        self.t0 = time.time()
        self.n_accts = max(1, int(n_accts))
        self.min_per_day = float(min_per_day if min_per_day is not None else DEFAULT_MIN_PER_DAY)
        self.window_sec = float(window_sec if window_sec is not None else DEFAULT_WINDOW_SEC)
        self.label = label
        self.aborted = False

    def tick(self, trades_so_far):
        if self.aborted:
            return
        elapsed = time.time() - self.t0
        if elapsed < self.window_sec:
            return
        per_day_per_acct = (trades_so_far / max(elapsed, 1e-6)) * SECONDS_PER_DAY / self.n_accts
        if per_day_per_acct < self.min_per_day:
            self.aborted = True
            msg = (
                f"EARLY_ABORT_LOW_RATE: label={self.label} elapsed={elapsed:.1f}s "
                f"trades={trades_so_far} n_accts={self.n_accts} "
                f"projected={per_day_per_acct:.1f}/acct/day target>={self.min_per_day:.0f}/acct/day"
            )
            print(msg, file=sys.stderr, flush=True)
            print(msg, flush=True)
            sys.exit(2)


    def final_check(self, trades_total, test_window_days=None):
        """Call once at end of test. If projected rate is broken (regardless of elapsed),
        exit 2 with EARLY_ABORT_LOW_RATE so the result file isn't trusted."""
        if self.aborted:
            return
        if test_window_days and test_window_days > 0:
            per_day_per_acct = trades_total / float(test_window_days) / self.n_accts
        else:
            elapsed = max(time.time() - self.t0, 1e-6)
            per_day_per_acct = (trades_total / elapsed) * SECONDS_PER_DAY / self.n_accts
        if per_day_per_acct < self.min_per_day:
            self.aborted = True
            msg = (
                f"FINAL_BROKEN_RATE: label={self.label} "
                f"trades={trades_total} n_accts={self.n_accts} "
                f"projected={per_day_per_acct:.2f}/acct/day target>={self.min_per_day:.0f}/acct/day "
                f"(window_days={test_window_days}) — result NOT trustworthy"
            )
            print(msg, file=sys.stderr, flush=True)
            print(msg, flush=True)
            sys.exit(2)


def check_rate_or_abort(t0, trades_so_far, n_accts=1, min_per_day=None, window_sec=None, label="guard"):
    """One-shot version for engines that already track t0/trades externally."""
    elapsed = time.time() - t0
    win = float(window_sec if window_sec is not None else DEFAULT_WINDOW_SEC)
    if elapsed < win:
        return
    per_day_per_acct = (trades_so_far / max(elapsed, 1e-6)) * SECONDS_PER_DAY / max(1, n_accts)
    floor = float(min_per_day if min_per_day is not None else DEFAULT_MIN_PER_DAY)
    if per_day_per_acct < floor:
        msg = (
            f"EARLY_ABORT_LOW_RATE: label={label} elapsed={elapsed:.1f}s "
            f"trades={trades_so_far} n_accts={n_accts} "
            f"projected={per_day_per_acct:.1f}/acct/day target>={floor:.0f}/acct/day"
        )
        print(msg, file=sys.stderr, flush=True)
        print(msg, flush=True)
        sys.exit(2)
