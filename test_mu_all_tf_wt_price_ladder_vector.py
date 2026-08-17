from types import SimpleNamespace
import gzip
import hashlib
import json

import numpy as np

from tools import mu_all_tf_wt_price_ladder_vector as lane
from tools.v8_research_mu_wt_ladder_adapter import MuWTPriceLadderReplayAdapter


def _data(n: int = 240):
    ts = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    close = np.linspace(100.0, 120.0, n)
    return SimpleNamespace(
        ts=ts, source_ts=ts.copy(), full_indices=np.arange(n, dtype=np.int64),
        open=close.copy(), high=close + 1.0, low=close - 1.0, close=close,
    )


def _sig(fill, source=None, family="TEST"):
    fill = np.asarray(fill, dtype=np.int32)
    source = np.asarray(source if source is not None else fill - 1, dtype=np.int32)
    return lane.SparseSignal(fill, source, "5m", family)


def test_event_quality_is_discovery_bounded() -> None:
    data = _data()
    sig = _sig([20, 80, 180])
    original = lane._event_quality(data, sig, 1, 10, "in", right=120)
    data.close[180:200] *= 1000.0
    changed_holdout = lane._event_quality(data, sig, 1, 10, "in", right=120)
    assert original == changed_holdout


def test_heartbeat_serializes_first_round_infinite_improvement_as_null(tmp_path) -> None:
    path = tmp_path / "heartbeat.jsonl"
    lane.heartbeat(path, "ROUND_COMPLETE", improvement_pp=float("inf"))
    assert json.loads(path.read_text())["improvement_pp"] is None


def test_sparse_simulation_never_consumes_events_after_fold_end() -> None:
    data = _data()
    result = lane.simulate_sparse(
        data, 1, _sig([180]), _sig([]), _sig([]), 4,
        right=120, initial_fill_mode="build_target",
    )
    assert result["entry_fill_actions"] == 0
    assert result["requested_notional_usd"] == 0.0
    assert result["final_equity_usd"] == lane.CAPACITY


def test_one_unit_initial_ladder_and_build_target_are_distinct() -> None:
    data = _data()
    entry, empty = _sig([10]), _sig([])
    one = lane.simulate_sparse(data, 1, entry, empty, empty, 4, collect_ledger=True)
    built = lane.simulate_sparse(
        data, 1, entry, empty, empty, 4, collect_ledger=True,
        initial_fill_mode="build_target",
    )
    assert one["entry_fill_actions"] == 1
    assert built["entry_fill_actions"] == 4
    assert one["avg_deployed_usd_per_entry_fill"] == lane.BASE_UNIT
    assert built["avg_deployed_usd_per_entry_fill"] == lane.BASE_UNIT


def test_true_ladder_augments_and_reduces_units() -> None:
    data = _data()
    result = lane.simulate_sparse(
        data, 1, _sig([10]), _sig([50, 70]), _sig([20, 30]), 3,
        collect_ledger=True, initial_fill_mode="one_unit",
    )
    kinds = [row["type"] for row in result["ledger"]]
    assert kinds[:5] == ["ENTRY", "AUGMENT", "AUGMENT", "REDUCE", "REDUCE"]
    assert result["close_actions"] == 2
    assert result["closed_lifecycles"] == 0
    assert kinds[-1] == "MTM_FINAL"


def test_short_rising_market_uses_explicit_cash_floor() -> None:
    data = _data()
    result = lane.simulate_sparse(data, -1, _sig([]), _sig([]), _sig([]), 1)
    assert result["underlying_long_bh_pct"] > 0
    assert result["side_bh_pct"] < 0
    assert result["cash_bh_floor_return_pct"] == 0.0
    assert result["multiple_denominator"] == "CASH_FLOOR_WEALTH_MULTIPLE"
    assert result["return_multiple_vs_bh_or_cash"] == 1.0


def test_zero_drawdown_ranks_as_real_zero_not_missing() -> None:
    base = {"eligible_for_exact": True, "return_multiple_vs_bh_or_cash": 5.0,
            "gain_pct_capacity": 10.0, "trade_sharpe": 1.0, "close_actions": 30}
    zero = {**base, "max_drawdown_pct": 0.0}
    missing = {**base, "max_drawdown_pct": None}
    assert lane._rank_key(zero) > lane._rank_key(missing)


def test_markdown_handles_missing_sharpe_without_crashing() -> None:
    result = {"rows": 1, "exact_queue": [], "leaders": [{
        "side": "SHORT", "gain_pct_capacity": 0.0,
        "return_multiple_vs_bh_or_cash": 1.0, "close_actions": 0,
        "max_drawdown_pct": 0.0, "daily_sharpe": None,
    }]}
    assert "daily Sharpe N/A" in lane.render_markdown(result)


def test_real_v8_adapter_accepts_partial_reduce_schedule(tmp_path) -> None:
    def event(kind, signal, fill, qty, before, after):
        return {
            "type": kind, "reason": kind, "signal_index": signal,
            "signal_clock_index": signal, "signal_ts": 1000 + signal * 300,
            "signal_availability_ts": 1000 + signal * 300,
            "signal_source_ts": 1000 + signal * 300, "signal_source_row_index": signal,
            "fill_index": fill, "fill_clock_index": fill, "fill_ts": 1000 + fill * 300,
            "fill_availability_ts": 1000 + fill * 300, "fill_source_ts": 1000 + fill * 300,
            "fill_source_row_index": fill, "latency_rth_bars": 0 if kind == "MTM_FINAL" else 1,
            "fill_px": 100.0, "quantity": qty, "position_qty_before_fill": before,
            "position_qty_after_fill": after, "post_fill_notional_usd": after * 100.0,
            "filled_notional_usd": qty * 100.0, "requested_notional_usd": qty * 100.0,
            "clamped": False,
        }
    events = [event("ENTRY", 0, 1, 20.0, 0.0, 20.0),
              event("AUGMENT", 1, 2, 20.0, 20.0, 40.0),
              event("REDUCE", 2, 3, 20.0, 40.0, 20.0),
              event("MTM_FINAL", 4, 4, 20.0, 20.0, 0.0)]
    schedule = tmp_path / "schedule.jsonl.gz"
    with gzip.open(schedule, "wt") as fh:
        for row in events:
            fh.write(json.dumps(row) + "\n")
    spec = {
        "kind": "V8_RESEARCH_MU_WT_PRICE_LADDER_REPLAY", "version": 3,
        "contract": lane.CONTRACT, "symbol": "MU", "side": "LONG", "account": "trb",
        "npz_path": str(tmp_path / "MU.npz"), "expected_npz_sha256": "0" * 64,
        "event_schedule": str(schedule),
        "expected_schedule_sha256": hashlib.sha256(schedule.read_bytes()).hexdigest(),
        "expected_event_ledger_sha256": lane.canonical_sha256(events),
        "parameters": {"lane": "combined", "initial_fill_mode": "one_unit", "target_units_requested": 2},
        "fold": {"start_ts": 1000, "end_ts": 2200}, "hard_capacity_usd": 16000.0,
        "base_unit_usd": 2000.0, "commission_bps_one_way": 2.5, "slippage_bps_one_way": 0.0,
        "expected_metrics": {"requested_notional_usd": 4000.0, "filled_notional_usd": 4000.0,
                             "clamp_count": 0, "final_equity_usd": 16000.0},
        "expected_counts": {"actions": 4}, "promotion_allowed": False,
        "matrix_written": False, "live_written": False,
    }
    spec_path = tmp_path / "spec.json"
    spec_path.write_text(json.dumps(spec))
    adapter = MuWTPriceLadderReplayAdapter(spec_path)
    assert [action.action for action in adapter.actions] == ["OPEN", "AUGMENT", "REDUCE", "CLOSE"]
    assert adapter.actions[2].order_side == "SELL"
