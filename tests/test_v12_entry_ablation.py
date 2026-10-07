"""C/003 entry-ablation guard: suppression only when watchdog replacement armed (cut#5).

Regression pin for the 2026-10-04 crypto flatline: the ABLATION_DISABLE_QUICK_ENTRY
block zeroed entry_sig unconditionally while the watchdog replacement stayed
unbuilt (WATCHDOG_DC_VEC_ENABLED=False) -> 0 trades/2881 bars on s1-gen NPZ.
Behavioral proof (s1+s2 full-tree gate, production evaluate_sanitized): BTCUSDC_LONG
145->216 trades restored with DC-breakout OR, MU_LONG preserved; see laneF PROOF +
cut#5 gate logs.
"""
import unittest
from pathlib import Path

V12 = Path(__file__).resolve().parent.parent / "v12_quick_engine.py"


class TestEntryAblationGuard(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lines = V12.read_text().split("\n")

    def test_suppression_requires_armed(self):
        cands = [
            l for l in self.lines
            if "ABLATION_DISABLE_QUICK_ENTRY" in l
            and "QUICK_ENTRY_ABLATION_SUPPRESS_VEC" in l
            and l.strip().startswith("if ")
        ]
        self.assertEqual(len(cands), 1)
        self.assertIn("_wd_armed", cands[0])

    def test_armed_predicate_complete(self):
        idx = next(i for i, l in enumerate(self.lines) if "_wd_armed = (" in l)
        self.assertIn("_wd_crypto", self.lines[idx])
        for term in (
            "MOMENTUM_SMA_WATCHDOG_ENABLED",
            "WATCHDOG_DC_FORCE_OPEN_ENABLED",
            "WATCHDOG_DC_VEC_ENABLED",
        ):
            self.assertIn(term, self.lines[idx])
        window = "\n".join(self.lines[max(0, idx - 12):idx])
        self.assertIn("_wd_crypto = str(getattr(cfg, 'MODE', 'crypto')) != 'tradier'", window)

    def test_open_build_uses_shared_predicate(self):
        self.assertTrue(
            any("watchdog_dc_breakout.dc_breakout_open_mask" in l for l in self.lines)
        )
        self.assertFalse(
            any("_wd_open = np.zeros(n, dtype=bool)" in l for l in self.lines)
        )


if __name__ == "__main__":
    unittest.main()
