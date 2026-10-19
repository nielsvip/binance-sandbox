"""Stock-NPZ pull must survive the Mac's default rsync (USER 2026-10-19: zero vec entries all day).

2026-10-19 outage: tools/stock_npz_pull.sh called bare `rsync --ignore-missing-args ...`.
/usr/bin/rsync on the Mac is openrsync (no such flag) under cron/launchd PATH, so EVERY
pull since 2026-10-17 failed rc=1 in 0 s, 160/178 stock NPZs froze at Friday's close,
the stocks VEC_EXACT twin fell back to native on every bar, and STRICT_VEC_PARITY
blocked every native entry (221 VEC_EXACT_STALE_FALLBACK, 1 filled open all day).

Pins: no rsync invocation in the script may pass --ignore-missing-args unless the
binary was capability-probed first. Offline/static: no network, no schedule.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "tools" / "stock_npz_pull.sh"
BACKUP = ROOT / "backups" / "before_stock_npz_pull_rsync_pin_20261019.sh"

FLAG = "--ignore-missing-args"


def _rsync_invocations(text: str) -> list[str]:
    """Logical shell lines that invoke rsync (join backslash continuations)."""
    joined = re.sub(r"\\\n", " ", text)
    return [ln.strip() for ln in joined.splitlines()
            if re.match(r"(^|\s)(rsync|\"\$RSYNC_BIN\"|\$RSYNC_BIN)\b", ln.strip())
            and ln.strip() and not ln.strip().startswith("#")]


def test_no_uncapable_rsync_flag():
    src = SCRIPT.read_text()
    assert "RSYNC_BIN" in src, "script must select its rsync binary explicitly"
    assert FLAG in src, "script still relies on --ignore-missing-args semantics"
    # The flag must be gated on a capability probe of the chosen binary.
    assert re.search(r"--help.*grep.*--ignore-missing-args|--ignore-missing-args.*--help",
                     src.replace("\\\n", " ")), \
        "script must probe the rsync binary for --ignore-missing-args support"
    for ln in _rsync_invocations(src):
        if FLAG in ln:
            assert "RSYNC_BIN" in ln, \
                f"flag passed to an unprobed rsync binary: {ln[:120]}"
        else:
            assert ln.lstrip().startswith(('"', "$")) or "RSYNC_BIN" in ln, \
                f"bare `rsync` invocation bypasses the pinned binary: {ln[:120]}"


def test_backup_proves_the_bug():
    """The pre-fix script must FAIL this contract (authentic failure, not a tautology)."""
    assert BACKUP.exists(), "pre-fix backup is the failing fixture"
    src = BACKUP.read_text()
    bare = [ln for ln in _rsync_invocations(src) if FLAG in ln and "RSYNC_BIN" not in ln]
    assert bare, "pre-fix script should contain the unprobed rsync invocation"
