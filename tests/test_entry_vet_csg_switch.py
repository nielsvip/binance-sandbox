"""ENTRY_VET_COMBINED_STOCH_GATE_ENABLED (USER 2026-10-08: "we never filter by stoch k, we use WT") — switch present on all four
surfaces, default OFF, and every live/vec gate site is conditioned on it."""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
K = "ENTRY_VET_COMBINED_STOCH_GATE_ENABLED"


def test_default_off_everywhere():
    import config, config_tradier, v12_quick_engine as V
    assert config.Config.ENTRY_VET_COMBINED_STOCH_GATE_ENABLED is False
    assert config_tradier.TradierConfig.ENTRY_VET_COMBINED_STOCH_GATE_ENABLED is False
    assert V.QuickConfig().ENTRY_VET_COMBINED_STOCH_GATE_ENABLED is False


def test_gate_sites_conditioned():
    ez = (ROOT / "ez_manage.py").read_text()
    assert re.search(r"if _csg < 100\.0 and _csg_on:", ez), "ez check_entry_vetting must be conditioned on the switch"
    tr = (ROOT / "tradier_manage.py").read_text()
    assert tr.count(f"bool(_cfg_auto('{K}', False))") == 2, "both tradier csg sites must be conditioned"
    vec = (ROOT / "v12_quick_engine.py").read_text()
    assert f"getattr(cfg, '{K}', False)" in vec and "entry_sig = entry_sig & _csg_mask" in vec


def test_vec_gate_math():
    import numpy as np
    import vec_decisions.twin_p0_crypto_a as T
    class C:
        COMBINED_STOCH_GATE_TRADIER = 60.0
    k = np.array([10.0, 59.9, 60.0, 92.0])
    assert T.vec_stoch_gate_pass(k, C, True).tolist() == [True, True, False, False]
    assert T.vec_stoch_gate_pass(k, C, False).tolist() == [False, True, True, True]
