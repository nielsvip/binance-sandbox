from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent / "tools"))
import vec_band_ladder_walkforward as ladder


def test_direct_file_bootstrap_can_import_replay_bundle_emitter():
    root = Path(__file__).resolve().parent
    code = (
        "import runpy;"
        "runpy.run_path('tools/vec_band_ladder_walkforward.py');"
        "from tools.v8_research_ladder_adapter import emit_replay_bundle;"
        "assert callable(emit_replay_bundle)"
    )
    proc = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        text=True,
        capture_output=True,
    )
    assert proc.returncode == 0, proc.stderr


def test_remembered_ladder_is_clipped_to_eight_x():
    assert ladder.ladder_mult(0.0, 10.0, 6.0, "linear") == 8.0
    assert ladder.ladder_mult(1.0, 10.0, 6.0, "linear") == 6.0


def test_below_lower_band_is_zero_and_above_is_top():
    assert ladder.ladder_mult(-0.01, 6.0, 4.0, "linear") == 0.0
    assert ladder.ladder_mult(1.01, 6.0, 4.0, "linear") == 4.0


def test_center_plateau_matches_live_function_shape():
    assert ladder.ladder_mult(0.2, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(0.5, 6.0, 4.0, "center_plateau") == 6.0
    assert ladder.ladder_mult(1.0, 6.0, 4.0, "center_plateau") == 4.0


def test_block_generator_preserves_valid_pairs():
    curves = ladder._curves(17, 30)
    assert len(curves) >= 30
    for curve in curves:
        for tf in ladder.TF_ORDER:
            bottom, top = curve.pair(tf)
            assert bottom >= top >= 0


def test_short_cash_ledger_and_benchmark_are_side_aware():
    n = 100
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=np.full(n, 100.0),
        high=np.full(n, 101.0),
        low=np.full(n, 89.0),
        close=np.linspace(100.0, 90.0, n),
    )
    entry = np.zeros(n)
    entry[0] = 1.0
    signals = ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    curve = ladder.Curve(
        "TEST",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(data, signals, curve, 0, n, 0.0, 0.0, "SHORT")
    assert result["side"] == "SHORT"
    assert result["capital_return_pct"] == 10.0
    assert result["bh_capital_return_pct"] == 10.0
    assert result["peak_post_fill_notional_usd"] == 2_000.0
    assert result["entry_capacity_breach"] is False
    assert result["benchmark_floor_kind"] == "SIDE_AWARE_BH"
    # The strategy earns the same move with 99/100 of B&H dollar-time because
    # the skipped signal bar is flat.
    assert result["deployed_alpha_vs_bh_or_cash_pp"] == pytest.approx(
        0.10101010101010033
    )
    assert result["bh_ratio_eligible"] is False
    assert result["honest_bh_multiple"] is None


def test_negative_side_bh_uses_cash_floor_and_small_bh_disables_ratio():
    n = 100
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=np.full(n, 100.0),
        high=np.full(n, 111.0),
        low=np.full(n, 99.0),
        close=np.linspace(100.0, 110.0, n),
    )
    signals = ladder.SignalData(
        entry_mult=np.zeros(n),
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    curve = ladder.Curve(
        "CASH",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(
        data, signals, curve, 0, n, 0.0, 0.0, "SHORT"
    )
    assert result["bh_return_on_deployed_pct"] == -10.0
    assert result["benchmark_floor_kind"] == "CASH_0PCT"
    assert result["benchmark_floor_return_pct"] == 0.0
    assert result["deployed_alpha_vs_bh_or_cash_pp"] == 0.0
    assert result["bh_ratio_eligible"] is False
    assert result["honest_bh_multiple"] is None


def test_large_negative_bh_has_magnitude_but_never_a_ratio():
    n = 100
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=np.full(n, 100.0),
        high=np.full(n, 141.0),
        low=np.full(n, 99.0),
        close=np.linspace(100.0, 140.0, n),
    )
    signals = ladder.SignalData(
        entry_mult=np.zeros(n),
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=np.zeros(n, dtype=np.uint8),
        exit_ref=np.full(n, np.nan),
        causality={},
    )
    curve = ladder.Curve(
        "NEGATIVE_DENOMINATOR",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(
        data, signals, curve, 0, n, 0.0, 0.0, "SHORT"
    )
    assert result["bh_return_on_deployed_pct"] == -40.0
    assert result["bh_magnitude_eligible"] is True
    assert result["bh_ratio_eligible"] is False
    assert result["honest_bh_multiple"] is None


def test_selection_score_uses_deployed_alpha_not_levered_label_alpha():
    common = {
        "max_drawdown_account_pct": 0.0,
        "fill_ratio": 1.0,
        "exposure_weighted_tim_pct": 70.0,
    }
    levered_illusion = {
        **common,
        "alpha_vs_bh_pp": 900.0,
        "deployed_alpha_vs_bh_or_cash_pp": -2.0,
    }
    real_alpha = {
        **common,
        "alpha_vs_bh_pp": 10.0,
        "deployed_alpha_vs_bh_or_cash_pp": 3.0,
    }
    assert ladder._score([real_alpha]) > ladder._score([levered_illusion])


def test_short_reclaim_and_favorable_gap_are_exact_mirrors():
    n = 100
    close = np.full(n, 101.0)
    close[0:4] = [100.0, 100.0, 101.0, 101.0]
    close[4] = 97.0
    open_ = close.copy()
    open_[1] = 100.0
    open_[3] = 100.0
    open_[5] = 97.0
    high = close + 1.0
    high[4] = 103.0
    low = close - 1.0
    data = SimpleNamespace(
        ts=np.arange(n, dtype=np.int64),
        open=open_,
        high=high,
        low=low,
        close=close,
    )
    entry = np.zeros(n)
    entry[0] = 1.0
    exit_event = np.zeros(n, dtype=np.uint8)
    exit_event[2] = 1
    exit_ref = np.full(n, np.nan)
    exit_ref[2] = 98.0
    signals = ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=exit_event,
        exit_ref=exit_ref,
        causality={},
    )
    curve = ladder.Curve(
        "TEST",
        "linear",
        "green",
        "target",
        30.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
        1.0,
    )
    result = ladder._simulate(data, signals, curve, 0, n, 0.0, 0.0, "SHORT")
    assert result["exit_count"] == 1
    assert result["reclaim_reentries"] == 1
    assert result["bars_flat_beyond_reclaim"] == 0


@pytest.mark.parametrize("side", ["LONG", "SHORT"])
@pytest.mark.parametrize("semantics", ["target", "add"])
def test_compiled_scanner_matches_python_oracle_with_parent_batches(
    side, semantics
):
    rng = np.random.default_rng(20260729)
    n = 900
    # Three synthetic child rows are released at each completed parent close.
    ts = (np.arange(n, dtype=np.int64) // 3) * 900
    close = 100.0 + np.cumsum(rng.normal(0.0, 0.45, n))
    open_ = close + rng.normal(0.0, 0.12, n)
    high = np.maximum(open_, close) + rng.uniform(0.05, 0.7, n)
    low = np.minimum(open_, close) - rng.uniform(0.05, 0.7, n)
    data = SimpleNamespace(
        ts=ts,
        open=open_,
        high=high,
        low=low,
        close=close,
    )
    entry = np.zeros(n)
    entry[::17] = rng.choice([1.0, 2.0, 4.0, 8.0], len(entry[::17]))
    exits = np.zeros(n, dtype=np.uint8)
    exits[23::61] = 1
    refs = np.full(n, np.nan)
    refs[23::61] = (
        high[23::61] + 0.5 if side == "LONG" else low[23::61] - 0.5
    )
    signals = ladder.SignalData(
        entry_mult=entry,
        event_tf=np.zeros((3, n), dtype=np.uint8),
        exit_event=exits,
        exit_ref=refs,
        causality={},
    )
    curve = ladder.Curve(
        "PARITY",
        "linear",
        "green",
        semantics,
        30.0,
        8.0,
        4.0,
        6.0,
        3.0,
        4.0,
        1.0,
    )
    expected = ladder._simulate_python(
        data, signals, curve, 0, n, 0.0005, 0.0002, side
    )
    actual = ladder._simulate_compiled(
        data, signals, curve, 0, n, 0.0005, 0.0002, side
    )
    assert actual.keys() == expected.keys()
    for key, expected_value in expected.items():
        actual_value = actual[key]
        if isinstance(expected_value, float):
            assert actual_value == pytest.approx(
                expected_value, rel=1e-11, abs=1e-9
            ), key
        else:
            assert actual_value == expected_value, key


def test_completed_htf_wt_state_trigger_is_persistent_and_causal():
    class FakeZ(dict):
        @property
        def files(self):
            return list(self)

    n = 120
    ts = np.arange(n, dtype=np.int64) * 300
    z = FakeZ()
    htfs = {}
    for slot, tf in enumerate(ladder.TF_ORDER):
        event_index = np.arange(1 + slot, n, 3 + slot, dtype=np.int64)
        source_ts = ts[event_index] - 1
        count = len(event_index)
        close = np.linspace(100.0, 110.0, count)
        htfs[tf] = ladder.top.HTFData(
            tf=tf,
            event_index=event_index,
            source_ts=source_ts,
            open=close,
            high=close + 1.0,
            low=close - 1.0,
            close=close,
            rsi=np.full(count, 50.0),
            atr=np.ones(count),
        )
        z[f"wt_cross_bull_{tf}"] = np.zeros(n, dtype=np.uint8)
        z[f"wt_cross_bear_{tf}"] = np.zeros(n, dtype=np.uint8)
        z[f"stoch_k_{tf}"] = np.full(n, 50.0)
        z[f"lrL_pct_b_{tf}"] = np.full(n, 0.5)
        z[f"wt1_{tf}"] = np.full(n, 2.0)
        z[f"wt2_{tf}"] = np.full(n, 1.0)
    data = ladder.top.ExecutionData(
        symbol="TEST",
        path="memory",
        ts=ts,
        open=np.full(n, 100.0),
        high=np.full(n, 101.0),
        low=np.full(n, 99.0),
        close=np.full(n, 100.0),
        synthetic=np.zeros(n, dtype=np.uint8),
        full_indices=np.arange(n, dtype=np.int64),
        z=z,
        contract={"valid": True},
    )
    curve = ladder.Curve(
        "STATE",
        "linear",
        "wt_state",
        "target",
        30.0,
        3.0,
        1.0,
        2.0,
        1.0,
        1.0,
        0.5,
    )
    signals = ladder._build_signals(data, htfs, curve, 30, "LONG")
    assert np.count_nonzero(signals.entry_mult) > 10
    assert all(
        row["source_timestamp_future_count"] == 0
        for row in signals.causality.values()
    )
    assert all(
        row["directional_wt_state_events"] == row["selected_events"]
        for row in signals.causality.values()
    )
