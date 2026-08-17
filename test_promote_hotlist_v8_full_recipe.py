import hashlib
import json

from tools.build_matrix_symbol_side_achievement import (
    valid_full_recipe_overlay_entry,
)
from tools.promote_hotlist_v8_full_recipe import (
    ENTRY_CONTRACT_FILES,
    EXIT_CONTRACT_FILES,
    promotion_receipt,
    selected_live_overrides,
    selected_v8_overrides,
)


def test_selected_live_overrides_are_narrow_and_recipe_isolated():
    out = selected_live_overrides(
        {
            "key": "ABC_LONG",
            "selected_entry_family": "ENTRY_LADDER_GREEN",
            "selected_entry_curve": {
                "trigger": "green", "mode": "linear", "stoch_low": 30,
                "d_bottom": 4, "h4_bottom": 3, "h1_bottom": 2,
                "d_top": 2, "h4_top": 1.5, "h1_top": 0.5,
            },
            "complete_recipe": {
                "schema": "complete-vector-lifecycle-recipe-v1",
                "ENTRY": {"family": "ENTRY_LADDER_GREEN", "params": {}},
                "EXIT": {
                    "family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                    "params": {
                        "arm_timeframe": "15m", "trail_timeframe": "5m",
                        "mode": "STDEV", "break_buffer_atr": 0.25,
                        "distance_mult": 1.0, "lookback": 6,
                    },
                }
            },
        }
    )
    assert out["FULL_RECIPE_ONLY_ENABLED"] is True
    assert out["LR_BAND_LADDER_ORDINARY_PARITY_ENABLED"] is True
    assert out["BOTTOM_A_PROTECTIVE_TRAIL_ENABLED"] is True
    assert out["LONG_ENABLED"] is True and out["SHORT_ENABLED"] is False
    assert out["DELTA_ENTRY_ENABLED"] is False


def test_prepared_direct_family_overrides_keep_exact_entry_and_exit_params():
    summary = {
        "key": "RDDT_SHORT",
        "selected_entry_family": "ENTRY_LONG_WAIT_ENABLED",
        "complete_recipe": {
            "schema": "complete-vector-lifecycle-recipe-v1",
            "ENTRY": {"family": "ENTRY_LONG_WAIT_ENABLED", "params": {
                "bounce_timeframe": "15m", "bounce_distance": .015,
                "deep_k4h": 50., "turn_k1h": 40., "confirmation": "stoch5",
            }},
            "EXIT": {"family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED", "params": {
                "arm_timeframe": "15m", "trail_timeframe": "15m", "mode": "ATR",
                "break_buffer_atr": .25, "distance_mult": 1.25, "lookback": 20,
            }},
        },
    }
    v8 = selected_v8_overrides(summary)
    live = selected_live_overrides(summary)
    assert v8["LONG_WAIT_DIRECT_BOUNCE_DISTANCE"] == .015
    assert v8["LONG_WAIT_DIRECT_CONFIRMATION"] == "stoch5"
    assert v8["BOTTOM_A_PROTECTIVE_TRAIL_DISTANCE_MULT"] == 1.25
    assert "SHORT_ENABLED" not in v8
    assert live["SHORT_ENABLED"] is True and live["LONG_ENABLED"] is False


def test_promotion_receipt_preserves_route_identity_params_markers_and_hashes():
    summary = {
        "key": "RDDT_SHORT", "run_id": "run", "real_closes": 11,
        "selected_entry_family": "ENTRY_LONG_WAIT_ENABLED",
        "selected_exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "complete_recipe_sha256": "recipe", "shared_route_audit": {"shared_route": True},
        "complete_recipe": {"ENTRY": {"params": {"turn_k1h": 40}}, "EXIT": {"params": {"lookback": 20}}},
    }
    receipt = promotion_receipt(
        key="RDDT_SHORT", summary=summary, overrides={"x": 1},
        hashes={"tradier_manage_sha256": "live", "long_wait_contract_sha256": "contract"},
    )
    assert receipt["selected_entry_params"] == {"turn_k1h": 40}
    assert receipt["selected_exit_params"] == {"lookback": 20}
    assert receipt["shared_runtime_markers"] == ["SHARED_LONG_WAIT_DIRECT_V8_PARITY_V1"]
    assert receipt["route_contract_hashes"] == {"long_wait_contract_sha256": "contract"}


def test_every_prepared_direct_family_has_a_promotion_contract_hash_binding():
    assert set(ENTRY_CONTRACT_FILES) == {
        "ENTRY_STOCH_HHHL", "ENTRY_BOUNCE_5M_LOW", "ENTRY_BOUNCE_15M_LOW",
        "ENTRY_1H_TURN_UP", "ENTRY_4H_DEEP_VALUE", "ENTRY_WT_DC",
        "ENTRY_BB_RECOVERY", "ENTRY_LONG_WAIT_ENABLED",
    }
    assert set(EXIT_CONTRACT_FILES) == {
        "BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED", "EXIT_MTF_ATR_TRAIL",
    }


def test_achievement_live_membership_uses_same_hash_bound_overlay_gate():
    overrides = {"FULL_RECIPE_ONLY_ENABLED": True, "LONG_ENABLED": True}
    overrides_sha = hashlib.sha256(
        json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    hashes = {
        "tradier_manage_sha256": "a",
        "backtest_v8_engine_sha256": "b",
        "protective_trail_contract_sha256": "c",
    }
    entry = {
        "overrides": overrides,
        "promotion": {
            "schema": "hotlist-v8-live-promotion-v1",
            "status": "LIVE_PROMOTION_PASS",
            "key": "ABC_LONG",
            "full_recipe_only_enabled": True,
            "real_closes": 11,
            "alpha_vs_bh_pp": 1.0,
            "overrides_sha256": overrides_sha,
            **hashes,
        },
    }
    assert valid_full_recipe_overlay_entry("ABC_LONG", entry, hashes) is True
    entry["promotion"]["tradier_manage_sha256"] = "stale"
    assert valid_full_recipe_overlay_entry("ABC_LONG", entry, hashes) is False
