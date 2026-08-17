import unittest
from unittest import mock
import tempfile
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from classic_formations import (
    detect_classic_formations,
    ensure_npz_formation_fields,
    formation_fields_from_ohlcv,
    formation_vector_mask,
    latest_formation_fields,
    select_latest_formation,
)
from backtest_v8_harness import IndicatorStore


def shaped(anchors, n=40):
    x = np.arange(n)
    close = np.interp(x, np.linspace(0, n - 1, len(anchors)), anchors)
    return close.copy(), close + 0.15, close - 0.15, close


class ClassicFormationTests(unittest.TestCase):
    def test_reversal_formations(self):
        cases = {
            "head_shoulders": [100, 110, 102, 116, 102, 110, 98],
            "inverse_head_shoulders": [110, 100, 108, 94, 108, 100, 112],
            "double_top": [100, 110, 100, 110, 99, 98],
            "double_bottom": [110, 100, 110, 100, 111, 112],
            "cup_handle": [110, 106, 98, 100, 109, 106, 111],
            "inverse_cup_handle": [100, 104, 112, 110, 101, 105, 99],
        }
        for name, anchors in cases.items():
            with self.subTest(name=name):
                o, h, l, c = shaped(anchors)
                result = detect_classic_formations(
                    o, h, l, c, lookback=40, tolerance=0.12, min_prominence=0.08
                )
                self.assertTrue(bool(result[name][-1]))

    def test_continuation_and_converging_formations(self):
        n = 40
        q = np.arange(n) / (n - 1)
        cases = {}
        high = 110 + 4 * q
        low = 100 + 10 * q
        close = (high + low) / 2
        close[-1] = 106
        cases["rising_wedge"] = (close.copy(), high, low, close)
        high = 120 - 10 * q
        low = 110 - 4 * q
        close = (high + low) / 2
        close[-1] = 120
        cases["falling_wedge"] = (close.copy(), high, low, close)
        high = np.full(n, 110.0)
        low = 100 + 8 * q
        close = (high + low) / 2
        close[-1] = 112
        cases["ascending_triangle"] = (close.copy(), high, low, close)
        high = 110 - 8 * q
        low = np.full(n, 100.0)
        close = (high + low) / 2
        close[-1] = 98
        cases["descending_triangle"] = (close.copy(), high, low, close)
        close = np.r_[np.linspace(100, 112, 13), np.linspace(111, 109, 26), [113.0]]
        cases["bull_flag_pennant"] = (close.copy(), close + 0.3, close - 0.3, close)
        close = np.r_[np.linspace(112, 100, 13), np.linspace(101, 103, 26), [99.0]]
        cases["bear_flag_pennant"] = (close.copy(), close + 0.3, close - 0.3, close)
        for name, values in cases.items():
            with self.subTest(name=name):
                result = detect_classic_formations(
                    *values, lookback=40, tolerance=0.12, min_prominence=0.08
                )
                self.assertTrue(bool(result[name][-1]))

    def test_causal_prefix_invariance(self):
        rng = np.random.default_rng(7)
        close = 100 + np.cumsum(rng.normal(0, 0.5, 140))
        high = close + rng.uniform(0.1, 0.8, len(close))
        low = close - rng.uniform(0.1, 0.8, len(close))
        full = detect_classic_formations(close, high, low, close)
        for stop in (60, 91, 120):
            prefix = detect_classic_formations(
                close[:stop], high[:stop], low[:stop], close[:stop]
            )
            for key in ("direction", "primary_code", "bull_score", "bear_score"):
                np.testing.assert_array_equal(full[key][:stop], prefix[key])

    def test_existing_npz_style_arrays_are_upgraded_as_parent_pulses(self):
        o, h, l, c = shaped([110, 100, 108, 90, 108, 100, 115], n=41)
        # Simulate mapped 1h parents repeated over four base rows.
        arrays = {
            "open_1h": np.repeat(o, 4),
            "high_1h": np.repeat(h, 4),
            "low_1h": np.repeat(l, 4),
            "close_1h": np.repeat(c, 4),
            "volume_1h": np.repeat(np.ones(41), 4),
        }
        added = ensure_npz_formation_fields(arrays, timeframes=("1h",), lookback=40)
        self.assertIn("formation_inverse_head_shoulders_1h", added)
        signal_rows = np.flatnonzero(added["formation_inverse_head_shoulders_1h"])
        self.assertTrue(signal_rows.size)
        self.assertTrue(np.all(signal_rows % 4 == 0))

    def test_native_5m_npz_collapses_broadcast_15m_parents_once(self):
        c = np.r_[np.linspace(100, 112, 13), np.linspace(111, 109, 26), [113.0]]
        o, h, l = c.copy(), c + 0.3, c - 0.3
        repeats = 3
        parent_ts = np.repeat(np.arange(40, dtype=np.int64) * 900 + 900, repeats)
        arrays = {
            "timestamp_15m": parent_ts,
            "open_15m": np.repeat(o, repeats),
            "high_15m": np.repeat(h, repeats),
            "low_15m": np.repeat(l, repeats),
            "close_15m": np.repeat(c, repeats),
            "volume_15m": np.ones(40 * repeats),
        }
        added = ensure_npz_formation_fields(arrays, timeframes=("15m",), lookback=40)
        signal_rows = np.flatnonzero(added["formation_bull_flag_pennant_15m"])
        self.assertTrue(signal_rows.size)
        self.assertTrue(np.all(signal_rows % repeats == 0))

    def test_parent_timestamp_preserves_identical_consecutive_candles(self):
        o, h, l, c = shaped([110, 100, 108, 94, 108, 100, 112], n=40)
        # Make two distinct completed parents byte-identical. Timestamp changes
        # still preserve both observations in the compact causal sequence.
        for values in (o, h, l, c):
            values[10] = values[9]
        arrays = {
            "timestamp_1h": np.repeat(np.arange(40, dtype=np.int64) * 3600 + 3600, 4),
            "open_1h": np.repeat(o, 4),
            "high_1h": np.repeat(h, 4),
            "low_1h": np.repeat(l, 4),
            "close_1h": np.repeat(c, 4),
            "volume_1h": np.ones(160),
        }
        added = ensure_npz_formation_fields(arrays, timeframes=("1h",), lookback=40)
        self.assertTrue(bool(added["formation_inverse_head_shoulders_1h"][-4]))

    def test_v8_vec_loader_forwards_parent_timestamps(self):
        from v8_vec_sweep import load_npz

        o, h, l, c = shaped([110, 100, 108, 94, 108, 100, 112], n=40)
        for values in (o, h, l, c):
            values[10] = values[9]
        repeats = 4
        n = len(c) * repeats
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TEST.npz"
            np.savez(
                path,
                timestamps=np.arange(n, dtype=np.int64) * 300,
                timestamp_1h=np.repeat(
                    np.arange(40, dtype=np.int64) * 3600 + 3600, repeats
                ),
                open_1h=np.repeat(o, repeats),
                high_1h=np.repeat(h, repeats),
                low_1h=np.repeat(l, repeats),
                close_1h=np.repeat(c, repeats),
                volume_1h=np.ones(n),
            )
            with mock.patch("v8_vec_sweep._resolve_npz_path", return_value=path):
                loaded, _ = load_npz("TEST", "tradier")
        self.assertTrue(bool(loaded["formation_inverse_head_shoulders_1h"][-repeats]))

    def test_live_scalar_and_vector_selectors_match(self):
        o, h, l, c = shaped([110, 100, 108, 94, 108, 100, 112])
        arrays = formation_fields_from_ohlcv(o, h, l, c, None, "1h", lookback=40,
                                             tolerance=0.12, min_prominence=0.08)
        latest = latest_formation_fields(arrays)
        cfg = SimpleNamespace(
            FORMATION_TFS="1h",
            FORMATION_MIN_SCORE=0.5,
            FORMATION_HEAD_SHOULDERS_ENTRY_ENABLED=True,
            FORMATION_HEAD_SHOULDERS_EXIT_ENABLED=False,
        )
        selected = select_latest_formation(
            latest, is_long=True, action="ENTRY", config=cfg
        )
        self.assertIsNotNone(selected)
        mask, scores, _ = formation_vector_mask(
            arrays, is_long=True, action="ENTRY", config=cfg, n=len(c)
        )
        self.assertTrue(bool(mask[-1]))
        self.assertAlmostEqual(selected["score"], float(scores[-1]), places=6)

    def test_indicator_store_preserves_pre_start_warmup(self):
        o, h, l, c = shaped([110, 100, 108, 90, 108, 100, 115], n=41)
        mapped = [np.repeat(values, 4) for values in (o, h, l, c)]
        n = len(mapped[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TEST.npz"
            np.savez(
                path,
                timestamps=np.arange(n, dtype=np.int64) * 300,
                open_1h=mapped[0], high_1h=mapped[1], low_1h=mapped[2], close_1h=mapped[3],
                volume_1h=np.ones(n), close_5m=mapped[3],
            )
            full = IndicatorStore(str(path))
            sliced = IndicatorStore(str(path), start_idx=100)
            key = "formation_inverse_head_shoulders_1h"
            np.testing.assert_array_equal(full.arrays[key][100:], sliced.arrays[key])

    def test_tradier_archive_with_compatibility_3m_fields_derives_when_opted_in(self):
        o, h, l, c = shaped([110, 100, 108, 90, 108, 100, 115], n=41)
        mapped = [np.repeat(values, 4) for values in (o, h, l, c)]
        n = len(mapped[0])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "TEST.npz"
            np.savez(
                path,
                timestamps=np.arange(n, dtype=np.int64) * 300,
                open_1h=mapped[0], high_1h=mapped[1], low_1h=mapped[2], close_1h=mapped[3],
                volume_1h=np.ones(n), close_5m=mapped[3], close_3m=mapped[3],
            )
            with mock.patch.dict(os.environ, {"V8_CLASSIC_FORMATION_FIELDS": "1"}):
                store = IndicatorStore(str(path))
            self.assertIn("formation_inverse_head_shoulders_1h", store.arrays)

    def test_ez_live_fields_use_closed_kline_not_mark_price(self):
        with tempfile.TemporaryDirectory() as tmp:
            os.environ.setdefault("EZ_LOG_DIR", tmp)
            import pandas as pd
            from ez_indicators import IndicatorCalculator

            n = 120
            x = np.arange(n)
            close = 100.0 + np.sin(x / 8.0) * 2.0 + x * 0.03
            frame = pd.DataFrame({
                "timestamp_dt": pd.date_range(
                    "2026-01-01", periods=n, freq="1h", tz="UTC"
                ),
                "open": close - 0.05,
                "high": close + 0.30,
                "low": close - 0.30,
                "close": close,
                "volume": np.full(n, 1000.0),
            })
            base = IndicatorCalculator().compute(frame, "1h", None, False)
            marked = IndicatorCalculator().compute(frame, "1h", 999.0, True)
            formation_keys = sorted(
                key for key in base if key.startswith("formation_")
            )
            self.assertEqual(len(formation_keys), 47)
            for key in formation_keys:
                self.assertEqual(base[key], marked[key], key)
            self.assertEqual(marked["current_price"], 999.0)


if __name__ == "__main__":
    unittest.main()
