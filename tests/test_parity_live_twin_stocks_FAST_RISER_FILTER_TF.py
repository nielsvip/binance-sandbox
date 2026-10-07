"""PARITY LANE C 2026-10-06 — FAST_RISER_FILTER_TF (FAST_RISER_DOUBLE augment source).

Vec: real vec_decisions.filter_tf_gates.fast_riser_sig + simulate_one gate live_pnl_pct > FAST_RISER_MIN_GAIN_PCT.
Live: tradier_filter_tf_twins.fast_riser_fires in StockStrategy.evaluate_augment (after universal gain gate + 300 s cooldown).
Live ha_{tf} is 'green'/'red' (vec int +-1). evaluate_augment is shadowed while BB4H_BREAKOUT_LADDER_ENABLED (documented).
"""
import numpy as np
import pytest

import vec_decisions.filter_tf_gates as G
from tests._laneC_parity_helpers import L, TFS, Cfg, vsafe, rng_npz_p3, tm_source


@pytest.mark.parametrize("tf", TFS)
@pytest.mark.parametrize("is_long", [True, False])
@pytest.mark.parametrize("gain", [0.5, 1.2])
def test_live_equals_vec(tf, is_long, gain):
    n = 400
    npz = rng_npz_p3(n, 60)
    sig = G.fast_riser_sig(npz, n, is_long, Cfg(FAST_RISER_FILTER_TF=tf), npz["close"], vsafe)
    vec_fire = sig & (gain > G.FAST_RISER_MIN_GAIN_PCT)
    fires = 0
    for i in range(n):
        d = {k: float(v[i]) for k, v in npz.items()}
        d[f"ha_{tf}"] = "green" if npz[f"ha_{tf}"][i] > 0 else "red"
        lf, _ = L.fast_riser_fires(d.get, float(npz["close"][i]), is_long, tf, gain)
        assert lf == bool(vec_fire[i]), i
        fires += int(lf)
    assert (fires > 0) if gain > 0.8 else fires == 0


def test_off_inert_and_hook():
    assert L.fast_riser_fires({}.get, 1.0, True, None, 5.0) == (False, "")
    assert "_ftf_twins.fast_riser_fires(lambda _k: (indicators or {}).get(_k), current_price, is_long, _fr_tf, gain)" in tm_source()
