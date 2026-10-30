import hashlib
import unittest
from tools.v15_canonical_frame import CanonicalFrame


class CanonicalFrameTests(unittest.TestCase):
    def test_exact_legacy_keys_and_task_values(self):
        base = {"BOOL": True, "FLOAT": 2.0, "DICT": {"b": False}, "INF": float("inf"), "LIST": [1, 2], "OFF": "OFF"}
        frame = CanonicalFrame(base, 7)
        for patch in ({}, {"BOOL": False}, {"FLOAT": 2}, {"NEW": "4h"}, {"DICT": {"a": 2}}, {"OFF": None}):
            expected = {**base, **patch}
            canonical = tuple(sorted((key, str(value)) for key, value in expected.items()))
            self.assertEqual(frame.canonical(patch, version=7), canonical)
            self.assertEqual(frame.task_config(patch, version=7), expected)
            self.assertEqual(frame.compact_key(patch, version=7, window_days=60, prepared_id=123), (hashlib.blake2b(repr(canonical).encode(), digest_size=16).digest(), 60, 123))

    def test_promotion_invalidates_old_frame(self):
        frame = CanonicalFrame({"X": 1}, 1)
        with self.assertRaises(ValueError):
            frame.canonical({}, version=2)
        with self.assertRaises(ValueError):
            frame.task_config({}, version=2)
        promoted = CanonicalFrame({"X": 2}, 2)
        self.assertEqual(promoted.canonical({}, version=2), (("X", "2"),))

    def test_base_and_task_mutations_cannot_poison_frame(self):
        base = {"X": {"nested": 1}}
        frame = CanonicalFrame(base, "frozen")
        base["X"]["nested"] = 99
        task = frame.task_config({}, version="frozen")
        task["X"]["nested"] = -1
        self.assertEqual(frame.canonical({}, version="frozen"), (("X", "{'nested': 1}"),))


if __name__ == "__main__":
    unittest.main()
