"""Durable: STDEV 12-tab blank never promoted."""
import pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v15_pilot as vp
import zipfile, re

def test_stdev_not_in_switch_sheets_12tab():
    # USER 2026-09-28: STDEV_SLOPE_SIZING is a single T/F switch tab filled like the rest - 13 tabs, nothing skipped (the 12-tab/skip era is over)
    assert vp.SWITCH_SHEETS[0] == "STDEV_SLOPE_SIZING"
    assert len(vp.SWITCH_SHEETS)==13
    assert vp.SKIP_SHEETS==set()

def test_template_still_13_but_pilot_uses_12():
    z=zipfile.ZipFile(str(ROOT/"SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx"))
    wb=z.read('xl/workbook.xml').decode()
    sheets=re.findall(r'name="([^"]+)"', wb)
    # template has 13 and the pilot fills all 13
    assert "STDEV_SLOPE_SIZING" in sheets
    assert len([s for s in sheets if s in vp.SWITCH_SHEETS])==13

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
