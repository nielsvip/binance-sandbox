"""2026-10-24 IP 418 ban (fin 219 throttle lines): sync_binance_time stamped
_last_time_sync ONLY on success, so every failure retried immediately — a
fail-loud loop hammering a banned IP and extending the ban. Both defs now
stamp _last_time_sync_fail on failure and stay quiet 600s. Presence pins:
if a future edit drops the quiet, this goes red before the IP does."""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _defs():
    src = (ROOT / "ez_manage.py").read_text()
    return [m.start() for m in re.finditer(r"def sync_binance_time\(self\):", src)]


def test_two_sync_defs_exist():
    assert len(_defs()) == 2


def test_both_defs_fail_quiet():
    src = (ROOT / "ez_manage.py").read_text()
    assert src.count("_last_time_sync_fail") >= 4
    for m in re.finditer(r"def sync_binance_time\(self\):", src):
        body = src[m.start():m.start() + 2500]
        assert "_last_time_sync_fail" in body
        assert "600.0" in body
