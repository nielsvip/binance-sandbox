"""USER 2026-10-27 operator rulings, phase 2: STALE_SET_HALT / VIGILANCE_DC4 / SYMBOL_LOSS_STREAK are one predicate on
both sides (live + v12_quick_engine). These tests pin the shared modules and that both engines call them."""

import itertools
import pathlib
import time

import stale_set_halt as live_ssh
from vec_decisions import stale_set_halt_twin as twin
from vec_decisions import vigilance_dc4 as vig

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _iso(e):
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(e))


def test_stale_twin_equals_live_evaluate_on_a_grid():
    now = 1_800_000_000.0
    mism = 0
    n = 0
    for age_d, apply_off_d, nets in itertools.product((1, 9.9, 10.0, 10.1, 30), (0.5, 5, 20), ([], [-1.0], [1.0, -1.0], [-1.0, -2.0], [-0.1, 0.2, -0.3, -0.4], [0.5, 0.5])):
        recalc = now - age_d * 86400.0
        apply_ep = now - apply_off_d * 86400.0
        closes_ts = [apply_ep + 3600.0 * (k + 1) for k in range(len(nets))]
        row = {"updated_at": _iso(apply_ep), "campaign_ts": _iso(apply_ep), "winning_tag": "", "meta_json": {}, "overrides_md5": "x"}
        live_closes = [{"ts": _iso(t), "net_pct": p} for t, p in zip(closes_ts, nets)]
        anc = {"recalc_epoch": recalc, "apply_epoch": live_ssh.iso_epoch(row["campaign_ts"])}
        hit, _ = live_ssh.trailing_losses(live_closes, anc["apply_epoch"], 2)
        live_halt = max(0.0, (now - recalc) / 86400.0) > 10.0 and hit  # the HALT condition of stale_set_halt.evaluate
        vec_halt, _ = twin.halt_fires(now, recalc, anc["apply_epoch"], list(zip(closes_ts, nets)), 10.0, 2)
        n += 1
        mism += int(bool(live_halt) != bool(vec_halt))
    assert n == 90 and mism == 0


def test_live_evaluate_routes_through_twin():
    src = (ROOT / "stale_set_halt.py").read_text()
    assert "_ssh_twin.halt_decision(age_days, days, hit)" in src
    assert "_ssh_twin.last_n_all_losses(" in src


def test_stale_twin_inert_without_anchor():
    assert twin.halt_fires(2e9, 0.0, 0.0, [(1.0, -5.0), (2.0, -5.0)], 10.0, 2) == (False, "NO_ANCHOR")


def test_engine_and_forward_state_wiring():
    eng = (ROOT / "v12_quick_engine.py").read_text()
    assert "_ssh_twin.halt_fires(_ssh_now, _ssh_recalc, _ssh_apply, _ssh_closes, _ssh_days, _ssh_losses)" in eng
    assert "_vig_dc4_mod.breach(is_long, px, _vg_lvl, _vig_tol * 100.0)" in eng
    assert "_vig_dc4_on = _vig_dc4_mod.armed(" in eng
    fs = (ROOT / "tools" / "v15_forward_state.py").read_text()
    assert '"STALE_SET_RECALC_EPOCH": _anc["STALE_SET_RECALC_EPOCH"]' in fs
    ez = (ROOT / "ez_manage.py").read_text()
    assert "_vig_dc4_mod.armed(_vg_get)" in ez and "_vig_dc4_mod.fires(" in ez
    assert "STALE_SET_PARITY_EXEMPT" not in ez
    assert "_sls.record_close(" in ez and "_sls.is_blocked(" in ez


def test_quickconfig_defaults_keep_behaviour():
    import v12_quick_engine as V
    c = V.QuickConfig()
    assert c.VIGILANCE_DC4_STOP_ARMED is False
    assert c.STALE_SET_RECALC_EPOCH == 0.0 and c.STALE_SET_APPLY_EPOCH == 0.0
    assert c.SYMBOL_LOSS_STREAK_BREAKER_ENABLED is False
    assert vig.armed(lambda k, d: getattr(c, k, d)) is False


def test_vigilance_predicate_equals_old_inline_formula():
    mism = 0
    for is_long, px, lvl, tol, gain in itertools.product((True, False), (98.0, 99.7, 99.75, 99.8, 100.0, 100.2, 100.25, 100.3, 102.0), (0.0, 100.0), (0.0, 0.25, 0.5), (-1.0, 0.0, 1.0)):
        old = gain < 0 and lvl > 0 and px > 0 and ((is_long and px <= lvl * (1 - tol / 100.0)) or ((not is_long) and px >= lvl * (1 + tol / 100.0)))
        mism += int(old != vig.fires(is_long, px, lvl, tol, gain))
    assert mism == 0
    assert vig.armed(lambda k, d: {"VIGILANCE_DC4_STOP_ARMED": True, "VIGILANCE_DC4_STOP_TF": "OFF"}.get(k, d)) is False
    assert vig.armed(lambda k, d: {"VIGILANCE_DC4_STOP_ARMED": True}.get(k, d)) is True
