import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.backtest_data_contract import audit_ladder_result, audit_npz


def valid_arrays(n=600):
    ts = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    x = np.linspace(10.0, 20.0, n, dtype=np.float32)
    arrays = {
        "timestamps": ts,
        "close": x,
        "synthetic_5m": np.zeros(n, dtype=np.int8),
    }
    for tf, lag in (("1h", 12), ("4h", 48), ("D", 78)):
        arrays[f"timestamp_{tf}"] = ts - lag * 300
    for tf in ("15m", "1h", "4h", "D"):
        for stem in ("wt1", "stoch_k", "dc_position"):
            arrays[f"{stem}_{tf}"] = np.sin(np.arange(n) / 17.0).astype(np.float32)
    for tf in ("1h", "4h", "D"):
        for stem in ("lrL_pct_b", "lrL_slope"):
            arrays[f"{stem}_{tf}"] = np.cos(np.arange(n) / 19.0).astype(np.float32)
    return arrays


class DataContractTests(unittest.TestCase):
    def write(self, arrays):
        tmp = tempfile.NamedTemporaryFile(suffix=".npz", delete=False)
        tmp.close()
        np.savez_compressed(tmp.name, **arrays)
        self.addCleanup(Path(tmp.name).unlink)
        return tmp.name

    def test_valid_ladder_dataset_passes(self):
        result = audit_npz("MU", self.write(valid_arrays()), "ladder")
        self.assertTrue(result.valid, result.errors)

    def test_synthetic_execution_window_is_quarantined(self):
        arrays = valid_arrays()
        arrays["synthetic_5m"][:] = 1
        result = audit_npz("MU", self.write(arrays), "floor")
        self.assertFalse(result.valid)
        self.assertTrue(any("synthetic 5m" in e for e in result.errors))

    def test_empty_vt_style_htf_fields_are_quarantined(self):
        arrays = valid_arrays()
        arrays["wt1_1h"][:] = 0
        result = audit_npz("VT", self.write(arrays), "core")
        self.assertFalse(result.valid)
        self.assertTrue(any("wt1_1h unusable" in e for e in result.errors))

    def test_old_timestamp_alias_is_quarantined(self):
        arrays = valid_arrays()
        arrays["timestamp_1h"] = arrays["timestamps"].copy()
        result = audit_npz("MU", self.write(arrays), "core")
        self.assertFalse(result.valid)
        self.assertTrue(any("predates closed-bar fix" in e for e in result.errors))

    def test_side_contaminated_result_is_invalid(self):
        result = audit_ladder_result({
            "trades": 4, "opens_long": 3, "opens_short": 1,
            "sized_open_events": 3, "max_requested_mult": 10,
            "requested_fill_ratio": 1.0, "size_clamp_count": 0,
        }, "LONG")
        self.assertFalse(result["valid"])

    def test_silently_clamped_result_is_invalid(self):
        result = audit_ladder_result({
            "trades": 4, "opens_long": 3, "opens_short": 0,
            "sized_open_events": 3, "max_requested_mult": 10,
            "requested_fill_ratio": 0.125, "size_clamp_count": 3,
        }, "LONG")
        self.assertFalse(result["valid"])

    def test_clean_side_isolated_sizing_result_is_valid(self):
        result = audit_ladder_result({
            "trades": 4, "opens_long": 3, "opens_short": 0,
            "sized_open_events": 3, "max_requested_mult": 10,
            "requested_fill_ratio": 0.99, "size_clamp_count": 0,
        }, "LONG")
        self.assertTrue(result["valid"])


if __name__ == "__main__":
    unittest.main()
