"""batch4 staged config switches: defaults equal today's effective live values; _sw_get precedence; R1 effective False. Run: python -m pytest -q tests/test_batch4_configs.py"""
import importlib.util
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STG = ROOT / "data/live_parity/staged/batch4_configs"
os.environ.setdefault("EZ_LOG_DIR", "/tmp/ez_log_parity")
sys.path.insert(0, str(ROOT))
EXPECT = {"PARABOLIC_EXIT_ENABLED": True, "KEY_LEVEL_CRASH_ENABLED": True, "AUGMENTED_DC_BREAK_ENABLED": True, "MOMENTUM_TP_ENABLED": True,
          "DC_BASIS_3M_REDUCE_ENABLED": True, "WT_CROSS_EXIT_3M_VETO_MAX_AGE": 30.0, "TREND_REGIME_VETO_ENABLED": True,
          "K1M_EXTREME_REVERSE_ENABLED": False, "REENTRY_GRACE_MINUTES": 30.0, "R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT": True}


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_config_fields_equal_effective_live_default():
    cfg = _load("config_staged", STG / "config.py").Config()
    for k, v in EXPECT.items():
        assert getattr(cfg, k) == v, k
    assert cfg.R1_DC_LOW4_3M_EMERGENCY_ENABLED is False
    live = __import__("config").Config()
    for k in EXPECT:
        assert not hasattr(live, k), f"{k} already exists in live config (staging out of date)"


def test_tradier_fields():
    t = _load("config_tradier_staged", STG / "config_tradier.py").TradierConfig()
    assert t.K1M_EXTREME_REVERSE_ENABLED is False and t.TREND_REGIME_VETO_ENABLED is False and t.R1_DC_LOW4_3M_EMERGENCY_ENABLED is False


def test_sw_get_precedence_and_defaults():
    epq = _load("epq_staged", STG / "ez_positions_quick.py")
    cfgmod = _load("config_staged2", STG / "config.py")
    epq.config = cfgmod.Config()  # staged config on the staged module
    for k, v in EXPECT.items():
        assert epq._sw_get("BTCUSDC", "LONG", k, v) == v, k
    # per-sym override wins
    epq._get_per_sym_overrides = lambda s, side: {"PARABOLIC_EXIT_ENABLED": False}
    assert epq._sw_get("BTCUSDC", "LONG", "PARABOLIC_EXIT_ENABLED", True) is False
    assert epq._sw_get("BTCUSDC", "LONG", "KEY_LEVEL_CRASH_ENABLED", True) is True
    # cat_side default beats config field
    epq._get_per_sym_overrides = lambda s, side: {}
    import cat_side_defaults as csd
    orig = csd.get_for
    csd.get_for = lambda key, sym, side, default=None, venue=None: False if key == "MOMENTUM_TP_ENABLED" else default
    try:
        assert epq._sw_get("BTCUSDC", "LONG", "MOMENTUM_TP_ENABLED", True) is False
    finally:
        csd.get_for = orig


def test_r1_effective_false_current_and_staged_reads():
    import cat_side_defaults as csd
    for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"):
        assert csd.get("R1_DC_LOW4_3M_EMERGENCY_ENABLED", cs, None) is False
    src = (STG / "ez_manage.py").read_text()
    assert '"R1_DC_LOW4_3M_EMERGENCY_ENABLED", False)' in src and '"R1_DC_LOW4_3M_EMERGENCY_ENABLED", True)' not in src
    assert "_psym_get(symbol, position_side, \"R1_RESTRICT_TO_OVERBOUGHT_BREAKOUT\", True)" in src
