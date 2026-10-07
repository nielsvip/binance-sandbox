"""PARITY LANE C 2026-10-06 — stocks live twin of BB_PULLBACK_GATE_FILTER_TF.

Vec: vec_decisions.bb_pullback_gate.bb_pullback_gate_vec when FILTER_TF != OFF (v12 ~12423); replaces the old live 0.20/0.80 double gate
Live: tradier_manage.process_position FILTER_TF veto stack -> tradier_filter_tf_twins.bb_pullback_filter_block (per-sym _cfg).
Proves live allow == vec entry mask on every bar (True and False cases, both sides, all TFs) and default inert.
"""
import numpy as np
import pytest

from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz, assert_agree, tm_source, tradier_default

import vec_decisions.bb_pullback_gate as G


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("enabled", [True, False])
def test_live_equals_vec(tf, is_long, enabled):
    npz = rng_npz(400, 13)
    cfg = Cfg(BB_PULLBACK_GATE_ENABLED=enabled, BB_PULLBACK_GATE_FILTER_TF=tf, BB_PULLBACK_GATE_TF="15m", BB_PULLBACK_GATE_LONG_MAX=0.30, BB_PULLBACK_GATE_SHORT_MIN=0.70)
    vm = ~G.bb_pullback_gate_vec(npz, 400, cfg, is_long)
    nb = assert_agree(npz, vm, lambda g, px: L.bb_pullback_filter_block(g, is_long, tf, enabled, 0.30, 0.70), "BB_PULLBACK")
    assert (0 < nb < 400) if enabled else nb == 0


def test_not_the_old_double_gate():
    # pctB 0.25: old live 0.20 hard gate vetoed a LONG; vec (LONG_MAX 0.30) allows it
    assert L.bb_pullback_filter_block({"bb_pct_b_1h": 0.25}.get, True, "1h", True, 0.30, 0.70) is None
    assert L.bb_pullback_filter_block({"bb_pct_b_1h": 0.35}.get, True, "1h", True, 0.30, 0.70) is not None


def test_default_inert_and_hook_present():
    assert L.resolve_tf(tradier_default("BB_PULLBACK_GATE_FILTER_TF")) is None
    src = tm_source()
    assert "_ftf_twins.bb_pullback_filter_block(" in src
    assert "_bv > 0.20) or ((not is_long) and _bv < 0.80)" not in src
