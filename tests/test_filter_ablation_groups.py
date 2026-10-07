"""Filter-ablation groups (2026-10-07 lane): registration pins + behavior-preservation proofs.

Covers: 6 ABLATION_DISABLE_FILTER_* flags in Config/TradierConfig/QuickConfig
(all default False), AUTO_WIRED_PARAMS + _DEFAULTS_625 registration, the
simulate_one hook, apply() no-op when all False, per-flag forcing incl.
TF->OFF, GS ABLATION_FLAGS wiring + FILTER_SERIES default OFF, and mapping
sanity (real fields, disjoint groups, no permits/bypasses inside).
Ledger-delta proofs (each flag True moves a real ledger) are manual S1/Mac
single-eval runs quoted in the lane report — they need NPZs, not unit runs.
"""

import dataclasses as dc
import unittest
from pathlib import Path

import config
import config_tradier
import v12_quick_engine as V
from vec_decisions import filter_ablation_groups as fab

FLAGS = (
    "ABLATION_DISABLE_FILTER_ENTRY",
    "ABLATION_DISABLE_FILTER_MTF_HTF",
    "ABLATION_DISABLE_FILTER_REENTRY",
    "ABLATION_DISABLE_FILTER_EXIT",
    "ABLATION_DISABLE_FILTER_AUGMENT",
    "ABLATION_DISABLE_FILTER_REDUCE",
)
ROOT = Path(__file__).resolve().parent.parent


class TestFilterAblationRegistration(unittest.TestCase):
    def test_live_configs_carry_flags_default_false(self):
        c, t = config.Config(), config_tradier.TradierConfig()
        for f in FLAGS:
            self.assertIs(getattr(c, f), False, f)
            self.assertIs(getattr(t, f), False, f)

    def test_quickconfig_defaults_false(self):
        q = V.QuickConfig()
        for f in FLAGS:
            self.assertIs(getattr(q, f), False, f)

    def test_existing_defaults_untouched(self):
        self.assertIs(config.Config().ABLATION_DISABLE_REENTRY, False)
        self.assertIs(V.QuickConfig().ABLATION_DISABLE_REENTRY, False)
        for (
            f
        ) in FLAGS:  # my 6 additions are unique (list has pre-existing dups — not mine)
            self.assertEqual(V.AUTO_WIRED_PARAMS.count(f), 1, f)

    def test_auto_wired_and_625(self):
        for f in FLAGS:
            self.assertIn(f, V.AUTO_WIRED_PARAMS)
            self.assertIs(V._DEFAULTS_625[f], False, f)

    def test_tradier_overlay_keeps_false(self):
        q = V.QuickConfig()
        q.apply_tradier_defaults()
        for f in FLAGS:
            self.assertIs(getattr(q, f), False, f)

    def test_simulate_one_hook_present(self):
        src = (ROOT / "v12_quick_engine.py").read_text()
        self.assertIn("filter_ablation_groups", src)
        self.assertIn("_fabl.apply(cfg)", src)


class TestFilterAblationApply(unittest.TestCase):
    def test_all_false_is_noop(self):
        q = V.QuickConfig()
        before = {f.name: getattr(q, f.name) for f in dc.fields(q)}
        self.assertEqual(fab.apply(q), [])
        after = {f.name: getattr(q, f.name) for f in dc.fields(q)}
        self.assertEqual(before, after)

    def test_each_flag_forces_members(self):
        for f in FLAGS:
            q = V.QuickConfig()
            setattr(q, f, True)
            forced = fab.apply(q)
            self.assertGreater(len(forced), 0, f)
            for name in forced:
                v = getattr(q, name)
                self.assertIn(v, (False, "OFF"), (f, name))

    def test_spot_members(self):
        q = V.QuickConfig()
        q.ABLATION_DISABLE_FILTER_ENTRY = True
        fab.apply(q)
        self.assertIs(q.FUNDING_GATE_ENABLED, False)
        self.assertIs(q.OI_CONFIRM_ENABLED, False)
        self.assertEqual(q.FH_MOMENTUM_FILTER_TF, "OFF")
        q2 = V.QuickConfig()
        q2.ABLATION_DISABLE_FILTER_MTF_HTF = True
        fab.apply(q2)
        self.assertIs(q2.MTF_GR_FILTER_ENABLED, False)
        self.assertIs(q2.DG_HTF_ALIGN_REQUIRE_4H, False)
        q3 = V.QuickConfig()
        q3.ABLATION_DISABLE_FILTER_EXIT = True
        fab.apply(q3)
        self.assertIs(q3.STRUCTURAL_EXIT_GATE_ENABLED, False)
        self.assertEqual(q3.BREAKEVEN_GAIN_EROSION_FILTER_TF, "OFF")

    def test_excluded_permits_bypasses_untouched(self):
        q = V.QuickConfig()
        for f in FLAGS:
            setattr(q, f, True)
        fab.apply(q)
        for name in (
            "DELTA_GATE_OPEN",
            "DELTA_GATE_REENTRY",
            "DELTA_GATE_AUGMENT",
            "HTF_GATE_BYPASS_RZ",
            "MTF_GR_EXIT_GATE_ENABLED",
            "KG_STOCKS_LIVE_GATE",
            "STRICT_VEC_PARITY_GATE_ENTRIES",
            "KINDERGARTEN_FILTER_TF",
            "FAST_RISER_FILTER_TF",
            "FROZEN_STOP_FILTER_TF",
            "MTF_DC_REJECT_FILTER_TF",
            "NEWBORN_LOSS_KILL_FILTER_TF",
        ):
            fresh = getattr(V.QuickConfig(), name)
            self.assertEqual(getattr(q, name), fresh, name)


class TestFilterAblationMapping(unittest.TestCase):
    def test_members_are_real_bool_or_tf_fields(self):
        q = V.QuickConfig()
        for flag, members in fab.GROUPS.items():
            self.assertGreater(len(members), 0, flag)
            for m in members:
                self.assertTrue(hasattr(q, m), (flag, m))
                self.assertIsInstance(getattr(q, m), (bool, str), (flag, m))
                self.assertFalse(m.startswith("ABLATION_"), (flag, m))

    def test_groups_disjoint_and_stable(self):
        seen = {}
        for flag, members in fab.GROUPS.items():
            for m in members:
                self.assertNotIn(m, seen, (flag, m, seen.get(m)))
                seen[m] = flag
        self.assertEqual(set(fab.GROUPS), set(fab.FLAGS))
        self.assertEqual(set(fab.FLAGS), set(FLAGS))

    def test_series_order_subset(self):
        for f in fab.SERIES_ORDER:
            self.assertIn(f, fab.FLAGS)


class TestFilterAblationGSWiring(unittest.TestCase):
    def test_ablation_flags_wired(self):
        import sys

        sys.path.insert(0, str(ROOT / "tools"))
        sys.path.insert(0, str(ROOT))
        import v15_graph_search as G

        for f in FLAGS:
            self.assertIn(f, G.ABLATION_FLAGS, f)
        self.assertIs(G.FILTER_SERIES_ENABLED, False)
        self.assertTrue(hasattr(G.GraphSearch, "ordered_filter_open"))

    def test_deep_open_default_untouched(self):
        lines = (ROOT / "tools" / "v15_graph_search.py").read_text().splitlines()
        i = next(
            i for i, l in enumerate(lines) if l.strip().startswith("def deep_open(")
        )
        j = next(j for j in range(i + 1, len(lines)) if lines[j].startswith("    def "))
        body = "\n".join(lines[i:j])
        self.assertNotIn("FILTER_SERIES", body)
        self.assertNotIn("ordered_filter_open", body)


if __name__ == "__main__":
    unittest.main()
