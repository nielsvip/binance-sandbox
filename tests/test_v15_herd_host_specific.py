import pathlib
import sys

# Ensure repo root on path
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import tools.v15_local_herd as herd


def test_sym_match_exact_prefix():
    # MU_LONG must not match ALMU_LONG (previous substring bug)
    assert herd._sym_match("MU_LONG_30d_matrix.xlsx", "MU_LONG") is True
    assert herd._sym_match("ALMU_LONG_30d_matrix.xlsx", "MU_LONG") is False
    assert herd._sym_match("MU_LONG_bh1p00_gain2p00_30d_matrix.xlsx", "MU_LONG") is True
    assert herd._sym_match("MU_LONG_365d_matrix.xlsx", "MU_LONG") is True
    # exact and dot suffix
    assert herd._sym_match("MU_LONG", "MU_LONG") is True
    assert herd._sym_match("MU_LONG.xlsx", "MU_LONG") is True
    # other symbols
    assert herd._sym_match("HOOD_LONG_30d_matrix.xlsx", "HOOD_LONG") is True
    assert herd._sym_match("HOOD_LONG_365d_matrix.xlsx", "HOOD_LONG") is True
    assert herd._sym_match("WOOD_LONG_30d_matrix.xlsx", "HOOD_LONG") is False


def test_local_done_prefix_not_substring(tmp_path, monkeypatch):
    # Create fake CELL dir with ALMU file, ensure MU not considered done
    import pathlib as pl

    fake_home = tmp_path / "home"
    cell = fake_home / "binance-sandbox" / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
    cell.mkdir(parents=True)
    # ALMU file >500k, no _2026
    (cell / "ALMU_LONG_30d_matrix.xlsx").write_bytes(b"x" * 600_000)
    # also create MU file for control
    # monkeypatch Path.home to fake_home
    monkeypatch.setattr(pl.Path, "home", lambda: fake_home)
    order = ["MU_LONG", "ALMU_LONG"]
    done = herd.local_done_set(order)
    assert "ALMU_LONG" in done
    assert "MU_LONG" not in done  # would have been true with substring bug

    # now add MU file, both should be done
    (cell / "MU_LONG_30d_matrix.xlsx").write_bytes(b"x" * 600_000)
    done2 = herd.local_done_set(order)
    assert "MU_LONG" in done2
    assert "ALMU_LONG" in done2


def test_find_order_host_specific(tmp_path, monkeypatch):
    # Verify find_order prefers host-specific S6 over S1 when urgent pending
    import subprocess

    fake_root = tmp_path / "root"
    fake_home = tmp_path / "home2"
    (fake_root / "SPREADSHEETS").mkdir(parents=True)
    (fake_home / "binance-sandbox" / "SPREADSHEETS").mkdir(parents=True)
    # create S1 and S6 queues
    (fake_root / "SPREADSHEETS" / "V15_SERVER_QUEUE_S1.txt").write_text("BNBUSDC_LONG\n")
    (fake_root / "SPREADSHEETS" / "V15_SERVER_QUEUE_S6.txt").write_text("HOOD_LONG\n")
    # urgent file pending
    import json

    (fake_root / "SPREADSHEETS" / "V15_URGENT_FINAL_83.json").write_text(json.dumps({"pending_urgent": 81, "urgent_total": 83, "final_pos_gain_list": ["a", "b"]}))
    # patch herd.ROOT and HOME
    monkeypatch.setattr(herd, "ROOT", fake_root)
    orig_home = pathlib.Path.home
    monkeypatch.setattr(pathlib.Path, "home", lambda: fake_home)
    # mock hostname s6
    monkeypatch.setattr(subprocess, "check_output", lambda *a, **k: "s6")
    order = herd.find_order()
    assert order is not None
    # must be S6, not S1
    assert "S6" in str(order)
    assert order.name == "V15_SERVER_QUEUE_S6.txt"
