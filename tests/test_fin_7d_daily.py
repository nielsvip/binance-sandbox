"""The fin 7D driver publishes only what it can honestly call tradeable (USER 2026-10-31).

fin is the 7D arm of the fin-vs-men comparison, so a set it trades must really be a fresh 7D set. The driver therefore
separates `validated` (v15_preopen_7d_stage.validate_one passed) from `vector_only` (everything passed except the
pilot's live/vector parity record, which it never writes) and keeps the second out of `sides{}` unless the operator
opts in. Nothing may be labelled more verified than it is.
"""
import importlib.util
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fin7d", ROOT / "tools" / "fin_7d_daily.py")
F = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = F
spec.loader.exec_module(F)

GOOD_PROGRESS = {"window_days": 7, "result_done_utc": "2026-10-31T01:00:00Z", "final_path": "/x.xlsx",
                 "publish_class": "FULL", "not_compliant": None, "unprotected": None,
                 "source_30d_sha256": "abc", "live_verified": {"parity": [True]},
                 "baseline_gain": 3.0, "cumulative_gain": 7.8621926202429995}


def result(valid=True, rc=0, why=None, progress=None):
    return {"verdict": {"valid": valid, "rc": rc, "why": why}, "progress": dict(progress or GOOD_PROGRESS), "host": "s6"}


def test_a_fully_validated_board_is_tradeable():
    assert F.classify(result())[0] == "validated"


def test_a_board_missing_only_the_live_parity_record_is_vector_only():
    p = dict(GOOD_PROGRESS, result_done_utc=None, final_path=None, live_verified={})
    tier, why = F.classify(result(valid=False, why="7D optimization has not completed", progress=p))
    assert tier == "vector_only" and "never recorded live/vector parity" in why


def test_a_board_that_failed_the_qualification_gate_is_refused():
    for bad in ({"not_compliant": True}, {"unprotected": True}, {"publish_class": "BEST_EFFORT"}):
        p = dict(GOOD_PROGRESS, result_done_utc=None, final_path=None, live_verified={}, **bad)
        assert F.classify(result(valid=False, why="failed gate", progress=p))[0] is None, bad


def test_a_crashed_board_is_refused():
    assert F.classify(result(valid=False, rc=124, why="timeout"))[0] is None


def test_a_non_7d_board_is_refused():
    p = dict(GOOD_PROGRESS, window_days=30, result_done_utc=None, final_path=None, live_verified={})
    assert F.classify(result(valid=False, why="progress is not 7D", progress=p))[0] is None


def test_no_result_yet_is_not_a_verdict():
    assert F.classify(None)[0] is None


def _publish(monkeypatch, tmp_path, sides, allow_vector_only):
    monkeypatch.setattr(F, "PUBLISHED", tmp_path / "sevenday_crypto.json")
    monkeypatch.setattr(F, "ALLOW_VECTOR_ONLY", allow_vector_only)
    monkeypatch.setattr(F, "_push_s1", lambda host: True)
    doc, delivered = F.publish(sides, "20261031", {})
    return json.loads((tmp_path / "sevenday_crypto.json").read_text()), doc


def _row(tier, age_s=0.0, staged=True):
    return {"tier": tier, "why": "", "host": "s6", "staged_epoch": time.time() - age_s, "staged_utc": "x", "staged": staged}


def test_vector_only_sets_are_not_tradeable_by_default(monkeypatch, tmp_path):
    on_disk, doc = _publish(monkeypatch, tmp_path, {"AUSDT_LONG": _row("vector_only")}, False)
    assert on_disk["sides"] == {}, "fin must stay flat rather than trade an unverified set"
    assert doc["counts"] == {"tradeable": 0, "vector_only": 1, "validated": 0, "redo": 0}
    assert on_disk["detail"]["AUSDT_LONG"]["tier"] == "vector_only", "the set is still RECORDED, just not tradeable"


def test_vector_only_sets_become_tradeable_only_on_an_explicit_opt_in(monkeypatch, tmp_path):
    on_disk, _ = _publish(monkeypatch, tmp_path, {"AUSDT_LONG": _row("vector_only")}, True)
    assert list(on_disk["sides"]) == ["AUSDT_LONG"] and on_disk["allow_vector_only"] is True


def test_a_validated_set_is_tradeable_without_any_opt_in(monkeypatch, tmp_path):
    on_disk, _ = _publish(monkeypatch, tmp_path, {"AUSDT_LONG": _row("validated")}, False)
    assert list(on_disk["sides"]) == ["AUSDT_LONG"]


def test_a_set_past_the_thirty_hour_window_is_dropped_entirely(monkeypatch, tmp_path):
    sides = {"OLD_LONG": _row("validated", age_s=31 * 3600), "NEW_LONG": _row("validated", age_s=3600)}
    on_disk, _ = _publish(monkeypatch, tmp_path, sides, False)
    assert list(on_disk["sides"]) == ["NEW_LONG"]
    assert "OLD_LONG" not in on_disk["detail"], "a stale side is neither tradeable nor reported as fresh"


def test_the_published_window_matches_the_traders_window():
    spec2 = importlib.util.spec_from_file_location("vdt_win", ROOT / "tools" / "vector_direct_trader.py")
    m = importlib.util.module_from_spec(spec2)
    sys.modules[spec2.name] = m
    spec2.loader.exec_module(m)
    assert F.MAX_AGE_S == m.SEVENDAY_MAX_AGE_S == 30 * 3600, "driver and trader must agree on freshness"


def test_s1_is_not_a_compute_worker():
    """S1 trades the venue and runs the NPZ/state chain; the trc runner gives it 0 slots for the same reason."""
    assert not any(h.startswith("s1") for h in F.WORKERS)


def test_a_board_that_schedules_its_own_redo_is_never_a_result():
    """"[NOT-FINISHED] <ss> REDO scheduled — herd relaunches into a fresh fill" is a request, not an answer."""
    r = result(valid=False, why="7D optimization has not completed",
               progress=dict(GOOD_PROGRESS, result_done_utc=None, final_path=None, live_verified={}))
    r["redo"] = True
    tier, why = F.classify(r)
    assert tier == "redo" and "relaunched, not published" in why


def test_a_redo_is_not_tradeable_even_with_the_opt_in(monkeypatch, tmp_path):
    on_disk, doc = _publish(monkeypatch, tmp_path, {"AUSDT_LONG": _row("redo")}, True)
    assert on_disk["sides"] == {} and doc["counts"]["redo"] == 1


def test_a_board_with_no_gain_of_its_own_is_refused():
    for gain in (None, 0.0):
        p = dict(GOOD_PROGRESS, result_done_utc=None, final_path=None, live_verified={}, cumulative_gain=gain)
        assert F.classify(result(valid=False, why="x", progress=p))[0] is None, gain


def test_a_real_vector_only_board_still_passes():
    p = dict(GOOD_PROGRESS, result_done_utc=None, final_path=None, live_verified={}, cumulative_gain=7.86)
    assert F.classify(result(valid=False, why="x", progress=p))[0] == "vector_only"


# ── rolling >20 h refresh (USER 2026-10-31) ──

def test_a_verified_set_that_was_not_staged_is_not_tradeable(monkeypatch, tmp_path):
    """fin trades from per_sym_store_7d.db; authorising a side with no staged set gives it nothing to trade from."""
    on_disk, _ = _publish(monkeypatch, tmp_path, {"AUSDT_LONG": _row("validated", staged=False)}, True)
    assert on_disk["sides"] == {}


def test_a_new_run_adds_to_the_previous_publication_instead_of_wiping_it(monkeypatch, tmp_path):
    _publish(monkeypatch, tmp_path, {"OLD_LONG": _row("validated", age_s=5 * 3600)}, False)
    on_disk, _ = _publish(monkeypatch, tmp_path, {"NEW_LONG": _row("validated")}, False)
    assert set(on_disk["sides"]) == {"OLD_LONG", "NEW_LONG"}, "a still-fresh side from an earlier run must stay tradeable"


def test_the_staged_epoch_is_carried_forward_not_restamped(monkeypatch, tmp_path):
    first, _ = _publish(monkeypatch, tmp_path, {"OLD_LONG": _row("validated", age_s=5 * 3600)}, False)
    second, _ = _publish(monkeypatch, tmp_path, {}, False)
    assert second["sides"]["OLD_LONG"] == first["sides"]["OLD_LONG"], "republishing must never give an old set a newer date"


def test_needs_refresh_after_twenty_hours():
    now = time.time()
    prior = {"FRESH": {"staged": True, "staged_epoch": now - 19 * 3600},
             "STALE": {"staged": True, "staged_epoch": now - 21 * 3600},
             "UNSTAGED": {"staged": False, "staged_epoch": now}}
    assert F.needs_refresh("FRESH", prior, now) is False
    assert F.needs_refresh("STALE", prior, now) is True
    assert F.needs_refresh("UNSTAGED", prior, now) is True
    assert F.needs_refresh("NEVER", prior, now) is True


def test_the_refresh_target_leaves_margin_inside_the_trading_window():
    assert F.REFRESH_AFTER_S == 20 * 3600 and F.MAX_AGE_S == 30 * 3600 and F.REFRESH_AFTER_S < F.MAX_AGE_S
