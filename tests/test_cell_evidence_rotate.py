"""Condemned-cell rotation: runtime skip maps release a deterministic 1-in-N subset daily (name-hash, reorder-proof)."""

import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "tools"))
import v15_cell_evidence as ce


def _mkprog(pd, sym, n_cells=30):
    done = {}
    for i in range(n_cells):
        done[f"ENTRY_REVERSAL_BOUNCE!{i}:ROT_SW_{i}=True"] = {"yellows": {"FH": -1.0}}
    (pd / f"{sym}_v14_progress.json").write_text(
        json.dumps({"symside": sym, "done": done})
    )


def _run(tmp_path, monkeypatch, day, every="25", rotate="1"):
    monkeypatch.setenv("V15_SKIP_ROTATE_DAY", day)
    monkeypatch.setenv("V15_SKIP_ROTATE_EVERY", every)
    monkeypatch.setenv("V15_SKIP_ROTATE", rotate)
    monkeypatch.setattr(
        sys,
        "argv",
        ["v15_cell_evidence.py", str(tmp_path / "prog"), str(tmp_path / "cell.json")],
    )
    ce.main()
    full = json.loads(open(tmp_path / "cell.json").read())
    rt = json.loads(open(tmp_path / "cell_evidence" / "STOCKS_LONG.json").read())
    rel = json.loads(open(tmp_path / "cell_evidence" / "_rotated_release.json").read())
    return full, rt, rel


def test_rotation_releases_subset_and_rotates(tmp_path, monkeypatch):
    pd = tmp_path / "prog"
    pd.mkdir()
    for i in range(10):
        _mkprog(pd, f"S{i:02d}_LONG")
    full, rt, rel = _run(tmp_path, monkeypatch, "20261009", every="2")
    assert len(full["cat_sides"]["STOCKS_LONG"]) == 30
    assert 0 < len(rel["released"]["STOCKS_LONG"]) < 30
    assert len(rt["cells"]) + len(rel["released"]["STOCKS_LONG"]) == 30
    _, _, rel2 = _run(tmp_path, monkeypatch, "20261010", every="2")
    assert set(rel2["released"]["STOCKS_LONG"]) != set(rel["released"]["STOCKS_LONG"])


def test_rotation_deterministic_and_disablable(tmp_path, monkeypatch):
    pd = tmp_path / "prog"
    pd.mkdir()
    for i in range(10):
        _mkprog(pd, f"S{i:02d}_LONG")
    _, _, r1 = _run(tmp_path, monkeypatch, "20261009")
    _, _, r2 = _run(tmp_path, monkeypatch, "20261009")
    assert r1["released"] == r2["released"]
    _, rt_off, _ = _run(tmp_path, monkeypatch, "20261009", rotate="0")
    assert len(rt_off["cells"]) == 30
