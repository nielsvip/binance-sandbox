"""OVERSIZE_TRIM (USER 2026-10-17): trim oversized PLATFORM positions back to the cap only at a LOSS; manual quantity exempt."""
import types
from pathlib import Path

import oversize_trim as OT

ROOT = Path(__file__).resolve().parents[1]
C = types.SimpleNamespace(OVERSIZE_TRIM_ENABLED=True, OVERSIZE_TRIM_TOLERANCE_PCT=10.0)


def test_loss_only_never_gain():
    q, why = OT.decide(C, True, 10.0, 10.0, 100.0, 99.0, 500.0)  # notional 990 vs cap 500, at -1 %
    assert abs(q - (990.0 - 500.0) / 99.0) < 1e-9
    q, why = OT.decide(C, True, 10.0, 10.0, 100.0, 101.0, 500.0)  # at a gain
    assert q == 0 and "gain" in why
    q, _ = OT.decide(C, False, 10.0, 10.0, 100.0, 101.0, 500.0)   # short at a loss
    assert q > 0


def test_tolerance_and_off():
    assert OT.decide(C, True, 5.4, 5.4, 100.0, 99.0, 500.0)[0] == 0          # 534.6 <= 500 x 1.10
    assert OT.decide(types.SimpleNamespace(OVERSIZE_TRIM_ENABLED=False), True, 10, 10, 100, 99, 500)[0] == 0


def test_manual_quantity_never_trimmed():
    q, why = OT.decide(C, True, 10.0, 0.0, 100.0, 99.0, 500.0)
    assert q == 0 and "manual" in why
    q, _ = OT.decide(C, True, 10.0, 2.0, 100.0, 99.0, 500.0)    # mixed: only the platform's 2 units may go
    assert q == 2.0


def test_whole_shares_and_min_notional():
    q, _ = OT.decide(C, True, 10.0, 10.0, 100.0, 99.0, 500.0, whole_shares=True)
    assert q == 4.0
    assert OT.decide(C, True, 10.0, 10.0, 100.0, 99.0, 500.0, min_notional=1000.0)[0] == 0


def test_platform_qty_binance_and_tradier():
    t0 = 1_700_000_000.0
    rows = [
        {"side": "BUY", "position_side": "LONG", "executed_qty": 5, "submitted_at": t0 - 5000},  # previous lifecycle
        {"side": "BUY", "position_side": "LONG", "executed_qty": 3, "submitted_at": t0 - 60},     # open (2 min slack)
        {"side": "BUY", "position_side": "LONG", "executed_qty": 2, "submitted_at": t0 + 600},    # augment
        {"side": "SELL", "position_side": "LONG", "executed_qty": 1, "submitted_at": t0 + 900},   # reduce
        {"side": "SELL", "position_side": "SHORT", "executed_qty": 9, "submitted_at": t0 + 100},  # other side
    ]
    assert OT.platform_qty(rows, True, "LONG", t0, "binance") == 4.0
    assert OT.platform_qty(rows, True, "LONG", None, "binance") == 0.0
    tr = [{"side": "sell_short", "position_side": "", "executed_qty": 10, "submitted_at": t0},
          {"side": "buy_to_cover", "position_side": "", "executed_qty": 4, "submitted_at": t0 + 60},
          {"side": "buy", "position_side": "", "executed_qty": 7, "submitted_at": t0 - 9000}]
    assert OT.platform_qty(tr, False, "SHORT", t0, "tradier") == 6.0
    assert OT.platform_qty(tr, True, "LONG", t0, "tradier") == 0.0


def test_wired_both_venues_and_gates():
    ez = (ROOT / "ez_manage.py").read_text()
    tr = (ROOT / "tradier_manage.py").read_text()
    assert "await _pp_oversize_trim(trade_manager, account_key, position_key)" in ez
    assert '"OVERSIZE_TRIM_LOSS" in reason_upper' in ez  # no-loss gate bypass
    assert "await _tr_oversize_trim(account_key, position_key, symbol, position_side, order_queue, trade_manager, position, current_price)" in tr
    from vec_paths.vec_parity_gate import is_vec_achievable
    assert is_vec_achievable("OVERSIZE_TRIM_LOSS_cap120")
    import per_sym_store as S
    assert "OVERSIZE_TRIM_ENABLED" in S.LIVE_AUTHORITY_KNOBS
