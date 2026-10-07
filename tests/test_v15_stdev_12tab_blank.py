"""Durable: STDEV 12-tab blank never promoted."""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v15_pilot as vp
import zipfile, re

def test_stdev_not_in_switch_sheets_12tab():
    assert "STDEV_SLOPE_SIZING" not in vp.SWITCH_SHEETS
    assert len(vp.SWITCH_SHEETS)==12
    assert vp.SKIP_SHEETS=={"STDEV_SLOPE_SIZING"}

def test_template_still_13_but_pilot_uses_12():
    z=zipfile.ZipFile(str(ROOT/"SPREADSHEETS/TEMPLATE.xlsx"))
    wb=z.read('xl/workbook.xml').decode()
    sheets=re.findall(r'name="([^"]+)"', wb)
    # template has 13
    assert "STDEV_SLOPE_SIZING" in sheets
    # pilot uses 12
    assert len([s for s in sheets if s in vp.SWITCH_SHEETS])==12

def test_default_seq_mode_cycle_and_worst_first_alias():
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--seq-mode", default="cycle", choices=["sequential","cycle","round_robin","worst2best","worst_to_best","worst_first","worst-first","shuffle"])
    assert p.parse_args([]).seq_mode=="cycle"
    for alias in ("worst_first","worst-first","round_robin"):
        a=p.parse_args(["--seq-mode",alias])
        if a.seq_mode in ("round_robin","worst_first","worst-first"):
            a.seq_mode="cycle"
        assert a.seq_mode=="cycle"
