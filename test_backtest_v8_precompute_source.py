import unittest

import pandas as pd

from backtest_v8_precompute import (
    _choose_tradier_resample_source,
    _hybrid_tradier_5m,
    _merge_authentic_bars,
    resample_tf,
)


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


if __name__ == "__main__":
    unittest.main()
