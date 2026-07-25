import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.backtest_data_contract import audit_ladder_result, audit_npz, audit_stage0_result


def valid_arrays(n=600):
    ts = np.arange(n, dtype=np.int64) * 300 + 1_700_000_000
    x = np.linspace(10.0, 20.0, n, dtype=np.float32)
    arrays = {
        "timestamps": ts,
        "close": x,
        "synthetic_5m": np.zeros(n, dtype=np.int8),
        "timestamp_15m": ts - 300,
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

    def test_synthetic_execution_window_is_disclosed_but_accepted(self):
        arrays = valid_arrays()
        arrays["synthetic_5m"][:] = 1
        result = audit_npz("MU", self.write(arrays), "floor")
        self.assertTrue(result.valid)
        self.assertTrue(any("interpolated 5m" in e for e in result.warnings))

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

    def test_clean_one_unit_stage0_is_valid(self):
        result = audit_stage0_result({
            "trades": 1, "opens_long": 1, "opens_short": 0,
            "real_closes": 0, "mtm_count": 1,
            "time_in_mkt_long_pct": 99.9,
            "sized_open_events": 1, "max_requested_mult": 1,
            "max_filled_start_mult": 1, "requested_fill_ratio": 1.0,
            "size_clamp_count": 0, "open_notional_sum": 2000,
            "max_open_notional": 2000, "benchmark_deployed_usd": 2000,
            "reentry_pending": 0, "reentry_violations": 0,
        }, "LONG")
        self.assertTrue(result["valid"], result)

    def test_stage0_rejects_non_seed_entry_attempt_without_a_clamp(self):
        result = audit_stage0_result({
            "trades": 1, "opens_long": 1, "opens_short": 0,
            "real_closes": 0, "mtm_count": 1,
            "time_in_mkt_long_pct": 99.9,
            "sized_open_events": 2, "max_requested_mult": 3,
            "max_filled_start_mult": 1, "requested_fill_ratio": 1.0,
            "size_clamp_count": 0, "open_notional_sum": 3000,
            "max_open_notional": 2000, "benchmark_deployed_usd": 2000,
            "reentry_pending": 0, "reentry_violations": 0,
        }, "LONG")
        self.assertFalse(result["valid"])
        self.assertEqual(result["non_seed_sized_open_events"], 1)

    def test_stage0_rejects_clamped_add_after_seed(self):
        result = audit_stage0_result({
            "trades": 1, "opens_long": 1, "opens_short": 0,
            "real_closes": 0, "mtm_count": 1,
            "time_in_mkt_long_pct": 99.9,
            "sized_open_events": 2, "max_requested_mult": 3,
            "max_filled_start_mult": 1, "requested_fill_ratio": 0.3643,
            "size_clamp_count": 1, "open_notional_sum": 2914.58,
            "max_open_notional": 2000, "benchmark_deployed_usd": 2000,
            "reentry_pending": 0, "reentry_violations": 0,
        }, "LONG")
        self.assertFalse(result["valid"])

    def test_contiguous_indicator_warmup_is_accepted(self):
        arrays = valid_arrays()
        arrays["stoch_k_D"][:40] = np.nan
        result = audit_npz("VT", self.write(arrays), "core")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.stats["stoch_k_D_warmup_rows"], 40)

    def test_internal_indicator_nan_gap_is_quarantined(self):
        arrays = valid_arrays()
        arrays["stoch_k_D"][250:290] = np.nan
        result = audit_npz("VT", self.write(arrays), "core")
        self.assertFalse(result.valid)
        self.assertTrue(any("stoch_k_D unusable" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
