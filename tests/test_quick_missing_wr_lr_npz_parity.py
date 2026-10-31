"""Actual frozen 1000PEPE proof for account-specific WR/LR scalar parity."""

import os
from pathlib import Path

import numpy as np
import pytest

from vec_decisions.check_entry_candidates_crypto__wr_lr_pullback import check_wr_lr_pullback
from vec_decisions.htf_causal_align import align_store
from vec_decisions.quick_missing_wr_lr import (
    _ha_strings,
    _wt_cross_labels,
    historical_wr_membership_mask,
    wr_lr_quick_good_from_npz,
)


NPZ_PATH = Path(os.environ.get("REENTRY_PARITY_NPZ", "/tmp/1000PEPEUSDC_30d_reentry_audit.npz"))


class _Config:
    WR_PULLBACK_ENABLED = True
    USE_1M_3M_SIGNALS_ENABLED = False
    VEC_ACCOUNT_KEY = "flz"


@pytest.mark.skipif(not NPZ_PATH.is_file(), reason="frozen 1000PEPE NPZ is unavailable")
def test_account_specific_wr_lr_matches_native_scalar_on_full_npz():
    with np.load(NPZ_PATH, allow_pickle=True) as data:
        raw = {key: data[key] for key in data.files}
    npz = align_store(raw, "1000PEPEUSDC", "crypto")
    n = len(npz["timestamps"])
    ha = _ha_strings(npz.get("ha_4h"), n)
    cross = _wt_cross_labels(npz.get("wt_cross_15m", np.zeros(n)))
    for is_long in (True, False):
        actual = wr_lr_quick_good_from_npz(
            npz, "1000PEPEUSDC", is_long, _Config(), account="flz"
        )
        assert actual is not None
        fire, score = actual
        membership = historical_wr_membership_mask(
            "1000PEPEUSDC", npz["timestamps"], is_long, "flz"
        )
        if is_long:
            assert membership.any()
        expected = np.zeros(n, dtype=bool)
        for index in range(n):
            indicators = {
                "ha_4h": ha[index],
                "stoch_k_1h": npz.get("stoch_k_1h", np.full(n, 50.0))[index],
                "stoch_k_15m": npz.get("stoch_k_15m", np.full(n, 50.0))[index],
                "stoch_k_15m_prev": npz.get("stoch_k_15m_prev", np.full(n, 50.0))[index],
                "wt_cross_15m": cross[index],
            }
            expected[index], _ = check_wr_lr_pullback(
                _Config(), indicators, {}, bool(membership[index]), is_long
            )
        np.testing.assert_array_equal(fire, expected)
        assert np.all(score[fire] >= 22.0)
