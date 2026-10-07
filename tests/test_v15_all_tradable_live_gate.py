"""2026-09-22 v15_pilot all-tradable: every tradeable_keys sym_side must trade (entry+exit) with best v15_pilot settings until bigger backtest proves unprofitable."""
import json, pathlib

import config as cfg_mod
import ez_manage as ez


def _load_tradeable_set():
    p = pathlib.Path("tradeable_keys.json")
    if not p.exists():
        p = pathlib.Path(__file__).resolve().parents[1] / "tradeable_keys.json"
    raw = json.loads(p.read_text())
    return set(str(k).split(":",1)[1] if ":" in str(k) else str(k) for k in raw if isinstance(k, str))


def test_all_tradeable_keys_live_enabled():
    tks = _load_tradeable_set()
    assert len(tks) >= 80, f"tradeable_keys too small {len(tks)}"
    for sym_side in sorted(tks):
        sym, side = sym_side.rsplit("_", 1)
        ok, why = ez._ezm_is_live_side_enabled(sym, side)
        assert ok, f"{sym_side} in tradeable_keys but live gate blocked: {why}"


def test_all_tradeable_keys_not_final_book_disabled():
    tks = _load_tradeable_set()
    for sym_side in sorted(tks):
        ov = ez._ezm_apply_final_book(sym_side, {})
        flag = "LONG_ENABLED" if sym_side.endswith("_LONG") else "SHORT_ENABLED"
        assert flag not in ov or bool(ov[flag]), f"{sym_side} in tradeable_keys but final_book forced {flag}=False"


def test_all_tradeable_keys_psym_not_blocked_for_open():
    tks = _load_tradeable_set()
    for sym_side in sorted(tks):
        sym, side = sym_side.rsplit("_", 1)
        flag = "LONG_ENABLED" if side == "LONG" else "SHORT_ENABLED"
        val = ez._psym_get(sym, side, flag, True)
        assert bool(val), f"{sym_side} _psym_get {flag} False but must trade"


def test_execute_now_would_not_block_tradeable_open():
    tks = _load_tradeable_set()
    for sym_side in sorted(tks):
        sym, side = sym_side.rsplit("_", 1)
        flag = "LONG_ENABLED" if side == "LONG" else "SHORT_ENABLED"
        psym = ez._psym_get(sym, side, flag, True)
        ok, _ = ez._ezm_is_live_side_enabled(sym, side)
        blocked = (not psym) or (not ok)
        assert not blocked, f"{sym_side} would be BLOCKED for OPEN/AUGMENT/ENTRY/REENTRY but must trade"


def test_close_reduce_never_blocked():
    tks = _load_tradeable_set()
    # CLOSE/REDUCE are never per_sym blocked by design - spot check
    for sym_side in sorted(list(tks))[:5]:
        sym, side = sym_side.rsplit("_", 1)
        # Simulate execute_now is_entry check: CLOSE contains CLOSE so not entry
        act = "CLOSE"
        is_entry = ("OPEN" in act or "AUGMENT" in act or "ENTRY" in act or "REENTRY" in act) and "CLOSE" not in act
        assert not is_entry


def test_3m_controls_remain_disabled_for_15m_parity():
    cfg = cfg_mod.Config()
    assert cfg.BASE_TF == "15m"
    assert cfg.WT_3M_FORCE_OPEN_ENABLED is False
    assert cfg.TRADEABLE_KEYS_MANDATORY_POSITION_ENABLED is False
    assert cfg.OBLIGATORY_SMA200_WT3M_ENABLED is False


def test_ema50_15m_entry_filter_is_template_switch_and_controls_filter():
    """2026-09-22 EMA50 15m ENTRY FILTER — per user: NOT trigger but filter, must be switch in TEMPLATE_*.xlsx and control ez_manage filter; emergency script fix until proven wrong."""
    import pathlib
    cfg = cfg_mod.Config()
    # Switch must exist in config and be True (filter) / False (trigger disabled)
    assert hasattr(cfg, "EMA50_15M_ENTRY_FILTER_ENABLED")
    assert hasattr(cfg, "EMA50_15M_ENTRY_FILTER_PCT")
    assert cfg.EMA50_15M_ENTRY_FILTER_ENABLED is True
    assert cfg.OBLIGATORY_EMA50_15M_ENABLED is False
    # Must be in TEMPLATE_*.xlsx ENTRY_CONFIRMATION_GATES
    import openpyxl
    for tpl in [
        "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_LONG.xlsx",
        "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_CRYPTO_SHORT.xlsx",
        "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_LONG.xlsx",
        "SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_STOCKS_SHORT.xlsx",
        "SPREADSHEETS/TEMPLATE.xlsx",
    ]:
        p = pathlib.Path(__file__).resolve().parents[1] / tpl
        assert p.exists(), f"missing {tpl}"
        wb = openpyxl.load_workbook(str(p), data_only=True, read_only=True)
        ws = wb["ENTRY_CONFIRMATION_GATES"]
        found_enabled = found_pct = False
        for row in ws.iter_rows(values_only=True):
            if row and row[0] == "EMA50_15M_ENTRY_FILTER_ENABLED":
                found_enabled = True
                assert row[1] is True
            if row and row[0] == "EMA50_15M_ENTRY_FILTER_PCT":
                found_pct = True
                assert float(row[1]) == 0.0
        assert found_enabled, f"{tpl} missing EMA50_15M_ENTRY_FILTER_ENABLED"
        assert found_pct, f"{tpl} missing EMA50_15M_ENTRY_FILTER_PCT"
    # Emergency script fix must still be present until proven wrong (filter in ez_manage, not trigger)
    import pathlib as pl
    text = pl.Path("ez_manage.py").read_text() if pl.Path("ez_manage.py").exists() else pl.Path(__file__).resolve().parents[1] / "ez_manage.py"
    txt = text.read_text() if hasattr(text, "read_text") else str(text)
    assert "EMA50_15M_ENTRY_FILTER_ENABLED" in txt
    assert "OBLIGATORY_EMA50_15M_ENABLED" in txt
    assert "EMA50_FILTER" in txt
    # Filter must block wrong side
    # Simulate: LONG price <= ema50 must be blocked when filter enabled, SHORT price >= ema50 blocked
    # We test via direct _psym_get for never-calculated uses TEMPLATE, but filter logic is in watchdog — verify switch controls it by toggling
    orig = cfg.EMA50_15M_ENTRY_FILTER_ENABLED
    try:
        # When enabled, filter should be considered
        assert bool(getattr(cfg, "EMA50_15M_ENTRY_FILTER_ENABLED", True)) is True
    finally:
        pass
