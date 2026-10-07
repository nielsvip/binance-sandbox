"""USER 2026-10-06 mandates: unconditional SHORT dc_high+0.25% close, loss exits bypass
profit-confirms, single-source config (sqlite overrides > TEMPLATE), non-tradeable
wt1_15m-against exit. Twin predicates run live; structural pins guard the call sites."""
import unittest
from pathlib import Path

import tradier_filter_tf_twins as twins

ROOT = Path(__file__).resolve().parent.parent


def _fn_body(path, name):
    lines = (ROOT / path).read_text().split("\n")
    start = next(i for i, l in enumerate(lines) if l.startswith(f"def {name}("))
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
        for marker in ("DC_HIGH_4H_UNCOND", "DC_LOW_4H_UNCOND", "DC_HARD_STOP_1H_ENABLED", "DC_HARD_STOP_15M_ENABLED", "UNCOND_DC_CLOSED"):
            self.assertIn(marker, self.src)
            self.assertLess(self.src.index(marker), ui)

    def test_all_confirm_sites_pass_gain(self):
        n = self.src.count("position_side)), safe_fetch_float(getattr(position, 'gain', 0), 0.0))")
        self.assertEqual(n, 3)

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

    def test_quickconfig_switches_default_true(self):
        self.assertIn("DC_HARD_STOP_1H_ENABLED: bool = True", self.src)
        self.assertIn("DC_HARD_STOP_15M_ENABLED: bool = True", self.src)

    def test_twin_block(self):
        for marker in ("UNCOND_DC_HIGH_4H", "UNCOND_DC_HIGH_1H", "UNCOND_DC_HIGH_15M", "UNCOND_DC_LOW_4H", "UNCOND_DC_LOW_1H", "UNCOND_DC_LOW_15M"):
            self.assertIn(marker, self.src)
        self.assertGreaterEqual(self.src.count("1.0025"), 3)
        self.assertGreaterEqual(self.src.count("0.9975"), 3)

    def test_twin_precedes_ultimate(self):
        ui = self.src.index("2026-09-19 USER MANDATE — ABSOLUTE ULTIMATE STOP")
        self.assertLess(self.src.index("UNCOND_DC_HIGH_4H"), ui)

    def test_xc_loss_bypass(self):
        self.assertIn("_xc_loss = pos is not None", self.src)
        self.assertIn("_xc_ok = _xc_loss or (", self.src)


class TestUncondTwinBehavioral(unittest.TestCase):
    """Executes the REAL twin block text (sliced, staged namespace) — reason, pnl, controls."""

    @classmethod
    def setUpClass(cls):
        import textwrap

        lines = (ROOT / "v12_quick_engine.py").read_text().split("\n")
        t0 = next(i for i, l in enumerate(lines) if "USER MANDATE twin — UNCONDITIONAL DC STOP BOTH SIDES" in l)
        t1 = next(i for i in range(t0, len(lines)) if lines[i].strip() == "except Exception:" and lines[i + 1].strip() == "pass")
        cls.twin = "for _twin_probe in [0]:\n" + textwrap.indent(textwrap.dedent("\n".join(lines[t0:t1 + 2])), "    ")

    def _fire(self, px, is_long, highs=(100.0, 200.0, 200.0), lows=(50.0, 50.0, 50.0), h1=True, h15=True):
        import numpy as np

        n = 10
        arr = lambda v: np.full(n, v, dtype=float)

        class Cfg:
            DC_PRIOR_BAR_CHANNEL = True
            DC_HARD_STOP_1H_ENABLED = h1
            DC_HARD_STOP_15M_ENABLED = h15
            DC_HARD_STOP_REENTRY_COOLDOWN_HOURS = 4.0

        ns = {"i": 5, "px": px, "is_long": is_long, "pos": {"qty": 2.0, "avg_price": 100.0, "entry_price": 100.0,
              "entry_qty": 2.0, "deployed": 200.0, "realized": 0.0, "entry_bar": 0, "peak_pnl_pct": 0.0,
              "fees": 0.0, "entry_reason": "SEED"},
              "cfg": Cfg(), "ts": np.arange(n, dtype=float) * 900.0, "half_fee": 0.0005,
              "trades": [], "cd": 0, "cooldown_bars": 3, "has_closed_before": False,
              "bmin": 15, "is_tradier": True,
              "_uh_h4": arr(highs[0]), "_uh_h1": arr(highs[1]), "_uh_h15": arr(highs[2]),
              "_uh_l4": arr(lows[0]), "_uh_l1": arr(lows[1]), "_uh_l15": arr(lows[2])}
        exec(self.twin, {}, ns)
        return ns

    def test_short_4h_fires_with_loss(self):
        r = self._fire(100.3, False)
        self.assertEqual(len(r["trades"]), 1)
        self.assertEqual(r["trades"][0]["exit_reason"], "UNCOND_DC_HIGH_4H")
        self.assertLess(r["trades"][0]["pnl_pct"], 0)
        self.assertIsNone(r["pos"])

    def test_long_4h_fires_with_loss(self):
        r = self._fire(99.7, True, lows=(100.0, 50.0, 50.0))
        self.assertEqual(len(r["trades"]), 1)
        self.assertEqual(r["trades"][0]["exit_reason"], "UNCOND_DC_LOW_4H")
        self.assertLess(r["trades"][0]["pnl_pct"], 0)
        self.assertIsNone(r["pos"])

    def test_below_tolerance_holds(self):
        r = self._fire(100.1, False)
        self.assertEqual(r["trades"], [])
        r = self._fire(99.9, True, lows=(100.0, 50.0, 50.0))
        self.assertEqual(r["trades"], [])


if __name__ == "__main__":
    unittest.main()
