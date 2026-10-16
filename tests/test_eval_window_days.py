"""Task 09 (2026-10-14): evaluate_sanitized(window_days=7) silently returned the 30D slice.

Pins the contract at the slicer level: bars(7) < bars(30) on both venues, both
slicers agree, and unsupported windows raise instead of silently becoming 30.
"""
import sys
import unittest
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt.evaluate_v12 import _exact_30d_slice
from tools.opt.lifecycle_pilot import exact_month_slice


def _crypto_npz(days=40):
    bars = days * 96
    end = 1_792_000_000.0
    ts = end - np.arange(bars - 1, -1, -1) * 900.0
    return {"timestamps": ts, "close": np.ones(bars)}


def _stocks_npz(days=40, per_day=30):
    bars = days * per_day
    day0 = 1_792_000_000.0 - days * 86400.0
    ts = np.concatenate([np.linspace(day0 + d * 86400.0 + 34200.0, day0 + d * 86400.0 + 57600.0, per_day) for d in range(days)])
    return {"timestamps": ts, "close": np.ones(bars)}


class TestWindowDays(unittest.TestCase):
    def test_crypto_bars_7_lt_30(self):
        npz = _crypto_npz()
        _, w7 = exact_month_slice(npz, True, window_days=7)
        _, w30 = exact_month_slice(npz, True, window_days=30)
        self.assertEqual(w7["policy"], "7_calendar_days")
        self.assertEqual(w30["policy"], "30_calendar_days")
        self.assertLess(w7["bars"], w30["bars"])

    def test_stocks_bars_7_lt_30(self):
        npz = _stocks_npz()
        _, w7 = exact_month_slice(npz, False, window_days=7)
        _, w30 = exact_month_slice(npz, False, window_days=30)
        self.assertEqual(w7["policy"], "7_trading_sessions")
        self.assertEqual(w30["policy"], "30_trading_sessions")
        self.assertLess(w7["bars"], w30["bars"])

    def test_slicers_agree(self):
        for crypto, npz in ((True, _crypto_npz()), (False, _stocks_npz())):
            for wd in (1, 7, 30):
                s, ws = exact_month_slice(npz, crypto, window_days=wd)
                t, pt = _exact_30d_slice(npz, crypto, window_days=wd)
                self.assertEqual(len(np.asarray(s["timestamps"])), len(np.asarray(t["timestamps"])), f"bars differ crypto={crypto} wd={wd}")
                self.assertEqual(ws["policy"], pt, f"policy differs crypto={crypto} wd={wd}")

    def test_unsupported_raises(self):
        for crypto, npz in ((True, _crypto_npz()), (False, _stocks_npz())):
            for wd in (8, 14, 60):
                with self.assertRaises(ValueError, msg=f"month crypto={crypto} wd={wd}"):
                    exact_month_slice(npz, crypto, window_days=wd)
                with self.assertRaises(ValueError, msg=f"exact30d crypto={crypto} wd={wd}"):
                    _exact_30d_slice(npz, crypto, window_days=wd)


if __name__ == "__main__":
    unittest.main()
