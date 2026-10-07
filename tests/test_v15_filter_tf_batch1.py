"""Batch-1 NONE wiring (USER 2026-10-07): DELTA_ENGINE / DC_MOMENTUM_BOTA_SCORER /
CIRCUIT_SHARPE_GATES _FILTER_TF have live batch1 entry vetoes + inline v12 legs,
so they must NOT be UNWIRED-skipped. Fail-closed on leg removal or re-adding."""

import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import v15_pilot as PILOT

BATCH1 = ["DELTA_ENGINE_FILTER_TF", "DC_MOMENTUM_BOTA_SCORER_FILTER_TF", "CIRCUIT_SHARPE_GATES_FILTER_TF"]


def _has_live_consumer():
    import v12_quick_engine as V12
    import inspect
    src = inspect.getsource(V12.simulate_one)
    return "FILTER_TF_MAP.items()" in src and "_entry_filter_masks.append(_gm)" in src


class Batch1WiredTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.v12 = (ROOT / "v12_quick_engine.py").read_text().splitlines()
        cls.ez = (ROOT / "ez_manage.py").read_text()

    def test_not_unwired(self):
        d = json.loads((ROOT / "data" / "vec_unwired.json").read_text())
        for f in BATCH1:
            self.assertNotIn(f, d.get("switches") or [])
            self.assertNotIn(f, d.get("filters") or [])
            self.assertNotIn(f, PILOT.UNWIRED_SWITCHES)
            self.assertNotIn(f, PILOT.UNWIRED_FILTERS)

    def test_map_entries_live_faithful(self):
        from vec_decisions.generic_filter_tf import FILTER_TF_MAP
        for f in BATCH1:
            self.assertIn(f, FILTER_TF_MAP)
            self.assertEqual(FILTER_TF_MAP[f], ("entry", "wt_cross_side"))

    def test_live_consumer_path_intact(self):
        self.assertTrue(_has_live_consumer())

    def test_live_batch1_veto_exists(self):
        for f in BATCH1:
            self.assertIn(f"getattr(config, '{f}'", self.ez)
            self.assertIn(f"{f}_TF'", self.ez)

    def test_quickconfig_fields_exist(self):
        import v12_quick_engine as V12
        import dataclasses
        names = {f.name for f in dataclasses.fields(V12.QuickConfig)}
        for f in BATCH1:
            self.assertIn(f, names)


if __name__ == "__main__":
    unittest.main()
