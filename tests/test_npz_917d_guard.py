import tempfile
import unittest
from pathlib import Path

import numpy as np

from tools.npz_guard import should_allow_overwrite
from tools.pull_massive_15m_priority import prioritized_symbols, PRIORITY_TIER_1


def make_npz(path: Path, span_days: int, dt: int, keys: int, has_w: bool):
    n = int(span_days * 86400 / dt)
    ts = np.arange(n, dtype=np.int64) * dt + 1700000000
    data = {"timestamps": ts, "timestamp_15m": ts}
    for i in range(keys - len(data)):
        data[f"k{i}"] = np.zeros(n)
    if has_w:
        data["wt1_W"] = np.zeros(n)
    np.savez_compressed(path, **data)


class Npz917dGuardTests(unittest.TestCase):
    def test_blocks_77d_overwrite_of_917d(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "AAPL.npz"
            make_npz(existing, span_days=917, dt=900, keys=938, has_w=True)
            allowed, reason = should_allow_overwrite(existing, candidate_span_days=77, candidate_dt=900, candidate_keys=649)
            self.assertFalse(allowed)
            self.assertIn("BLOCK", reason)

    def test_blocks_low_keys_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "A.npz"
            make_npz(existing, span_days=917, dt=900, keys=938, has_w=True)
            allowed, _ = should_allow_overwrite(existing, candidate_span_days=2115, candidate_dt=900, candidate_keys=508)
            self.assertFalse(allowed)

    def test_allows_no_existing(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "MISSING.npz"
            allowed, _ = should_allow_overwrite(missing, candidate_span_days=77, candidate_dt=900, candidate_keys=649)
            self.assertTrue(allowed)

    def test_allows_upgrade(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing = Path(tmp) / "OLD.npz"
            make_npz(existing, span_days=77, dt=900, keys=649, has_w=False)
            allowed, _ = should_allow_overwrite(existing, candidate_span_days=917, candidate_dt=900, candidate_keys=938)
            self.assertTrue(allowed)


class MassivePriorityTests(unittest.TestCase):
    def test_prioritized_starts_with_tier1(self):
        syms = prioritized_symbols()
        # At least first few are tier1 members if present in universe
        self.assertGreater(len(syms), 0)
        # PRIORITY_TIER_1 members that exist should be at front
        tier1_in_universe = [s for s in PRIORITY_TIER_1 if s in syms]
        if tier1_in_universe:
            self.assertEqual(syms[0], tier1_in_universe[0])

    def test_massive_pull_uses_correct_endpoint_and_key(self):
        import tools.pull_massive_15m_priority as mod
        self.assertEqual(mod.BASE_URL, "https://api.massive.com/v2/aggs")
        self.assertEqual(mod.API_KEY, "2kX_tMy4PWx4JZMQZxqSJxQnznuOYAC0")


if __name__ == "__main__":
    unittest.main()
