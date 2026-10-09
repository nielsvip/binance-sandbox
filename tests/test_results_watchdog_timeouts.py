"""results-watchdog timeouts (USER 2026-10-09): slow-but-working pilots (25-90 min boards) must finish — stuck thresholds stay long. Guards against silent re-tightening."""

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _defaults():
    txt = (ROOT / "tools" / "v15_results_watchdog.sh").read_text()
    m = re.search(r"RESULT_WINDOW_MIN=\$\{V15_RESULTS_WINDOW_MIN:-(\d+)\}; PROGRESS_STALL_MIN=\$\{V15_PROGRESS_STALL_MIN:-(\d+)\}; SYM_STALL_MIN=\$\{V15_SYM_STALL_MIN:-(\d+)\}; BOOT_GRACE_MIN=\$\{V15_BOOT_GRACE_MIN:-(\d+)\}", txt)
    assert m, "defaults line not found"
    return int(m.group(1)), int(m.group(2)), int(m.group(3)), int(m.group(4))


def test_timeouts_long_enough_for_slow_pilots():
    window, prog, sym, boot = _defaults()
    assert window >= 120, window
    assert prog >= 60, prog
    assert sym >= 90, sym
    assert boot >= 20, boot
