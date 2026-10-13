"""V17 SCOUT + ROUTE end-to-end on the REAL _spec_fill_workbook + the real CRYPTO_SHORT template, fake engine
(tests/v15_fake_engine_harness.py). Pins: (1) the masked entry path is found (filter stripped + path grafted),
(2) round-1 engine calls drop >= 4x vs SCOUT/ROUTE off, (3) the board stays publishable (no RULE#3 holes),
(4) every ROUTE promotion is an honest fresh eval (E-chain record consistent), (5) knowledge persisted."""
import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

try:
    import aiofiles  # noqa: F401  (config.py import chain)
    HAVE_DEPS = True
except Exception:
    HAVE_DEPS = False

pytestmark = pytest.mark.skipif(not HAVE_DEPS, reason="config deps missing (aiofiles)")

_RUN = {}


def _run(flag):
    if os.environ.get("V15_SKIP_SLOW") == "1":
        pytest.skip("slow end-to-end fill skipped (V15_SKIP_SLOW=1)")
    if flag not in _RUN:
        import v15_fake_engine_harness as H
        _RUN[flag] = H.run(flag, flag, workers=4)
    return _RUN[flag]


def _prog(r):
    p = next(Path(r["tmp"]).glob("*_v14_progress.json"))
    return json.loads(p.read_text())


def test_scout_route_finds_masked_path_and_is_cheaper():
    new = _run("1")
    old = _run("0")
    assert new["overrides"].get("RZ_BREAKOUT_ENTRY_ENABLED") is True
    assert new["overrides"].get("STRENGTH_FILTER_ENABLED") is False
    assert float(new["final_gain"]) > float(old["final_gain"]) + 1.0
    assert new["engine_calls"] * 4 < old["engine_calls"], (new["engine_calls"], old["engine_calls"])


def test_board_publishable_no_holes():
    from tools.v15_row_guards import incomplete_rows, scan_board_for_hollow
    prog = _prog(_run("1"))
    done = prog["done"]
    assert incomplete_rows(done) == []
    hollow = scan_board_for_hollow(done, assume_tablevel_on=False)
    assert hollow["drop"] == [], hollow["tally"]
    assert sum(1 for v in done.values() if v.get("scout_deferred_filters")) > 100


def test_route_records_honest_chain():
    prog = _prog(_run("1"))
    routes = [(k, v) for k, v in prog["done"].items() if k.startswith("ROUTE!")]
    assert routes
    for k, v in routes:
        assert v["promoted"] and abs((v["vec_gain"] - v["cumulative_before"]) - v["delta"]) < 1e-6
        assert v["cumulative_after"] == v["vec_gain"]
    assert prog["route"]["steps"]["S2_UNMASK"]["unmasked"] >= 1
    assert prog["endgame"].get("skipped", "").startswith("route round")


def test_knowledge_persisted_outside_progress_dir():
    r = _run("1")
    kd = Path(r["tmp"]) / "know"
    k = json.loads((kd / "CRYPTO_SHORT.json").read_text())
    assert k["boards"]["n"] == 1 and k["moves"]
    assert (kd / "ledger_CRYPTO_SHORT.jsonl").exists()


def test_scout_gate_pure():
    import v15_pilot as P
    assert P.scout_wants_yellows(False, "a", "b", False) is True       # binding naked -> yellows
    assert P.scout_wants_yellows(False, "a", "a", False) is False      # non-binding -> deferred
    assert P.scout_wants_yellows(True, "a", "a", False) is False       # running identity -> deferred
    assert P.scout_wants_yellows(True, "a", "a", True) is True         # sacred/essential/new exempt
    assert P.scout_wants_yellows(False, None, "a", False) is True      # unknown fingerprint: fail-open
    assert P.scout_wants_yellows(False, "a", "a", False, True) is True  # naked error: fail-open
    assert P.scout_effective_cap(24) == min(24, P._SCOUT_CAP) if P._SCOUT_ON else 24
