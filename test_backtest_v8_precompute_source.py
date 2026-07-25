import unittest

import pandas as pd

from backtest_v8_precompute import (
    _choose_tradier_resample_source,
    _hybrid_tradier_5m,
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
        self.assertGreater(int(hybrid["_synthetic_5m"].sum()), 0)


if __name__ == "__main__":
    unittest.main()
