"""PARITY LANE C 2026-10-06 — stocks live twin of BAR_PATTERNS_FILTER_TF.

Vec: vec_decisions.generic_filter_tf FILTER_TF_MAP ('entry','bar_pattern_side') — code sets from backtest_v8_precompute.BAR_PATTERN_CODES
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.bar_pattern_side_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.generic_filter_tf as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_live_equals_vec(tf, is_long):
    npz = rng_npz(400, 18)
    vm = G.build_masks(npz, 400, is_long, Cfg(BAR_PATTERNS_FILTER_TF=tf), npz["close"], vsafe)["entry"]
    nb = assert_agree(npz, vm, lambda g, px: L.bar_pattern_side_block(g, is_long, tf), "BAR_PATTERNS")
    assert 0 < nb < 400


def test_outside_bar_is_not_bullish_like_vec():
    # live bar_direction gives outside_bar +-1; vec code 15 is in neither set -> blocked both sides
    assert L.bar_pattern_side_block({"bar_pattern_1h": "outside_bar"}.get, True, "1h") is not None
    assert L.bar_pattern_side_block({"bar_pattern_1h": "hammer"}.get, True, "1h") is None
    assert L.bar_pattern_side_block({}.get, True, "1h") is None


def test_default_inert_and_hook_present():
    assert str(tradier_default("BAR_PATTERNS_FILTER_TF")).upper() == "OFF"
    src = tm_source()
    assert "_veto = _ftf_twins.bar_pattern_side_block(_ftf_get, is_long, _ftf_twins.resolve_tf(_ftf_ps('BAR_PATTERNS_FILTER_TF')))" in src
