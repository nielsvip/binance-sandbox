import sys

import numpy as np
import pytest

sys.path.insert(0, "tests")

import v12_quick_engine as V
from test_tradier_reviewed_exit_ledgers import _base_npz, _cfg


def test_rule_b_ltf_on_requires_aligned_real_5m_inputs():
    npz = _base_npz()
    cfg = _cfg()
    cfg.LTF_SUB15M_TRADING_ENABLED = True
    cfg.RULE_B_5M_EXIT_ENABLED = True
    with pytest.raises(ValueError, match="RULE_B_5M_EXIT_ENABLED unsupported: NPZ missing aligned field low_5m"):
        V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)


def test_rule_b_ltf_on_uses_aligned_5m_inputs_in_real_ledger():
    npz = _base_npz()
    n = len(npz["close"])
    fire_bar = 201
    npz["close"][fire_bar] = 104.0
    npz["close_15m"][fire_bar] = 104.0
    for field, default in (("low_5m", 100.0), ("low_5m_prev", 100.0), ("high_5m", 100.0), ("high_5m_prev", 100.0)):
        npz[field] = np.full(n, default)
    npz["low_5m"][fire_bar] = 103.5
    npz["low_5m_prev"][fire_bar] = 104.0
    npz["high_5m"][fire_bar] = 104.5
    npz["high_5m_prev"][fire_bar] = 105.0
    cfg = _cfg()
    cfg.LTF_SUB15M_TRADING_ENABLED = True
    cfg.RULE_B_5M_EXIT_ENABLED = True
    result = V.simulate_one(npz, "SYNTH", True, cfg, force_initial_seed=True)
    fires = [
        row for row in result["ledger"]
        if row.get("type") == "CLOSE" and str(row.get("reason", "")).startswith("RULE_B_5M_")
    ]
    assert len(fires) == 1
    assert fires[0]["bar_exit"] == fire_bar
    assert fires[0]["reason"] == "RULE_B_5M_LL_LH_gain:4.00%"
