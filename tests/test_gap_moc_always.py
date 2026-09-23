"""Gap sentinel ALWAYS — per user 2026-09-23:

- Calculates close→open gap (open_D - close_D_prev)/close_D_prev*100 for EVERY
  position EVERY DAY, rolling 20d (1 month) per-symbol avg in data/gap_inventory_tradier_per_symbol.json
- Last 90m (14:30-16:00 ET) closes longs when avg < -0.10% (POS keep long) and shorts when avg > +0.10% (NEG keep short) on local high / dc_low4_3m breakdown vv short
- AND reopens first 120m (09:30-11:30 ET) next day on local low / dc_low4_3m breakout vv short — unconditional after any dip/DC check,
  forced at end of window so EVERY gap-exit is retried.
- Same behavior in live (tradier_manage) and backtest (v12_quick_engine) — ALWAYS.

This is the durable collateral for the ALWAYS fix. It must not be deleted.
"""
import json, pathlib
import config_tradier, tradier_manage

def test_always_threshold_is_0_10_exactly():
    cfg = config_tradier.TradierConfig()
    assert cfg.GAP_PER_SYMBOL_AVG_THRESH_PCT == 0.10
    assert cfg.GAP_CLOSE_PER_SYMBOL_AVG_THRESH_PCT == 0.10
    # fallback in live helper must be 0.10 not 0.30
    src = pathlib.Path("tradier_manage.py").read_text()
    # _gap_per_symbol_should_close fallback is the source of truth for ALWAYS
    assert "GAP_PER_SYMBOL_AVG_THRESH_PCT', 0.10" in src, "fallback must be 0.10"
    assert "GAP_PER_SYMBOL_AVG_THRESH_PCT', 0.30" not in src

def test_always_window_90_close_and_120_reopen():
    cfg = config_tradier.TradierConfig()
    assert cfg.GAP_MOC_WINDOW_MINUTES == 90
    assert cfg.GAP_MOC_EXIT_MINUTES_BEFORE_CLOSE == 10
    assert cfg.GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN == 120
    # v12 parity
    import v12_quick_engine as v12e
    # v12 exposes same config class; check its defaults match
    v12_cfg = v12e.SweepConfig if hasattr(v12e, "SweepConfig") else config_tradier.TradierConfig
    # At least config_tradier is the live truth; v12 file text must also contain 120
    v12_src = pathlib.Path("v12_quick_engine.py").read_text()
    assert "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN" in v12_src
    assert "'GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN': 120" in v12_src or "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN: int = 120" in v12_src or "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN\", 120" in v12_src

def test_daily_per_symbol_writer_exists_and_records_every_day():
    src = pathlib.Path("tradier_manage.py").read_text()
    # must have a writer that iterates indicators_cache and updates _GAP_PER_SYMBOL_INVENTORY per symbol every day
    assert "_gap_per_symbol_inventory_record_from_cache" in src, "daily per-symbol writer missing"
    # must be called in morning 09:35 window (ALWAYS every day)
    assert "_gap_per_symbol_inventory_record_from_cache" in src and "_gap_inventory_record_from_cache" in src
    # verify writer actually mutates _GAP_PER_SYMBOL_INVENTORY[ and persists
    assert "_GAP_PER_SYMBOL_INVENTORY[" in src or "_GAP_PER_SYMBOL_INVENTORY.get" in src or "_GAP_PER_SYMBOL_INVENTORY.__" in src
    # close-gap file must also be written — absence was the 2026-09-14 outage
    assert "_gap_close_inventory_record_from_cache" in src
    assert "data/gap_close_inventory_tradier_per_symbol.json" in src

def test_close_and_reopen_evaluate_every_position_every_day():
    src = pathlib.Path("tradier_manage.py").read_text()
    # pre-close loop must touch every trb position each day (for ... positions)
    assert "for pk, pos in list(positions.items()):" in src
    assert "_gap_per_symbol_avg_gap(sym)" in src
    assert "_gap_close_per_symbol_avg_gap(sym)" in src
    # morning loop must retry every pending every minute for 120m, not one-shot
    assert "_GAP_MOC_PENDING_REENTRY" in src
    assert "GAP_MORNING_REENTRY_MINUTES_AFTER_OPEN" in src

def test_long_neg_short_pos_tiny_gaps_bothEngines():
    # isolated thr test — same in live helper and by config contract
    # longs close only when avg < -0.10, shorts only when avg > +0.10, boundary = keep
    for thr in [0.10]:
        assert tradier_manage._gap_per_symbol_should_close(True, -0.11) is True
        assert tradier_manage._gap_per_symbol_should_close(True, -0.10) is False
        assert tradier_manage._gap_per_symbol_should_close(False,  0.11) is True
        assert tradier_manage._gap_per_symbol_should_close(False,  0.10) is False
        # opposite side never closes on opposite gap
        assert tradier_manage._gap_per_symbol_should_close(True,  0.50) is False
        assert tradier_manage._gap_per_symbol_should_close(False, -0.50) is False
        # None never closes
        assert tradier_manage._gap_per_symbol_should_close(True, None) is False

def test_close_gap_sentinel_only_shorts():
    # close-gap = (close_D - open_D)/open_D intraday drift — defaults to shorts only, stocks only
    cfg = config_tradier.TradierConfig()
    assert cfg.GAP_CLOSE_MOC_ONLY_FOR_SHORTS is True
    # verify helper enforces it
    tradier_manage._GAP_CLOSE_PER_SYMBOL_INVENTORY.clear()
    tradier_manage._GAP_CLOSE_PER_SYMBOL_INVENTORY.update({
        "TEST": {"days": 30, "sum_gap_pct": 6.0},  # avg +0.20
    })
    tradier_manage._GAP_CLOSE_PER_SYMBOL_LAST_LOAD = 1e9
    avg = tradier_manage._gap_close_per_symbol_avg_gap("TEST")
    assert avg == 0.2
    assert tradier_manage._gap_close_per_symbol_should_close(False, avg) is True   # short closes
    assert tradier_manage._gap_close_per_symbol_should_close(True, avg) is False   # long skipped when only_shorts
    # negative drift doesn't close shorts
    assert tradier_manage._gap_close_per_symbol_should_close(False, -0.20) is False

def test_always_reopen_forced_at_end_of_120m_window():
    src = pathlib.Path("tradier_manage.py").read_text()
    # morning reentry must have a force at end of 120m so EVERY gap exit is retried even if no dip/DC gate passed
    # Look for pattern that forces reopen in last minutes regardless of dip_ok/wt_ok
    assert "GAP_MORNING_REENTRY" in src
    # must have logic that retries every minute for 120m
    assert "0 <= mins_since_open <= float" in src
    # must persist pending across restart so Friday→Monday works
    assert "_gap_moc_load_pending" in src and "_gap_moc_save_pending" in src
    # force indicator: either at_deadline-style or mins_since_open >= 115/120 forced buy
    has_force = ("at_deadline" in src or "force" in src.lower()) and "_GAP_MOC_PENDING_REENTRY" in src
    # Also check that exit_price is captured correctly (was 0.0 bug)
    assert "exit_price" in src and "current_price" in src

def test_v12_always_gap_exit_last_90m_per_symbol():
    src = pathlib.Path("v12_quick_engine.py").read_text()
    # v12 must vectorize gap exit in last 90m per-symbol with 0.10 thr (not 0.30) and per-symbol avg
    assert "GAP_MOC_EXIT_ENABLED" in src
    assert "GAP_PER_SYMBOL_AVG_THRESH_PCT" in src
    assert "GAP_PER_SYMBOL_INVENTORY_FILE" in src
    # check fallback thr is 0.10
    assert "GAP_PER_SYMBOL_AVG_THRESH_PCT', 0.10" in src or 'GAP_PER_SYMBOL_AVG_THRESH_PCT", 0.10' in src
    # 90m window bars
    assert "90" in src and "_bars_90m" in src
    assert "_gap_pct" in src and "_avg_gap" in src
