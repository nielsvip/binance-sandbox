from types import SimpleNamespace

from vec_decisions.ppl_reaugment_gate import passes_short_sma_gate


def test_short_sma_gate_blocks_large_short_above_200_without_override():
    cfg = SimpleNamespace(
        EXTREME_OB_OS_OVERRIDE_ENABLED=True,
        EXTREME_OB_RSI_4H_MIN=80.0,
        EXTREME_OB_RSI_D_MIN=75.0,
        EXTREME_OB_BB_PCT_B_4H_MIN=1.0,
    )
    assert not passes_short_sma_gate(cfg, False, 110.0, 100.0, 60.0, 50.0, 79.0, 80.0, 1.2)
    assert passes_short_sma_gate(cfg, False, 110.0, 100.0, 60.0, 50.0, 80.0, 75.0, 1.0)
    assert passes_short_sma_gate(cfg, False, 110.0, 100.0, 40.0, 50.0, 50.0, 50.0, 0.5)


def test_short_sma_gate_uses_live_no_data_and_long_side_defaults():
    cfg = SimpleNamespace(EXTREME_OB_OS_OVERRIDE_ENABLED=False)
    assert passes_short_sma_gate(cfg, False, 110.0, 0.0, 60.0, 50.0, 50.0, 50.0, 0.5)
    assert passes_short_sma_gate(cfg, True, 90.0, 100.0, 60.0, 50.0, 50.0, 50.0, 0.5)
