"""vector_enforce (USER 2026-10-21): bar-boundary cross-confirmed execution; violations kill the family."""

import json
import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vector_enforce as ve


def _iso(tmp_path, monkeypatch, enforce=True):
    k = tmp_path / "kills.json"
    v = tmp_path / "viol.jsonl"
    q = tmp_path / "retrace.jsonl"
    m = tmp_path / "monitor.jsonl"
    monkeypatch.setattr(ve, "KILLS", k)
    monkeypatch.setattr(ve, "VIOLATIONS", v)
    monkeypatch.setattr(ve, "RETRACE_QUEUE", q)
    monkeypatch.setattr(ve, "MONITOR_LOG", m)
    ve._KILLS.update(loaded=0.0, killed={})
    mod = types.ModuleType("config")
    mod.Config = type(
        "Config", (), {"VECTOR_ENFORCE_ENABLED": enforce, "VECTOR_BAR_WINDOW_S": 180.0}
    )
    monkeypatch.setitem(sys.modules, "config", mod)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: True)
    return k, v


def test_bar_window_math(tmp_path, monkeypatch):
    import types as _t
    import sys as _s

    mod = _t.ModuleType("config")
    mod.Config = type(
        "Config", (), {"VECTOR_ENFORCE_ENABLED": True, "VECTOR_BAR_WINDOW_S": 180.0}
    )
    monkeypatch.setitem(_s.modules, "config", mod)
    assert ve.in_bar_window(1800 * 900 + 10) is True
    assert ve.in_bar_window(1800 * 900 + 400) is False
    assert ve.in_bar_window(1800 * 900 + 899) is False


def test_cross_exits_allowed(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    for reason in (
        "WT_LOWER_CROSS_EXIT wt_1h",
        "DC_BREACH_REDUCE_UNHEDGED_LOW_15m",
        "R3_HTF_FLIP_DAILY_g1%",
        "VEC_DRIVEN_OPEN_RECONCILE_",
        "FIXED_STOP_ADOPTED 123",
        "BROKER_RECONCILED order=1",
        "LH_LL_TOP_EXIT_arm=4h",
        "OBLIGATORY_REENTRY_X",
    ):
        ok, verdict = ve.check("CLOSE", "ang:X_LONG", reason)
        assert ok is True, (reason, verdict)


def test_non_cross_exit_kills_family(tmp_path, monkeypatch):
    k, v = _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check("REDUCE", "ang:X_LONG", "MAKER_PROFIT_EXIT_px1_g0.05")
    assert ok is False and verdict == "VIOLATION_FAMILY_KILLED"
    assert json.loads(k.read_text())["MAKER_PROFIT_EXIT"]["why"].startswith(
        "non-cross exit"
    )
    ok2, v2 = ve.check("CLOSE", "ang:Y_LONG", "MAKER_PROFIT_EXIT_px2_g0.1")
    assert ok2 is False and v2 == "KILLED_FAMILY_MAKER_PROFIT_EXIT"
    ok3, _ = ve.check("OPEN", "ang:Z_LONG", "QUICK_OPEN_STRONG_BUY_k1")
    assert ok3 is True, "killing an exit family must not touch entries"


def test_vel_exit_exempt_with_faithful_twin(tmp_path, monkeypatch):
    """VEL_EXIT is vector-modeled + switch-gated AND twin-faithful since 2026-10-21:
    completed-bar 4h velocity (wt_velocity_4h_closed) + per-sym STRUCTURAL veto in
    the live lane — replay proof twin==vector on all 5 fire bars. The exemption is
    conditional on that fidelity: the pins below fail if either half is removed."""
    assert "VEL_EXIT" in ve.VEC_SWITCH_EXIT_FAMILIES
    twin_src = (ROOT / "vec_decisions" / "twin_gates_sizing_a.py").read_text()
    assert "wt_velocity_4h_closed" in twin_src, "twin must read completed-bar velocity"
    ind_src = (ROOT / "ez_indicators.py").read_text()
    assert "wt_velocity_%s_closed" in ind_src, "producer must emit completed velocity"
    lane_src = (ROOT / "ez_manage.py").read_text()
    assert "structural_exit_permitted" in lane_src, "VEL lane must hold STRUCT veto"
    assert "STRUCTURAL_EXIT_GATE_ENABLED" in lane_src, "veto must follow per-sym switch"
    k, v = _iso(tmp_path, monkeypatch)
    assert ve.family_of("VEL_EXIT_v4h-10_g-0.05") == "VEL_EXIT"
    ok, verdict = ve.check("CLOSE", "ang:X_LONG", "VEL_EXIT_v4h-10_g-0.05")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    assert not k.exists(), "exempt family must not write a kill entry"
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok2, v2 = ve.check("CLOSE", "ang:X_LONG", "VEL_EXIT_v4h-10_g-0.05")
    assert (ok2, v2) == (False, "OFF_BAR_WINDOW")


def test_bb_recovery_bbkc_stay_killed(tmp_path, monkeypatch):
    """BB_RECOVERY + BBKC have vector masks but the vector books NEITHER with
    VEC_EXIT_SIG_IS_TRIGGER=False fleet-wide (764 boards): exit_sig closes land as
    bare TECHNICAL_EXIT and are suppressed (v12:22617-22620). Live fires are
    unmodeled P&L — keep killed; vectorizable only via a pilot-proven trigger flip."""
    assert "BB_RECOVERY" not in ve.VEC_SWITCH_EXIT_FAMILIES
    assert "BBKC" not in ve.VEC_SWITCH_EXIT_FAMILIES
    _iso(tmp_path, monkeypatch)
    reason = "QUICK_BB_RECOVERY_EXIT_TRADIER_LONG_px3.9560_k81"
    ok, _ = ve.check("REDUCE", "a", reason)
    assert ok is False, "QUICK dispatcher prefix must not launder the family"
    assert ve.check("CLOSE", "a", "BBKC_EXIT_1h_px-above-kc-mid_g0.07")[0] is False
    # 2026-10-21 backlog audit: unvectorized rater output (REDUCE_K) stays killed.
    assert "REDUCE_K" not in ve.VEC_SWITCH_EXIT_FAMILIES


def test_exit_velocity_exempt_with_completed_bars(tmp_path, monkeypatch):
    """EXIT velocity is vector-BOOKED (lane-D labeled closes, not exit_sig
    suppressed) with a faithful twin since 2026-10-21: completed-bar 1h/4h/D
    velocities + shared predicate/MIN_TFS. Replay: twin==vector 3/3."""
    assert "EXIT" in ve.VEC_SWITCH_EXIT_FAMILIES
    twin_src = (ROOT / "vec_decisions" / "twin_exits_dead.py").read_text()
    assert "wt_velocity_%s_closed" in twin_src
    k, v = _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check(
        "CLOSE", "a", "EXIT_VELOCITY_WT_1h_vel37.4-against-short_g0.00"
    )
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    assert not k.exists(), "exempt family must not write a kill entry"


def test_technical_exempt_with_crossing_hold(tmp_path, monkeypatch):
    """TECHNICAL targets are vector-BOOKED (labeled dc_* crosses + min_hold) with
    a faithful twin since 2026-10-21: fresh-cross predicate + grid-faithful hold
    in the lane. Replay: DOGE suppression MATCH, XMR cross."""
    assert "TECHNICAL" in ve.VEC_SWITCH_EXIT_FAMILIES
    twin_src = (ROOT / "vec_decisions" / "dc_channel_exits.py").read_text()
    assert "def dc_target_cross" in twin_src
    assert "def technical_dc_exit_crossing" in twin_src
    lane_src = (ROOT / "ez_manage.py").read_text()
    assert "technical_dc_exit_crossing" in lane_src
    assert "TECHNICAL_HOLD" in lane_src
    k, v = _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check(
        "CLOSE", "a", "TECHNICAL_TARGET_dc_15m_high_-0.10%_TARGET_BUF_g0.03"
    )
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    assert not k.exists(), "exempt family must not write a kill entry"


def test_ratio_trim_allowed_close_killed(tmp_path, monkeypatch):
    """USER 2026-10-22 OPERATOR ORDER ("ratio is L/S, make sure that is on"):
    RATIO REDUCE/CLOSE flow as VEC_SWITCH_EXIT (de-risk toward target);
    AUGMENT stays trim-allowed at the enforcer but stood down in code;
    RATIO OPEN stays killed (an open is never vector-dictated). Supersedes
    the 10-21 trim-only pin."""
    _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check("REDUCE", "a", "RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    ok, verdict = ve.check("CLOSE", "a", "RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    ok, verdict = ve.check(
        "AUGMENT", "a", "RATIO_REDUCE_SHORT_L0_S100_tgt25/75_breadth26%_gain0.2%"
    )
    assert (ok, verdict) == (True, "RATIO_TRIM_ALLOWED")
    # RATIO OPEN passes the enforcer entry path (timing) but is stood down in
    # code (candidates=[]) — an open is never vector-dictated. Both layers pinned.
    ok, verdict = ve.check(
        "OPEN", "a", "RATIO_REBALANCE_LONG_L0_S100_tgt25/75_4x_score50"
    )
    assert ok is True
    ez_src = (ROOT / "ez_manage.py").read_text()
    assert "candidates = []" in ez_src


def test_ppl_reaugment_allowed_ppl_tp_stays_killed(tmp_path, monkeypatch):
    """USER 2026-10-22 PPL re-augment switch (standing order -> per-sym_side
    switch, backtest decides; implied augment MULT x TP qty on WT cross back
    above dc_low_15m): the switch-gated DC-reclaim AUGMENT flows as its own
    narrow family even with the broad PPL kill present; standing-order
    PPL_TP_*/PPL_SL_* reasons stay mapped to the killed PPL family
    (operator-open question, not auto-exempt)."""
    k, _v = _iso(tmp_path, monkeypatch)
    k.write_text(json.dumps({"PPL": {"disabled_at": 0.0, "why": "standing order"}}))
    assert ve.family_of("PPL_REAUGMENT_DC_RECLAIM_wt1.2>dc0.5000") == "PPL_REAUGMENT"
    assert ve.family_of("PPL_REAUGMENT_WT_CROSS_wt1.2x0.5") == "PPL_REAUGMENT"
    assert ve.family_of("PPL_REAUGMENT_WT_SLOWDOWN_wt1.2>prev0.5") == "PPL_REAUGMENT"
    assert ve.family_of("PPL_TP_gain1.50_URL2_50pct") == "PPL"
    ok, verdict = ve.check(
        "QUICK_AUGMENT", "a", "PPL_REAUGMENT_DC_RECLAIM_wt1.2>dc0.5000"
    )
    assert (ok, verdict) == (True, "PPL_REAUGMENT_ALLOWED")
    ok, verdict = ve.check("AUGMENT", "a", "PPL_REAUGMENT_WT_CROSS_wt1.2x0.5")
    assert (ok, verdict) == (True, "PPL_REAUGMENT_ALLOWED")
    # Both live lanes + the v12 walk leg gate on the same per-sym switch +
    # trigger (pins use names + arm keys, not exact call wrapping; reasons
    # are built as PPL_REAUGMENT_{TRIGGER}_{tag}).
    ez_src = (ROOT / "ez_manage.py").read_text()
    assert '"PPL_REAUGMENT_ENABLED"' in ez_src
    assert '"PPL_REAUGMENT_TRIGGER"' in ez_src
    assert '"reaug_armed"' in ez_src and "PPL_REAUGMENT_{_rg_trig}" in ez_src
    tr_src = (ROOT / "tradier_manage.py").read_text()
    assert '"PPL_REAUGMENT_ENABLED"' in tr_src
    assert '"PPL_REAUGMENT_TRIGGER"' in tr_src
    assert '"reaug_armed"' in tr_src and "PPL_REAUGMENT_{_rg_trig}" in tr_src
    v12_src = (ROOT / "v12_quick_engine.py").read_text()
    assert '"PPL_REAUGMENT_" + _reaug_trig_name' in v12_src
    assert "PPL_REAUGMENT_ENABLED" in v12_src
    assert "PPL_REAUGMENT_TRIGGER" in v12_src


def test_monitor_logs_watched_families_only(tmp_path, monkeypatch):
    """Family watch: monitored verdicts logged, unmonitored never touch the log."""
    _iso(tmp_path, monkeypatch)
    ve.check("CLOSE", "a", "VEL_EXIT_v4h-10_g-0.05")
    ve.check("REDUCE", "a", "MAKER_PROFIT_EXIT_px1_g0.05")
    rows = [json.loads(line) for line in ve.MONITOR_LOG.read_text().splitlines()]
    assert [r["family"] for r in rows] == ["VEL_EXIT"]
    assert rows[0]["verdict"] == "VEC_SWITCH_EXIT"


def test_rule_b_route_gated_per_sym():
    """REDUCE_K parity (§126.1 step 3): the 3m-structure route must read the
    per-sym switch, never the global default."""
    src = (ROOT / "ez_positions_quick.py").read_text()
    assert '_sw_get(symbol, position_side, "RULE_B_3M_EXIT_ENABLED", False)' in src


def test_backstop_catches_stay_killed(tmp_path, monkeypatch):
    """2026-10-21 backstop audit round 2: STRONG_REDUCE is a 06-02-mandate
    stochastic trap whose PANIC sanction hole is now closed — stays killed.
    (ALL_TF_AGAINST was pinned here on a stale zero-callers verdict; 10-22
    ledger proof — 17 booked rows on KSMUSDT_LONG — restored it with the live
    lane narrowed to the 4 vector-visible TFs. See test_all_tf_against_restore.)"""
    assert "STRONG_REDUCE" not in ve.VEC_SWITCH_EXIT_FAMILIES
    _iso(tmp_path, monkeypatch)
    assert (
        ve.check(
            "STRONG_REDUCE", "a", "QUICK_REDUCE_STRONG_REDUCE_k_1m:98_SIMPLE_TP_0.16%"
        )[0]
        is False
    )
    src = (ROOT / "ez_positions_quick.py").read_text()
    assert '"PANIC",' not in src, "PANIC sanction hole must stay closed"


def test_new_kill_files_retrace_order(tmp_path, monkeypatch):
    """Every newly killed family files one TODO retrace work order (BIBLE §126.1)."""
    k, v = _iso(tmp_path, monkeypatch)
    q = ve.RETRACE_QUEUE
    ok, _ = ve.check("CLOSE", "ang:X_LONG", "MAKER_PROFIT_EXIT_px1_g0.05")
    assert ok is False
    rows = [json.loads(line) for line in q.read_text().splitlines()]
    assert len(rows) == 1 and rows[0]["family"] == "MAKER_PROFIT_EXIT"
    assert rows[0]["status"] == "TODO" and "retrace" in rows[0]
    ok2, _ = ve.check("CLOSE", "ang:Y_LONG", "MAKER_PROFIT_EXIT_px2_g0.1")
    assert ok2 is False
    rows2 = [json.loads(line) for line in q.read_text().splitlines()]
    assert len(rows2) == 1, "one TODO row per family, no per-bar spam"


def test_off_bar_entries_refused_without_kill(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    import time as _t

    now = int(_t.time() // 900) * 900 + 400
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok, verdict = ve.check("QUICK_OPEN", "ang:X_LONG", "QUICK_OPEN_STRONG_BUY_k1")
    assert (ok, verdict) == (False, "OFF_BAR_WINDOW")
    assert not ve.KILLS.exists(), "timing refusal must not kill"


def test_enforce_off_allows_all(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch, enforce=False)
    assert ve.check("REDUCE", "a", "VEL_EXIT_x") == (True, "ENFORCE_OFF")


def test_execute_now_calls_enforcer():
    src = (ROOT / "ez_manage.py").read_text()
    assert "vector_enforce" in src and "VECTOR_EXEC_REFUSED" in src


def test_quick_category_never_killed(tmp_path, monkeypatch):
    """USER 2026-10-21 retrace: QUICK_* is the whole twin-lane dispatcher — the family
    is the embedded rec token, and the category itself can never be disabled."""
    k, v = _iso(tmp_path, monkeypatch)
    assert (
        ve.family_of(
            "QUICK_REDUCE_SCALP_REDUCE_k_1m:89/d_1m:89_TIGHT_LEASH_PROFIT_SAVE_REDUCE"
        )
        == "SCALP_REDUCE"
    )
    ok, verdict = ve.check(
        "REDUCE", "inf:X_LONG", "QUICK_REDUCE_SCALP_REDUCE_k_1m:89_TIGHT_LEASH_REDUCE"
    )
    assert (ok, verdict) == (False, "VIOLATION_FAMILY_KILLED")
    assert "SCALP_REDUCE" in json.loads(
        k.read_text()
    ), "sub-rec family killed, not QUICK_*"
    assert "QUICK_REDUCE" not in json.loads(
        k.read_text()
    ) and "QUICK" not in json.loads(k.read_text())
    ok2, v2 = ve.check(
        "REDUCE", "inf:Y_LONG", "QUICK_CLOSE_STRONG_REDUCE_k1_TIGHT_REDUCE"
    )
    assert (ok2, v2) == (False, "VIOLATION_FAMILY_KILLED")
    assert "STRONG_REDUCE" in json.loads(k.read_text())


def test_vec_switch_exits_skip_kill_keep_window(tmp_path, monkeypatch):
    """BB_TAKE_4h under a promoting board is vector-modeled switch output, not a violation."""
    _iso(tmp_path, monkeypatch)
    assert ve.family_of("CLOSE BB_TAKE_4h_g0.02") == "BB_TAKE"
    ok, verdict = ve.check("CLOSE", "inf:X_LONG", "BB_TAKE_4h_g0.02")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    ok2, v2 = ve.check("CLOSE", "inf:X_LONG", "BB_TAKE_4h_g0.02")
    assert (ok2, v2) == (False, "OFF_BAR_WINDOW")


def test_board_confirmed_and_stops_skip_window(tmp_path, monkeypatch):
    _iso(tmp_path, monkeypatch)
    monkeypatch.setattr(ve, "in_bar_window", lambda now=None: False)
    assert ve.check("CLOSE", "a", "VEC_DRIVEN_EXIT_RECONCILE_x")[0] is True
    assert ve.check("CLOSE", "a", "BOARD_CONVERGE_CLOSE vec=leg")[0] is True
    assert ve.check("CLOSE", "a", "FIXED_STOP_HIT_px")[0] is True
    ok, v = ve.check("CLOSE", "a", "WT_LOWER_CROSS_EXIT")
    assert (ok, v) == (False, "OFF_BAR_WINDOW")


def test_e1_wt_delta_restore(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: E_1_WT_DELTA leg booked in v12 +
    faithful twin both venues + boards hold the switch (96 sym_sides). Killed
    10-22 13:28Z FOR an E_1 reason (false positive) — restored, never re-kill."""
    _iso(tmp_path, monkeypatch)
    assert ve.family_of("E_1_WT_DELTA_EXIT_delta=-19_thr=0_g0.32") == "E_1"
    assert "E_1" in ve.VEC_SWITCH_EXIT_FAMILIES
    ok, verdict = ve.check("CLOSE", "trb:SHOP_LONG", "E_1_WT_DELTA_EXIT_delta=-19_thr=0_g0.32")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    assert not (tmp_path / "vector_kills.json").exists()
    lane_src = (ROOT / "ez_manage.py").read_text()
    assert "_e1_wt_delta_fires" in lane_src, "E_1 lane must call the shared fire core"


def test_bb_band_live_mapping(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: BB_BAND_EXIT_LIVE_* reasons are the
    shared-core twin of the booked BB legs — the BB_LOSS/BB_TAKE body names the
    modeled member even though the head names killed parent BB. Killed 10-22
    19:35Z FOR a BB_LOSS reason (false positive) — mapping fixed, BB restored."""
    _iso(tmp_path, monkeypatch)
    assert ve.family_of("BB_BAND_EXIT_LIVE_BB_LOSS_15m_g=-0.64%") == "BB_LOSS"
    assert ve.family_of("BB_BAND_EXIT_LIVE_BB_TAKE_15m_g=0.64%") == "BB_TAKE"
    ok, _ = ve.check("CLOSE", "trb:GE_SHORT", "BB_BAND_EXIT_LIVE_BB_LOSS_15m_g=-0.64%")
    assert ok is True
    assert not (tmp_path / "vector_kills.json").exists()


def test_oversize_trim_and_non_tradeable_allowed(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore (mandates): OVERSIZE_TRIM_* partial trims
    (USER 2026-10-17 trim-excess-even-at-loss; 283 boards hold the switch) and
    NON_TRADEABLE_* exits (USER 2026-10-06 exit-dead-symbols) are never refused
    and never killed. Killed 10-22 13:35/15:54Z (false positives) — restored."""
    _iso(tmp_path, monkeypatch)
    ok, v = ve.check("REDUCE", "trb:BWXT_SHORT", "OVERSIZE_TRIM_LOSS_cap3600")
    assert (ok, v) == (True, "OVERSIZE_TRIM_ALLOWED")
    ok2, v2 = ve.check("CLOSE", "trb:SCHW_SHORT", "NON_TRADEABLE_WT15M_EXIT_SHORT_wt27.1_g-0.84")
    assert (ok2, v2) == (True, "NON_TRADEABLE_EXIT_ALLOWED")
    assert "OVERSIZE" in ve.NEVER_KILL_FAMILIES
    assert "NON" in ve.NEVER_KILL_FAMILIES
    assert not (tmp_path / "vector_kills.json").exists()


def test_wt_percentile_twin_matches_vector():
    """USER 2026-10-22 parity restore: the stocks twin
    (twin_sizing_reduce.wt_percentile_fires) and the booked v12 leg
    (check_wt_percentile_exit_vec) agree on a threshold grid, including the 15m
    turn-confirm boundary both live lanes require. Proves the RESTORE below."""
    from vec_decisions import twin_sizing_reduce as tsr
    from vec_decisions.process_position_crypto__wt_percentile_exit import (
        check_wt_percentile_exit_vec as vec,
    )

    import numpy as np

    class Cfg:
        WT_PERCENTILE_EXIT_ENABLED = True
        WT_PERCENTILE_EXIT_OB_D = 75.0
        WT_PERCENTILE_EXIT_OB_4H = 55.0
        WT_PERCENTILE_EXIT_OS_D = 10.0
        WT_PERCENTILE_EXIT_OS_4H = 25.0

    get = lambda k, d=None: getattr(Cfg, k, d)  # noqa: E731
    cfg = Cfg()
    n_agree = n_fire = 0
    for pd in (74.0, 75.0, 76.0, 9.0, 10.0, 11.0):
        for p4 in (54.0, 55.0, 56.0, 24.0, 25.0, 26.0):
            for w1, w2 in ((40.0, 50.0), (50.0, 40.0), (50.0, 50.0)):
                for is_long in (True, False):
                    ind = {
                        "wt_percentile_D": pd,
                        "wt_percentile_4h": p4,
                        "wt1_15m": w1,
                        "wt2_15m": w2,
                    }
                    t_fire, _ = tsr.wt_percentile_fires(get, ind, is_long)
                    v_fire = bool(
                        vec(
                            cfg,
                            np.array([pd]),
                            np.array([p4]),
                            is_long,
                            np.array([w1]),
                            np.array([w2]),
                        )[0]
                    )
                    assert t_fire == v_fire, (pd, p4, w1, w2, is_long)
                    n_agree += 1
                    n_fire += t_fire
    assert n_fire > 0 and n_agree == 6 * 6 * 3 * 2


def test_wt_percentile_restore(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: WT_PERCENTILE leg booked in v12
    (wt_percentile_exit OR'd into _all_exit) + faithful twin both venues
    (fidelity pinned above) + 45 stocks boards hold the switch. The 647
    10-22 trb/trc refusals (UNATTRIBUTED, never kill) flow in-window."""
    _iso(tmp_path, monkeypatch)
    assert "WT" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert ve.family_of("WT_PERCENTILE_pctD=2_4h=22_g-0.86") == "WT"
    ok, verdict = ve.check("CLOSE", "trb:SCHW_SHORT", "WT_PERCENTILE_pctD=2_4h=22_g-0.86")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    v12_src = (ROOT / "v12_quick_engine.py").read_text()
    assert "| wt_percentile_exit" in v12_src, "leg must be OR'd into _all_exit"
    assert "WT_PERCENTILE_EXIT_ENABLED" in v12_src
    assert not (tmp_path / "vector_kills.json").exists()


def _ksm_npz():
    import numpy as np
    raw = np.load(ROOT / "backtest_v8/indicators/KSMUSDT.npz", allow_pickle=True)
    npz = {}
    for k in raw.files:
        try:
            a = np.asarray(raw[k], dtype=float).ravel()
            if a.size > 100:
                npz[k] = a
        except Exception:
            pass
    return npz, min(len(v) for v in npz.values())


def test_mtf_core_matches_live_lane_arithmetic():
    """USER 2026-10-22 parity restore: the shared scalar+vec core must compute
    exactly what each live lane computes — ez counts 15m/1h/4h/D (ez 54706),
    tradier counts 1h/4h/D/W (tradier 18055), both with the wt1!=0 guard and
    the 0.0 missing default. Real KSMUSDT bars, both sides."""
    import numpy as np
    from vec_decisions.process_position_stocks__mtf_gr_wt_exit import (
        check_mtf_gr_wt_exit_vec as core,
    )
    npz, n = _ksm_npz()

    class Cfg:
        MTF_GR_EXIT_GATE_ENABLED = True
        MTF_WT_CROSS_EXIT_ENABLED = True
        MTF_GR_EXIT_MIN_TFS = 3
        MTF_WT_CROSS_EXIT_TF = "1h"

    cfg = Cfg()
    c1 = npz["wt1_1h"][:n]
    c2 = npz["wt2_1h"][:n]
    for is_long in (True, False):
        for set_name, tfs in (
            ("ez", ("15m", "1h", "4h", "D")),
            ("tradier", ("1h", "4h", "D", "W")),
        ):
            g1 = [npz[f"wt1_{t}"][:n] for t in tfs]
            g2 = [npz[f"wt2_{t}"][:n] for t in tfs]
            got = np.asarray(core(cfg, c1, c2, g1, g2, is_long), dtype=bool)
            # Independent mirror of the live lane arithmetic (safe_fetch 0.0,
            # wt1!=0 guard, count>=3, cross-against on the 1h pair).
            if is_long:
                against = c1 < c2
            else:
                against = c1 > c2
            cnt = np.zeros(n, dtype=int)
            for a1, a2 in zip(g1, g2):
                if is_long:
                    cnt += ((a1 < a2) & (a1 != 0)).astype(int)
                else:
                    cnt += ((a1 > a2) & (a1 != 0)).astype(int)
            want = against & (cnt >= 3)
            assert (got == want).all(), (set_name, is_long)
            assert got.sum() > 0, (set_name, is_long, "fixture must fire")


def test_mtf_engine_delta_within_core_mask():
    """USER 2026-10-22 parity restore: the v12 MTF leg must feed the exit
    aggregate exactly the venue TF set the live lane counts — the ON-vs-OFF
    aggregate delta on real KSM bars stays inside the independently computed
    core mask, in all four venue/side arms."""
    import numpy as np
    import v12_quick_engine as V
    from vec_decisions.process_position_stocks__mtf_gr_wt_exit import (
        check_mtf_gr_wt_exit_vec as core,
    )
    npz, n = _ksm_npz()
    for mode, tfs in (
        ("crypto", ("15m", "1h", "4h", "D")),
        ("tradier", ("1h", "4h", "D", "W")),
    ):
        for is_long in (True, False):
            on = V.QuickConfig()
            on.MODE = mode
            on.MTF_GR_EXIT_GATE_ENABLED = True
            on.MTF_WT_CROSS_EXIT_ENABLED = True
            on.MTF_WT_CROSS_EXIT_TF = "1h"
            off = V.QuickConfig()
            off.MODE = mode
            off.MTF_GR_EXIT_GATE_ENABLED = False
            off.MTF_WT_CROSS_EXIT_ENABLED = True
            off.MTF_WT_CROSS_EXIT_TF = "1h"
            m_on = V.compute_exit_signals(npz, n, is_long, on)
            m_off = V.compute_exit_signals(npz, n, is_long, off)
            delta = np.asarray(m_on & ~m_off, dtype=bool)
            assert int(delta.sum()) > 0, (mode, is_long, "leg must fire")
            mask = np.asarray(
                core(
                    on,
                    npz["wt1_1h"][:n],
                    npz["wt2_1h"][:n],
                    [npz[f"wt1_{t}"][:n] for t in tfs],
                    [npz[f"wt2_{t}"][:n] for t in tfs],
                    is_long,
                ),
                dtype=bool,
            )
            assert ((delta & ~mask).sum() == 0), (mode, is_long)


def test_mtf_tradier_walk_arm_wired():
    """USER 2026-10-22 parity restore: the tradier walk arm books MTF_GR closes
    (the crypto compound block is crypto-only). Measured 10-22 on MINAUSDT
    MODE=tradier SHORT: triple-gates OFF -> 0 MTF_GR rows / 773 trades;
    triple ON (COMPOUND+GATE+CROSS, TF 1h, min 3) -> 14 MTF_GR_WT_EXIT_1h
    rows / 785 trades. This test pins the wiring; the numbers above are the
    recorded booking proof."""
    v12_src = (ROOT / "v12_quick_engine.py").read_text()
    assert "_mtftr_w1 is not None" in v12_src
    assert "gr_wt_exit_fires(" in v12_src
    assert '"MTF_GR_WT_EXIT_{_mtftr_tf}_grTFs={_mtftr_cnt}"' in v12_src
    # Round 2: tradier DC + BB arms (16 stocks syms hold the combo live).
    # Measured 10-22 on MINAUSDT MODE=tradier SHORT alongside the GR arm:
    # all-off 0 MTF rows / 773 trades; all-on DC 14 + BB 5 + GR 10.
    assert "_mtftrdc_band is not None" in v12_src
    assert "_mtftrdc_stepfn(" in v12_src
    assert '"MTF_DC_REJECT_{_mtftrdc_tf}_px{px:.4f}"' in v12_src
    assert "_mtftrbb_u is not None" in v12_src
    assert "bb_reject_step(" in v12_src
    assert '"MTF_BB_REJECT_{_mtftrbb_tf}"' in v12_src
    # Tradier arm follows live defaults (COMPOUND/GATE/CROSS False): it must
    # NOT ride the True-default crypto master, or unpromoted syms diverge.
    assert "MTF_EXIT_USE_COMPOUND\", False" in v12_src


def test_mtf_gr_restore(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: MTF_GR_WT_EXIT (killed 10-22 20:47Z on
    fin:KSMUSDT_LONG) and GR_HTF_DIRECT_EXIT (killed 10-22 14:00Z on
    trb:CRWD_LONG) flow in-window — legs booked (fidelity pinned above), twins
    faithful after the venue-split + ez guard-port fixes."""
    _iso(tmp_path, monkeypatch)
    assert "MTF" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert "GR" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert ve.family_of("MTF_GR_WT_EXIT_1h_grTFs=3_g-0.44%") == "MTF"
    ok, verdict = ve.check("CLOSE", "fin:KSMUSDT_LONG", "MTF_GR_WT_EXIT_1h_grTFs=3_g-0.44%")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    ok2, verdict2 = ve.check("CLOSE", "trb:CRWD_LONG", "GR_HTF_DIRECT_EXIT_8_g1.72%_tfs=4/1req")
    assert (ok2, verdict2) == (True, "VEC_SWITCH_EXIT")
    v12_src = (ROOT / "v12_quick_engine.py").read_text()
    assert "| mtf_gr_exit" in v12_src, "MTF leg must be OR'd into _all_exit"
    assert "| gr_htf_exit" in v12_src, "GR leg must be OR'd into _all_exit"
    assert "process_position_stocks__mtf_gr_wt_exit" in v12_src
    ez_src = (ROOT / "ez_manage.py").read_text()
    for marker in (
        "GR_HTF_DIRECT_EXIT_GAIN_AGE_VETO",
        "_GR_HTF_EXIT_CONFIRM_STATE_EZ",
        "GR_HTF_DIRECT_EXIT_HTF_VETO",
        "GR_HTF_DIRECT_EXIT_STRUCT_VETO",
        "WT_PERCENTILE_STRUCT_VETO",
        "E_1_STRUCT_VETO",
    ):
        assert marker in ez_src, marker
    # MTF books unvetoed on both venues (crypto compound block + tradier walk
    # arm): neither live lane may carry a structural veto — assert absence.
    assert "MTF_GR_STRUCT_VETO" not in ez_src
    tr_src = (ROOT / "tradier_manage.py").read_text()
    assert "MTF_GR_STRUCT_VETO" not in tr_src
    assert "TSR_STRUCT_VETO" in tr_src
    # Veto precedes the order in every hardened lane (a veto after the send is no veto).
    assert ez_src.index("WT_PERCENTILE_STRUCT_VETO") < ez_src.index("WT_PERCENTILE_pctD=")
    assert ez_src.index("E_1_STRUCT_VETO") < ez_src.index("E_1_WT_DELTA_EXIT_delta=")
    assert tr_src.index("TSR_STRUCT_VETO") < tr_src.index("_tiered_tp_hit_stocks")
    assert not (tmp_path / "vector_kills.json").exists()


def test_gr_twin_two_bar_staging():
    """USER 2026-10-22 parity restore: the shared GR twin fires only on two
    consecutive score>=min bars (bar 0 never fires) — the staging the ez lane
    now mirrors. Real KSMUSDT bars, both sides."""
    import numpy as np
    import v12_quick_engine as V
    from vec_decisions import gr_htf_direct_entry as grd
    npz, n = _ksm_npz()
    for is_long in (True, False):
        cfg = V.QuickConfig()
        cfg.GR_HTF_DIRECT_EXIT_ENABLED = True
        close = V._base_safe(npz, "close", n, cfg)
        twin = np.asarray(grd.gr_direct_exit_mask(npz, n, is_long, cfg, close), dtype=bool)
        assert twin.dtype == bool and twin.shape == (n,)
        assert not twin[0], "bar 0 never fires (cold counter)"
        gate = grd._resolve_gate(cfg)
        score = grd._gate_score_vec(
            npz, n, not is_long, np.asarray(close, dtype=float),
            gate["dc_thr"], gate["bb_thr"], gate["vote_min"], gate["min_tfs"],
            gate["req_act"], gate["act_list"], gate["entry_list"],
            gate["default_tfs"], gate["min_ind"],
        )
        smin = float(getattr(cfg, "GR_HTF_DIRECT_EXIT_SCORE", 22.0))
        ok = np.asarray(score, dtype=float) >= smin
        # Twin == two-bar staging of the raw score mask, nothing else.
        want = np.zeros(n, dtype=bool)
        want[1:] = ok[1:] & ok[:-1]
        assert (twin == want).all(), is_long


def test_all_tf_against_restore(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: ALL_TF_AGAINST_CLOSE (killed 10-22 on
    fin:ALGOUSDT_LONG) flows in-window — booked in v12 via the shared 4-TF
    twin, live lane narrowed to the same 4 TFs (3m exists in no NPZ)."""
    import numpy as np
    from vec_decisions.process_position_crypto__all_tf_against import (
        check_all_tf_against_vec as core,
    )
    _iso(tmp_path, monkeypatch)
    assert "ALL_TF_AGAINST" in ve.VEC_SWITCH_EXIT_FAMILIES
    ok, verdict = ve.check("CLOSE", "fin:ALGOUSDT_LONG", "ALL_TF_AGAINST_CLOSE_3m-71_15m-16_1h-49_4h59_D-28")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    npz, n = _ksm_npz()
    for is_long in (True, False):
        got = np.asarray(
            core(
                None,
                {t: npz[f"wt1_{t}"][:n] for t in ("15m", "1h", "4h", "D")},
                {t: npz[f"wt2_{t}"][:n] for t in ("15m", "1h", "4h", "D")},
                is_long,
                min_tfs=4,
            ),
            dtype=bool,
        )
        cnt = np.zeros(n, dtype=int)
        for t in ("15m", "1h", "4h", "D"):
            a1, a2 = npz[f"wt1_{t}"][:n], npz[f"wt2_{t}"][:n]
            cnt += ((a1 < a2) if is_long else (a1 > a2)).astype(int)
        assert (got == (cnt >= 4)).all(), is_long
    ez_src = (ROOT / "ez_manage.py").read_text()
    assert "int(_at_w1_3m < _at_w2_3m)" not in ez_src
    assert "int(_at_w1_3m > _at_w2_3m)" not in ez_src
    assert not (tmp_path / "vector_kills.json").exists()


def test_ratio_on_operator_order(tmp_path, monkeypatch):
    """USER 2026-10-22 OPERATOR ORDER ("ratio is L/S, make sure that is on"):
    the RATIO rebalance REDUCE/CLOSE/OW arms flow (de-risk toward target);
    opens/augments stay stood down (never vector-dictated). This is an
    operator override, not a parity proof — no vector model exists."""
    _iso(tmp_path, monkeypatch)
    assert "RATIO" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert ve.family_of("RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%") == "RATIO"
    ok, verdict = ve.check("REDUCE", "fin:LINKUSDC_LONG", "RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    ez_src = (ROOT / "ez_manage.py").read_text()
    assert "for _rpk, _rpos, _rgain in _reduce_candidates:" in ez_src
    assert "for _cpk2, _cpos2, _cgain2 in _close_candidates:" in ez_src
    assert "for _ow_pk, _ow_pos, _ow_gain, _ow_delta in _ow_candidates:" in ez_src
    assert "RATIO opens/augments stay stood down" in ez_src
    assert not (tmp_path / "vector_kills.json").exists()


def test_dc_daytrade_restore(tmp_path, monkeypatch):
    """USER 2026-10-22 parity restore: DC_HOPELESS (killed 10-22 13:33Z on
    trb:BNO_LONG, board-authorized) and DAYTRADE_TARGET (killed 10-22 13:31Z
    on trb:LRCX_SHORT, +10.32% winner refused exit) flow in-window — both
    booked in v12 via shared twins + live lanes both venues."""
    _iso(tmp_path, monkeypatch)
    assert "DC_HOPELESS" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert "DAYTRADE_TARGET" in ve.VEC_SWITCH_EXIT_FAMILIES
    assert ve.family_of("DC_HOPELESS_entry=68.7591_dc=[64.0000,68.7400]_g0.00") == "DC_HOPELESS"
    assert ve.family_of("DC_HOPELESS_TWIN_entry68.7591_g0.00%") == "DC_HOPELESS"
    ok, verdict = ve.check("CLOSE", "trb:BNO_LONG", "DC_HOPELESS_entry=68.7591_dc=[64.0000,68.7400]_g0.00")
    assert (ok, verdict) == (True, "VEC_SWITCH_EXIT")
    ok2, verdict2 = ve.check("CLOSE", "trb:LRCX_SHORT", "DAYTRADE_TARGET_dc_1h_low_+0.10%_TARGET_BUF_g10.32")
    assert (ok2, verdict2) == (True, "VEC_SWITCH_EXIT")
    assert not (tmp_path / "vector_kills.json").exists()


def test_dc_hopeless_twin_grid():
    """USER 2026-10-22 parity restore: the shared dc_hopeless twin fires iff
    entry is outside the dc_4h channel on the hopeless side AND age beats the
    minimum — the exact live predicate, both sides, boundary included."""
    from vec_decisions.grey_wire_exits import dc_hopeless
    get = lambda k, d=None: {"DC_HOPELESS_EXIT_MIN_AGE_S": 900}.get(k, d)
    g = lambda k, d=None: {"dc_high_4h": 68.74, "dc_low_4h": 64.00}.get(k, d)
    cases = [
        # (is_long, entry, age_s, want)
        (True, 68.7591, 901.0, True),
        (True, 68.7591, 900.0, False),
        (True, 68.74, 5000.0, False),
        (True, 60.00, 5000.0, False),
        (False, 63.99, 901.0, True),
        (False, 63.99, 899.0, False),
        (False, 64.00, 5000.0, False),
        (False, 70.00, 5000.0, False),
    ]
    for is_long, entry, age, want in cases:
        fire, reason = dc_hopeless(get, g, {"entry_px": entry, "age_s": age}, is_long)
        assert fire is want, (is_long, entry, age)
        if want:
            assert reason.startswith("DC_HOPELESS_entry="), reason


def test_daytrade_target_twin_boundaries():
    """USER 2026-10-22 parity restore: the shared daytrade_dc_exit twin fires
    a 1h TARGET exactly at px >= dc_high_1h*(1-buf) LONG / px <= dc_low_1h*
    (1+buf) SHORT — the killed LRCX dc_1h shape, boundary-probed."""
    from vec_decisions.dc_channel_exits import daytrade_dc_exit
    spec = {"tf": "1h", "field_long": "dc_high_1h", "field_short": "dc_low_1h", "buf": 0.001, "mode": "day", "tag": "dc_1h"}
    lv = lambda f: {"dc_high_1h": 100.0, "dc_low_1h": 90.0}.get(f, 0.0)
    fire, reason = daytrade_dc_exit(99.95, True, [], [spec], lv)
    assert fire is True and reason.startswith("DAYTRADE_TARGET dc_1h_high -0.10%"), reason
    fire2, _ = daytrade_dc_exit(99.85, True, [], [spec], lv)
    assert fire2 is False
    fire3, reason3 = daytrade_dc_exit(90.05, False, [], [spec], lv)
    assert fire3 is True and reason3.startswith("DAYTRADE_TARGET dc_1h_low +0.10%"), reason3
    fire4, _ = daytrade_dc_exit(90.15, False, [], [spec], lv)
    assert fire4 is False
