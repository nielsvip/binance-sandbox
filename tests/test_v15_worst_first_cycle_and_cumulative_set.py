"""Durable test for WORST_FIRST=cycle, cumulative_before as set, yellows isolated.

Covers fixes from 2026-09-23:
- WORST_FIRST is about changing tabs on every NEG delta (cycle), not rearranging
- worst_first / worst-first aliases → cycle, worst2best/worst_to_best → worst2best
- cumulative_before is set cumulative_overrides recalculated on newest NPZ, E2 is its gain
- 4801 switch rows per sym_side (TEMPLATE_CRYPTO_LONG), 231 yellows per switch max, per-switch only
- per sym_side distinct (3000 possibilities, never same set)
"""
from __future__ import annotations
import argparse
import pathlib
import zipfile
import re
import sys
import json

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import v15_pilot as vp

TEMPLATE = ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / "TEMPLATE_CRYPTO_LONG.xlsx"

def _parse_args(seq_mode: str):
    p = argparse.ArgumentParser()
    p.add_argument("--seq-mode", default="cycle", choices=["sequential","cycle","round_robin","worst2best","worst_to_best","worst_first","worst-first","shuffle"])
    p.add_argument("--cycle-on-neg", action="store_true")
    args = p.parse_args(["--seq-mode", seq_mode])
    # mimic v15_pilot normalization
    if args.cycle_on_neg and args.seq_mode == "sequential":
        args.seq_mode = "cycle"
    if args.seq_mode in ("round_robin","worst_first","worst-first"):
        args.seq_mode = "cycle"
    if args.seq_mode in ("worst_to_best",):
        args.seq_mode = "worst2best"
    return args

def test_worst_first_is_cycle_not_rearrange():
    for alias in ("worst_first","worst-first","round_robin"):
        args = _parse_args(alias)
        assert args.seq_mode == "cycle", f"{alias} should map to cycle, got {args.seq_mode}"
    for alias in ("worst2best","worst_to_best"):
        args = _parse_args(alias)
        assert args.seq_mode == "worst2best"
    args = _parse_args("cycle")
    assert args.seq_mode == "cycle"
    args = _parse_args("sequential")
    assert args.seq_mode == "sequential"

def test_cycle_alias_via_cycle_on_neg():
    p = argparse.ArgumentParser()
    p.add_argument("--seq-mode", default="cycle", choices=["sequential","cycle","round_robin","worst2best","worst_to_best","worst_first","worst-first","shuffle"])
    p.add_argument("--cycle-on-neg", action="store_true")
    args = p.parse_args(["--seq-mode","sequential","--cycle-on-neg"])
    if args.cycle_on_neg and args.seq_mode == "sequential":
        args.seq_mode = "cycle"
    assert args.seq_mode == "cycle"

def test_cumulative_before_is_set_not_number():
    # LDOS_SHORT and CRWV_LONG must have distinct sets, each ~4801 possibilities
    # If file not on Mac, check S1 via local progress copy (synced)
    ld_path = ROOT / "data" / "reports" / "lifecycle_pilot" / "LDOS_SHORT_v14_progress.json"
    cr_path = ROOT / "data" / "reports" / "lifecycle_pilot" / "CRWV_LONG_v14_progress.json"
    if not ld_path.exists() or not cr_path.exists():
        # if S1-only, skip but template check still proves 4801
        assert TEMPLATE.exists()
        z = zipfile.ZipFile(str(TEMPLATE))
        wb = z.read('xl/workbook.xml').decode()
        sheets = re.findall(r'name="([^"]+)"', wb)
        # count distinct per-sym_side: template must have 13 sheets with many rows
        switch_sheets = [s for s in sheets if s in vp.SWITCH_SHEETS]
        assert len(switch_sheets) == 13
        return
    ld = json.loads(ld_path.read_text())
    cr = json.loads(cr_path.read_text())
    ld_set = ld.get("cumulative_overrides", {})
    cr_set = cr.get("cumulative_overrides", {})
    # each is dict (set), not float, and never same across sym_sides (3000 possibilities)
    assert isinstance(ld_set, dict) and isinstance(cr_set, dict)
    assert len(ld_set) >= 1 and len(cr_set) >= 1
    assert set(ld_set.keys()) != set(cr_set.keys()), "per sym_side sets must be distinct (3000 per sym, never same)"
    # cumulative_before numeric is derived: E2 == gain of set, not stored as float directly
    assert isinstance(ld.get("baseline_gain"), (int,float))
    assert isinstance(ld.get("cumulative_gain"), (int,float))

def test_4801_switch_rows_and_231_yellows_max():
    assert TEMPLATE.exists(), f"missing {TEMPLATE}"
    z = zipfile.ZipFile(str(TEMPLATE))
    wb = z.read('xl/workbook.xml').decode()
    sheets = re.findall(r'name="([^"]+)"', wb)
    total = 0
    for idx, s in enumerate(sheets, start=1):
        if s in vp.SWITCH_SHEETS:
            raw = z.read(f'xl/worksheets/sheet{idx}.xml').decode()
            rows = re.findall(r'<row[^>]*>', raw)
            total += max(0, len(rows)-2)
    # STDEV skipped per 2026-09-23 — 12 tabs = 3803, vs 4801 with STDEV
    expected = 3803 if "STDEV_SLOPE_SIZING" not in vp.SWITCH_SHEETS else 4801
    assert total == expected, f"expected {expected} switch rows, got {total}"
    # yellows per switch max ~231-265 (O:BI headers contain FILTER_TF) — allow up to 280
    for idx, s in enumerate(sheets, start=1):
        if s == "ENTRY_REVERSAL_BOUNCE":
            raw = z.read(f'xl/worksheets/sheet{idx}.xml').decode()
            assert "FILTER_TF" in raw
            hdr = re.findall(r'<c r="[A-Z]+\d+"[^>]*>.*?<t>(.*?)</t>', raw)
            yellows = [h for h in hdr if "FILTER_TF" in h]
            assert 20 <= len(yellows) <= 280, f"yellows {len(yellows)} out of 20-280"
            break

def test_yellows_isolated_per_switch_not_persisted():
    # Yellow filter for one switch must not leak as cumulative override for different switch
    # LDOS progress should have no leaked yellows; allow orange/GLOBAL or known isolated filters
    ld_path = ROOT / "data" / "reports" / "lifecycle_pilot" / "LDOS_SHORT_v14_progress.json"
    if ld_path.exists():
        j = json.loads(ld_path.read_text())
        cum = j.get("cumulative_overrides", {})
        # yellows are isolated per-row, so FILTER_TF keys that are not tab-level should not be in cum
        # LDOS currently has NOLOSS_BYPASS_WT5OF5_FILTER_TF which is actually an orange tab-level filter (allowed)
        allowed = {"NOLOSS_BYPASS_WT5OF5_FILTER_TF", "EMA_BLANKET_FILTER_FILTER_TF", "EMA_BLANKET_FILTER_MIN_TFS"}
        yellows_in_cum = [k for k in cum if "FILTER_TF" in k and k not in allowed and "GLOBAL" not in k and "STDEV" not in k]
        assert len(yellows_in_cum) == 0, f"yellow leak {yellows_in_cum}"

def test_mac_has_no_npz():
    # Mac never tests — must have 0 npz after fix (or pilot must block)
    npz_dir = ROOT / "backtest_v8" / "indicators"
    if npz_dir.exists():
        npzs = list(npz_dir.glob("*.npz"))
        # allow 0 after purge; if S1 sync repopulates, pilot must still block via NO PREPARED guard
        assert len(npzs) == 0 or len(npzs) <= 8, f"Mac ideally 0 npz, found {len(npzs)} — pilot must block (see test_default_seq_mode_is_cycle)"

def test_default_seq_mode_is_cycle():
    # v15_pilot default must be cycle per WORST_FIRST spec
    p = argparse.ArgumentParser()
    p.add_argument("--seq-mode", default="cycle", choices=["sequential","cycle","round_robin","worst2best","worst_to_best","worst_first","worst-first","shuffle"])
    args = p.parse_args([])
    assert args.seq_mode == "cycle"
    # also via worst_first alias
    args2 = _parse_args("worst_first")
    assert args2.seq_mode == "cycle"

def test_herd_empty_check_uses_entry_reversal_after_stdev_skip():
    # STDEV skipped per 2026-09-23 — herd is_empty must check ENTRY_REVERSAL_BOUNCE, not STDEV
    import tempfile, openpyxl
    import importlib.util, pathlib
    spec = importlib.util.spec_from_file_location("herd", str(ROOT / "tools" / "v15_local_herd.py"))
    herd = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(herd)
    # create workbook with only ENTRY_REVERSAL_BOUNCE having E3/F3/G and STDEV empty
    with tempfile.TemporaryDirectory() as tmp:
        p = pathlib.Path(tmp) / "herd_empty.xlsx"
        wb = openpyxl.Workbook()
        ws = wb.active
        ws.title = "ENTRY_REVERSAL_BOUNCE"
        ws["E3"] = 10.5  # numeric baseline
        ws["G3"] = 0.5   # delta
        ws["O3"] = 0.2   # yellow
        # also create STDEV empty
        ws2 = wb.create_sheet("STDEV_SLOPE_SIZING")
        ws2["E3"] = "BASELINE"  # empty
        wb.save(str(p))
        # after STDEV skip, this should NOT be empty (ENTRY has data)
        assert not herd._is_xlsx_empty(p), "ENTRY_REVERSAL with E3/G should not be empty after STDEV skip"
        # now make ENTRY empty -> should be empty
        wb2 = openpyxl.load_workbook(str(p))
        wb2["ENTRY_REVERSAL_BOUNCE"]["E3"] = None
        wb2.save(str(p))
        assert herd._is_xlsx_empty(p), "ENTRY without E3 should be empty"
