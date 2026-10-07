"""PARITY LANE C 2026-10-06 — stocks live twin of BREAKOUT_RETEST_FILTER_TF.

Vec: vec_decisions.generic_filter_tf FILTER_TF_MAP ('entry','dc_retest_hold'); v12 [JSN2] forces OFF unless BREAKOUT_RETEST_ARMED_ENABLED
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.dc_retest_hold_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.generic_filter_tf as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("master", [True, False])
def test_live_equals_vec(tf, is_long, master):
    npz = rng_npz(400, 19)
    # v12 simulate_one [JSN2]: master off -> setattr(_gcfg, 'BREAKOUT_RETEST_FILTER_TF', 'OFF')
    vm = G.build_masks(npz, 400, is_long, Cfg(BREAKOUT_RETEST_FILTER_TF=(tf if master else "OFF")), npz["close"], vsafe)["entry"]
    nb = assert_agree(npz, vm, lambda g, px: L.dc_retest_hold_block(g, px, is_long, tf, master), "BREAKOUT_RETEST")
    assert (0 < nb < 400) if master else nb == 0


def test_default_inert_and_hook_present():
    assert tradier_default("BREAKOUT_RETEST_ARMED_ENABLED") in (False, "False", 0)
    assert "_ftf_twins.dc_retest_hold_block(" in tm_source()
