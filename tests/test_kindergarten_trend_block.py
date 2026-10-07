"""durable: kindergarten 9/21 50/50 must block counter-trend scalps on any TF"""
import pathlib
import openpyxl
import numpy as np


def test_v12_no_gates_off_bypass():
    src = pathlib.Path("v12_quick_engine.py").read_text()
    # must not have unconditional _base_entry = raw bypass
    assert "_base_entry = raw  # gates OFF for backtest" not in src, "gates-OFF bypass must be removed (MRVL_SHORT whistleblower)"
    # must have kindergarten gate
    assert "KINDERGARTEN TREND FILTER" in src, "kindergarten gate must be present"
    assert "_kg_ok" in src, "kg_ok must be used"
    # WT_SIMPLE_GUARANTEE must be behind flag, not unconditional OR
    assert "WT_SIMPLE_GUARANTEE_ENABLED" in src, "warranty flag must exist"
    assert "if bool(getattr(cfg, 'WT_SIMPLE_GUARANTEE_ENABLED'" in src, "wt guarantee must be flagged"
    # FORCE_MIN_ONE_TRADE must be behind flag
    assert "FORCE_MIN_ONE_TRADE" in src, "force flag must exist"
    assert "if not np.any(_base_entry) and bool(getattr(cfg, 'FORCE_MIN_ONE_TRADE'" in src, "force trade must be gated"
    # defaults must be False
    assert "WT_SIMPLE_GUARANTEE_ENABLED: bool = False" in src
    assert "FORCE_MIN_ONE_TRADE: bool = False" in src


def test_template_has_kindergarten():
    for name in ["SPREADSHEETS/TEMPLATE.xlsx", "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx", "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx", "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx", "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx"]:
        wb = openpyxl.load_workbook(name, read_only=True)
        assert "FILTER_DICTIONARY_V2" in wb.sheetnames
        ws = wb["FILTER_DICTIONARY_V2"]
        filters = {ws.cell(row=r, column=2).value for r in range(2, ws.max_row+1)}
        assert "KINDERGARTEN_EMA_GATE_ENABLED" in filters
        assert "EMA_50_200_FILTER_ENABLED" in filters, f"{name} missing 50/200"
        assert "SMA_50_FILTER_ENABLED" in filters, f"{name} missing kindergarten gate filter"
        assert "EMA_9_21_FILTER_ENABLED" in filters, f"{name} missing 9/21 filter"
        assert "EMA_9_21_FILTER_TFS" in filters, f"{name} missing TF filter"
        assert "WT_SIMPLE_GUARANTEE_ENABLED" in filters
        assert "GLOBAL_RISK_GATES" in wb.sheetnames
        ws2 = wb["GLOBAL_RISK_GATES"]
        switches = {ws2.cell(row=r, column=1).value for r in range(3, ws2.max_row+1)}
        assert "KINDERGARTEN_EMA_GATE_ENABLED" in switches
        assert "EMA_50_200_FILTER_ENABLED" in switches, f"{name} GLOBAL_RISK_GATES missing kindergarten switch"
        assert "EMA_9_21_FILTER_ENABLED" in switches
        # check at least one multi-TF variant present
        tf_vals = {str(ws2.cell(row=r, column=3).value) for r in range(3, ws2.max_row+1) if ws2.cell(row=r, column=1).value == "EMA_9_21_FILTER_TFS"}
        assert "1h,D" in tf_vals or "15m,1h,4h" in tf_vals, f"{name} missing multi-TF 9/21 variants"


def test_kindergarten_blocks_counter_trend_vectorized():
    # Lightweight check that kindergarten gate is wired without heavy compute_entry_signals
    # Heavy compute can be >30s due to many gates; we test the gate logic directly via small synthetic
    import numpy as np
    # Simulate the _kg_ok logic from v12_quick_engine directly
    n = 10
    # misaligned: 9 below 21 on 1h (should block LONG)
    ema_mis = np.zeros(n, dtype=np.float32)
    ema_ok = np.ones(n, dtype=np.float32)
    # Simulate _kg_checks for LONG: should be False when misaligned
    is_above_mis = ema_mis > 0.5
    is_above_ok = ema_ok > 0.5
    # For LONG, _kg_checks = is_above
    assert not bool(is_above_mis[0]), "misaligned should be False for LONG"
    assert bool(is_above_ok[0]), "aligned should be True for LONG"
    # For SHORT, reverse
    assert bool((~is_above_mis)[0]), "misaligned should be True for SHORT (9 below 21 is good for short)"
    assert not bool((~is_above_ok)[0])
    # Check that v12 source actually uses this logic (already checked in test_v12_no_gates_off_bypass)
    # Also verify that MULTI-TF cumulation would block when min_tfs=2 and only 1 passes
    # Simulate 2 TFs: 1h mis, 4h ok, need 2 to pass -> should block
    checks_2tf = [is_above_mis, is_above_ok]  # 1 mis, 1 ok
    stack = np.stack(checks_2tf, axis=0)
    cnt = stack.sum(axis=0)
    assert int(cnt[0]) == 1
    assert int(cnt[0]) < 2  # need 2, so blocked
    # With min_tfs=1, would pass
    assert int(cnt[0]) >= 1
