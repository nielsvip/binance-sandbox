"""USER 2026-10-06 mandates: unconditional SHORT dc_high+0.25% close, loss exits bypass
profit-confirms, single-source config (sqlite overrides > TEMPLATE), non-tradeable
wt1_15m-against exit. Twin predicates run live; structural pins guard the call sites."""
import unittest
from pathlib import Path

import tradier_filter_tf_twins as twins

ROOT = Path(__file__).resolve().parent.parent


def _fn_body(path, name):
    lines = (ROOT / path).read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith((f"def {name}(", f"async def {name}(")))
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("def ") or lines[i].startswith("class ")), len(lines))
    return "\n".join(lines[start:end])


class TestUncondPredicates(unittest.TestCase):
    def test_short_stop_fires_above_tol(self):
        self.assertTrue(twins.short_dc_high_stop_fires(106.39, 103.51))
        self.assertTrue(twins.short_dc_high_stop_fires(100.25, 100.0))

    def test_short_stop_holds_at_or_below(self):
        self.assertFalse(twins.short_dc_high_stop_fires(100.24, 100.0))
        self.assertFalse(twins.short_dc_high_stop_fires(99.0, 100.0))

    def test_short_stop_invalid_never_fires(self):
        for px, lvl in [(0, 100.0), (100.0, 0), (None, 100.0), (100.0, None), ("x", 100.0)]:
            self.assertFalse(twins.short_dc_high_stop_fires(px, lvl))

    def test_long_stop_fires_below_tol(self):
        self.assertTrue(twins.long_dc_low_stop_fires(99.7, 100.0))
        self.assertFalse(twins.long_dc_low_stop_fires(99.76, 100.0))
        self.assertFalse(twins.long_dc_low_stop_fires(101.0, 100.0))
        self.assertFalse(twins.long_dc_low_stop_fires(0, 100.0))
        self.assertFalse(twins.long_dc_low_stop_fires(99.0, 0))

    def test_wt15m_against(self):
        self.assertTrue(twins.wt15m_against(False, 25.0))
        self.assertTrue(twins.wt15m_against(True, -25.0))
        self.assertFalse(twins.wt15m_against(False, -25.0))
        self.assertFalse(twins.wt15m_against(True, 25.0))
        self.assertFalse(twins.wt15m_against(False, 0))
        self.assertFalse(twins.wt15m_against(True, None))


class TestConfirmLossBypass(unittest.TestCase):
    def _unconfirmed(self):
        return lambda k: {"wt1_15m": 80.0, "_lc_wt1_15m_prev": 70.0}.get(k)

    def test_loss_bypasses_confirm(self):
        self.assertIsNone(twins.exit_confirm_block(self._unconfirmed(), False, "WT_CROSSOVER_FINAL_x", "15m", None, -6.6))
        self.assertIsNone(twins.exit_confirm_block(self._unconfirmed(), True, "TECHNICAL_DC_STOP_x", "15m", None, -0.01))

    def test_profit_still_vetoed(self):
        v = twins.exit_confirm_block(self._unconfirmed(), False, "WT_CROSSOVER_FINAL_x", "15m", None, 1.2)
        self.assertEqual(v, "EXIT_TOP_FADE_FILTER_TF_15m_NO_CONFIRM")

    def test_legacy_none_gain_unchanged(self):
        v = twins.exit_confirm_block(self._unconfirmed(), False, "WT_CROSSOVER_FINAL_x", "15m", None)
        self.assertEqual(v, "EXIT_TOP_FADE_FILTER_TF_15m_NO_CONFIRM")

    def test_non_exit_reason_none(self):
        self.assertIsNone(twins.exit_confirm_block(self._unconfirmed(), False, "SOME_ENTRY", "15m", None, 5.0))


class TestTradierSingleSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cfg = _fn_body("tradier_manage.py", "_cfg")
        cls.src = (ROOT / "tradier_manage.py").read_text()

    def test_cfg_no_conflicting_layers(self):
        for banned in ("_load_tradier_per_sym_cfgs", "_load_global_per_sym_cfgs", "_load_full_recipe_live_cfgs", "_tradier_final_book_get", "get_symbol_setting", "get_full_config", "R1_DC_LOW4_3M_EMERGENCY_ENABLED", "PARTIAL_PROFIT_LOCK_ENABLED"):
            self.assertNotIn(banned, self.cfg)

    def test_cfg_single_source_markers(self):
        self.assertIn("get_overrides", self.cfg)
        self.assertIn("cat_side_defaults", self.cfg)
        self.assertIn("V8_DISABLE_PER_SYM", self.cfg)
        self.assertIn("_v8_sweep_override", self.cfg)

    def test_uncond_block_before_ultimate(self):
        ui = self.src.index("2026-09-19 USER MANDATE — ABSOLUTE ULTIMATE STOP")
        for marker in ("DC_HIGH_4H_UNCOND", "DC_LOW_4H_UNCOND", "UNCOND_DC_CLOSED"):
            self.assertIn(marker, self.src)
            self.assertLess(self.src.index(marker), ui)
        body = _fn_body("tradier_manage.py", "process_position")
        stop = body[body.index("UNCONDITIONAL DC STOP BOTH SIDES"):body.index("NON-TRADEABLE", body.index("UNCONDITIONAL DC STOP BOTH SIDES"))]
        self.assertNotIn("DC_HARD_STOP_1H_ENABLED", stop)
        self.assertNotIn("DC_HARD_STOP_15M_ENABLED", stop)
        self.assertIn("_uh_tol = _udcs.DEFAULT_BUFFER_PCT", stop)

    def test_all_confirm_sites_pass_gain(self):
        sites = self.src.split("exit_confirm_block(")[1:]
        self.assertEqual(len(sites), 3)
        for site in sites:
            self.assertIn("safe_fetch_float(getattr(position, \"gain\", 0), 0.0)", site[:1400])

    def test_non_tradeable_block(self):
        for marker in ("NON_TRADEABLE_WT15M_EXIT", "is_symbol_tradeable(symbol, account_key, position_side)", "NON_TRADEABLE_WT15M_CLOSED"):
            self.assertIn(marker, self.src)


class TestEzSingleSource(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "ez_manage.py").read_text()

    def test_psym_no_conflicting_layers(self):
        body = _fn_body("ez_manage.py", "_psym_get")
        for banned in ("_ezm_per_sym_cfgs", "_ezm_apply_final_book", "tradeable_keys.json", "openpyxl", "get_full_config"):
            self.assertNotIn(banned, body)
        self.assertIn("get_overrides", body)
        self.assertIn("_ezm_cat_side_default", body)

    def test_gate_template_baseline(self):
        body = _fn_body("ez_manage.py", "_ezm_is_live_side_enabled")
        for banned in ("tradeable_keys", "SPREADSHEETS/BEST", "_ezm_per_sym_raw", "_ezm_per_sym_stocks_raw", "beat bh"):
            self.assertNotIn(banned, body)
        self.assertIn("TEMPLATE baseline trades", body)

    def test_ez_non_tradeable_block(self):
        for marker in ("NON_TRADEABLE_WT15M_EXIT", "_ezm_is_live_side_enabled(symbol, position_side, account_key)", "NON_TRADEABLE_WT15M_CLOSED"):
            self.assertIn(marker, self.src)

    def test_sps_raw_single_source(self):
        body = _fn_body("ez_manage.py", "_psym_sps_raw")
        self.assertNotIn("_ezm_per_sym_cfgs", body)
        self.assertIn("get_overrides", body)


class TestV12UncondTwin(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.src = (ROOT / "v12_quick_engine.py").read_text()

    def test_quickconfig_rolling_stop_switches_default_false(self):
        self.assertIn("DC_HARD_STOP_1H_ENABLED: bool = False", self.src)
        self.assertIn("DC_HARD_STOP_15M_ENABLED: bool = False", self.src)

    def test_twin_block(self):
        self.assertIn("ULTIMATE_DC_{_hs_tf_v12}_HARD_STOP", self.src)
        self.assertNotIn("UNCOND_DC_LOW_1H", self.src)
        self.assertNotIn("UNCOND_DC_LOW_15M", self.src)
        self.assertNotIn("UNCOND_DC_HIGH_1H", self.src)
        self.assertNotIn("UNCOND_DC_HIGH_15M", self.src)
        self.assertIn("0.9975 if is_long else 1.0025", self.src)
        self.assertIn("_ult_wick <= _ult_stop", self.src)
        self.assertIn("_ult_wick >= _ult_stop", self.src)

    def test_twin_precedes_ultimate(self):
        ui = self.src.index("USER 2026-10-25: ABSOLUTE ULTIMATE STOP")
        self.assertLess(self.src.index("_ult_breach = _ult_lvl > 0"), self.src.index("# WT LOWER CROSS EXIT"))

    def test_xc_loss_bypass(self):
        self.assertIn("pos is not None\n            and _xc_ap != 0", self.src[self.src.index("_xc_loss = ("):self.src.index("_xc_ok =")])
        self.assertIn("_xc_ok = _xc_loss or (", self.src)


if __name__ == "__main__":
    unittest.main()
