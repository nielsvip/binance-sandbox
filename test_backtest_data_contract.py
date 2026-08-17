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
        "synthetic_5m_parent_close_ts": ts.copy(),
        "timestamp_15m": ts - 300,
    }
    for tf, lag in (("1h", 12), ("4h", 48), ("D", 78)):
        arrays[f"timestamp_{tf}"] = ts - lag * 300
    for tf in ("15m", "1h", "4h", "D"):
        arrays[f"open_{tf}"] = x - 0.05
        arrays[f"high_{tf}"] = x + 0.10
        arrays[f"low_{tf}"] = x - 0.10
        arrays[f"close_{tf}"] = x
        arrays[f"volume_{tf}"] = np.linspace(100.0, 200.0, n, dtype=np.float32)
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

    def test_valid_formation_dataset_passes_without_ladder_fields(self):
        arrays = valid_arrays()
        for tf in ("1h", "4h", "D"):
            arrays.pop(f"lrL_pct_b_{tf}")
            arrays.pop(f"lrL_slope_{tf}")
        result = audit_npz("MU", self.write(arrays), "formations")
        self.assertTrue(result.valid, result.errors)

    def test_formation_dataset_requires_every_timeframe_ohlcv(self):
        arrays = valid_arrays()
        arrays.pop("high_4h")
        result = audit_npz("MU", self.write(arrays), "formations")
        self.assertFalse(result.valid)
        self.assertTrue(any("missing required field high_4h" in e for e in result.errors))

    def test_formation_dataset_accepts_a_large_leading_zero_warmup(self):
        arrays = valid_arrays()
        for stem in ("open", "high", "low", "close", "volume"):
            arrays[f"{stem}_D"] = arrays[f"{stem}_D"].copy()
            arrays[f"{stem}_D"][:400] = 0.0
        result = audit_npz("CDW", self.write(arrays), "formations")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.stats["close_D_warmup_rows"], 400)
        self.assertEqual(result.stats["close_D_usable_tail_rows"], 200)

    def test_formation_dataset_rejects_an_internal_zero_ohlc_gap(self):
        arrays = valid_arrays()
        arrays["close_D"][:100] = 0.0
        arrays["close_D"][300:320] = 0.0
        result = audit_npz("CDW", self.write(arrays), "formations")
        self.assertFalse(result.valid)
        self.assertTrue(any("close_D unusable tail" in e for e in result.errors))

    def test_formation_timestamp_accepts_historical_weekend_gap_when_terminal_is_fresh(self):
        arrays = valid_arrays()
        arrays["timestamp_15m"][200] = arrays["timestamps"][200] - 4 * 86400
        result = audit_npz("COE", self.write(arrays), "formations")
        self.assertTrue(result.valid, result.errors)
        self.assertTrue(any("historical market-closure lag" in w for w in result.warnings))

    def test_formation_timestamp_rejects_stale_terminal_parent(self):
        arrays = valid_arrays()
        arrays["timestamp_15m"][-1] = arrays["timestamps"][-1] - 4 * 86400
        result = audit_npz("COE", self.write(arrays), "formations")
        self.assertFalse(result.valid)
        self.assertTrue(any("stale terminal parent" in e for e in result.errors))

    def test_formation_rejects_an_isolated_roundtrip_jump(self):
        arrays = valid_arrays()
        arrays["close"] = arrays["close"].copy()
        arrays["close"][300] *= 2.2
        result = audit_npz("HAO", self.write(arrays), "formations")
        self.assertFalse(result.valid)
        self.assertEqual(result.stats["max_bar_jump_classification"], "TRANSIENT_ROUNDTRIP")
        self.assertTrue(any("corrupt price discontinuity" in e for e in result.errors))

    def test_formation_rejects_a_persistent_split_scale_jump(self):
        arrays = valid_arrays()
        arrays["close"] = arrays["close"].copy()
        arrays["close"][300:] *= 4.0
        result = audit_npz("CRWD", self.write(arrays), "formations")
        self.assertFalse(result.valid)
        self.assertEqual(result.stats["max_bar_jump_classification"], "PERSISTENT_OR_UNVERIFIED")

    def test_synthetic_execution_window_is_disclosed_but_accepted(self):
        arrays = valid_arrays()
        arrays["synthetic_5m"][:] = 1
        arrays["synthetic_5m_parent_close_ts"] = arrays["timestamps"] + 600
        result = audit_npz("MU", self.write(arrays), "floor")
        self.assertTrue(result.valid)
        self.assertTrue(any("interpolated 5m" in e for e in result.warnings))
        self.assertEqual(result.stats["synthetic_5m_parent_lag_max_s"], 600)

    def test_synthetic_parent_must_be_the_containing_15m_bar(self):
        arrays = valid_arrays()
        arrays["synthetic_5m"][:] = 1
        arrays["synthetic_5m_parent_close_ts"] = arrays["timestamps"] + 900
        result = audit_npz("MU", self.write(arrays), "floor")
        self.assertFalse(result.valid)
        self.assertTrue(any("outside its containing" in e for e in result.errors))

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

    def test_pending_reclaim_is_valid_when_no_unopposed_overshoot_exists(self):
        result = audit_ladder_result({
            "trades": 4, "opens_long": 3, "opens_short": 0,
            "sized_open_events": 3, "max_requested_mult": 8,
            "requested_fill_ratio": 1.0, "size_clamp_count": 0,
            "reentry_pending": 0, "reclaim_pending": 1,
        }, "LONG")
        self.assertTrue(result["valid"])
        self.assertEqual(result["reclaim_pending"], 1)

    def test_unopposed_reentry_overshoot_is_invalid(self):
        result = audit_ladder_result({
            "trades": 4, "opens_long": 3, "opens_short": 0,
            "sized_open_events": 3, "max_requested_mult": 8,
            "requested_fill_ratio": 1.0, "size_clamp_count": 0,
            "reentry_pending": 1, "reclaim_pending": 1,
            "reentry_violations": 1,
        }, "LONG")
        self.assertFalse(result["valid"])

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

    def test_large_leading_warmup_is_accepted_when_finite_tail_is_long_enough(self):
        arrays = valid_arrays()
        arrays["stoch_k_D"][:400] = np.nan
        result = audit_npz("MSTR", self.write(arrays), "core")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.stats["stoch_k_D_warmup_rows"], 400)

    def test_zero_unavailable_sentinel_before_nan_warmup_is_accepted(self):
        arrays = valid_arrays()
        arrays["stoch_k_D"][:20] = 0.0
        arrays["stoch_k_D"][20:400] = np.nan
        result = audit_npz("MSTR", self.write(arrays), "core")
        self.assertTrue(result.valid, result.errors)
        self.assertEqual(result.stats["stoch_k_D_warmup_rows"], 400)

    def test_nonzero_observations_before_nan_gap_are_still_quarantined(self):
        arrays = valid_arrays()
        arrays["stoch_k_D"][20:400] = np.nan
        result = audit_npz("MSTR", self.write(arrays), "core")
        self.assertFalse(result.valid)
        self.assertTrue(any("stoch_k_D unusable" in e for e in result.errors))


if __name__ == "__main__":
    unittest.main()
