"""Account-scoped partial-lot regression checks against the canonical engine."""
import importlib.util
import json
from pathlib import Path
import sys

import numpy as np
import pytest

import v12_quick_engine as engine
from vec_decisions.partial_account_execution import plan_partial_reduce, validate_context


def _npz_fixture():
    n = 100
    close = np.linspace(100.0, 100.5, n)
    timestamps = np.arange(1700000000, 1700000000 + n * 180, 180)
    npz = {
        "close": close,
        "close_3m": close,
        "close_15m": close,
        "close_5m": close,
        "timestamps": timestamps,
        "timestamp_3m": timestamps,
        "timestamp_15m": timestamps,
        "low_3m": close,
        "high_3m": close,
        "open_3m": close,
        "dc_low_4h": np.full(n, 80.0),
        "dc_high_4h": np.full(n, 120.0),
        "dc_low_15m": np.full(n, 90.0),
        "dc_high_15m": np.full(n, 130.0),
    }
    for timeframe in ("15m", "1h", "4h", "D"):
        npz[f"wt1_{timeframe}"] = np.zeros(n)
        npz[f"wt2_{timeframe}"] = np.zeros(n)
    return npz


def _cfg():
    cfg = engine.QuickConfig()
    cfg.MODE = "crypto"
    cfg.BASE_TF = "3m"
    cfg.START_POSITION_SIZE = 10.0
    cfg.COOLDOWN_BARS = 0
    cfg.CYCLE_TP_TIERED_ENABLED = True
    cfg.CYCLE_TP_TIERED_LEVELS = [0.0015, 0.003]
    cfg.CYCLE_TP_TIERED_FRAC = 0.25
    cfg.NOLOSS_MIN_PROFIT_PCT = 0.05
    cfg.COMMISSION_BUFFER_PCT = 0.08
    cfg.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
    cfg.PROFIT_TARGET_ENABLED = False
    cfg.STOP_LOSS_ENABLED = False
    cfg.ALL_TF_AGAINST_FORCE_CLOSE_ENABLED = False
    cfg.AUGMENTED_DC_BREAK_ENABLED = False
    return cfg


def test_account_partial_quantizer_rejects_small_leg_and_preserves_dust_policy():
    blocked = plan_partial_reduce(0.025, 0.1, 0.1, 0.001, 0.1)
    assert blocked["ok"] is False
    assert blocked["reason"] == "PARTIAL_BELOW_MIN_QTY"
    assert plan_partial_reduce(0.1, 0.4, 0.4, 0.001, 0.1)["account_reduce_qty"] == pytest.approx(0.1)
    dust = plan_partial_reduce(0.27, 0.3, 0.3, 0.001, 0.1)
    assert dust["ok"] is True
    assert dust["reason"] == "PARTIAL_EXECUTABLE_FULL_DUST_CLOSE"
    assert dust["account_reduce_qty"] == pytest.approx(0.3)


def test_whole_share_account_qty_one_rejects_fractional_partial_without_full_close():
    one_share = plan_partial_reduce(0.5, 1.0, 1.0, 1.0, 1.0)
    two_shares = plan_partial_reduce(0.5, 1.0, 2.0, 1.0, 1.0)
    assert one_share["ok"] is False
    assert one_share["reason"] == "PARTIAL_BELOW_MIN_QTY"
    assert two_shares["ok"] is True
    assert two_shares["account_reduce_qty"] == pytest.approx(1.0)


def test_account_context_requires_exact_observation_window_and_identity():
    context = {
        "schema_version": 1,
        "account_key": "ang",
        "symbol": "QNTUSDT",
        "side": "LONG",
        "window_first_ts": 1700000000,
        "window_last_ts": 1700017820,
        "observation_bar_ts": 1700001440,
        "observed_qty": 0.1,
        "market_lot": {"step_size": 0.001, "min_qty": 0.1},
        "decision_generation": "g1",
        "config_sha256": "f" * 64,
    }
    assert validate_context(context, "QNTUSDT", "LONG", 1700000000, 1700017820) == (True, "OK")
    assert validate_context(context, "OTHER", "LONG", 1700000000, 1700017820)[0] is False


def test_canonical_engine_no_context_ledger_matches_exact_pre_edit_backup():
    backup = Path(__file__).resolve().parents[1] / "backups/before_account_partial_context_20261031_024950.py"
    spec = importlib.util.spec_from_file_location("_v12_pre_account_partial", backup)
    old = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = old
    spec.loader.exec_module(old)
    old_cfg = old.QuickConfig()
    cfg = engine.QuickConfig()
    for current in (old_cfg, cfg):
        current.MODE = "crypto"
        current.BASE_TF = "3m"
        current.START_POSITION_SIZE = 10.0
        current.COOLDOWN_BARS = 0
        current.CYCLE_TP_TIERED_ENABLED = True
        current.CYCLE_TP_TIERED_LEVELS = [0.0015, 0.003]
        current.CYCLE_TP_TIERED_FRAC = 0.25
        current.NOLOSS_MIN_PROFIT_PCT = 0.05
        current.COMMISSION_BUFFER_PCT = 0.08
        current.CRYPTO_ROUND_TRIP_COMMISSION_PCT = 0.04
        current.PROFIT_TARGET_ENABLED = False
        current.STOP_LOSS_ENABLED = False
        current.ALL_TF_AGAINST_FORCE_CLOSE_ENABLED = False
        current.AUGMENTED_DC_BREAK_ENABLED = False
    npz = _npz_fixture()
    before = old.simulate_one(npz, "QNTUSDT", True, old_cfg, force_initial_seed=True)
    after = engine.simulate_one(npz, "QNTUSDT", True, cfg, force_initial_seed=True)
    assert before["ledger"] == after["ledger"]
    assert "account_partial_audit" not in after


def test_canonical_cycle_tp_rejects_unexecutable_actual_lot_without_consuming_tier():
    npz = _npz_fixture()
    baseline = engine.simulate_one(npz, "QNTUSDT", True, _cfg(), force_initial_seed=True)
    first = next(row for row in baseline["ledger"] if str(row.get("reason", "")).startswith("CYCLE_TP_TIERED_T0_"))
    ts = npz["timestamps"]
    context = {
        "schema_version": 1,
        "account_key": "ang",
        "symbol": "QNTUSDT",
        "side": "LONG",
        "window_first_ts": int(ts[0]),
        "window_last_ts": int(ts[-1]),
        "observation_bar_ts": int(first["ts"]),
        "observed_qty": 0.1,
        "market_lot": {"step_size": 0.001, "min_qty": 0.1},
        "decision_generation": "g-test",
        "config_sha256": "a" * 64,
    }
    cfg = _cfg()
    cfg._PARTIAL_EXECUTION_CONTEXT = context
    result = engine.simulate_one(npz, "QNTUSDT", True, cfg, force_initial_seed=True)
    ctp_rows = [row for row in result["ledger"] if str(row.get("reason", "")).startswith("CYCLE_TP_TIERED_T0_")]
    assert not ctp_rows
    assert result["account_partial_audit"]["rejected_count"] >= 1
    assert result["account_partial_audit"]["rejection_counts"]["PARTIAL_BELOW_MIN_QTY"] >= 1
    assert result["account_partial_audit"]["last_rejection"] in {
        "PARTIAL_BELOW_MIN_QTY",
        "NO_FRESH_ACCOUNT_OBSERVATION_FOR_BAR",
    }
    assert all(row.get("projection_only_not_a_fill") is not True for row in result["ledger"])


def test_accepted_cycle_tp_is_labeled_projection_not_exchange_fill():
    npz = _npz_fixture()
    baseline = engine.simulate_one(npz, "QNTUSDT", True, _cfg(), force_initial_seed=True)
    first = next(row for row in baseline["ledger"] if str(row.get("reason", "")).startswith("CYCLE_TP_TIERED_T0_"))
    ts = npz["timestamps"]
    cfg = _cfg()
    cfg._PARTIAL_EXECUTION_CONTEXT = {
        "schema_version": 1,
        "account_key": "ang",
        "symbol": "QNTUSDT",
        "side": "LONG",
        "window_first_ts": int(ts[0]),
        "window_last_ts": int(ts[-1]),
        "observation_bar_ts": int(first["ts"]),
        "observed_qty": 0.4,
        "market_lot": {"step_size": 0.001, "min_qty": 0.1},
        "decision_generation": "g-test-accepted",
        "config_sha256": "b" * 64,
    }
    result = engine.simulate_one(npz, "QNTUSDT", True, cfg, force_initial_seed=True)
    row = next(row for row in result["ledger"] if str(row.get("reason", "")).startswith("CYCLE_TP_TIERED_T0_"))
    projection = row["account_partial_projection"]
    assert projection["projected_reduce_qty"] == pytest.approx(0.1)
    assert projection["projection_only_not_a_fill"] is True
    assert result["account_partial_audit"]["accepted_count"] == 1


@pytest.mark.parametrize(
    "gate_name",
    [
        "_cs_ok",
        "_bbr_ok_early",
        "_lec_ok",
        "_sfk_ok",
        "_hq_account_ok",
        "_afb_account_ok",
        "_ctp_account_ok",
        "_ppl_account_ok",
        "_qr_account_ok",
        "_dd_account_ok",
        "_w2_account_ok",
        "_red_account_ok",
        "_satoshi_account_ok",
        "_bbr_account_ok",
    ],
)
def test_each_canonical_partial_mutation_family_has_account_gate_and_projection_commit(gate_name):
    source = (Path(__file__).resolve().parents[1] / "v12_quick_engine.py").read_text()
    call = source.index(f"{gate_name},")
    assert "_account_partial_plan(" in source[max(0, call - 1200) : call + 1800]
    assert "_account_partial_commit(" in source[call : call + 5000]


def test_stock_whole_share_postprocessor_preserves_account_quantized_projection():
    source = (Path(__file__).resolve().parents[1] / "v12_quick_engine.py").read_text()
    assert source.count('if _ws_e.get("account_partial_projection")') == 2
