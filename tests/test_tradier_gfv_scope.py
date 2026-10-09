"""GFV gates apply to cash accounts only (USER 2026-10-09): trb margin + trc paper skip."""

import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import tradier_manage as TM


class GfvScopeTest(unittest.TestCase):
    def test_cash_guarded_margin_paper_skip(self):
        with patch.object(TM, "_cfg_auto", side_effect=lambda k, d=None: {"ACCOUNT_TYPE_TRA": "cash", "ACCOUNT_TYPE_TRB": "margin", "ACCOUNT_TYPE_TRC": "paper"}.get(k, d)):
            self.assertTrue(TM._gfv_guard_applies("tra"))
            self.assertFalse(TM._gfv_guard_applies("trb"))
            self.assertFalse(TM._gfv_guard_applies("trc"))

    def test_unknown_fails_closed(self):
        with patch.object(TM, "_cfg_auto", side_effect=lambda k, d=None: d):
            self.assertTrue(TM._gfv_guard_applies("trb"))
            self.assertTrue(TM._gfv_guard_applies("zzz"))
            self.assertTrue(TM._gfv_guard_applies(None))

    def test_config_declares_types(self):
        import config_tradier
        c = config_tradier.TradierConfig()
        self.assertEqual(c.ACCOUNT_TYPE_TRA, "cash")
        self.assertEqual(c.ACCOUNT_TYPE_TRB, "margin")
        self.assertEqual(c.ACCOUNT_TYPE_TRC, "paper")


if __name__ == "__main__":
    unittest.main()
