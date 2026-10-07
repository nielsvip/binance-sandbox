"""Test live gate: gain>0 AND beat bh per side — if both sides positive keep both, if one loses keep one.
Backtest still probes opposite side."""
import json
from pathlib import Path

import ez_manage as em


def test_live_gate_and_logic():
    # Both positive keep both
    assert em._ezm_is_live_side_enabled("BTCUSDC", "LONG")[0] is True
    assert em._ezm_is_live_side_enabled("BTCUSDC", "SHORT")[0] is True
    # Vectorized_opt with wsharpe fallback (no gain data) keep both if w>0
    assert em._ezm_is_live_side_enabled("1INCHUSDT", "LONG")[0] is True
    assert em._ezm_is_live_side_enabled("1INCHUSDT", "SHORT")[0] is True
    # One loses a lot keep one: AXS long loses, short wins
    assert em._ezm_is_live_side_enabled("AXSUSDT", "LONG")[0] is False
    assert em._ezm_is_live_side_enabled("AXSUSDT", "SHORT")[0] is True
    # KSM short loses (gain>0 but not beating)
    assert em._ezm_is_live_side_enabled("KSMUSDT", "SHORT")[0] is False
    # SNDK stock long BEST not beating
    assert em._ezm_is_live_side_enabled("SNDKUSDT", "LONG")[0] is False
    # Missing per_sym ancient defaults blocked
    assert em._ezm_is_live_side_enabled("BNCUSDT", "LONG")[0] is False


def test_backtest_still_probes_blocked_side():
    # Live blocked but backtest dual still would simulate (check engine exists)
    from tools.per_sym_engine_crypto_isolated import simulate_dual
    from tools.per_sym_engine_stocks_isolated import simulate_dual_stocks

    assert callable(simulate_dual)
    assert callable(simulate_dual_stocks)
    # Verify that disabled side is still in per_sym for backtest, even though live blocked
    pc = json.loads((Path("data/hourly_reconfig/per_sym_active_config.json")).read_text())
    assert "AXSUSDT_LONG" in pc
    # Live blocked, but per_sym still has entry for backtest probing
    assert em._ezm_is_live_side_enabled("AXSUSDT", "LONG")[0] is False
    assert "AXSUSDT_LONG" in pc  # still available for backtest
