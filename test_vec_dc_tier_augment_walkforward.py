from types import SimpleNamespace

import numpy as np

from tools import audit_dc_tier_augment_wiring as wiring
from tools import vec_augment_trend_resume_walkforward as aug
from tools import vec_band_ladder_walkforward as ladder
from tools import vec_dc_tier_augment_walkforward as tier
from tradier_augment_gates import dc_tier_aug_enabled


def _data(rows=80):
    close = np.linspace(100.0, 110.0, rows)
    return SimpleNamespace(
        open=close.copy(),
        close=close,
        high=close + 1,
        low=close - 1,
        ts=np.arange(rows) + 1_700_000_000,
    )


def _curve():
    return ladder.Curve(
        label="test", mode="linear", trigger="union", semantics="target",
        stoch_low=30, d_bottom=8, d_top=6, h4_bottom=6, h4_top=4,
        h1_bottom=4, h1_top=1,
    )


def test_inventory_switch_is_connected_default_true():
    result = wiring.audit_repo()
    assert result["classification"] == "CONNECTED_DEFAULT_TRUE_EXISTING_BEHAVIOR_PRESERVED"
    assert result["switch_declared"]
    assert result["switch_read"]
    assert result["switch_default_true"]
    assert result["disabled_reason_present"]
    assert result["evaluate_augment_routed"]


def test_switch_audit_fails_closed_for_false_and_preserves_true_source_mapping():
    config_source = "DC_TIER_AUG_ENABLED: bool = True"
    manage_source = """
async def evaluate_augment():
    if not _dc_tier_aug_enabled(config):
        return False, "DC_TIER_AUG_DISABLED", 0.0, 0.0
    buf = 0.001
    tier_mults = {1: 1.0, 2: 2.0, 3: 3.0, 4: 5.0}
    if current_value < target_value * 0.75:
        return True, f"DC_TIER{active_tier}_DC5M_AUG", 80.0, qty
strategy.evaluate_augment()
"""
    gate_source = (
        'def dc_tier_aug_enabled(config_obj):\n'
        '    return bool(getattr(config_obj, "DC_TIER_AUG_ENABLED", True))\n'
    )
    result = wiring.audit_sources(config_source, manage_source, gate_source)
    assert result["classification"] == "CONNECTED_DEFAULT_TRUE_EXISTING_BEHAVIOR_PRESERVED"


def test_dc_tier_gate_true_false_and_missing_legacy_default():
    assert dc_tier_aug_enabled(SimpleNamespace(DC_TIER_AUG_ENABLED=True))
    assert not dc_tier_aug_enabled(SimpleNamespace(DC_TIER_AUG_ENABLED=False))
    assert dc_tier_aug_enabled(SimpleNamespace())


def test_grid_contains_exact_source_setting_and_labeled_ranges():
    rows = tier.candidates()
    assert len(rows) == 243
    assert tier.Candidate(
        3.0, 0.001, (1.0, 2.0, 3.0, 5.0), 0.75, None
    ) in rows


def test_dc_tier_requests_absolute_target_and_respects_capacity():
    data = _data()
    entries = np.zeros(len(data.ts))
    entries[0] = 1.0
    signals = ladder.SignalData(
        entry_mult=entries,
        event_tf=np.zeros(len(data.ts), dtype=np.int8),
        exit_event=np.zeros(len(data.ts), dtype=bool),
        exit_ref=np.full(len(data.ts), np.nan),
        causality={},
    )
    state = np.full(len(data.ts), 4, dtype=np.int8)
    candidate = tier.Candidate(
        0.0, 0.001, (1.0, 2.0, 3.0, 5.0), 0.75, None
    )
    result = aug.simulate(
        data, signals, _curve(), state, candidate, 0, len(data.ts),
        0.0, 0.0, "LONG",
    )
    assert result["augment_signal_count"] > 0
    assert result["augment_request_count"] == result["augment_fill_count"]
    assert result["augment_fill_count"] == 1
    assert result["peak_post_fill_notional_usd"] <= ladder.CAPACITY + 1e-6
    assert not result["entry_capacity_breach"]
