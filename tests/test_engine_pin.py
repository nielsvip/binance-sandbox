"""Composite engine pin: stable, sensitive, excludes tests."""

import hashlib
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.engine_pin import basis_files, composite


class TestEnginePin(unittest.TestCase):
    def test_live_tree(self):
        digest, manifest = composite(str(ROOT))
        self.assertEqual(len(digest), 32)
        paths = [l.split("=")[0] for l in manifest]
        self.assertIn("v12_quick_engine.py", paths)
        self.assertTrue(any(p.startswith("vec_decisions/") for p in paths))
        self.assertFalse(any("/test_" in p or p.endswith("__init__.py") for p in manifest))

    def test_stable_and_sensitive(self, tmp=None):
        import tempfile

        with tempfile.TemporaryDirectory() as t:
            Path(t, "v12_quick_engine.py").write_text("v1")
            Path(t, "vec_decisions").mkdir()
            Path(t, "vec_decisions", "a.py").write_text("a")
            Path(t, "vec_decisions", "test_a.py").write_text("t")
            d1, m1 = composite(t)
            d2, m2 = composite(t)
            self.assertEqual(d1, d2)
            self.assertEqual(len(m1), 2)
            Path(t, "vec_decisions", "a.py").write_text("a2")
            d3, _ = composite(t)
            self.assertNotEqual(d1, d3)
            Path(t, "vec_decisions", "test_a.py").write_text("t2")
            d4, _ = composite(t)
            self.assertEqual(d3, d4)


if __name__ == "__main__":
    unittest.main()
