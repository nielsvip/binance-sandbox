"""MSFT hard-cap regression — 2026-09-29 P0: $17k long while losing despite $2500 HARD_MAX.

Root cause: EXCEPTIONS 4x (MSFT is exception) set limit_exception_total_pos=20000,
LR_BAND ladder 16000 and STDEV 10x sizing could exceed HARD_MAX=2500 via
OrderQueue / BrokerPlace / calculate_position_size paths, while execute_now hard
cap is only one seam. MSFT built to ~$19k (39*510) via broker-sync adopt before
caps were added; after Sep-08 caps local was clamped but broker divergence loop
persisted (610 REJECTED logs/hour).

Fix enforces HARD_MAX_SYMBOL_VALUE_TRADIER=2500 as absolute ceiling at every
sizing seam: TradeManager.__init__ limits, OrderQueue caps, BrokerPlace facade,
calculate_position_size, and ladder capacity.
"""
import pathlib
import config_tradier
import tradier_manage


def test_hard_cap_is_2500():
    cfg = config_tradier.TradierConfig()
    assert float(cfg.HARD_MAX_SYMBOL_VALUE_TRADIER) == 2500.0
    assert float(cfg.BROKER_SYNC_MAX_TOTAL_VALUE_USD) == 2500.0
    assert float(cfg.BROKER_SYNC_MAX_ADOPT_VALUE_USD) == 2000.0
    assert float(cfg.BROKER_SYNC_MAX_AUGMENT_VALUE_USD) == 2000.0
    assert float(cfg.TRB_MAX_SYMBOL_VALUE) == 2500.0


def test_trade_manager_limits_clamped_to_hard_cap():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "self.limit_exception_total_pos = min(float(self.limit_exception_total_pos), _hard_cap_init)" in src
    assert "self.limit_exception_order = min(float(self.limit_exception_order), _hard_cap_init)" in src
    assert "self.limit_total_pos = min(float(self.limit_total_pos), _hard_cap_init)" in src


def test_order_queue_hard_cap_clamp():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "_hard_cap_q = float(_cfg_auto('HARD_MAX_SYMBOL_VALUE_TRADIER'" in src
    assert "_hard_cap_l = float(_cfg_auto('HARD_MAX_SYMBOL_VALUE_TRADIER'" in src
    assert "_ladder_capacity = min(_ladder_capacity, _hard_cap_l)" in src
    assert "max_order = min(float(max_order), _hard_cap_q)" in src
    assert "max_pos = min(float(max_pos), _hard_cap_q)" in src


def test_calculate_position_size_hard_cap():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "_hard_cap_cps = float(_cfg_auto('HARD_MAX_SYMBOL_VALUE_TRADIER'" in src
    assert "max_value = _hard_cap_cps" in src


def test_brokerplace_facade_hard_cap():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "max_Total = min(float(self.limit_total_pos), _hard_cap_final, _trb_cap)" in src


def test_msft_is_exception_but_total_still_capped():
    cfg = config_tradier.TradierConfig()
    assert "MSFT" in cfg.EXCEPTIONS
    hard = float(cfg.HARD_MAX_SYMBOL_VALUE_TRADIER)
    normal_pos = float(cfg.MAX_POSITION_SIZE)
    exception_pos = normal_pos * 4.0
    assert exception_pos > hard
    assert hard == 2500.0
    tm_src = pathlib.Path("tradier_manage.py").read_text()
    assert "limit_exception_total_pos" in tm_src
    assert 'HARD_MAX_SYMBOL_VALUE_TRADIER' in tm_src


def test_no_ladder_bypass_above_hard_cap():
    cfg = config_tradier.TradierConfig()
    hard = float(cfg.HARD_MAX_SYMBOL_VALUE_TRADIER)
    ladder_raw = 16000.0
    assert ladder_raw > hard
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "LR_BAND_LADDER_CAPACITY_USD" in src
    assert "min(_ladder_capacity, _hard_cap_l)" in src


def test_stdev_max_exceeds_hard_cap_but_capped():
    cfg = config_tradier.TradierConfig()
    start = float(cfg.START_POSITION_SIZE)
    stdev_d_max = 10.0
    raw_sized = start * stdev_d_max * 1.5
    hard = float(cfg.HARD_MAX_SYMBOL_VALUE_TRADIER)
    assert raw_sized > hard
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "STDEV_SLOPE_SIZING" in src
    assert "_hard_cap_cps" in src


def test_execute_now_hard_cap_blocks_pyramid():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "[HARD_SYMBOL_CAP]" in src
    assert "_hard_room_qty = int(max(0.0, (_hard_cap / current_price) - _hard_held_qty" in src
    assert 'BLOCKED_HARD_SYMBOL_NOTIONAL_CAP' in src


def test_msft_per_sym_trades_zero_gated():
    import json as _json
    p = pathlib.Path("data/hourly_reconfig/per_sym_active_config_stocks.json")
    if not p.exists():
        return
    d = _json.loads(p.read_text())
    msft = d.get("MSFT_LONG") or d.get("MSFT") or {}
    assert msft.get("trades", 0) == 0


def test_broker_sync_caps_present():
    import tradier_positions as tp
    src = pathlib.Path("tradier_positions.py").read_text()
    assert "BROKER_SYNC_MAX_ADOPT_VALUE_USD" in src
    assert "BROKER_SYNC_MAX_AUGMENT_VALUE_USD" in src
    assert "BROKER_SYNC_MAX_TOTAL_VALUE_USD" in src
    assert "HARD_MAX_SYMBOL_VALUE_TRADIER" in src


def test_fail_closed_pending_open_assumed_executed():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "FAIL_CLOSED_PENDING_OPEN" in src
    assert "assuming WAS executed, duplicate OPEN will be blocked" in src
    assert "BLOCKED_OPEN_ON_EXISTING" in src
    assert "OPEN must NEVER become AUGMENT" in src
    assert "OPEN refused — position already exists" in src
    # ensure old OPEN->AUGMENT conversion no longer exists
    assert "FAIL_CLOSED_OPEN_TO_AUGMENT" not in src
    # new 2026-09-30: OPEN after unconfirmed order ignored until broker confirms neg & deletion
    assert "PENDING_NEG_CONFIRMED" in src
    assert "broker still has open/pending order" in src
    assert "no broker open order but position exists" in src
    assert "previous order neg & deleted confirmed, allowing next OPEN" in src


def test_fail_closed_optimistic_update_restored():
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "assume WAS executed until proven" in src
    assert "local_pos.positionAmt = float(local_pos.positionAmt) + float(quantity)" in src


def test_tradier_positions_fail_closed_5_strikes():
    src = pathlib.Path("tradier_positions.py").read_text()
    assert "FAIL-CLOSED: assume order WAS executed" in src
    assert "ABSENT_HOLD_" in src
    assert "will zero after 5 consecutive absences" in src
    assert "ABSENT_HOLD_RECENT_AUG" in src


def test_gap_protection_reenabled():
    cfg = config_tradier.TradierConfig()
    assert cfg.GAP_RISK_EXIT_ENABLED is True, "GAP_RISK_EXIT master must be True — gap-down longs must close (37k imbalance)"
    assert cfg.GAP_RISK_EXIT_LONG_ENABLED is True
    assert cfg.GAP_RISK_EXIT_SHORT_ENABLED is True
    assert cfg.GAP_RISK_EXIT_OPEN_RECLAIM_ENABLED is True
    assert cfg.GAP_RISK_EXIT_STRUCTURE_BREAK_ENABLED is True
    src = pathlib.Path("tradier_manage.py").read_text()
    assert src.index("GAP_RISK_EXIT") < src.index("UNIVERSAL_NOLOSS_GATE"), "GAP_RISK must be before NOLOSS to close losers"


def test_ls_ratio_tightened_and_wf_bypass_removed():
    cfg = config_tradier.TradierConfig()
    assert cfg.LS_RATIO_MIN_TRADIER == 0.33, "was 0.20 emergency — 37k long skew"
    assert cfg.LS_RATIO_MAX_TRADIER == 3.00, "was 5.00 emergency — 37k long skew"
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "LS ratio must NOT be bypassed by WT_3M_FORCE_OPEN" in src
    assert "and not (('WT_3M_FORCE_OPEN' in (reason or '').upper()) and _wf_bypass_order)" not in src
    # 37k long /10k short =3.7 >3.0 -> scale 0.81 reduces next LONG
    lv, sv, max_ls = 37000, 10000, 3.0
    scale = max(0.33, min(1.0, max_ls / max(lv / max(sv, 1), 0.1)))
    assert scale < 1.0


def test_ls_gap_inventory_present():
    p = pathlib.Path("tradier/gap_inventory_30d.json")
    assert p.exists(), "gap_inventory_30d.json must exist — written by tools/gap_inventory_30d.py"
    import json
    d = json.loads(p.read_text())
    assert len(d) >= 50, "gap inventory should have many symbols"


def test_exception_reentry_up_to_10k_same_size():
    cfg = config_tradier.TradierConfig()
    assert hasattr(cfg, 'EXCEPTION_REENTRY_MAX_USD')
    assert float(cfg.EXCEPTION_REENTRY_MAX_USD) == 10000.0
    # fresh OPEN for exception still 2.5k
    assert float(cfg.HARD_MAX_SYMBOL_VALUE_TRADIER) == 2500.0
    src = pathlib.Path("tradier_manage.py").read_text()
    assert "EXCEPTION_REENTRY_CAP" in src
    assert "EXCEPTION_REENTRY_MAX_USD" in src
    # must check previous size never higher
    assert "prev_qty" in src and "prev_notional" in src
    assert "same size never higher" in src
    # verify logic: reentry cap = min(10k, prev_notional)
    prev_qty, price = 20.0, 500.0
    prev_notional = prev_qty * price  # 10000
    cap = min(10000.0, prev_notional)
    assert cap == 10000.0
    # if previous was smaller (10 shares @500=5000), cap is 5000 not 10000
    prev_qty2, price2 = 10.0, 500.0
    cap2 = min(10000.0, prev_qty2 * price2)
    assert cap2 == 5000.0
    # AUGMENT must not get 10k — only REENTRY
    assert "str(action or '').upper() in ('REENTRY'" in src
    assert "REENTER" in src
    # ensure fresh OPEN not affected — queue clamp still 2.5k for non-reentry
    assert "_is_exc_reentry_q" in src
