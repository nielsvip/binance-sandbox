"""PARITY LANE C 2026-10-06 — stocks live twin of MOMENTUM_BREAKOUT_FILTER_TF.

Vec: vec_decisions.filter_tf_gates.momentum_breakout_gate
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.momentum_breakout_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.filter_tf_gates as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
def test_live_equals_vec(tf, is_long):
    npz = rng_npz(400, 12)
    vm = G.momentum_breakout_gate(npz, 400, is_long, Cfg(MOMENTUM_BREAKOUT_FILTER_TF=tf), npz["close"], vsafe)
    nb = assert_agree(npz, vm, lambda g, px: L.momentum_breakout_block(g, px, is_long, tf), "MOMENTUM_BREAKOUT")
    assert 0 < nb < 400


def test_default_inert_and_hook_present():
    assert L.resolve_tf(tradier_default("MOMENTUM_BREAKOUT_FILTER_TF")) is None
    assert "_ftf_twins.momentum_breakout_block(" in tm_source()
