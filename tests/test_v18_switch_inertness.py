"""V18 switch inertness + wiring coverage (tests update 2026-10-20, doc 19_TESTS_V18_UPDATE).

With every V18_* switch OFF (the code default) the planner helpers must be inert and the pilot must reference every planner accessor.
Import-level and source-level only: nothing here runs the pilot, an engine or touches a board.
"""
import inspect
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools import v15_v18_planner as P  # noqa: E402

PILOT_SRC = (ROOT / "v15_pilot.py").read_text()
PLAN_SRC = (ROOT / "tools" / "v15_v18_planner.py").read_text()

# phase / feature master switches the pilot reads through _V18P.flag_on("...")
PILOT_MASTERS = ("V18_BRAIN", "V18_WINNERS", "V18_EXIT_REPAIR", "V18_ABLATION", "V18_FLOOR", "V18_C_BARE_VALUES", "V18_SET_SHA", "V18_COMPACT_KEYS", "V18_MEM_FREE", "V18_MUST_ORDER")  # V18_CATCHUP is read via os.environ at import (module flag), not flag_on  # 2026-10-20: C_BARE (regression #3 fix) declared by the pilot owner
# planner accessors that wrap one master switch each: accessor -> switch
PLANNER_ACCESSORS = {
    "identity_settle_on": "V18_IDENTITY_SETTLE",
    "io_throttle_on": "V18_IO_THROTTLE",
    "ledger_writeback_on": "V18_LEDGER_WRITEBACK",
    "floor_on": "V18_FLOOR",
}


@pytest.fixture()
def v18_off(monkeypatch):
    import os

    for k in list(os.environ):
        if k.startswith("V18_"):
            monkeypatch.delenv(k, raising=False)
    return monkeypatch


def test_every_no_arg_on_accessor_is_default_off_and_follows_its_switch(v18_off):
    """Discovers every `*_on()` accessor of the planner (new ones included): False unset, True only for its own '=1'."""
    found = {}
    for name, fn in inspect.getmembers(P, inspect.isfunction):
        if not name.endswith("_on") or name == "flag_on" or inspect.signature(fn).parameters:
            continue
        m = re.search(r'flag_on\("(V18_[A-Z0-9_]+)"\)', inspect.getsource(fn))
        assert m, f"{name} must wrap exactly one V18_* switch via flag_on"
        found[name] = m.group(1)
    assert set(PLANNER_ACCESSORS) <= set(found), "a known V18 accessor disappeared"
    for name, switch in found.items():
        fn = getattr(P, name)
        assert fn() is False, f"{name} must be OFF with {switch} unset"
        v18_off.setenv(switch, "1")
        assert fn() is True, f"{name} must follow {switch}=1"
        v18_off.setenv(switch, "0")
        assert fn() is False, f"{name} must stay OFF with {switch}=0"
        v18_off.delenv(switch)
    for name, switch in PLANNER_ACCESSORS.items():
        assert found[name] == switch


def test_flag_on_accepts_only_exact_one(v18_off):
    for val in ("", "0", "true", "yes", "on", "2"):
        v18_off.setenv("V18_TEST_PROBE_SWITCH", val)
        assert P.flag_on("V18_TEST_PROBE_SWITCH") is False
    v18_off.setenv("V18_TEST_PROBE_SWITCH", "1")
    assert P.flag_on("V18_TEST_PROBE_SWITCH") is True
    v18_off.delenv("V18_TEST_PROBE_SWITCH")
    assert P.flag_on("V18_TEST_PROBE_SWITCH") is False


def test_helpers_are_inert_with_everything_off(v18_off):
    # io throttle OFF = the periodic workbook save is never held back, whatever the position in the fill
    assert P.xlsx_save_allowed(False, False) is True and P.xlsx_save_allowed(False, True) is True
    # throttle ON narrows it to tab boundaries only
    assert P.xlsx_save_allowed(True, False) is False and P.xlsx_save_allowed(True, True) is True
    # numeric defaults are the documented ones (floor 5/1000, untried 50/1000) and stay probabilities
    assert P.floor_p() == pytest.approx(0.005)
    assert P.untried_p() == pytest.approx(0.05)
    assert 0.0 <= P.floor_draw("CRYPTO_LONG", "AAAUSDT_LONG", "TAB", "SW=1", "r0") < 1.0
    assert P.floor_draw("CRYPTO_LONG", "AAAUSDT_LONG", "TAB", "SW=1", "r0") == P.floor_draw("CRYPTO_LONG", "AAAUSDT_LONG", "TAB", "SW=1", "r0")
    # a hostile value falls back to the default, never raises
    v18_off.setenv("V18_FLOOR_PER_1000", "not-a-number")
    assert P.floor_p() == pytest.approx(0.005)
    v18_off.setenv("V18_UNTRIED_PER_1000", "not-a-number")
    assert P.untried_p() == pytest.approx(0.05)


def test_floor_apply_never_touches_a_structural_skip(v18_off):
    info = {"kind": "skip", "reason": "NO_CANDIDATE: structural", "hdrs": ["A=1"]}
    before = dict(info)
    st = P.floor_apply(info, lambda scope, hdr: True, 1.0)
    assert info == before, "floor must leave a structurally skipped row untouched even at p=1"
    assert st["rows_seen"] == 0 and st["rows_rescued"] == 0


def test_planner_identity_book_is_pure_import():
    """Constructing the book and hashing a set has no side effects (no file, no env read needed)."""
    b = P.IdentityBook()
    assert P.set_sha256({"A": 1, "B": 2}) == P.set_sha256({"B": 2, "A": 1})
    assert P.set_sha256({"A": 1}) != P.set_sha256({"A": 2})
    assert b is not None


def test_pilot_reads_every_master_switch_through_the_planner():
    for sw in PILOT_MASTERS:
        assert f'"{sw}"' in PILOT_SRC, f"{sw} missing from v15_pilot.py"
    # V18_FLOOR is the one switch named in BOTH files
    assert '"V18_FLOOR"' in PLAN_SRC and '"V18_FLOOR"' in PILOT_SRC
    # no master switch is ever defaulted ON in code (the S2 version gate injects defaults through data/v15_gate_flags.json)
    for sw in PILOT_MASTERS + tuple(PLANNER_ACCESSORS.values()):
        assert not re.search(rf'["\']{sw}["\']\s*,\s*["\']1["\']', PILOT_SRC + PLAN_SRC), f"{sw} must not default ON in code"
    assert '"V18_"' in PILOT_SRC or "V18_" in PILOT_SRC
    assert 'startswith(("V15_", "V18_"))' in PILOT_SRC, "gate-flag injection must cover V18_* keys"


def test_pilot_references_every_planner_accessor_and_each_switch_name_is_declared():
    for name, switch in PLANNER_ACCESSORS.items():
        assert f"_V18P.{name}" in PILOT_SRC, f"pilot never calls planner accessor {name}"
        assert f'flag_on("{switch}")' in PLAN_SRC, f"planner lost {switch}"
    # fail-open contract: a missing planner module means every V18 feature is OFF
    assert "_V18P = None" in PILOT_SRC
    # every V18_* literal the pilot passes to flag_on exists as a name the planner/doc layer knows, or is a documented pilot-side switch
    pilot_flags = set(re.findall(r'_V18P\.flag_on\("(V18_[A-Z0-9_]+)"\)', PILOT_SRC))
    assert pilot_flags <= set(PILOT_MASTERS) | set(PLANNER_ACCESSORS.values()) | {"V18_BRAIN"}, f"undeclared pilot-side V18 switch(es): {sorted(pilot_flags - set(PILOT_MASTERS) - set(PLANNER_ACCESSORS.values()))}"
