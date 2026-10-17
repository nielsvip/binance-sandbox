"""Repo-test bypass for the NPZ freshness gate (tools/opt/v12_pilot.npz_freshness).

Unit tests run against fixture/frozen NPZs that are stale by design — the
STALE_NPZ_SKIPPED gate must not fail them. Production default (env unset) is
enforce; this file only affects pytest runs.

Cross-test pollution guard: several suites build a throwaway fixture root with
tiny config.py / config_tradier.py / v12_quick_engine.py stand-ins and let the
code under test import them by name. The import caches those fixture modules in
sys.modules for the rest of the session, so a later suite that imports the same
name (switch_parity._venue_values -> v12_quick_engine) silently gets the fixture
instead of the real repo module. Evicting them after each test keeps every suite
reading the real repo modules, whether it runs alone or with others.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")

REPO_ROOT = Path(__file__).resolve().parent.parent
TMP_ROOT = Path(tempfile.gettempdir()).resolve()


def _under_tmp(path_text):
    if not path_text:
        return False
    try:
        return TMP_ROOT in Path(path_text).resolve().parents
    except (OSError, ValueError, RuntimeError):
        return False


def _shadowing_fixture_modules():
    names = []
    for name, module in list(sys.modules.items()):
        if not _under_tmp(getattr(module, "__file__", None)):
            continue
        if (REPO_ROOT / f"{name.split('.')[0]}.py").exists():
            names.append(name)
    return names


@pytest.fixture(autouse=True)
def _evict_tmp_fixture_modules():
    yield
    for name in _shadowing_fixture_modules():
        sys.modules.pop(name, None)
    for entry in [e for e in sys.path if _under_tmp(e)]:
        while entry in sys.path:
            sys.path.remove(entry)
