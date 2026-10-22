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
    """USER 2026-10-21 (+18:0x, 'augment too'): RATIO trims (REDUCE) and adds
    (AUGMENT) stay allowed in-window; OPEN/CLOSE fall through to the kill."""
    _iso(tmp_path, monkeypatch)
    ok, verdict = ve.check("REDUCE", "a", "RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%")
    assert (ok, verdict) == (True, "RATIO_TRIM_ALLOWED")
    ok, verdict = ve.check(
        "AUGMENT", "a", "RATIO_REDUCE_SHORT_L0_S100_tgt25/75_breadth26%_gain0.2%"
    )
    assert (ok, verdict) == (True, "RATIO_TRIM_ALLOWED")
    ok, verdict = ve.check("CLOSE", "a", "RATIO_CLOSE_LONG_L100_S0_tgt30/70_gain1.5%")
    assert ok is False and verdict == "VIOLATION_FAMILY_KILLED"
    ok, verdict = ve.check(
        "OPEN", "a", "RATIO_REDUCE_SHORT_L0_S100_tgt25/75_breadth26%_gain0.2%"
    )
    assert ok is False


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
    """2026-10-21 backstop audit round 2: ALL_TF_AGAINST_CLOSE has vector code
    with zero callers (dead template wiring, never books); STRONG_REDUCE is a
    06-02-mandate stochastic trap whose PANIC sanction hole is now closed.
    Both stay killed."""
    assert "ALL_TF_AGAINST" not in ve.VEC_SWITCH_EXIT_FAMILIES
    assert "STRONG_REDUCE" not in ve.VEC_SWITCH_EXIT_FAMILIES
    _iso(tmp_path, monkeypatch)
    assert (
        ve.check("CLOSE", "a", "ALL_TF_AGAINST_CLOSE_3m-53_15m1_1h65_4h69_D-28")[0]
        is False
    )
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
