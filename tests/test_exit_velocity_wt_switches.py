"""EXIT_VELOCITY_WT_MIN_TFS + EXIT_VELOCITY_WT_MIN_HOLD_ENABLED (2026-10-08): shared predicate + engine behaviour."""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("BASE_PATH", str(ROOT))
from vec_decisions import twin_exits_dead as T  # noqa: E402


def _get(d):
    return lambda k, dflt: d.get(k, dflt)


def test_default_min_tfs_fires_on_any_tf_with_unchanged_reason():
    ind = {"wt_velocity_1h": -2.5, "wt_velocity_4h": 1.0, "wt_velocity_D": 1.0}
    fire, reason = T.velocity_wt_exit_live_fire(ind, True, _get({}))
    assert fire and reason == "EXIT_VELOCITY_WT_1h_vel-2.5-against-long"


def test_min_tfs_2_needs_two_against():
    ind = {"wt_velocity_1h": -2.5, "wt_velocity_4h": 1.0, "wt_velocity_D": 1.0}
    assert T.velocity_wt_exit_live_fire(ind, True, _get({"EXIT_VELOCITY_WT_MIN_TFS": 2}))[0] is False
    ind["wt_velocity_4h"] = -0.1
    fire, reason = T.velocity_wt_exit_live_fire(ind, True, _get({"EXIT_VELOCITY_WT_MIN_TFS": 2}))
    assert fire and reason.startswith("EXIT_VELOCITY_WT_1h_vel-2.5-against-long")


def test_short_mirror_and_fire_at():
    import numpy as np
    arrs = {"1h": np.array([0.0, 3.0]), "4h": np.array([0.0, -1.0]), "D": np.array([0.0, 2.0])}
    assert T.velocity_wt_fire_at(arrs, 1, False, _get({})) is True
    assert T.velocity_wt_fire_at(arrs, 1, False, _get({"EXIT_VELOCITY_WT_MIN_TFS": 2})) is True
    assert T.velocity_wt_fire_at(arrs, 1, False, _get({"EXIT_VELOCITY_WT_MIN_TFS": 3})) is False
    assert T.velocity_wt_fire_at(arrs, 1, True, _get({"EXIT_VELOCITY_WT_MIN_TFS": 2})) is False


def test_quickconfig_and_configs_carry_the_fields():
    import v12_quick_engine as V
    import config as C
    import config_tradier as CT
    c = V.QuickConfig()
    assert c.EXIT_VELOCITY_WT_MIN_TFS == 1 and c.EXIT_VELOCITY_WT_MIN_HOLD_ENABLED is False
    assert C.Config.EXIT_VELOCITY_WT_MIN_TFS == 1 and C.Config.EXIT_VELOCITY_WT_MIN_HOLD_ENABLED is False
    assert CT.TradierConfig.EXIT_VELOCITY_WT_MIN_TFS == 1


@pytest.mark.skipif(not (ROOT / "backtest_v8" / "indicators" / "1000BONKUSDC.npz").exists(), reason="no local NPZ")
def test_engine_ledger_flips_with_min_tfs_and_default_is_unchanged():
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized
    prep = prepare_batch("1000BONKUSDC_LONG", 30)
    base = evaluate_prepared_sanitized(prep, {}, 30, include_ledger=True)
    same = evaluate_prepared_sanitized(prep, {"EXIT_VELOCITY_WT_MIN_TFS": 1, "EXIT_VELOCITY_WT_MIN_HOLD_ENABLED": False}, 30, include_ledger=True)
    two = evaluate_prepared_sanitized(prep, {"EXIT_VELOCITY_WT_MIN_TFS": 2}, 30, include_ledger=True)
    hold = evaluate_prepared_sanitized(prep, {"EXIT_VELOCITY_WT_MIN_HOLD_ENABLED": True}, 30, include_ledger=True)
    vel = lambda r: sum(1 for x in (r.get("ledger") or []) if x.get("type") == "CLOSE" and "EXIT_VELOCITY_WT" in str(x.get("reason")))
    assert base["trades"] == same["trades"] and abs(float(base["gain_pct"]) - float(same["gain_pct"])) < 1e-9
    assert vel(base) > 0 and vel(two) < vel(base) and vel(hold) < vel(base)
