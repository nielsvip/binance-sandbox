import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd
import numpy as np

from backtest_v8_precompute import (
    _apply_split_adjustments,
    _choose_tradier_resample_source,
    _hybrid_tradier_5m,
    _merge_authentic_bars,
    _prepend_authentic_history,
    load_klines,
    resample_tf,
)
import backtest_v8_precompute as precompute
import backtest_v8_precompute_tradier as tradier_precompute


def frame(start, periods, minutes):
    idx = pd.date_range(start, periods=periods, freq=f"{minutes}min", tz="UTC")
    return pd.DataFrame(
        {
            "open": range(periods),
            "high": [x + 1 for x in range(periods)],
            "low": [x - 1 for x in range(periods)],
            "close": [x + 0.5 for x in range(periods)],
            "volume": [10.0] * periods,
        },
        index=idx,
    )


class TradierSourceIntegrityTests(unittest.TestCase):
    def test_existing_kline_json_loads_instead_of_returning_none(self):
        with tempfile.NamedTemporaryFile(suffix=".json", mode="w", delete=False) as handle:
            json.dump(
                [
                    {
                        "timestamp": f"2026-01-02T00:{minute:02d}:00Z",
                        "open": 10,
                        "high": 11,
                        "low": 9,
                        "close": 10.5,
                        "volume": 100,
                    }
                    for minute in (0, 15, 30, 45)
                ],
                handle,
            )
            path = Path(handle.name)
        self.addCleanup(path.unlink)
        loaded = load_klines(path)
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), 4)

    def test_tradier_symbol_loader_returns_existing_raw_15m_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "AAPL_15m.json"
            path.write_text(
                json.dumps(
                    [
                        {
                            "timestamp": f"2026-01-02T00:{minute:02d}:00Z",
                            "open": 10,
                            "high": 11,
                            "low": 9,
                            "close": 10.5,
                            "volume": 100,
                        }
                        for minute in (0, 15, 30, 45)
                    ]
                )
            )
            loaded = tradier_precompute.load_klines("AAPL", "15m", Path(tmp))
        self.assertIsNotNone(loaded)
        self.assertEqual(len(loaded), 4)

    def test_opt_in_split_adjustment_is_applied_before_indicator_computation(self):
        original = precompute.SPLIT_ADJUSTMENTS
        self.addCleanup(setattr, precompute, "SPLIT_ADJUSTMENTS", original)
        effective = int(pd.Timestamp("2026-01-02 00:30", tz="UTC").timestamp())
        precompute.SPLIT_ADJUSTMENTS = {
            "CRWD": [{"effective_epoch": effective, "price_factor": 0.25, "source": "TEST"}]
        }
        source = frame("2026-01-02", 4, 15)
        adjusted = _apply_split_adjustments("CRWD", {"15m": source})["15m"]
        self.assertEqual(float(adjusted.iloc[0]["close"]), float(source.iloc[0]["close"]) * 0.25)
        self.assertEqual(float(adjusted.iloc[0]["volume"]), 40.0)
        self.assertEqual(float(adjusted.iloc[-1]["close"]), float(source.iloc[-1]["close"]))
        self.assertEqual(float(source.iloc[0]["volume"]), 10.0)

    def test_split_adjustment_auto_orients_both_source_scales(self):
        effective = pd.Timestamp("2026-01-02 01:00", tz="UTC")
        standard = frame("2026-01-02", 8, 15).astype(float)
        standard.loc[standard.index < effective, "close"] = 40.0
        standard.loc[standard.index >= effective, "close"] = 10.0
        reverse = standard.copy()
        reverse.loc[reverse.index < effective, "close"] = 10.0
        reverse.loc[reverse.index >= effective, "close"] = 40.0
        action = {"split_ratio": 4.0}
        self.assertEqual(precompute._resolve_split_price_factor(standard, effective, action), 0.25)
        self.assertEqual(precompute._resolve_split_price_factor(reverse, effective, action), 4.0)

    def test_split_adjustment_accepts_already_adjusted_source(self):
        effective = pd.Timestamp("2026-07-02", tz="UTC")
        index = pd.date_range("2026-06-20", periods=30, freq="D", tz="UTC")
        already_adjusted = pd.DataFrame(
            {"close": [190.0] * 12 + [195.0] * 18}, index=index
        )
        action = {"split_ratio": 4.0}
        self.assertEqual(
            precompute._resolve_split_price_factor(
                already_adjusted, effective, action
            ),
            1.0,
        )

    def test_split_adjustment_resolves_each_timeframe_independently(self):
        effective = pd.Timestamp("2026-07-02", tz="UTC")
        index = pd.date_range("2026-06-20", periods=30, freq="D", tz="UTC")
        raw = pd.DataFrame(
            {"open": [760.0] * 12 + [195.0] * 18,
             "high": [760.0] * 12 + [195.0] * 18,
             "low": [760.0] * 12 + [195.0] * 18,
             "close": [760.0] * 12 + [195.0] * 18,
             "volume": [100.0] * 30}, index=index
        )
        adjusted = pd.DataFrame(
            {"open": [190.0] * 12 + [195.0] * 18,
             "high": [190.0] * 12 + [195.0] * 18,
             "low": [190.0] * 12 + [195.0] * 18,
             "close": [190.0] * 12 + [195.0] * 18,
             "volume": [400.0] * 30}, index=index
        )
        prior = precompute.SPLIT_ADJUSTMENTS
        try:
            precompute.SPLIT_ADJUSTMENTS = {
                "CRWD": [{"effective_epoch": int(effective.timestamp()), "split_ratio": 4.0}]
            }
            result = precompute._apply_split_adjustments(
                "CRWD", {"15m": raw, "5m": adjusted}
            )
        finally:
            precompute.SPLIT_ADJUSTMENTS = prior
        self.assertAlmostEqual(float(result["15m"].iloc[0].close), 190.0)
        self.assertAlmostEqual(float(result["5m"].iloc[0].close), 190.0)

    def test_split_adjustment_reconciles_mixed_scale_rows_inside_5m(self):
        effective = pd.Timestamp("2026-07-01 15:00", tz="UTC")
        index = pd.date_range(
            "2026-07-01 13:30", periods=12, freq="15min", tz="UTC"
        )
        reference_close = np.where(index < effective, 760.0, 190.0)
        reference = pd.DataFrame(
            {
                name: reference_close.copy()
                for name in ("open", "high", "low", "close")
            },
            index=index,
        )
        reference["volume"] = 100.0
        fine_index = pd.date_range(index[0], periods=34, freq="5min", tz="UTC")
        mixed_close = np.full(len(fine_index), 190.0)
        mixed_close[1::3] = 760.0
        fine = pd.DataFrame(
            {
                name: mixed_close.copy()
                for name in ("open", "high", "low", "close")
            },
            index=fine_index,
        )
        fine["volume"] = np.where(mixed_close > 200, 100.0, 400.0)
        prior = precompute.SPLIT_ADJUSTMENTS
        try:
            precompute.SPLIT_ADJUSTMENTS = {
                "CRWD": [
                    {
                        "effective_epoch": int(effective.timestamp()),
                        "split_ratio": 4.0,
                    }
                ]
            }
            result = precompute._apply_split_adjustments(
                "CRWD", {"15m": reference, "5m": fine}
            )
        finally:
            precompute.SPLIT_ADJUSTMENTS = prior
        self.assertTrue(np.allclose(result["15m"]["close"], 190.0))
        self.assertTrue(np.allclose(result["5m"]["close"], 190.0))
        self.assertTrue(np.allclose(result["5m"]["volume"], 400.0))

    def test_split_adjustment_repairs_isolated_four_x_roundtrip_before_indicators(self):
        effective = pd.Timestamp("2026-07-01 17:05", tz="UTC")
        index = pd.DatetimeIndex(
            [
                *pd.date_range("2026-06-24 08:05", periods=5, freq="5min", tz="UTC"),
                *pd.date_range("2026-07-01 17:05", periods=5, freq="5min", tz="UTC"),
            ]
        )
        close = np.asarray(
            [169.5, 169.8525, 681.72, 170.1, 170.4, 170.5, 170.7, 170.8, 171.0, 171.2]
        )
        source = pd.DataFrame(
            {name: close.copy() for name in ("open", "high", "low", "close")},
            index=index,
        )
        source["volume"] = np.asarray(
            [400.0, 400.0, 100.0, 400.0, 400.0, 400.0, 400.0, 400.0, 400.0, 400.0]
        )
        prior = precompute.SPLIT_ADJUSTMENTS
        try:
            precompute.SPLIT_ADJUSTMENTS = {
                "CRWD": [
                    {
                        "effective_epoch": int(effective.timestamp()),
                        "split_ratio": 4.0,
                    }
                ]
            }
            result = precompute._apply_split_adjustments(
                "CRWD", {"5m": source}
            )["5m"]
        finally:
            precompute.SPLIT_ADJUSTMENTS = prior

        self.assertAlmostEqual(float(result.iloc[2].close), 170.43, places=5)
        self.assertAlmostEqual(float(result.iloc[2].open), 170.43, places=5)
        self.assertAlmostEqual(float(result.iloc[2].volume), 400.0, places=5)
        self.assertAlmostEqual(float(source.iloc[2].close), 681.72, places=5)

    def test_short_15m_does_not_erase_long_5m_history(self):
        d15 = frame("2026-06-01", 40, 15)
        d5 = frame("2026-01-01", 4000, 5)
        tf, selected = _choose_tradier_resample_source({"15m": d15, "5m": d5})
        self.assertEqual(tf, "5m")
        self.assertIs(selected, d5)

    def test_complete_15m_remains_preferred(self):
        d15 = frame("2026-01-01", 1400, 15)
        d5 = frame("2026-01-01", 4000, 5)
        tf, selected = _choose_tradier_resample_source({"15m": d15, "5m": d5})
        self.assertEqual(tf, "15m")
        self.assertIs(selected, d15)

    def test_hybrid_preserves_authentic_overlap_and_marks_provenance(self):
        d15 = frame("2026-01-01 00:10", 4, 15)
        real = frame("2026-01-01 00:30", 3, 5)
        real.loc[:, "close"] = 999.0
        hybrid = _hybrid_tradier_5m(d15, real)
        self.assertEqual(float(hybrid.loc[real.index[0], "close"]), 999.0)
        self.assertEqual(int(hybrid.loc[real.index[0], "_synthetic_5m"]), 0)
        self.assertEqual(
            hybrid.loc[real.index[0], "_synthetic_5m_parent_close_ts"],
            real.index[0],
        )
        self.assertGreater(int(hybrid["_synthetic_5m"].sum()), 0)
        first_synthetic = hybrid.loc[hybrid["_synthetic_5m"] == 1].iloc[0]
        self.assertIn(
            (
                first_synthetic["_synthetic_5m_parent_close_ts"]
                - hybrid.loc[hybrid["_synthetic_5m"] == 1].index[0]
            ).total_seconds(),
            (0.0, 300.0, 600.0),
        )

    def test_hybrid_keeps_newer_15m_tail_when_real_5m_has_more_rows(self):
        real = frame("2026-01-01", 100, 5)
        d15 = frame("2026-01-01 08:10", 20, 15)
        hybrid = _hybrid_tradier_5m(d15, real)
        self.assertGreater(hybrid.index[-1], real.index[-1])
        self.assertEqual(int(hybrid.loc[hybrid.index[-1], "_synthetic_5m"]), 1)

    def test_tradier_15m_resample_is_close_labelled(self):
        d5 = frame("2026-01-01 00:05", 6, 5)
        d15 = resample_tf(d5, "15m")
        expected = pd.Timestamp("2026-01-01 00:15", tz="UTC")
        self.assertEqual(d15.index[0], expected)
        self.assertEqual(float(d15.loc[expected, "close"]), float(d5.iloc[2]["close"]))

    def test_authentic_15m_wins_overlap_without_erasing_history(self):
        broad = frame("2026-01-01 00:15", 4, 15)
        authentic = frame("2026-01-01 00:45", 2, 15)
        authentic.loc[:, "close"] = 777.0
        merged = _merge_authentic_bars(broad, authentic)
        self.assertEqual(merged.index[0], broad.index[0])
        self.assertEqual(float(merged.loc[authentic.index[0], "close"]), 777.0)

    def test_stale_htf_only_prepends_history_and_cannot_replace_rebuilt_tail(self):
        authentic = frame("2025-01-01", 10, 60)
        authentic.loc[:, "close"] = 777.0
        rebuilt = frame("2025-01-01 06:00", 10, 60)
        rebuilt.loc[:, "close"] = 123.0
        merged = _prepend_authentic_history(rebuilt, authentic)
        self.assertEqual(merged.index[0], authentic.index[0])
        self.assertEqual(float(merged.loc[rebuilt.index[0], "close"]), 123.0)
        self.assertEqual(len(merged), 16)


if __name__ == "__main__":
    unittest.main()
