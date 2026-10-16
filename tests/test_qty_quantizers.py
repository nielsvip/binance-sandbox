"""USER 2026-10-16: STDEV 1-5x ladder + streak quantizers — one implementation, identical in vector and live."""
import re
import types
from pathlib import Path

import numpy as np

import vec_decisions.qty_quantizers as qq

ROOT = Path(__file__).resolve().parents[1]


def _cfg(**kw):
    base = dict(STDEV_SLOPE_SIZING_ENABLED=True, STDEV_LADDER_TF="D", STDEV_LADDER_MAX_MULT=5.0, QTY_STREAK_SIZING_ENABLED=True, QTY_STREAK_MIN_MOVE_PCT=1.0, QTY_STREAK_LOSS_COUNT=1, QTY_STREAK_LOSS_MULT=0.8, QTY_STREAK_WIN_COUNT=1, QTY_STREAK_WIN_STEP=0.2, QTY_STREAK_MAX_MULT=3.0, EXIT_DUST_USD=5.0)
    base.update(kw)
    return types.SimpleNamespace(**base)


def test_ladder_is_1x_top_5x_bottom_long_and_mirrored_short():
    c = _cfg()
    assert qq.ladder_scalar(1.0, True, c) == 1.0
    assert qq.ladder_scalar(0.0, True, c) == 5.0
    assert qq.ladder_scalar(0.5, True, c) == 3.0
    assert qq.ladder_scalar(0.0, False, c) == 1.0
    assert qq.ladder_scalar(1.0, False, c) == 5.0
    pbs = np.linspace(-0.2, 1.2, 57)
    longs = [qq.ladder_scalar(p, True, c) for p in pbs]
    shorts = [qq.ladder_scalar(p, False, c) for p in pbs]
    assert all(a >= b for a, b in zip(longs, longs[1:])), "long size must grow toward the bottom"
    assert all(a <= b for a, b in zip(shorts, shorts[1:])), "short size must grow toward the top"
    assert min(longs + shorts) == 1.0 and max(longs + shorts) == 5.0


def test_ladder_off_missing_or_nan_is_1x():
    assert qq.ladder_scalar(0.0, True, _cfg(STDEV_SLOPE_SIZING_ENABLED=False)) == 1.0
    assert qq.ladder_scalar(None, True, _cfg()) == 1.0
    assert qq.ladder_scalar(float("nan"), True, _cfg()) == 1.0
    assert qq.ladder_from_indicators({}, True, _cfg()) == 1.0
    assert qq.ladder_from_indicators({"lrL_pct_b_4h": 0.0}, True, _cfg()) == 1.0
    assert qq.ladder_from_indicators({"lrL_pct_b_4h": 0.0}, True, _cfg(STDEV_LADDER_TF="4h")) == 5.0


def test_ladder_vec_equals_scalar_bar_for_bar():
    c = _cfg(STDEV_LADDER_MAX_MULT=4.0)
    pb = np.array([0.0, 0.1, 0.33, 0.5, 0.77, 1.0, 1.3, -0.4, np.nan])
    for is_long in (True, False):
        v = qq.ladder_vec({"lrL_pct_b_D": pb}, len(pb), is_long, c)
        s = [qq.ladder_scalar(p, is_long, c) for p in pb]
        assert np.allclose(v, s)


def test_ladder_vec_matches_former_inline_block():
    rng = np.random.default_rng(7)
    pb = rng.uniform(-0.3, 1.3, 500)
    for is_long in (True, False):
        edge = np.clip((1.0 - pb) if is_long else pb, 0.0, 1.0)
        old = np.clip(1.0 + 4.0 * edge, 1.0, 5.0)
        assert np.array_equal(qq.ladder_vec({"lrL_pct_b_D": pb}, 500, is_long, _cfg()), old)


def _fold(moves, c):
    st = qq.new_state()
    for m in moves:
        qq.streak_update(st, m, c)
    return qq.streak_mult(st, c)


def test_streak_losses_shrink_and_small_results_are_ignored():
    c = _cfg()
    assert _fold([-1.5], c) == 0.8
    assert _fold([-1.5, -0.3, 0.4], c) == 0.8
    assert _fold([-0.9], c) == 1.0
    assert _fold([-0.6], _cfg(QTY_STREAK_MIN_MOVE_PCT=0.5)) == 0.8
    assert _fold([-2.0], _cfg(QTY_STREAK_LOSS_COUNT=2)) == 1.0
    assert _fold([-2.0, -1.0], _cfg(QTY_STREAK_LOSS_COUNT=2, QTY_STREAK_LOSS_MULT=0.4)) == 0.4
    assert _fold([-2.0, -2.0, -2.0], _cfg(QTY_STREAK_LOSS_MULT=0.6)) == 0.6


def test_streak_wins_compound_and_cap():
    c = _cfg(QTY_STREAK_WIN_STEP=0.5)
    assert _fold([1.2], c) == 1.5
    assert abs(_fold([1.2, 3.0], c) - 2.25) < 1e-12
    assert _fold([1.2, 3.0, 1.0, 1.0], c) == 3.0
    c2 = _cfg(QTY_STREAK_WIN_COUNT=2, QTY_STREAK_WIN_STEP=0.8, QTY_STREAK_MAX_MULT=10.0)
    assert _fold([2.0], c2) == 1.0
    assert _fold([2.0, 2.0], c2) == 1.8
    assert abs(_fold([2.0, 2.0, 2.0], c2) - 3.24) < 1e-12


def test_streak_resets_on_opposite_result_and_off_is_1x():
    c = _cfg()
    assert _fold([2.0, 2.0, -1.0], c) == 0.8
    assert _fold([-2.0, -2.0, 1.0], c) == 1.2
    assert _fold([-2.0, 5.0], _cfg(QTY_STREAK_SIZING_ENABLED=False)) == 1.0


def test_trade_move_pct_side_signed_from_average_entry():
    assert abs(qq.trade_move_pct(True, 100.0, 101.0) - 1.0) < 1e-12
    assert abs(qq.trade_move_pct(False, 100.0, 101.0) + 1.0) < 1e-12
    assert qq.trade_move_pct(True, 0.0, 101.0) is None


def test_new_lifecycle_actions_only():
    for a in ("OPEN", "QUICK_OPEN", "REENTRY", "REENTER", "HARDCODED_RALLY_REENTRY"):
        assert qq.is_new_lifecycle_action(a)
    for a in ("AUGMENT", "QUICK_AUGMENT", "REDUCE", "CLOSE", "HEDGE_OPEN", "REDUCE_TO_FLAT"):
        assert not qq.is_new_lifecycle_action(a)


def test_live_state_equals_vector_fold_with_lifecycle_dedupe_and_dust(tmp_path):
    c = _cfg(QTY_STREAK_WIN_STEP=0.5)
    seq = [(100.0, 102.0, "L1"), (100.0, 102.0, "L1"), (50.0, 50.2, "L2"), (10.0, 10.3, "L3"), (20.0, 19.0, "L4"), (20.0, 19.0, "DUST")]
    for e, x, life in seq:
        qq.live_record_close(tmp_path, "ez", "men", "ENJUSDT", "LONG", e, x, c, lifecycle=life, notional_usd=(3.0 if life == "DUST" else 60.0))
    vec = _fold([qq.trade_move_pct(True, e, x) for e, x, life in seq if life not in ("DUST",)][1:], c)
    assert qq.live_mult(tmp_path, "ez", "men", "ENJUSDT", "LONG", c) == vec == 0.8
    assert qq.live_mult(tmp_path, "ez", "inf", "ENJUSDT", "LONG", c) == 1.0
    assert qq.live_mult(tmp_path, "ez", "men", "ENJUSDT", "SHORT", c) == 1.0


def test_vector_cap_mirrors_live_order_cap_in_start_units():
    c = types.SimpleNamespace(START_POSITION_SIZE=45.0, QTY_LIVE_ORDER_CAP_MULT=4.0)
    assert qq.cap_dollar(c, 450.0, 1250.0) == 180.0
    c.QTY_LIVE_ORDER_CAP_MULT = 0.0
    assert qq.cap_dollar(c, 2000.0, 1250.0) == 1250.0
    assert qq.cap_dollar(c, 2000.0, 0.0) == 2000.0


def _src(name):
    return (ROOT / name).read_text()


def test_every_surface_calls_the_shared_functions():
    eng = _src("v12_quick_engine.py")
    assert "_qq.ladder_vec(npz, n, is_long, cfg)" in eng
    assert "_qq.streak_update(_qs_state" in eng and "_qq.streak_mult(_qs_state, cfg)" in eng
    assert "_qq.cap_dollar(cfg, _dollar, _cap)" in eng
    assert "_stdev_max = 5.0" not in eng, "inline ladder must stay replaced by the shared function"
    ez = _src("ez_manage.py")
    assert "_qq.ladder_from_indicators(i, is_long, _qq_cfg)" in ez
    assert ez.count("_qq.live_record_close(") == 2 and "_qq.live_mult(" in ez
    assert re.search(r'_NON_VEC_KNOBS_EZ = frozenset\(\[(?:(?!\]\)).)*"STDEV_SLOPE_SIZING_ENABLED"', ez, re.S) is None
    assert 'bool(getattr(config, "DC_POS_SIZE_LIVE_ENABLED", False))' in ez
    tr = _src("tradier_manage.py")
    assert "_qq.ladder_from_indicators(_qq_ind, side == \"LONG\", _qq_cfg)" in tr
    assert tr.count("_qq.live_record_close(") == 2 and "_qq.live_mult(" in tr


def test_config_defaults_match_quickconfig():
    import config
    import config_tradier
    import v12_quick_engine as E

    q = E.QuickConfig()
    keys = ("STDEV_LADDER_TF", "STDEV_LADDER_MAX_MULT", "QTY_STREAK_SIZING_ENABLED", "QTY_STREAK_MIN_MOVE_PCT", "QTY_STREAK_LOSS_COUNT", "QTY_STREAK_LOSS_MULT", "QTY_STREAK_WIN_COUNT", "QTY_STREAK_WIN_STEP", "QTY_STREAK_MAX_MULT")
    c = config.Config()
    t = config_tradier.TradierConfig()
    for k in keys:
        assert getattr(c, k) == getattr(t, k) == getattr(q, k), k
    assert c.DC_POS_SIZE_LIVE_ENABLED is False
