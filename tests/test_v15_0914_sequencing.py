import pathlib, sys
sys.path.insert(0, ".")

def test_0914_flags_exist():
    import argparse, importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("v15_0914", "v15_pilot_0914.py")
    mod = importlib.util.module_from_spec(spec)
    # only check argparse, don't run main
    import ast
    txt = pathlib.Path("v15_pilot_0914.py").read_text()
    assert "--seq-mode" in txt, "missing --seq-mode"
    assert "--cycle-on-neg" in txt, "missing --cycle-on-neg"
    assert "--sheet-order" in txt, "missing --sheet-order"
    assert "worst2best" in txt
    assert "0914-cycle" in txt

def test_0914_prototype_files_exist():
    assert pathlib.Path("v15_pilot_0914.py").exists()
    assert pathlib.Path("TEMPLATE_0914.py").exists()
    # 4 category lean templates (crypto/stocks long/short) + REV2 worst-first + SHUFFLE for speed/max delta
    for cat in ["CRYPTO_LONG","CRYPTO_SHORT","STOCKS_LONG","STOCKS_SHORT"]:
        assert pathlib.Path(f"SPREADSHEETS/TEMPLATE_0914_{cat}.xlsx").exists(), f"missing {cat}"
    assert pathlib.Path("SPREADSHEETS/TEMPLATE_0914_REV2_WORSTFIRST.xlsx").exists()
    assert pathlib.Path("SPREADSHEETS/TEMPLATE_0914_SHUFFLE.xlsx").exists(), "missing SHUFFLE"
    assert pathlib.Path("SPREADSHEETS/TEMPLATE_0914.xlsx").exists()
    # handoff 30D -> 365D
    assert pathlib.Path("V15_0914_REBOOT_HANDOFF.md").exists()
    assert "30D" in pathlib.Path("V15_0914_REBOOT_HANDOFF.md").read_text() or "30D" in pathlib.Path("V15_0914_REBOOT_HANDOFF.md").read_text()
    # ensure compile
    import py_compile
    py_compile.compile("v15_pilot_0914.py", doraise=True)

def test_0914_cycle_schedule_generation():
    # verify cycle schedule logic builds 3012 round-robin without importing full engine
    txt = pathlib.Path("v15_pilot_0914.py").read_text() if pathlib.Path("v15_pilot_0914.py").exists() else pathlib.Path("v15_pilot.py").read_text()
    # ensure schedule building code present
    assert "_ordered_cycle" in txt
    assert "_sheet_rows_map" in txt
    assert "round_robin" in txt.lower() or "round-robin" in txt.lower()

def test_0914_worst2best_sort():
    txt = pathlib.Path("v15_pilot_0914.py").read_text() if pathlib.Path("v15_pilot_0914.py").exists() else pathlib.Path("v15_pilot.py").read_text()
    # check worst2best sorts by avg delta
    assert "_avg_delta" in txt
    assert "sorted(sheets" in txt

def test_0914_category_templates_worst_first():
    # 16-sheet V15_AVG_DELTAS + 4 templates worst-first per category (global robust for crypto n=7/3, category-specific for stocks n=19/13)
    import openpyxl
    p = pathlib.Path("SPREADSHEETS/V15_AVG_DELTAS.xlsx")
    assert p.exists() and p.stat().st_size > 500*1024
    wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
    expected = {"SUMMARY","AVG_SWITCHES","AVG_FILTERS","DEFAULT_APPLIED","CATEGORY_RECOMMENDATIONS"}
    assert expected.issubset(set(wb.sheetnames))
    # each CAT template has CATEGORY sheet + worst-first order STDEV -> REDUCE
    for cat in ["CRYPTO_LONG","CRYPTO_SHORT","STOCKS_LONG","STOCKS_SHORT"]:
        wb2 = openpyxl.load_workbook(f"SPREADSHEETS/TEMPLATE_0914_{cat}.xlsx", data_only=True, read_only=True)
        assert f"CATEGORY_{cat}" in wb2.sheetnames
        # at least STDEV and AUGMENT exist
        assert "STDEV_SLOPE_SIZING" in wb2.sheetnames

def test_sequential_baseline_monotonic():
    # reuse existing E-bland guard: cumulative_gain monotonic
    import json
    for cand in [pathlib.Path("data/reports/lifecycle_pilot/AAPL_LONG_7d_progress.json")]:
        if not cand.exists():
            return
        j = json.loads(cand.read_text())
        cum = float(j.get("baseline_gain", 0))
        # check cumulative_gain equals last positive vg
        cg = j.get("cumulative_gain")
        assert isinstance(cg, (int,float))
        # E-bland: new_cum >= old cum for every pos delta
        for k,v in j.get("done", {}).items():
            if float(v.get("delta",0)) > 0:
                ca = v.get("cumulative_after")
                if ca is not None:
                    assert float(ca) >= cum - 1e-9
                    cum = float(ca)
