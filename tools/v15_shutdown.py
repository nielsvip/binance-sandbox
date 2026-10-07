"""Shared graceful-shutdown signal for v15 sweep pilots (USER 2026-10-07: THROTTLE instead of OOM, no double calc).

SIGTERM/SIGINT sets a flag in the pilot; compute loops (fill rows, diagnose chunks, DONE waits)
raise V15Shutdown at the next cell/chunk boundary AFTER persisting progress, so a scheduler
TERM-then-KILL reap loses <60s of work and never corrupts the workbook (atomic tmp+rename).
BaseException (like KeyboardInterrupt) so broad `except Exception` handlers cannot swallow it.
"""


class V15Shutdown(BaseException):
    pass
