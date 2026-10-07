"""PARITY LANE C 2026-10-06 — stocks live twin of EMA50_15M_ENTRY_FILTER_ENABLED (+ _PCT).

Vec: the inline EMA50 block of v12_quick_engine.simulate_one is EXTRACTED FROM THE LIVE v12 SOURCE AND EXECUTED here (no copy).
Live: tradier_manage FILTER_TF veto stack -> tradier_filter_tf_twins.ema50_block, per-sym _cfg (cat_side STOCKS default True carried forward).
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, Cfg, vsafe, rng_npz_p3, assert_agree, tm_source, v12_block

BLOCK = v12_block("bool(getattr(cfg, 'EMA50_15M_ENTRY_FILTER_ENABLED', False))", "_entry_filter_masks.append(~_ema_block)")


def _vec_allow(npz, is_long, enabled, pct):
    n = len(npz["close"])
    ns = {"np": np, "npz": npz, "n": n, "is_long": is_long, "close": npz["close"], "_safe": vsafe, "entry_sig": np.ones(n, dtype=bool), "_entry_filter_masks": [],
          "cfg": Cfg(EMA50_15M_ENTRY_FILTER_ENABLED=enabled, EMA50_15M_ENTRY_FILTER_PCT=pct)}
    exec(compile("if True:\n    " + BLOCK.replace("\n", "\n    ") + "\n    pass", "v12_ema50_block", "exec"), ns)
    return ns["entry_sig"]


@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("pct", [0.0, 0.5, -0.3])
def test_live_equals_vec(is_long, pct):
    npz = rng_npz_p3(400, 31)
    nb = assert_agree(npz, _vec_allow(npz, is_long, True, pct), lambda g, px: L.ema50_block(g, px, is_long, True, pct), "EMA50")
    assert 0 < nb < 400


@pytest.mark.parametrize("is_long", [True, False])
def test_disabled_inert_both(is_long):
    npz = rng_npz_p3(200, 32)
    assert _vec_allow(npz, is_long, False, 0.0).all()
    assert assert_agree(npz, None, lambda g, px: L.ema50_block(g, px, is_long, False, 0.0), "EMA50_OFF") == 0


def test_hook_present_per_sym():
    assert "_ftf_twins.ema50_block(_ftf_get, _px_ftf, is_long, _ftf_ps('EMA50_15M_ENTRY_FILTER_ENABLED', False)" in tm_source()
