"""2026-10-17: a published-board refresh must not overwrite a NEWER measured h2h decision.

Measured that day. The hourly h2h cycle (tools/v15_golive_headtohead.py) re-measures the incumbent against the
latest published board on ONE NPZ and applies the winner with its MEASURED gain — 10 stock sets at 06:55Z,
read back and verified key-by-key by the applier, "applied 10, failed 0". Within the hour the published-board
refresh (tools/v15_persym_golive.py -> switch_parity.register_workbook_result) re-registered the SAME boards and
overwrote 2 of those 10 with the cat_side snapshot + coerced keys, carrying the board's CLAIMED gain:

    PBF_LONG     h2h measured 14.9874  ->  row became 12.348
    PLTR_SHORT   h2h measured  4.0914  ->  row became the 4.6633 CLAIM, 2 keys off the approved set

Both writers reported success, so nothing alerted. That is the NO-LIES failure reappearing in the gap between the
decision and the next writer.

The fix is deliberately narrow, because the obvious fix was wrong: refusing every n_promoted=0 registration would
break go-live outright — 418 of 551 live rows are legitimately n_promoted=0. The trigger is AUTHORITY and AGE, not
promotion count: skip only when the live row IS an h2h decision AND it is newer than this board's publish stamp.
Pure function test against a stubbed per_sym_store: no DB, no network, no live mutation.
"""
from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _load(store_rows):
    """Load v15_persym_golive with per_sym_store stubbed to `store_rows`."""
    stub = types.ModuleType("per_sym_store")
    stub.get = lambda ss: store_rows.get(ss)
    sys.modules["per_sym_store"] = stub
    spec = importlib.util.spec_from_file_location("_golive_uut", ROOT / "tools" / "v15_persym_golive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


H2H = {"source": "v15_golive_headtohead", "measured_at": "2026-10-17T17:55:47Z",
       "updated_at": "2026-10-17T17:57:11Z", "gain_30d": 78.5717}
PARITY = {"source": "switch_parity workbook-end PBF_LONG n_promoted=0",
          "updated_at": "2026-10-17T19:06:45Z", "gain_30d": 12.348}

OLDER_PUBLISH = "2026-10-16T02:13:26Z"   # the board h2h already measured
NEWER_PUBLISH = "2026-10-18T09:00:00Z"   # a board published AFTER the measurement


def test_a_stale_publish_cannot_overwrite_a_newer_measured_decision():
    """The actual regression: this is what silently reverted PBF_LONG and PLTR_SHORT."""
    m = _load({"X_LONG": dict(H2H)})
    why = m._measured_decision_newer("X_LONG", OLDER_PUBLISH)
    assert why, "a publish older than the measurement must not overwrite it"
    assert "measured beats claimed" in why and "78.5717" in why, f"the skip reason must name the evidence: {why}"


def test_a_genuinely_newer_board_still_goes_live():
    """The guard must not freeze go-live: a board published AFTER the measurement is new information."""
    m = _load({"X_LONG": dict(H2H)})
    assert m._measured_decision_newer("X_LONG", NEWER_PUBLISH) == ""


def test_the_n_promoted_zero_path_is_untouched():
    """418 of 551 live rows are n_promoted=0 switch_parity rows. Gating on promotion count would break go-live,
    so a non-h2h incumbent must always proceed however it was written."""
    m = _load({"X_LONG": dict(PARITY)})
    assert m._measured_decision_newer("X_LONG", OLDER_PUBLISH) == ""


def test_unknown_publish_age_keeps_the_measured_row():
    """An unknown age cannot be SHOWN to be newer than a measurement, so the measured set stands."""
    m = _load({"X_LONG": dict(H2H)})
    assert m._measured_decision_newer("X_LONG", None) != ""


@pytest.mark.parametrize("rows", [{}, {"X_LONG": {}}, {"X_LONG": {"source": "v15_golive_headtohead"}}])
def test_missing_or_incomplete_rows_never_block_the_refresh(rows):
    """Fail-OPEN on anything unreadable: no row, no source, no timestamp -> normal refresh proceeds.
    A guard that blocks on missing data would stop go-live for every new sym_side."""
    m = _load(rows)
    assert m._measured_decision_newer("X_LONG", OLDER_PUBLISH) == ""


def test_an_unreadable_store_never_blocks_the_refresh():
    stub = types.ModuleType("per_sym_store")

    def _boom(_ss):
        raise RuntimeError("db locked")

    stub.get = _boom
    sys.modules["per_sym_store"] = stub
    spec = importlib.util.spec_from_file_location("_golive_uut2", ROOT / "tools" / "v15_persym_golive.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod._measured_decision_newer("X_LONG", OLDER_PUBLISH) == ""


@pytest.mark.parametrize("value,expected", [
    ("2026-10-17T17:55:47Z", 1792259747.0), (1792000000.0, 1792000000.0),
    (None, None), ("garbage", None), ("", None),
])
def test_timestamp_parsing(value, expected):
    m = _load({})
    assert m._ts_epoch(value) == expected


def test_the_guard_is_wired_into_the_loop_before_any_registration():
    """A helper nothing calls protects nothing — pin the call site and its order."""
    src = (ROOT / "tools" / "v15_persym_golive.py").read_text()
    assert "_measured_decision_newer(ss, rec.get(\"published_at\"))" in src, "guard not called in the loop"
    assert src.index("_measured_decision_newer(ss,") < src.index("sp.register_workbook_result"), \
        "the guard must run BEFORE the set is registered, or it cannot prevent the overwrite"
