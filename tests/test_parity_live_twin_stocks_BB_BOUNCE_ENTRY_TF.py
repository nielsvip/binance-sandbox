"""PARITY LANE C 2026-10-06 — stocks live twin of BB_BOUNCE_ENTRY_TF.

Vec: vec_decisions.generic_filter_tf FILTER_TF_MAP ('entry','bb_bounce') AND-mask (source half B_BB_BOUNCE_ENTRY needs bb_pct_b_{tf}_prev: BLOCKED_NO_LIVE_DATA)
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.bb_bounce_filter_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.generic_filter_tf as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_live_equals_vec(tf, is_long):
    npz = rng_npz(400, 20)
    vm = G.build_masks(npz, 400, is_long, Cfg(BB_BOUNCE_ENTRY_TF=tf), npz["close"], vsafe)["entry"]
    nb = assert_agree(npz, vm, lambda g, px: L.bb_bounce_filter_block(g, px, is_long, tf), "BB_BOUNCE")
    assert 0 < nb < 400


def test_truth_table():
    g = {"bb_lower_1h": 100.0, "low_1h": 99.0}.get
    assert L.bb_bounce_filter_block(g, 101.0, True, "1h") is None
    assert L.bb_bounce_filter_block(g, 99.5, True, "1h") is not None
    assert L.bb_bounce_filter_block({}.get, 99.5, True, "1h") is None


def test_default_inert_and_hook_present():
    assert L.resolve_tf(tradier_default("BB_BOUNCE_ENTRY_TF")) is None
    assert "_ftf_twins.bb_bounce_filter_block(" in tm_source()
