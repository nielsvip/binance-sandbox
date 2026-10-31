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


# ── full-pass preemption (USER 2026-10-31: <1h for all men sym_sides) ──

def _local_py(tmp_path, monkeypatch):
    """Run the remote snippets locally against a fake HOME, so the hold logic itself is what is tested."""
    import subprocess as sp
    monkeypatch.setenv("HOME", str(tmp_path))
    (tmp_path / "binance-sandbox" / "data").mkdir(parents=True, exist_ok=True)
    def run(host, code, timeout=60):
        r = sp.run([sys.executable, "-c", code], capture_output=True, text=True, env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"})
        return r.returncode, r.stdout, r.stderr
    monkeypatch.setattr(F, "_py", run)
    return tmp_path / "binance-sandbox" / "data" / "fleet_hold_hosts.json"


def test_the_fin_hold_never_overwrites_another_lanes_live_hold(tmp_path, monkeypatch):
    hf = _local_py(tmp_path, monkeypatch)
    hf.write_text(json.dumps({"s5": {"until": time.time() + 3600, "why": "TRC7D: 30D paused"}}))
    F.hold(True)
    d = json.loads(hf.read_text())
    assert d["s5"]["why"].startswith("TRC7D"), "a live trc hold must survive"
    assert d["s2"]["why"].startswith("FIN7D") and d["s6"]["why"].startswith("FIN7D")


def test_release_removes_only_fins_own_holds(tmp_path, monkeypatch):
    hf = _local_py(tmp_path, monkeypatch)
    hf.write_text(json.dumps({"s5": {"until": time.time() + 3600, "why": "TRC7D: 30D paused"}}))
    F.hold(True)
    F.hold(False)
    d = json.loads(hf.read_text())
    assert set(d) == {"s5"} and d["s5"]["why"].startswith("TRC7D")


def test_an_expired_foreign_hold_is_replaced(tmp_path, monkeypatch):
    hf = _local_py(tmp_path, monkeypatch)
    hf.write_text(json.dumps({"s5": {"until": time.time() - 60, "why": "15-minute NN training reservation"}}))
    F.hold(True)
    assert json.loads(hf.read_text())["s5"]["why"].startswith("FIN7D")


def _fake_run(monkeypatch, n_sides, boards_raise=False):
    calls = []
    monkeypatch.setattr(F, "build_queue_on_s1", lambda run_id: (Path("/dev/null"), {}, [f"S{i}_LONG" for i in range(n_sides)]))
    monkeypatch.setattr(F, "load_prior", lambda: {})
    monkeypatch.setattr(F, "sh", lambda h, c, timeout=40: (0, "", ""))
    monkeypatch.setattr(F, "ship_queue", lambda h, q, r: True)
    monkeypatch.setattr(F, "preempt", lambda: calls.append("preempt") or ["X30D_LONG"])
    monkeypatch.setattr(F, "release", lambda: calls.append("release"))
    def boards(*a, **k):
        calls.append("boards")
        if boards_raise:
            raise RuntimeError("driver crashed mid-pass")
        return 0
    monkeypatch.setattr(F, "_run_boards", boards)
    return calls


def test_a_full_pass_pauses_the_30d_boards_and_releases_them(monkeypatch):
    calls = _fake_run(monkeypatch, F.FULL_PASS_MIN)
    F.run("20261031")
    assert calls == ["preempt", "boards", "release"]


def test_the_30d_boards_are_released_even_when_the_pass_crashes(monkeypatch):
    calls = _fake_run(monkeypatch, F.FULL_PASS_MIN, boards_raise=True)
    try:
        F.run("20261031")
    except RuntimeError:
        pass
    assert calls[-1] == "release", "a crashed pass must never leave the 30D fleet paused"


def test_a_trickle_pauses_nothing(monkeypatch):
    calls = _fake_run(monkeypatch, F.FULL_PASS_MIN - 1)
    F.run("20261031")
    assert calls == ["boards"]


def test_the_full_pass_fits_the_hour():
    assert F.FULL_PASS_DEADLINE_S <= 75 * 60 and sum(F.WORKERS.values()) >= 10 and F.FULL_PASS_REDO_ATTEMPTS <= 1


def test_a_sigterm_still_releases_the_30d_boards(monkeypatch):
    import signal
    calls = _fake_run(monkeypatch, F.FULL_PASS_MIN)
    def boards(*a, **k):
        calls.append("boards")
        F._terminate(signal.SIGTERM, None)
    monkeypatch.setattr(F, "_run_boards", boards)
    try:
        F.run("20261031")
    except SystemExit:
        pass
    assert calls[-1] == "release"


def test_every_preempted_host_gets_a_dead_man_release(monkeypatch):
    sent = []
    monkeypatch.setattr(F, "hold", lambda on: {})
    monkeypatch.setattr(F, "_py", lambda h, c, timeout=60: (0, "[]", ""))
    monkeypatch.setattr(F, "sh", lambda h, c, timeout=40: sent.append((h, c)) or (0, "", ""))
    F.preempt()
    deadman = {h for h, c in sent if "sleep 7200" in c and "v15_preopen_7d.pause" in c}
    assert deadman == set(F.PREEMPT_HOSTS)


# ── the experiment stays off the live stock host during its session (USER 2026-10-31) ──

def _et(y, mo, d, h, mi):
    from zoneinfo import ZoneInfo
    return dt.datetime(y, mo, d, h, mi, tzinfo=ZoneInfo("America/New_York")).timestamp()


import datetime as dt  # noqa: E402


def test_s5_is_off_limits_through_the_stock_session_on_weekdays():
    mon = (2026, 11, 2)
    for hh, mm in ((8, 0), (8, 45), (9, 30), (12, 0), (16, 0), (16, 29)):
        assert F.host_in_live_session("s5", _et(*mon, hh, mm)) is True, (hh, mm)
    for hh, mm in ((7, 59), (16, 30), (20, 0)):
        assert F.host_in_live_session("s5", _et(*mon, hh, mm)) is False, (hh, mm)


def test_s5_is_usable_at_the_weekend():
    assert F.host_in_live_session("s5", _et(2026, 10, 31, 12, 0)) is False   # Saturday


def test_hosts_without_a_live_session_are_always_usable():
    for h in ("s2", "s6"):
        assert F.host_in_live_session(h, _et(2026, 11, 2, 12, 0)) is False


def test_s1_never_hosts_a_7d_board():
    assert "s1" not in F.WORKERS and not any(h.startswith("s1") for h in F.WORKERS)


def test_preemption_never_touches_a_host_in_its_live_session(monkeypatch):
    sent = []
    monkeypatch.setattr(F, "hold", lambda on: {})
    monkeypatch.setattr(F, "_py", lambda h, c, timeout=60: sent.append(h) or (0, "[]", ""))
    monkeypatch.setattr(F, "sh", lambda h, c, timeout=40: (0, "", ""))
    monkeypatch.setattr(F, "host_in_live_session", lambda h, now=None: h == "s5")
    F.preempt()
    assert "s5" not in sent and {"s2", "s6"} <= set(sent)


def test_a_full_disk_never_kills_the_pass(monkeypatch, capsys):
    """2026-10-31: a log line raised ENOSPC and took the whole full pass down after one board."""
    import builtins
    real_print = builtins.print
    def broken_print(*a, **k):
        raise OSError(28, "No space left on device")
    monkeypatch.setattr(builtins, "print", broken_print)
    try:
        F.log("this line cannot be written")   # must not raise
    finally:
        monkeypatch.setattr(builtins, "print", real_print)
