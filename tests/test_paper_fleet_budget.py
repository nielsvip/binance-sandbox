import unittest
import math

from tools.paper_fleet_budget import decide_admission, render_paper_service, stable_host


def healthy_observed():
    return {
        "mem_available_bytes": 16 * 1024**3,
        "disk_free_bytes": 20 * 1024**3,
        "load1": 2.0,
        "ncpu": 16,
        "cpu_psi_avg10": 1.0,
    }


class PaperFleetBudgetTests(unittest.TestCase):
    def test_stable_split_and_arm_pairing(self):
        for venue, symbol, side, allowed in (
            ("crypto", "BTCUSDT", "LONG", {"s1", "s2"}),
            ("stocks", "AAPL", "SHORT", {"s5", "s6"}),
        ):
            host = stable_host(venue, symbol, side)
            self.assertEqual(host, stable_host(venue, symbol, side))
            self.assertEqual(host, stable_host(venue, symbol, side))
            self.assertIn(host, allowed)
            # Baseline and switch arms use the same venue/symbol/side key.
            baseline_host = stable_host(venue, symbol, side, arm="baseline")
            switch_host = stable_host(venue, symbol, side, arm="switch")
            self.assertEqual(baseline_host, switch_host)

    def test_wrong_venue_host_is_rejected(self):
        result = decide_admission("crypto", "s5", healthy_observed())
        self.assertFalse(result["admit"])
        self.assertIn("host_not_allowed_for_venue", result["reasons"])

    def test_each_resource_threshold_blocks_admission(self):
        cases = (
            ("mem_available_bytes", 8 * 1024**3 - 1, "mem_available_below_reserve"),
            ("disk_free_bytes", 8 * 1024**3 - 1, "disk_free_below_reserve"),
            ("load1", 12.81, "load1_above_limit"),
            ("cpu_psi_avg10", 10.01, "cpu_psi_avg10_above_limit"),
        )
        for field, value, expected in cases:
            with self.subTest(field=field):
                observed = healthy_observed()
                observed[field] = value
                result = decide_admission("stocks", "s6", observed)
                self.assertFalse(result["admit"])
                self.assertIn(expected, result["reasons"])

    def test_threshold_boundary_is_admitted(self):
        observed = {
            "mem_available_bytes": 8 * 1024**3,
            "disk_free_bytes": 8 * 1024**3,
            "load1": 12.8,
            "ncpu": 16,
            "cpu_psi_avg10": 10.0,
        }
        self.assertTrue(decide_admission("crypto", "s1", observed)["admit"])

    def test_missing_reading_fails_closed(self):
        observed = healthy_observed()
        observed["cpu_psi_avg10"] = None
        result = decide_admission("crypto", "s2", observed)
        self.assertFalse(result["admit"])
        self.assertIn("missing_cpu_psi_avg10", result["reasons"])

    def test_nonfinite_negative_and_boolean_readings_fail_closed(self):
        cases = (
            ("load1", math.nan),
            ("cpu_psi_avg10", math.inf),
            ("mem_available_bytes", -1),
            ("disk_free_bytes", True),
        )
        for field, value in cases:
            with self.subTest(field=field):
                observed = healthy_observed()
                observed[field] = value
                result = decide_admission("crypto", "s1", observed)
                self.assertFalse(result["admit"])
                self.assertIn(f"invalid_{field}", result["reasons"])

    def test_nonpositive_cpu_count_fails_closed(self):
        observed = healthy_observed()
        observed["ncpu"] = 0
        result = decide_admission("crypto", "s1", observed)
        self.assertFalse(result["admit"])
        self.assertIn("invalid_ncpu", result["reasons"])

    def test_partial_thresholds_merge_defaults(self):
        observed = healthy_observed()
        observed["mem_available_bytes"] = 7 * 1024**3
        result = decide_admission("crypto", "s1", observed, {"cpu_psi_avg10": 20.0})
        self.assertFalse(result["admit"])
        self.assertIn("mem_available_below_reserve", result["reasons"])

    def test_invalid_thresholds_fail_closed(self):
        for value in (-1, math.nan, math.inf, True):
            with self.subTest(value=value):
                result = decide_admission("crypto", "s1", healthy_observed(), {"mem_available_bytes": value})
                self.assertFalse(result["admit"])
                self.assertIn("invalid_threshold_mem_available_bytes", result["reasons"])

    def test_service_rendering_has_only_requested_limits(self):
        unit = render_paper_service("/usr/bin/python3 /opt/paper_capture.py")
        for setting in (
            "Nice=19",
            "CPUWeight=1",
            "CPUQuota=25%",
            "MemoryHigh=256M",
            "MemoryMax=512M",
            "IOWeight=1",
        ):
            self.assertIn(setting, unit)
        self.assertNotIn("sudo", unit)
        self.assertNotIn("systemctl", unit)
        self.assertIn("not a budget for a live adapter", unit)


if __name__ == "__main__":
    unittest.main()
