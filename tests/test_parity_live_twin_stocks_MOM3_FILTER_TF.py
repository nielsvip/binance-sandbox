"""PARITY LANE C 2026-10-06 — stocks live twin of MOM3_FILTER_TF.

Vec: vec_decisions.filter_tf_gates.mom3_entry_gate (v12 simulate_one WAVE1 FILTER_TF gates)
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.mom3_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.filter_tf_gates as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("thr", [(-1.0, 1.0), (0.0, 0.0), (1.5, -1.5)])
def test_live_equals_vec(tf, is_long, thr):
    npz = rng_npz(400, 11)
    cfg = Cfg(MOM3_FILTER_TF=tf, MOM3_LONG_THRESHOLD=thr[0], MOM3_SHORT_THRESHOLD=thr[1])
    vm = G.mom3_entry_gate(npz, 400, is_long, cfg, npz["close"], vsafe)
    nb = assert_agree(npz, vm, lambda g, px: L.mom3_block(g, px, is_long, tf, thr[0], thr[1]), "MOM3")
    assert 0 < nb < 400


def test_zero_threshold_not_coerced():
    g = {"close_3bar_1h": 100.0}.get
    assert L.mom3_block(g, 99.5, True, "1h", 0.0, 0.0) is None
    assert L.mom3_block(g, 100.5, True, "1h", 0.0, 0.0) is not None


def test_default_inert_and_hook_present():
    assert L.resolve_tf(tradier_default("MOM3_FILTER_TF")) is None
    assert L.mom3_block({"close_3bar_15m": 1.0}.get, 5.0, True, None, -1, 1) is None
    assert "_ftf_twins.mom3_block(" in tm_source()
