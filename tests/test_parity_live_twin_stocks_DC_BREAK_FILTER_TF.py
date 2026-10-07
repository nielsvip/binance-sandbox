"""PARITY LANE C 2026-10-06 — stocks live twin of DC_BREAK_FILTER_TF.

Vec: vec_decisions.generic_filter_tf FILTER_TF_MAP ('entry','dc_break')
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.dc_break_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.generic_filter_tf as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_live_equals_vec(tf, is_long):
    npz = rng_npz(400, 16)
    vm = G.build_masks(npz, 400, is_long, Cfg(DC_BREAK_FILTER_TF=tf), npz["close"], vsafe)["entry"]
    nb = assert_agree(npz, vm, lambda g, px: L.dc_break_block(g, px, is_long, tf), "DC_BREAK")
    assert 0 < nb < 400


def test_default_inert_and_hook_present():
    assert L.resolve_tf(tradier_default("DC_BREAK_FILTER_TF")) is None
    assert "_ftf_twins.dc_break_block(" in tm_source()
