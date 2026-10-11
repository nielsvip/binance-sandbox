"""Guardian rotation tolerance (USER 2026-10-11 fix-as-discovered): manager logs rotate at 5MB but the guardian parsed the current file only — after flz/fin rotations their decision history vanished (div/noref/ref collapsed to 0, orphans spiked 0->17). tail() must include the rotated .1 sibling so rotations stop faking spikes.
"""
from pathlib import Path
from tools.forward_parity import live_guardian as G


def test_tail_includes_rotated_sibling(tmp_path):
    cur = tmp_path / "ez_manage_tst.log"
    cur.write_text("new1\nnew2\n")
    (tmp_path / "ez_manage_tst.log.1").write_text("old1\nold2\n")
    out = G.tail(cur, 1_000_000)
    assert out == ["old1", "old2", "new1", "new2"]


def test_tail_without_sibling_unchanged(tmp_path):
    cur = tmp_path / "solo.log"
    cur.write_text("a\nb\n")
    assert G.tail(cur, 1_000_000) == ["a", "b"]


def test_tail_missing_file_empty(tmp_path):
    assert G.tail(tmp_path / "nope.log", 1_000_000) == []
