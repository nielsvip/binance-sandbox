import numpy as np
import pytest

import v12_quick_engine as V
from vec_decisions.check_entry_b12_wt_mom import b12_fires_scalar
from vec_decisions.check_entry_b14_ha_stack import b14_fires_scalar
from vec_paths.vec_parity_gate import is_vec_achievable, matched_token

@pytest.mark.parametrize("is_long", [True, False])
def test_registered_families_have_actual_switch_controlled_engine_masks(is_long):
    rng = np.random.default_rng(41)
    n = 64
    npz = {"close_15m": np.full(n, 100.0)}
    for tf in ("15m", "1h"):
        npz[f"wt1_{tf}"] = rng.uniform(-10, 10, n)
        npz[f"wt2_{tf}"] = rng.uniform(-10, 10, n)
        npz[f"ha_{tf}"] = rng.choice([-1, 1], n).astype(np.int8)
    npz["wt_velocity_15m"] = rng.uniform(-3, 3, n)
    npz["stoch_k_15m"] = rng.uniform(0, 100, n)
    cfg = V.QuickConfig()
    cfg.MODE = "tradier"
    cfg.BASE_TF = "15m"
    cfg.REENTRY_B12_WT_MOM_ENABLED = True
    cfg.REENTRY_B14_HA_TREND_ENABLED = True
    actual = V.compute_reentry_blocks(npz, n, is_long, cfg)
    expected_b12 = [b12_fires_scalar(npz["wt1_15m"][j], npz["wt2_15m"][j], npz["wt1_15m"][j], npz["wt2_15m"][j], npz["wt1_1h"][j], npz["wt2_1h"][j], npz["wt_velocity_15m"][j], is_long) for j in range(n)]
    expected_b14 = [b14_fires_scalar(npz["ha_15m"][j], npz["ha_15m"][j], npz["ha_1h"][j], npz["stoch_k_15m"][j], is_long) for j in range(n)]
    assert any(expected_b12) and any(expected_b14)
    np.testing.assert_array_equal(actual["B12"], expected_b12)
    np.testing.assert_array_equal(actual["B14"], expected_b14)
    for prefix in ("TRADIER_B12_WT_MOM_", "TRADIER_B14_HA_STACK_"):
        reason = prefix + ("L" if is_long else "S")
        assert is_vec_achievable(reason)
        assert matched_token(reason) == prefix
    cfg.REENTRY_B12_WT_MOM_ENABLED = False
    cfg.REENTRY_B14_HA_TREND_ENABLED = False
    disabled = V.compute_reentry_blocks(npz, n, is_long, cfg)
    assert "B12" not in disabled and "B14" not in disabled

def test_unregistered_stock_reason_still_reports_missing_family():
    assert not is_vec_achievable("TRADIER_UNIMPLEMENTED_FAMILY_L")

@pytest.mark.parametrize("is_long", [True, False])
def test_b04_registered_reason_is_emitted_by_shared_native_proposal(is_long):
    from vec_decisions.ez_missing_b04_retest import tradier_flat_open_proposal
    ind = {"k_15m": 62.0 if is_long else 38.0, "d_15m": 58.0 if is_long else 42.0, "k_15m_prev": 60.0 if is_long else 40.0}
    channel = "dc_high_4h" if is_long else "dc_low_4h"
    ind[channel] = 102.0 if is_long else 98.0
    ind[channel + "_ant"] = 100.0
    price = 100.2 if is_long else 99.8
    proposal = tradier_flat_open_proposal(ind, price, is_long, True, 600.0)
    assert proposal is not None
    assert matched_token(proposal[0]) == "B04_DC_RETEST_"
    npz = {key.replace("k_15m", "stoch_k_15m").replace("d_15m", "stoch_d_15m"): np.full(2, value) for key, value in ind.items()}
    npz["close"] = npz["close_15m"] = np.full(2, price)
    cfg = V.QuickConfig()
    cfg.REENTRY_B04_DC_RETEST_ENABLED = True
    assert V.compute_reentry_blocks(npz, 2, is_long, cfg)["B04"].all()
    cfg.REENTRY_B04_DC_RETEST_ENABLED = False
    assert "B04" not in V.compute_reentry_blocks(npz, 2, is_long, cfg)
    assert tradier_flat_open_proposal(ind, price, is_long, False, 600.0) is None
