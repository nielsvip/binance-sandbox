"""Pre-dispatch NPZ verification + the clock-aware venue phase (USER 2026-10-15).

(1) "BEFORE any calculations start a verification of npz data, recalc if needed while the symbol is skipped
    and the next good one runs, then requeue the one that could have caused 0 trades instead of blindly
    following a list of symbols."
    Measured on s1 /tmp/v15_dispatch.log (966 records): 889 launches carried prep.ok=True (97.8 %) but 20
    (2.2 %) launched with ok=None — slot-wait-timeout (XOM, AGCO), regen_blocked by the bar-loss guard
    (CLS, AG), prep crashes `no-output rc=-15` (CVX, WDAY). ok=None must never launch, and each cause needs
    its own disposition: transient -> requeue, bar-loss -> alert, never a rebuild under a frozen symbol.

(2) "obviously stocks wont have an up to date npz outside market hours but for now we could run stocks
    backtests during market hours and crypto the rest of the time until they catch up then run stocks on the
    latest market hour bar npz."
"""
import datetime
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
import v15_dispatch_job as D
import v15_fleet_scheduler as S

RTH = datetime.datetime(2026, 10, 15, 14, 0, tzinfo=datetime.timezone.utc)      # Wed 10:00 ET
RTH_LATE = datetime.datetime(2026, 10, 15, 19, 55, tzinfo=datetime.timezone.utc)  # Wed 15:55 ET
OFF = datetime.datetime(2026, 10, 15, 3, 58, tzinfo=datetime.timezone.utc)      # Tue 23:58 ET, the observed tick
SAT = datetime.datetime(2026, 10, 17, 14, 0, tzinfo=datetime.timezone.utc)      # Saturday
BOTH = {"AAPL": {"LONG": ("need30", 1)}, "BTCUSDT": {"LONG": ("need30", 1)}}


# ---------------------------------------------------------------- (1) prep disposition: ok=None never launches
def test_ok_true_launches():
    assert D.prep_disposition({"ok": True, "reason": "tail current to latest closed bar"})[:2] == (True, "ok")


def test_prep_that_did_not_run_is_retry_not_a_pass():
    launch, kind, _, rb = D.prep_disposition(None)
    assert launch is False and kind == "retry" and rb is False


def test_slot_wait_timeout_is_retry_without_a_rebuild():
    launch, kind, _, rb = D.prep_disposition({"ok": None, "reason": "slot-wait-timeout"})
    assert launch is False and kind == "retry" and rb is False


def test_reaped_prep_is_retry_without_a_rebuild():
    launch, kind, _, rb = D.prep_disposition({"ok": None, "reason": "no-output rc=-15 Terminated"})
    assert launch is False and kind == "retry" and rb is False


def test_frozen_symbol_never_asks_for_a_rebuild():
    # a frozen symbol owns an unfinished board; a rebuild wipes it (NPZ-CHANGED-REFILL)
    launch, kind, _, rb = D.prep_disposition({"ok": None, "action": "frozen", "reason": "frozen: unfinished board"})
    assert launch is False and kind == "retry" and rb is False


def test_push_failed_is_retry_the_target_kept_its_own_copy():
    launch, kind, _, rb = D.prep_disposition({"ok": None, "reason": "built | push failed: target keeps its own copy"})
    assert launch is False and kind == "retry" and rb is False


def test_regen_blocked_alerts_and_never_spins_a_rebuild():
    for ok in (None, False):
        launch, kind, _, rb = D.prep_disposition({"ok": ok, "action": "regen_blocked", "reason": "guard: would lose bars 24181->2017"})
        assert launch is False and kind == "alert" and rb is False


def test_regen_failed_alerts():
    assert D.prep_disposition({"ok": None, "action": "regen_failed", "reason": "builder returned failure"})[1] == "alert"


def test_generic_false_is_a_rebuildable_block():
    launch, kind, _, rb = D.prep_disposition({"ok": False, "reason": "some new refusal"})
    assert launch is False and kind == "block" and rb is True


# ---------------------------------------------------------------- (1) deferral bookkeeping / requeue
def test_rebuild_requested_once_per_cooldown_not_per_tick():
    st = {}
    a = S._npz_defer_note(st, "ADP", "block", "bars=1", now_ts=1000.0, cooldown_s=1800, max_defer=3)
    b = S._npz_defer_note(st, "ADP", "block", "bars=1", now_ts=1100.0, cooldown_s=1800, max_defer=3)
    c = S._npz_defer_note(st, "ADP", "block", "bars=1", now_ts=3000.0, cooldown_s=1800, max_defer=3)
    assert a[0] is True and b[0] is False and c[0] is True
    assert (a[1], b[1], c[1]) == (1, 2, 3)


def test_transient_and_alert_kinds_never_request_a_rebuild():
    st = {}
    assert S._npz_defer_note(st, "XOM", "retry", "slot-wait-timeout", now_ts=1.0, cooldown_s=0)[0] is False
    assert S._npz_defer_note(st, "CLS", "alert", "bar-loss guard", now_ts=1.0, cooldown_s=0)[0] is False


def test_may_rebuild_false_overrides_a_block():
    st = {}
    assert S._npz_defer_note(st, "AEM", "block", "bars=1", now_ts=1.0, cooldown_s=0, may_rebuild=False)[0] is False


def test_past_the_defer_cap_it_alerts_instead_of_spinning():
    st = {}
    last = None
    for i in range(5):
        last = S._npz_defer_note(st, "AXTI", "block", "bars=1", now_ts=1.0 + i * 5000, cooldown_s=1800, max_defer=3)
    assert last[1] == 5 and last[2] is True and last[0] is False


def test_passing_again_clears_the_deferral_and_reports_its_count():
    st = {}
    S._npz_defer_note(st, "CDW", "block", "bars=1", now_ts=1.0)
    S._npz_defer_note(st, "CDW", "block", "bars=1", now_ts=2.0)
    assert S._npz_defer_clear(st, "CDW") == 2
    assert S._npz_defer_clear(st, "CDW") == 0  # idempotent, and a never-deferred symbol reports 0


def test_preflight_fails_open_when_the_module_is_not_pushed_yet(monkeypatch):
    # a dependency that has not reached the host must never deadlock the fleet
    monkeypatch.setitem(sys.modules, "v15_npz_preflight", None)
    ok, why, res = S._npz_preflight("ANYUSDT", 30)
    assert ok is True and "preflight-unavailable" in why and res == {}


# ---------------------------------------------------------------- (2) RTH stock tail allowance (parameter-only)
POST_CLOSE = datetime.datetime(2026, 10, 15, 22, 0, tzinfo=datetime.timezone.utc)   # Wed 18:00 ET, after the close
OVERNIGHT = datetime.datetime(2026, 10, 16, 3, 58, tzinfo=datetime.timezone.utc)   # Thu 23:58 ET Wed
SUNDAY = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=datetime.timezone.utc)      # Sun midday


def test_stock_tail_limit_inside_rth_demands_the_latest_market_hour_bar():
    assert D.npz_tail_limit_h("AAPL", RTH) <= 1.0          # ~30 min after the open
    assert D.npz_tail_limit_h("AAPL", RTH_LATE) <= 1.0     # still one bar, not the whole session


def test_stock_tail_limit_accepts_a_session_final_bar_after_the_close():
    """USER 2026-10-15: "no new klines are produced after 20:00utc" — a post-close NPZ rebuilt to the session's
    final bar IS current, so the post-close stock pass must not be called stale."""
    assert 2.0 <= D.npz_tail_limit_h("AAPL", POST_CLOSE) <= 3.5
    assert 7.5 <= D.npz_tail_limit_h("AAPL", OVERNIGHT) <= 10.0


def test_stock_tail_limit_is_session_relative_not_an_hour_count_on_weekends():
    """The fixed 20 h stocks limit wrongly blocks a perfectly current NPZ on a Sunday (last bar Friday 20:00 UTC
    = 40 h old). The cutoff comes from npz_cutoffs.last_rth_close_bar instead."""
    assert D.npz_tail_limit_h("AAPL", SUNDAY) > 20.0


def test_crypto_always_uses_the_modules_own_bar_accurate_limit():
    for n in (RTH, OFF, SAT, SUNDAY):
        assert D.npz_tail_limit_h("BTCUSDT", n) is None
        assert D.npz_tail_limit_h("UNIUSDC", n) is None


def test_stock_tail_limit_grace_and_override_are_env_tunable(monkeypatch):
    base = D.npz_tail_limit_h("AAPL", RTH)
    monkeypatch.setenv("V15_SCHED_STOCK_TAIL_GRACE_H", "2.5")
    assert D.npz_tail_limit_h("AAPL", RTH) == round(base - 0.5 + 2.5, 2)
    monkeypatch.setenv("V15_SCHED_STOCK_TAIL_H", "9")
    assert D.npz_tail_limit_h("AAPL", RTH) == 9.0


# ---------------------------------------------------------------- (2) clock-aware venue phase
def test_stocks_keep_their_daily_pass_at_every_hour():
    """USER 2026-10-15 (corrected): stocks are "where the real money goes" and get a guaranteed daily rerun
    even after the 20:00 UTC close — a post-close NPZ rebuilt to the session's final bar IS current."""
    for n in (RTH, OFF, SAT):
        assert S._venue_phase(BOTH, mode="auto", now=n)[0] == "stocks", n


def test_the_rest_of_the_night_goes_to_crypto_once_the_stock_pass_is_done():
    drained = {"AAPL": {"LONG": ("terminal_ok", 1)}, "BTCUSDT": {"LONG": ("need30", 1)}}
    assert S._venue_phase(drained, mode="auto", now=OFF)[0] == "crypto"
    assert S._venue_phase(drained, mode="auto", now=RTH)[0] == "crypto"


def test_both_venues_drained_never_idles_the_fleet():
    assert S._venue_phase({"AAPL": {"LONG": ("terminal_ok", 1)}}, mode="auto", now=OFF)[0] == "any"
    assert S._venue_phase({}, mode="auto", now=OFF)[0] == "any"


def test_explicit_overrides_are_unchanged():
    assert S._venue_phase(BOTH, mode="stocks", now=OFF)[0] == "stocks"
    assert S._venue_phase(BOTH, mode="crypto", now=RTH)[0] == "crypto"
    assert S._venue_phase(BOTH, mode="", now=OFF)[0] == "any"


def test_kill_switch_removes_the_crypto_phase_only(monkeypatch):
    monkeypatch.setenv("V15_SCHED_VENUE_CLOCK", "0")
    assert S._venue_phase(BOTH, mode="auto", now=OFF)[0] == "stocks"
    # with the switch off a drained stock queue falls back to "any", the exact 2026-10-12 behaviour
    assert S._venue_phase({"BTCUSDT": {"LONG": ("need30", 1)}}, mode="auto", now=OFF)[0] == "any"


def test_only_launchable_sides_hold_a_phase():
    assert S._venue_phase(BOTH, mode="auto", now=OFF, launchable=lambda s, sd: False)[0] == "any"


def test_an_exhausted_stock_side_no_longer_holds_crypto_hostage():
    only_stock_exhausted = {"AAPL": {"LONG": ("need30", 1)}, "BTCUSDT": {"LONG": ("need30", 1)}}
    phase, pend = S._venue_phase(only_stock_exhausted, mode="auto", now=OFF, launchable=lambda s, sd: not s.startswith("AAPL"))
    assert phase == "crypto" and pend == []


def test_venue_allowed_has_a_real_crypto_phase():
    assert S._venue_allowed("BTCUSDT", phase="crypto") is True
    assert S._venue_allowed("AAPL", phase="crypto") is False
    assert S._venue_allowed("AAPL", phase="stocks") is True
    assert S._venue_allowed("BTCUSDT", phase="stocks") is False
    assert S._venue_allowed("AAPL", phase="any") is True and S._venue_allowed("BTCUSDT", phase="any") is True


def test_tokenized_stock_is_crypto_in_both_phases():
    # AAPLUSDT is NEVER stocks (data/excluded_tokenized_stocks.json rule)
    assert S._venue_allowed("AAPLUSDT", phase="stocks") is False
    assert S._venue_allowed("AAPLUSDT", phase="crypto") is True


def test_venue_order_is_unchanged_by_the_2026_10_15_policy():
    # the corrected policy governs the PHASE and FRESHNESS, not the ordering — stocks keep their daily pass
    assert S.venue_order(OFF) == ["stocks", "crypto"]
    assert S.venue_order(RTH) == ["crypto", "stocks"]


def test_venue_order_still_honours_the_explicit_pin(monkeypatch):
    monkeypatch.setenv("V15_SCHED_VENUE_FIRST", "stocks")
    assert S.venue_order(OFF) == ["stocks", "crypto"]


# ---------------------------------------------------------------- the screens must actually RUN, not fail open
def _mk_npz(path, sessions, bars_per_session=26, last_day="2026-10-14"):
    """Minimal NPZ with plausible 15m RTH timestamps, enough for both screens to reach a real verdict."""
    import numpy as np

    day = datetime.date.fromisoformat(last_day)
    ts = []
    d = 0
    made = 0
    while made < sessions and d < 400:
        cur = day - datetime.timedelta(days=d)
        d += 1
        if cur.weekday() >= 5:
            continue
        base = datetime.datetime.combine(cur, datetime.time(13, 30), tzinfo=datetime.timezone.utc).timestamp()
        ts.extend(base + 900 * i for i in range(bars_per_session))
        made += 1
    ts = np.array(sorted(ts), dtype="float64")
    # >= 8 keys: tools/v15_npz_preflight applies an absolute thin-build floor of max(8, 0.5*host_median),
    # and a real NPZ carries ~1100-1400 keys, so a 7-column fixture would trip it as "thin".
    cols = {k: np.full(len(ts), 100.0) for k in ("close", "open_15m", "high_15m", "low_15m", "close_15m",
                                                 "volume_15m", "high_1h", "low_1h", "close_1h", "close_4h")}
    np.savez(path, timestamps=ts, **cols)


def test_gap_screen_reaches_a_real_verdict_and_never_fails_open_silently(tmp_path):
    """Regression: the screen caught a NameError (pathlib) and returned "gap-screen-unavailable", i.e. it read
    every holed stock NPZ as usable. Any such reason is a BUG, not a verdict."""
    _mk_npz(tmp_path / "GOODCO.npz", sessions=40)
    ok, why, info = S._npz_gap_screen("GOODCO", ind_dir=str(tmp_path))
    assert "unavailable" not in why and "-crash" not in why, why
    assert ok is True and info.get("sessions", 0) >= 30, (ok, why, info)


def test_gap_screen_blocks_a_one_session_stub(tmp_path):
    # ADP/AEM/ALMU/AXTI/CDW on s1 were exactly this: a rebuild in flight leaving a 1-session NPZ visible
    _mk_npz(tmp_path / "STUBCO.npz", sessions=1)
    ok, why, info = S._npz_gap_screen("STUBCO", ind_dir=str(tmp_path))
    assert ok is False and "session" in why and info.get("sessions") == 1, (ok, why, info)


def test_gap_screen_blocks_an_intra_window_hole(tmp_path):
    """The 17 x "20 sessions in last 60d, 29 d gap (2026-08-26->09-24)" profile: the tail is current and bars
    exist inside the window, so _npz_preflight passes — only the session census sees the hole."""
    import numpy as np

    recent = tmp_path / "HOLECO.npz"
    _mk_npz(recent, sessions=12)
    with np.load(recent) as z:
        ts = np.asarray(z["timestamps"], dtype="float64")
        cols = {k: np.asarray(z[k]) for k in z.files if k != "timestamps"}
    old = ts[:1] - 40 * 86400.0  # one bar 40 days before the block -> a >5 d intra-window gap
    ts2 = np.concatenate([old, ts])
    cols2 = {k: np.concatenate([v[:1], v]) for k, v in cols.items()}
    np.savez(recent, timestamps=ts2, **cols2)
    ok, why, _ = S._npz_gap_screen("HOLECO", ind_dir=str(tmp_path))
    assert ok is False and ("gap" in why or "session" in why), why
    # ...and the structural preflight is happy with the same file, which is why both screens are needed
    pf_ok, pf_why, _ = S._npz_preflight("HOLECO", 30, ind_dir=str(tmp_path), check_freshness=False)
    assert pf_ok is True, pf_why


def test_gap_screen_skips_crypto_entirely(tmp_path):
    assert S._npz_gap_screen("BTCUSDT", ind_dir=str(tmp_path)) == (True, "", {})


def test_gap_screen_kill_switch(monkeypatch, tmp_path):
    _mk_npz(tmp_path / "STUBCO.npz", sessions=1)
    monkeypatch.setenv("V15_SCHED_NPZ_GAP_SCREEN", "0")
    assert S._npz_gap_screen("STUBCO", ind_dir=str(tmp_path))[0] is True


# ---------------------------------------------------------------- stale tail must not veto while RUN_STALE=1
class _FakePf:
    """Stand-in for tools/v15_npz_preflight so the RUN_STALE policy can be pinned without a real NPZ."""

    def __init__(self, reasons):
        self.reasons = reasons
        self.recorded = []

    def verify(self, symbol, window_days=30, check_freshness=True, max_tail_age_h=None, **kw):
        return {"ok": not self.reasons, "reasons": list(self.reasons), "tail_age_h": 8.2, "max_tail_age_h": 1.5}

    def record_deferral(self, res):
        self.recorded.append(res)


def _with_fake(monkeypatch, reasons):
    fake = _FakePf(reasons)
    monkeypatch.setitem(sys.modules, "v15_npz_preflight", fake)
    return fake


def test_stale_tail_alone_does_not_block_while_run_stale_is_on(monkeypatch):
    """Regression 2026-10-15: enforcing freshness here blocked EVERY crypto dispatch (9 symbols, tails 7.1-8.7 h
    after the job's own refresh) and 0 crypto boards launched. V15_SCHED_RUN_STALE is the fleet's policy."""
    fake = _with_fake(monkeypatch, ["stale tail: last bar 8.2h old > 1.5h for crypto — rebuild the NPZ"])
    monkeypatch.setenv("V15_SCHED_RUN_STALE", "1")
    ok, why, _ = D.npz_preflight("ENAUSDC", 30, check_freshness=True)
    assert ok is True and why.startswith("DEGRADED-STALE-TAIL")
    assert len(fake.recorded) == 1  # the deferral is still recorded, so the rebuild is still asked for


def test_stale_tail_blocks_when_run_stale_is_off(monkeypatch):
    _with_fake(monkeypatch, ["stale tail: last bar 8.2h old > 1.5h for crypto"])
    monkeypatch.setenv("V15_SCHED_RUN_STALE", "0")
    ok, why, _ = D.npz_preflight("ENAUSDC", 30, check_freshness=True)
    assert ok is False and "stale tail" in why


def test_structural_defects_always_block_even_with_run_stale(monkeypatch):
    # the silent 0-trades producer must never be waved through by a freshness policy
    for reasons in (["span 0.0d < 80% of the 30d window"],
                    ["thin NPZ: 9 keys vs host median 1392 for stocks"],
                    ["close is all zero inside the window"],
                    ["stale tail: last bar 8.2h old > 1.5h for crypto", "span 0.0d < 80% of the 30d window"]):
        _with_fake(monkeypatch, reasons)
        monkeypatch.setenv("V15_SCHED_RUN_STALE", "1")
        ok, why, _ = D.npz_preflight("ADP", 30, check_freshness=True)
        assert ok is False, (reasons, why)


def test_a_clean_npz_passes_without_a_degraded_note(monkeypatch):
    _with_fake(monkeypatch, [])
    monkeypatch.setenv("V15_SCHED_RUN_STALE", "1")
    ok, why, _ = D.npz_preflight("AAPL", 30, check_freshness=True)
    assert ok is True and not why
