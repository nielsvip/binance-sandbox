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

    @staticmethod
    def _stmt_at(lines, idx):
        """The whole statement starting at idx, whitespace-collapsed.

        2026-10-28: these pins used to assert that every term sat on ONE physical line. black then
        wrapped both the `_wd_armed = (...)` predicate and the `if (...)` suppression across lines, so
        the pins failed while the wiring was intact and correct — a stale text-matcher, not a regression.
        They now read the whole parenthesised statement, which is formatting-proof and asserts exactly
        the same thing: suppression happens ONLY when the watchdog replacement is armed.
        """
        buf, depth = [], 0
        for ln in lines[idx:]:
            stripped = ln.split("#", 1)[0]
            buf.append(stripped.strip())
            depth += stripped.count("(") - stripped.count(")")
            if depth <= 0 and buf:
                break
        return " ".join(x for x in buf if x)

    def test_suppression_requires_armed(self):
        starts = [
            i for i, l in enumerate(self.lines)
            if l.strip().startswith("if ")
            and "ABLATION_DISABLE_QUICK_ENTRY" in self._stmt_at(self.lines, i)
            and "QUICK_ENTRY_ABLATION_SUPPRESS_VEC" in self._stmt_at(self.lines, i)
        ]
        self.assertEqual(len(starts), 1, "exactly one suppression site expected")
        stmt = self._stmt_at(self.lines, starts[0])
        self.assertIn("_wd_armed", stmt, "suppression must be conditioned on the armed predicate")

    def test_armed_predicate_complete(self):
        idx = next(i for i, l in enumerate(self.lines) if "_wd_armed = (" in l)
        stmt = self._stmt_at(self.lines, idx)
        self.assertIn("_wd_crypto", stmt)
        for term in (
            "MOMENTUM_SMA_WATCHDOG_ENABLED",
            "WATCHDOG_DC_FORCE_OPEN_ENABLED",
            "WATCHDOG_DC_VEC_ENABLED",
        ):
            self.assertIn(term, stmt)
        window = " ".join(x.split("#", 1)[0].strip() for x in self.lines[max(0, idx - 12):idx])
        self.assertIn('_wd_crypto = str(getattr(cfg, "MODE", "crypto")) != "tradier"', window)

    def test_open_build_uses_shared_predicate(self):
        self.assertTrue(
            any("watchdog_dc_breakout.dc_breakout_open_mask" in l for l in self.lines)
        )
        self.assertFalse(
            any("_wd_open = np.zeros(n, dtype=bool)" in l for l in self.lines)
        )


if __name__ == "__main__":
    unittest.main()
