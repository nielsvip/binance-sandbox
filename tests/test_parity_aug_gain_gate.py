"""parity-loop-crypto 2026-10-06: vec twin of the live AUGMENT gain gate (vec_decisions/aug_gain_gate.py vs ez_manage ~24930-24997)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
from vec_decisions import aug_gain_gate as G  # noqa: E402


class C:
    MIN_GAIN = 3.0
    MIN_POSITION_SIZE = 1.0


def test_normal_threshold_blocks_below_min_gain():
    assert not G.passes(C, True, 100, 2.14, 100, 0, 1, 0, 1, 0)
    assert G.passes(C, True, 100, 3.1, 100, 0, 1, 0, 1, 0)


def test_bounce_halves_threshold_long_and_short():
    assert G.passes(C, True, 100, 1.6, 100, 101, 2, 1, 1, 0)
    assert G.passes(C, False, 100, 1.6, 100, 99, 0, 1, 0, -1)
    assert not G.passes(C, True, 100, 1.6, 100, 101, 2, 1, -1, 0)  # LL trough: no bounce


def test_loser_kill_and_foothold():
    assert not G.passes(C, True, 100, -0.1, 100, 101, 2, 1, 1, 0)
    assert G.passes(C, True, 100, 0.1, 0.5, 0, 1, 0, 1, 0)
