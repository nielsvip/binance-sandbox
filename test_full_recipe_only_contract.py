from pathlib import Path

from tools.run_hotlist_v8_full_recipe import (
    audit_result,
    current_status_key_count,
    ladder_overrides,
)


ROOT = Path(__file__).resolve().parent


def test_full_recipe_override_is_explicit_and_default_is_inert():
    config_text = (ROOT / "config_tradier.py").read_text()
    override = ladder_overrides(
        {
            "trigger": "green",
            "mode": "linear",
            "stoch_low": 30,
            "d_bottom": 4,
            "h4_bottom": 3,
            "h1_bottom": 2,
            "d_top": 2,
            "h4_top": 1.5,
            "h1_top": 0.5,
        },
        {
            "EXIT": {
                "family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
                "params": {
                    "arm_timeframe": "15m",
                    "trail_timeframe": "5m",
                    "mode": "STDEV",
                    "break_buffer_atr": 0.25,
                    "distance_mult": 1.0,
                    "lookback": 6,
                },
            }
        },
    )
    assert "FULL_RECIPE_ONLY_ENABLED: bool = False" in config_text
    assert override["FULL_RECIPE_ONLY_ENABLED"] is True


def test_full_recipe_only_has_entry_exit_and_augment_guards():
    text = (ROOT / "tradier_manage.py").read_text()
    entry_guard = text.index('return "FULL_RECIPE_ONLY_HOLD_ENTRY"')
    fallback = text.index("# --- 4. DELTA ENGINE ENTRY", entry_guard)
    assert entry_guard < fallback

    stop = text[text.index("    async def evaluate_stop("):]
    bottom_a = stop.index("_bottom_a_eval_signal = _ordinary_bottom_a_exit(")
    exit_guard = stop.index('return False, "FULL_RECIPE_ONLY_HOLD_EXIT", 0')
    opening_buffer = stop.index("# OPENING_BUFFER_NO_TRADE")
    assert bottom_a < exit_guard < opening_buffer

    process = text[text.index("async def process_position("):text.index("async def queue_trade_action(")]
    position_guard = process.index('log_reason = "FULL_RECIPE_ONLY_HOLD_POSITION"')
    augment = process.index("# --- 3. EVALUATE AUGMENT ---")
    assert position_guard < augment


def test_full_recipe_only_admits_only_shared_formation_actions():
    text = (ROOT / "tradier_manage.py").read_text()
    entry = text.index("classic_formation_open_action(")
    entry_guard = text.index('return "FULL_RECIPE_ONLY_HOLD_ENTRY"', entry)
    assert entry < entry_guard

    stop = text[text.index("    async def evaluate_stop("):]
    formation_exit = stop.index("CLASSIC_FORMATION_EXIT_")
    exit_guard = stop.index('return False, "FULL_RECIPE_ONLY_HOLD_EXIT", 0')
    assert formation_exit < exit_guard


def test_exact_runner_opts_formation_recipes_into_causal_npz_fields():
    text = (ROOT / "tools" / "run_hotlist_v8_full_recipe.py").read_text()
    assert 'env["V8_CLASSIC_FORMATION_FIELDS"] = "1"' in text
    assert 'env.pop("V8_CLASSIC_FORMATION_FIELDS", None)' in text


def test_full_recipe_audit_uses_selected_entry_event_sizing_when_engine_counter_is_zero():
    events = [
        {
            "timestamp": 1,
            "position_side": "SHORT",
            "action": "OPEN",
            "reason": "CLASSIC_FORMATION_ENTRY_TEST",
            "requested_qty": 10,
            "executed_qty": 12,
            "quantity": 12,
            "price": 100,
        },
        {
            "timestamp": 2,
            "position_side": "SHORT",
            "action": "CLOSE",
            "reason": "CLASSIC_FORMATION_EXIT_TEST",
            "pnl_dollars": 1,
        },
        {
            "timestamp": 3,
            "position_side": "SHORT",
            "action": "REENTRY",
            "reason": "MANDATORY_REENTRY_PRICE_CROSS",
        },
    ]
    result = audit_result(
        hot={"key": "ABC_SHORT"},
        champion={"bh_return_pct": 1.0},
        v8={
            "pnl": 5.0,
            "real_closes": 11,
            "reentry_violations": 0,
            "max_open_notional": 1000,
            "requested_fill_ratio": 0,
            "max_requested_mult": 0,
            "opens_long": 0,
        },
        events=events,
        returncode=0,
        entry_reason_prefix="CLASSIC_FORMATION_ENTRY_",
        exit_reason_prefix="CLASSIC_FORMATION_EXIT_",
    )
    assert result["status"] == "PASS"
    assert result["requested_fill_ratio"] == 1.2
    assert result["max_requested_mult"] == 0.5
    assert result["requested_fill_ratio_source"] == "SELECTED_ENTRY_RAW_EVENTS"


def test_current_pass_count_rejects_legacy_or_stale_rows(tmp_path):
    for name, value in {
        "tradier_manage.py": "manage-current",
        "backtest_v8_engine.py": "engine-current",
        "v8_completed_snapshot_adapter.py": "adapter-current",
        "protective_trail_contract.py": "trail-current",
        "classic_formations.py": "formations-current",
    }.items():
        (tmp_path / name).write_text(value)
    import hashlib

    hashes = {
        "tradier_manage_sha256": hashlib.sha256(b"manage-current").hexdigest(),
        "backtest_v8_engine_sha256": hashlib.sha256(b"engine-current").hexdigest(),
        "v8_completed_snapshot_adapter_sha256": hashlib.sha256(
            b"adapter-current"
        ).hexdigest(),
        "protective_trail_contract_sha256": hashlib.sha256(b"trail-current").hexdigest(),
        "classic_formations_sha256": hashlib.sha256(
            b"formations-current"
        ).hexdigest(),
    }
    good = {
        "key": "A_LONG",
        "exact_v8_status": "V8_FULL_RECIPE_PASS",
        "full_recipe_only_enabled": True,
        **hashes,
    }
    ordinary_without_formation_hash = {
        **good,
        "key": "ORD_LONG",
        "selected_entry_family": "ENTRY_LADDER_GREEN",
        "selected_exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "classic_formations_sha256": None,
    }
    formation_with_stale_formation_hash = {
        **good,
        "key": "FORM_LONG",
        "selected_entry_family": "ENTRY_CLASSIC_FORMATION_TREND_STRUCTURE",
        "selected_exit_family": "BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED",
        "classic_formations_sha256": "stale",
    }
    legacy = {**good, "key": "B_LONG", "full_recipe_only_enabled": False}
    stale = {**good, "key": "C_LONG", "tradier_manage_sha256": "stale"}
    assert current_status_key_count(
        [good, ordinary_without_formation_hash, formation_with_stale_formation_hash, legacy, stale],
        "V8_FULL_RECIPE_PASS",
        tmp_path,
    ) == 2
