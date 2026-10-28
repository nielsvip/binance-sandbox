"""2026-10-28: the harvester must deliver every board the pilot published (final_path, compliant) under the pilot's own file name,
even when v15_progress_board.is_complete() is False (V21 row selection fills ~2865/3977 rows), and must never put the newer
candidate's gain on a kept-best (older) workbook."""
import json
import os
import pathlib
import subprocess
import sys
import zipfile

REPO = pathlib.Path(__file__).resolve().parents[1]


def _xlsx(path):
    with zipfile.ZipFile(path, "w") as z:
        for i in range(12):
            z.writestr(f"xl/part{i}.xml", "<x/>")


def _run(tmp_path, progress):
    home = tmp_path / "home"
    sb = home / "binance-sandbox"
    cell = sb / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    cell.mkdir(parents=True)
    (sb / "tools").symlink_to(REPO / "tools")
    pdir = tmp_path / "progress"
    pdir.mkdir()
    for ss, prog in progress.items():
        (pdir / f"{ss}_v14_progress.json").write_text(json.dumps(prog))
    env = dict(os.environ, HOME=str(home), V15_PROGRESS_DIR=str(pdir))
    out = subprocess.run([sys.executable, str(REPO / "tools" / "v15_harvest_done.py")], capture_output=True, text=True, env=env, timeout=120)
    return home / "v15_mac_done", cell, out


def test_kept_best_published_board_harvested_under_pilot_name(tmp_path):
    home = tmp_path / "home" / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    kept = home / "ETN_SHORT_bh11_gain34_t52_30d_matrix.xlsx"
    prog = {"ETN_SHORT": {"final_gain": 31.12, "bh": 11.0, "final_trades": 50, "not_compliant": None, "final_path": str(kept), "done": {}}}
    home.mkdir(parents=True)
    _xlsx(kept)
    # _run creates the dirs itself; build them first so the published file exists, then reuse
    out_dir = tmp_path / "home" / "v15_mac_done"
    env_home = tmp_path / "home"
    (env_home / "binance-sandbox" / "tools").symlink_to(REPO / "tools")
    pdir = tmp_path / "progress"
    pdir.mkdir()
    (pdir / "ETN_SHORT_v14_progress.json").write_text(json.dumps(prog["ETN_SHORT"]))
    env = dict(os.environ, HOME=str(env_home), V15_PROGRESS_DIR=str(pdir))
    res = subprocess.run([sys.executable, str(REPO / "tools" / "v15_harvest_done.py")], capture_output=True, text=True, env=env, timeout=120)
    assert sorted(p.name for p in out_dir.glob("*.xlsx")) == ["ETN_SHORT_bh11_gain34_t52_30d_matrix.xlsx"], res.stdout + res.stderr
    assert "1 honest-complete" in res.stdout


def test_not_compliant_never_harvested(tmp_path):
    out_dir, cell, res = _run(tmp_path, {"ZILUSDT_SHORT": {"final_gain": 5.0, "bh": 1.0, "not_compliant": "invalid:npz covers 23d", "final_path": "/nonexistent/ZILUSDT_SHORT_bh1_gain5_t9_30d_matrix.xlsx", "done": {}}})
    assert list(out_dir.glob("*.xlsx")) == []
