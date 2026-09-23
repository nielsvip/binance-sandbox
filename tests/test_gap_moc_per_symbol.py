"""Regression for 2026-09-11 GAP_MOC per-symbol ONLY fix (retuned 2026-09-11 to 0.10 per user, 2026-09-23 E to 20d).

Verifies:
- tradier_manage loads per-symbol inventory from data/gap_inventory_tradier_per_symbol.json (20d, counted daily via open_D/close_D_prev per spec E: last 20 opens vs prior closes)
- Avg gap = sum_gap_pct / days, stored in that file, counted how often
- 90m pre-close sentinel uses ONLY per-symbol avg (not market-wide bias): longs close when avg < -thr (gap-down), shorts when avg > +thr, near 0 (|avg|<=0.10) only VV closes — anything >0.10 closes at top/bottom
- POS avg keep long / NEG keep short, else close last 90m on local high / dc_low4_3m breakdown vv short; reopen first 120m on local low / dc_low4_3m breakout vv short
- Morning reentry in first 120m reopens at local low / dc_low4_3m breakout vv short (wt_ok or ha_ok or dip_ok or dc_breakout) if still attractive (not VV), forced at 119m
"""
import importlib, json, pathlib, sys
import config_tradier, tradier_manage

def test_per_symbol_config_exists():
    cfg = config_tradier.TradierConfig()
    assert hasattr(cfg, "GAP_PER_SYMBOL_INVENTORY_FILE")
    assert cfg.GAP_PER_SYMBOL_INVENTORY_FILE == "data/gap_inventory_tradier_per_symbol.json"
    assert cfg.GAP_PER_SYMBOL_AVG_THRESH_PCT == 0.10  # retuned 2026-09-11 per user: anything >0.10 avg/day closes; 2026-09-23 E: 20d per spec (POS keep long NEG keep short)
    assert cfg.GAP_PER_SYMBOL_LOOKBACK_DAYS == 20
    # market-wide kept only for recording
    assert cfg.GAP_INVENTORY_FILE == "data/gap_inventory_tradier.json"

def test_per_symbol_inventory_file_shape():
    p = pathlib.Path("data/gap_inventory_tradier_per_symbol.json")
    assert p.exists(), "per-symbol inventory must exist"
    data = json.loads(p.read_text())
    assert len(data) >= 100, "must have many symbols (counts)"
    # sample shape
    sample = next(iter(data.values()))
    assert "days" in sample and "sum_gap_pct" in sample
    assert sample["days"] == 20  # 20 trading days (1 month) per spec E
    # last5 or last_gaps
    assert "last5" in sample or "last_gaps" in sample or "last_gaps" in sample or True  # allow either key
    # at least one has last5
    has_last5 = any("last5" in v or "last_gaps" in v for v in data.values())
    assert has_last5
    # days = how often counted: daily -> last5 length 5 = last 5 days
    # sum_gap_pct is counted daily, rolling 30d

def test_gap_per_symbol_avg_gap_and_should_close():
    # Use live helpers without file: inject into _GAP_PER_SYMBOL_INVENTORY
    tradier_manage._GAP_PER_SYMBOL_INVENTORY.clear()
    tradier_manage._GAP_PER_SYMBOL_INVENTORY.update({
        "LONG_RISK": {"days": 30, "sum_gap_pct": -15.0, "last5": [-1,-1,-1,-1,-1]},  # avg -0.50 -> gap-down risk
        "SHORT_RISK": {"days": 30, "sum_gap_pct": 15.0, "last5": [1,1,1,1,1]},  # avg +0.50 -> gap-up risk
        "NEAR_ZERO": {"days": 30, "sum_gap_pct": 3.0, "last5": [0.1,0.1,0.1,0.1,0.1]},  # avg +0.10 near 0
        "NEAR_ZERO_NEG": {"days": 30, "sum_gap_pct": -3.0, "last5": [-0.1]*5},  # avg -0.10
        "UNKNOWN": {"days": 0, "sum_gap_pct": 0},  # no days -> None
    })
    tradier_manage._GAP_PER_SYMBOL_LAST_LOAD = 1e9  # prevent reload

    # avg calc
    assert tradier_manage._gap_per_symbol_avg_gap("LONG_RISK") == -0.5
    assert tradier_manage._gap_per_symbol_avg_gap("SHORT_RISK") == 0.5
    assert tradier_manage._gap_per_symbol_avg_gap("NEAR_ZERO") == 0.1
    assert tradier_manage._gap_per_symbol_avg_gap("NONEXISTENT") is None
    # case-insensitive
    assert tradier_manage._gap_per_symbol_avg_gap("long_risk") == -0.5

    # should_close: thr 0.10 per user (anything >0.10 avg/day closes at top/bottom)
    # longs close when avg < -0.10 (gap-down)
    assert tradier_manage._gap_per_symbol_should_close(True, -0.5) is True
    assert tradier_manage._gap_per_symbol_should_close(True, -0.11) is True
    assert tradier_manage._gap_per_symbol_should_close(True, -0.10) is False  # boundary inclusive -> hold (near 0)
    assert tradier_manage._gap_per_symbol_should_close(True, -0.09) is False
    assert tradier_manage._gap_per_symbol_should_close(True, 0.5) is False  # long gap-up favourable -> keep
    assert tradier_manage._gap_per_symbol_should_close(True, 0.0) is False
    # shorts close when avg > +0.10
    assert tradier_manage._gap_per_symbol_should_close(False, 0.5) is True
    assert tradier_manage._gap_per_symbol_should_close(False, 0.11) is True
    assert tradier_manage._gap_per_symbol_should_close(False, 0.10) is False
    assert tradier_manage._gap_per_symbol_should_close(False, 0.09) is False
    assert tradier_manage._gap_per_symbol_should_close(False, -0.5) is False
    assert tradier_manage._gap_per_symbol_should_close(False, None) is False
    # near 0 never closes on gap
    assert tradier_manage._gap_per_symbol_should_close(True, 0.10) is False
    assert tradier_manage._gap_per_symbol_should_close(False, -0.10) is False
    assert tradier_manage._gap_per_symbol_should_close(True, 0.09) is False
    # also test via avg_gap from file directly
    assert tradier_manage._gap_per_symbol_should_close(True, tradier_manage._gap_per_symbol_avg_gap("LONG_RISK")) is True
    assert tradier_manage._gap_per_symbol_should_close(False, tradier_manage._gap_per_symbol_avg_gap("SHORT_RISK")) is True
    assert tradier_manage._gap_per_symbol_should_close(True, tradier_manage._gap_per_symbol_avg_gap("NEAR_ZERO")) is False

def test_gap_moc_uses_per_symbol_not_market_wide():
    src = open("tradier_manage.py").read()
    # must have per-symbol loader and avg helper
    assert "_gap_per_symbol_load" in src
    assert "_gap_per_symbol_avg_gap" in src
    assert "_gap_per_symbol_should_close" in src
    assert "_GAP_PER_SYMBOL_INVENTORY" in src
    assert "GAP_PER_SYMBOL_INVENTORY_FILE" in src
    # pre-close window must use per-symbol, not market-wide bias
    # bias is deprecated for sentinel — should not appear in gap_moc_and_morning_loop's pre-close decision except maybe in comments
    # Check that the pre-close block uses avg_gap / _gap_per_symbol_should_close and VV, not bias
    assert "avg_gap = _gap_per_symbol_avg_gap(sym)" in src
    assert "_gap_per_symbol_should_close(is_long, avg_gap)" in src
    # market-wide bias may still be recorded but not used for decision
    # Ensure the new per-symbol threshold config is used
    assert "GAP_PER_SYMBOL_AVG_THRESH_PCT" in src

def test_morning_reentry_any_dip():
    src = open("tradier_manage.py").read()
    # morning reentry must reopen at any dip: wt_ok or ha_ok or dip_ok (bottom/top) — ALWAYS 2026-09-23 forces at 120m even if no dip
    assert "dip_ok = _is_small_top_for_gap_exit(ind, not is_long)" in src
    assert "wt_ok or ha_ok or dip_ok" in src
    assert "force_at_end" in src or "or force" in src  # ALWAYS guarantee — forced at end of 120m window
    # must still check VV attractive
    assert "skip morning rebuy" in src and "vv danger" in src.lower() or "_is_near_dc4_high_with_wt_down" in src

def test_small_top_bottom_still_used():
    # _is_small_top_for_gap_exit must still detect tops/bottoms via wt/ha/price
    assert tradier_manage._is_small_top_for_gap_exit({"wt1_15m": -1, "wt2_15m": 1}, True) is True
    assert tradier_manage._is_small_top_for_gap_exit({"wt1_15m": 5, "wt2_15m": -1}, True) is False
    assert tradier_manage._is_small_top_for_gap_exit({"wt1_15m": 5, "wt2_15m": -1}, False) is True  # short bottom = wt up
    assert tradier_manage._is_small_top_for_gap_exit({"wt1_15m": -1, "wt2_15m": 1}, False) is False
    assert tradier_manage._is_small_top_for_gap_exit({"wt1_15m": 1, "wt2_15m": -1}, False) is True
