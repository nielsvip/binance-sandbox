import gzip
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

import backtest_v8_engine as engine
from tools.v8_research_ladder_adapter import (
    LadderReplayAdapter,
    LadderReplayError,
    SPEC_KIND,
    SPEC_VERSION,
    _canonical_sha256,
    _write_schedule_deterministic,
    audit_execution_provenance,
)


def _write_spec(
    tmp_path: Path,
    events: list[dict],
    *,
    side: str = "LONG",
    version: int = 1,
) -> Path:
    schedule = tmp_path / "schedule.jsonl.gz"
    with gzip.open(schedule, "wt") as fh:
        for event in events:
            fh.write(json.dumps(event) + "\n")
    spec = {
        "kind": SPEC_KIND,
        "version": version,
        "promotion_allowed": False,
        "matrix_written": False,
        "source_artifact": str(tmp_path),
        "symbol": "MU",
        "side": side,
        "account": "trb",
        "event_schedule": str(schedule),
        "expected_schedule_sha256": hashlib.sha256(
            schedule.read_bytes()
        ).hexdigest(),
        "npz_path": str(tmp_path / "MU.npz"),
        "expected_npz_sha256": "0" * 64,
        "validation_start": "2026-01-01",
        "validation_end_exclusive": "2026-01-03",
        "curve": {
            "label": "FROZEN",
            "mode": "linear",
            "trigger": "union",
            "semantics": "target",
            "stoch_low": 30.0,
            "d_bottom": 8.0,
            "d_top": 5.0,
            "h4_bottom": 4.0,
            "h4_top": 2.0,
            "h1_bottom": 2.0,
            "h1_top": 1.0,
        },
        "semantics": "target",
        "base_unit_usd": 2_000.0,
        "account_equity_usd": 10_000.0,
        "hard_capacity_usd": 16_000.0,
        "commission_bps_one_way": 5.0,
        "slippage_bps_one_way": 2.0,
        "expected_metrics": {
            "capital_return_pct": 9.79,
            "binary_tim_pct": 100.0,
            "exposure_weighted_tim_pct": 25.0,
            "peak_post_fill_notional_usd": 4_000.0,
            "requested_notional_usd": 4_000.0,
            "filled_notional_usd": 4_000.0,
            "clamp_count": 0,
            "rows": 2,
        },
        "expected_counts": {
            "entry_fills": 1,
            "technical_exits": 0,
            "mtm_final": 1,
            "actions": 2,
        },
        "accounting_tolerance_bp": 1e-4,
    }
    if version == SPEC_VERSION:
        source_result = tmp_path / "result.json"
        source_result.write_text('{"frozen":true}\n')
        selection_inputs = {
            "fold": 3,
            "train": ["2025-01-01", "2026-01-01"],
            "selected_curve": spec["curve"],
            "selected_candidate": None,
            "selection_score": 1.0,
            "inner_metrics": [],
        }
        spec.update(
            {
                "source_result": str(source_result),
                "expected_source_result_sha256": hashlib.sha256(
                    source_result.read_bytes()
                ).hexdigest(),
                "source_npz": {
                    "path": str(tmp_path / "MU.npz"),
                    "sha256": "0" * 64,
                    "execution_provenance": {
                        "safe_for_exact_engine": True,
                    },
                },
                "entry_schedule": {
                    "path": str(schedule),
                    "sha256": spec["expected_schedule_sha256"],
                },
                "fold_boundaries": {
                    "fold": 3,
                    "train_start": "2025-01-01",
                    "train_end_exclusive": "2026-01-01",
                    "validation_start": "2026-01-01",
                    "validation_end_exclusive": "2026-01-03",
                },
                "selection_provenance": {
                    "frozen_fold": 3,
                    "selected_from_training_only": True,
                    "validation_or_final_metrics_used_for_selection": False,
                    "selection_inputs": selection_inputs,
                    "selection_inputs_sha256": _canonical_sha256(
                        selection_inputs
                    ),
                    "validation_evidence_sha256": "f" * 64,
                },
                "ladder_multipliers": {
                    "D": {"bottom": 8.0, "top": 5.0},
                    "4h": {"bottom": 4.0, "top": 2.0},
                    "1h": {"bottom": 2.0, "top": 1.0},
                    "hard_max": 8.0,
                },
                "exit_n": 30,
                "exit_contract": {
                    "family": "E02_DONCHIAN",
                    "timeframe": "4h",
                    "n": 30,
                    "signal": "completed_4h_close_below_prior_N_low",
                },
            }
        )
    path = tmp_path / "spec.json"
    path.write_text(json.dumps(spec))
    return path


def _events(*, post_notional: float = 4_000.0) -> list[dict]:
    return [
        {
            "type": "ENTRY",
            "reason": "initial_ladder",
            "signal_index": 0,
            "signal_ts": 100,
            "fill_index": 1,
            "fill_ts": 200,
            "latency_rth_bars": 1,
            "fill_px": 100.0,
            "quantity": post_notional / 100.0,
            "requested_notional_usd": post_notional,
            "requested_target_notional_usd": post_notional,
            "filled_notional_usd": post_notional,
            "position_qty_before_fill": 0.0,
            "position_qty_after_fill": post_notional / 100.0,
            "post_fill_notional_usd": post_notional,
            "entry_capacity_usd": 16_000.0,
            "clamped": False,
            "semantics": "target",
            "entry_multiplier": 2.0,
            "completed_htf_source_ts": {"1h": 90},
        },
        {
            "type": "MTM_FINAL",
            "reason": "END_OF_VALIDATION_MTM",
            "signal_index": 2,
            "signal_ts": 300,
            "fill_index": 2,
            "fill_ts": 300,
            "latency_rth_bars": 0,
            "fill_px": 110.0,
            "quantity": post_notional / 100.0,
            "filled_notional_usd": 4_400.0,
            "position_qty_after_fill": 0.0,
            "entry_capacity_usd": 16_000.0,
            "completed_htf_source_ts": {},
        },
    ]


def test_target_schedule_is_explicit_and_capacity_is_enforced(tmp_path):
    adapter = LadderReplayAdapter(_write_spec(tmp_path, _events()))
    assert [action.action for action in adapter.actions] == ["OPEN", "CLOSE"]
    assert adapter.spec["semantics"] == "target"
    assert adapter.actions[0].source_event["requested_target_notional_usd"] == 4_000
    assert adapter.actions[0].fill_index == adapter.actions[0].signal_index + 1

    with pytest.raises(LadderReplayError, match="capacity"):
        LadderReplayAdapter(
            _write_spec(tmp_path, _events(post_notional=18_000.0))
        )


def test_short_schedule_uses_sell_to_open_and_buy_to_cover(tmp_path):
    adapter = LadderReplayAdapter(
        _write_spec(tmp_path, _events(), side="SHORT")
    )
    entry, final = adapter.actions
    assert entry.position_side == "SHORT"
    assert entry.order_side == "SELL"
    assert final.position_side == "SHORT"
    assert final.order_side == "BUY"
    assert adapter.expected_fill_from_loaded_bar(entry, 100.0) == pytest.approx(
        99.98
    )
    assert adapter.expected_fill_from_loaded_bar(final, 100.0) == pytest.approx(
        100.02
    )


def test_zero_fill_capacity_request_is_audited_but_not_executed(tmp_path):
    events = _events()
    events.insert(
        1,
        {
            "type": "CAPACITY_NO_FILL",
            "reason": "ladder_add",
            "signal_index": 1,
            "source_signal_index": 1,
            "signal_ts": 200,
            "fill_index": 2,
            "source_fill_index": 2,
            "fill_ts": 300,
            "latency_rth_bars": 1,
            "requested_notional_usd": 1_000.0,
            "filled_notional_usd": 0.0,
            "clamped": True,
            "entry_multiplier": 1.0,
            "completed_htf_source_ts": {"1h": 190},
        },
    )
    adapter = LadderReplayAdapter(_write_spec(tmp_path, events))
    assert len(adapter.actions) == 2
    assert len(adapter.audit_events) == 1
    assert adapter._requested == 1_000.0
    assert adapter._clamps == 1


def test_schedule_and_weighted_tim_audit_use_actual_faithful_quantities(tmp_path):
    adapter = LadderReplayAdapter(_write_spec(tmp_path, _events()))
    entry, final = adapter.actions
    adapter.record_result(
        entry,
        result="SUCCESS",
        actual_quantity=40.0,
        actual_price=100.0,
        post_position_qty=40.0,
    )
    adapter.observe_bar(close_price=100.0, position_qty=40.0)
    adapter.observe_bar(close_price=100.0, position_qty=40.0)
    adapter.record_result(
        final,
        result="SUCCESS",
        actual_quantity=40.0,
        actual_price=110.0,
        post_position_qty=0.0,
    )
    audit = adapter.final_audit()
    assert audit["status"] == "PASS"
    assert audit["time_in_market"]["actual_binary_pct"] == 100.0
    assert audit["time_in_market"]["actual_weighted_pct"] == 25.0
    assert audit["capacity"]["entry_capacity_breach"] is False


def test_research_one_way_fees_match_vector_cash_accounting():
    trades = [
        {
            "position_key": "trb:MU_LONG",
            "symbol": "MU",
            "position_side": "LONG",
            "action": "OPEN",
            "price": 100.0,
            "quantity": 10.0,
            "reason": "V8_RESEARCH_BAND_LADDER_REPLAY__INITIAL",
            "research_commission_bps_one_way": 5.0,
        },
        {
            "position_key": "trb:MU_LONG",
            "symbol": "MU",
            "position_side": "LONG",
            "action": "AUGMENT",
            "price": 80.0,
            "quantity": 10.0,
            "reason": "V8_RESEARCH_BAND_LADDER_REPLAY__ADD",
            "research_commission_bps_one_way": 5.0,
        },
        {
            "position_key": "trb:MU_LONG",
            "symbol": "MU",
            "position_side": "LONG",
            "action": "CLOSE",
            "price": 110.0,
            "quantity": 20.0,
            "reason": "V8_RESEARCH_BAND_LADDER_REPLAY__EXIT",
            "research_commission_bps_one_way": 5.0,
        },
    ]
    engine._compute_trade_pnl(trades)
    close = trades[-1]
    expected_gross = (110.0 - 90.0) * 20.0
    expected_fees = 0.0005 * (1_000.0 + 800.0 + 2_200.0)
    assert close["pnl_dollars"] == pytest.approx(expected_gross - expected_fees)
    assert close["commission_dollars_open"] == pytest.approx(0.9)
    assert close["commission_dollars_close"] == pytest.approx(1.1)


def test_engine_adapter_is_default_off_and_mutually_exclusive():
    source = Path("backtest_v8_engine.py").read_text()
    assert "--research-ladder-spec" in source
    assert "V8_RESEARCH_LADDER_SPEC" in source
    assert "mutually exclusive" in source
    assert "V8_RESEARCH_BAND_LADDER_REPLAY" in source


def test_schedule_gzip_is_byte_deterministic_across_paths(tmp_path):
    events = _events()
    first = _write_schedule_deterministic(tmp_path / "a.gz", events)
    second = _write_schedule_deterministic(tmp_path / "different_name.gz", events)
    assert first.read_bytes() == second.read_bytes()
    assert hashlib.sha256(first.read_bytes()).hexdigest() == hashlib.sha256(
        second.read_bytes()
    ).hexdigest()


def test_v2_parser_binds_source_selection_and_rejects_final_leakage(tmp_path):
    path = _write_spec(tmp_path, _events(), version=SPEC_VERSION)
    parsed = LadderReplayAdapter(path)
    assert parsed.spec["selection_provenance"][
        "validation_or_final_metrics_used_for_selection"
    ] is False

    payload = json.loads(path.read_text())
    payload["selection_provenance"][
        "validation_or_final_metrics_used_for_selection"
    ] = True
    path.write_text(json.dumps(payload))
    with pytest.raises(LadderReplayError, match="training-only"):
        LadderReplayAdapter(path)


def test_v2_parser_rejects_source_result_mutation(tmp_path):
    path = _write_spec(tmp_path, _events(), version=SPEC_VERSION)
    (tmp_path / "result.json").write_text('{"frozen":false}\n')
    with pytest.raises(LadderReplayError, match="source result fingerprint"):
        LadderReplayAdapter(path)


def test_v2_parser_requires_explicit_matching_exit_contract(tmp_path):
    path = _write_spec(tmp_path, _events(), version=SPEC_VERSION)
    payload = json.loads(path.read_text())
    payload["exit_contract"]["n"] = 17
    path.write_text(json.dumps(payload))
    with pytest.raises(LadderReplayError, match="exit contract"):
        LadderReplayAdapter(path)


def test_synthetic_5m_parent_future_fails_exact_engine_provenance():
    class FakeZ(dict):
        @property
        def files(self):
            return list(self)

    ts = np.arange(12, dtype=np.int64) * 300 + 1_700_000_000
    synthetic = np.zeros(12, dtype=np.uint8)
    synthetic[3:6] = 1
    parent = ts.copy()
    parent[3:6] += np.array([600, 300, 0], dtype=np.int64)
    data = SimpleNamespace(
        synthetic=synthetic,
        ts=ts,
        full_indices=np.arange(12, dtype=np.int64),
        z=FakeZ(synthetic_5m_parent_close_ts=parent),
    )
    audit = audit_execution_provenance(data, left=0, right=len(ts))
    assert audit["status"] == "BLOCKED"
    assert audit["safe_for_exact_engine"] is False
    assert audit["synthetic_rows"] == 3
    assert audit["future_parent_rows"] == 2
    assert audit["parent_lag_max_seconds"] == 600


def test_synthetic_rows_observable_at_parent_close_are_safe():
    class FakeZ(dict):
        @property
        def files(self):
            return list(self)

    ts = np.arange(12, dtype=np.int64) * 300 + 1_700_000_000
    synthetic = np.ones(12, dtype=np.uint8)
    data = SimpleNamespace(
        synthetic=synthetic,
        ts=ts,
        full_indices=np.arange(12, dtype=np.int64),
        z=FakeZ(synthetic_5m_parent_close_ts=ts.copy()),
    )
    audit = audit_execution_provenance(data, left=0, right=len(ts))
    assert audit["status"] == "PASS"
    assert audit["future_parent_rows"] == 0
