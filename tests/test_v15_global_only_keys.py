"""GLOBAL_ONLY keys (USER 2026-10-13/14): PARITY_VEC_EXACT_MODE/_FAMILIES/_SET_DIR + AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED are
never rows, never in a board's cumulative/final set, stripped on every ingest (start / history / needs_redo) and progress write.
3 live per-sym rows carried PARITY_VEC_EXACT_MODE=False -> crypto sym_sides traded without vector authorization."""
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()
# USER 2026-10-15: PARITY_VEC_EXACT_MODE is NO LONGER global-only (a measured per-sym_side row in GLOBAL_RISK_GATES, False survives only if it won on delta);
# SIM_ACCOUNT / SIM_TRADING_ACCOUNT (settlement-parity lane) joined the set.
KEYS = ("PARITY_VEC_EXACT_FAMILIES", "PARITY_VEC_EXACT_SET_DIR", "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED", "SIM_ACCOUNT", "SIM_TRADING_ACCOUNT")
DIRTY = {"PARITY_VEC_EXACT_FAMILIES": "x", "PARITY_VEC_EXACT_SET_DIR": "/tmp", "AUGMENT_INTO_REENTRY_LIVE_SIZING_ENABLED": True, "SIM_ACCOUNT": "tra", "SIM_TRADING_ACCOUNT": "tra", "WT_EXIT_ENABLED": True}


def test_set_contains_all_four():
    assert all(k in P.GLOBAL_ONLY_KEYS for k in KEYS)
    assert set(P.GLOBAL_ONLY_KEYS) == set(KEYS)
    assert "PARITY_VEC_EXACT_MODE" not in P.GLOBAL_ONLY_KEYS
    assert P.strip_global_only({"PARITY_VEC_EXACT_MODE": False, "WT_EXIT_ENABLED": True}) == {"PARITY_VEC_EXACT_MODE": False, "WT_EXIT_ENABLED": True}


def test_sanitize_and_ingest_strip():
    s, warns = P.sanitize_overrides(dict(DIRTY), {})
    assert not any(k in s for k in KEYS) and set(KEYS) <= set(warns)
    c = P._clean_ingested_overrides(dict(DIRTY))
    assert not any(k in c for k in KEYS) and c.get("WT_EXIT_ENABLED") is True
    assert P.strip_global_only(dict(DIRTY)) == {"WT_EXIT_ENABLED": True}


def test_progress_write_strips(tmp_path):
    prog = {"done": {}, "cumulative_overrides": dict(DIRTY), "initial_overrides": dict(DIRTY), "needs_redo": {"overrides": dict(DIRTY), "depth": 1}}
    p = tmp_path / "X_LONG_v14_progress.json"
    P._atomic_write_json(p, prog)
    d = json.loads(p.read_text())
    for k in ("cumulative_overrides", "initial_overrides"):
        assert not any(x in d[k] for x in KEYS) and d[k]["WT_EXIT_ENABLED"] is True
    assert not any(x in d["needs_redo"]["overrides"] for x in KEYS)


def test_never_a_row_never_adopted_start_and_redo_stripped():
    assert "str(switch).strip() in GLOBAL_ONLY_KEYS" in SRC  # row -> skip NOT_IN_CONFIG
    assert 'if _k in GLOBAL_ONLY_KEYS:\n                return "GLOBAL_ONLY"' in SRC  # surgeon/combo/ablation adoption blocked
    assert "_so = strip_global_only(_so)" in SRC and '_so2 = strip_global_only(dict(_nr["overrides"]))' in SRC
