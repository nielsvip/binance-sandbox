"""Exit reason must include DC level when DAYTRADE/TECHNICAL DC variant fires — user mandate 2026-09-26."""
import numpy as np
import v12_quick_engine as v12

def _npz_for_dc(n=300, price=100.0, dc_low=95.0, dc_high=105.0, tf="15m"):
    ts = np.arange(n, dtype=float)
    close = np.full(n, price, dtype=float)
    # trigger stop on bar 150: price drops to dc_low * (1 - 0.003) < stop threshold 0.25%
    # for LONG, dc_low 95, buf 0.25% => stop at 94.7625, so price 94.5 triggers
    close[150] = 94.5 if price == 100 else 94.5
    npz = {
        'timestamps': ts,
        'close': close,
        'close_15m': close,
        'open_15m': close,
        'high_15m': close + 1,
        'low_15m': close - 1,
        f'dc_low_{tf}': np.full(n, dc_low, dtype=float),
        f'dc_high_{tf}': np.full(n, dc_high, dtype=float),
        'dc_low_4h': np.full(n, 90.0),
        'dc_high_4h': np.full(n, 110.0),
        'dc_position_15m': np.full(n, 0.5),
        'wt1_15m': np.full(n, 0.0),
        'wt2_15m': np.full(n, 0.0),
    }
    return npz

def test_daytrade_stop_exit_reason_includes_dc():
    npz = _npz_for_dc(tf="15m")
    cfg = v12.QuickConfig()
    cfg.MODE = 'crypto'
    cfg.DC_DAYTRADE_ENABLED = True
    cfg.TRADIER_DC_DAYTRADE_ENABLED = False
    cfg.DAYTRADE_DC_STOP_TF = "15m"
    cfg.DAYTRADE_DC_STOP_BUFFER_PCT = 0.25
    cfg.DAYTRADE_DC_TARGET_TF = "OFF"
    cfg.TECHNICAL_DC_STOP_TF = "OFF"
    cfg.TECHNICAL_DC_TARGET_TF = "OFF"
    # ensure entry fires so we have a position to stop
    cfg.WT_15M_BOUNCE_OPEN_ENABLED = True
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.MIN_HOLD_BARS_BEFORE_EXIT = 0
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    cfg.NOLOSS_ENABLED = False
    r = v12.simulate_one(npz, "TEST", True, cfg)
    assert r is not None and r['trades'] >= 1
    reasons = [t['exit_reason'] for t in r['ledger']]
    # at least one DAYTRADE_STOP with dc_15m_low
    assert any("DAYTRADE_STOP dc_15m_low" in rr for rr in reasons), f"expected DAYTRADE_STOP dc_15m_low in {reasons}"
    assert any("dc_15m_low" in rr for rr in reasons)

def test_technical_exit_reason_includes_dc():
    npz = _npz_for_dc(tf="15m")
    cfg = v12.QuickConfig()
    cfg.MODE = 'crypto'
    cfg.DC_DAYTRADE_ENABLED = False
    cfg.TRADIER_DC_DAYTRADE_ENABLED = False
    cfg.TECHNICAL_DC_STOP_TF = "15m"
    cfg.TECHNICAL_DC_STOP_BUFFER_PCT = 0.25
    cfg.TECHNICAL_DC_TARGET_TF = "OFF"
    cfg.DAYTRADE_DC_STOP_TF = "OFF"
    cfg.DAYTRADE_DC_TARGET_TF = "OFF"
    cfg.WT_15M_BOUNCE_OPEN_ENABLED = True
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.MIN_HOLD_BARS_BEFORE_EXIT = 0
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    cfg.NOLOSS_ENABLED = False
    r = v12.simulate_one(npz, "TEST2", True, cfg)
    assert r is not None and r['trades'] >= 1
    reasons = [t['exit_reason'] for t in r['ledger']]
    assert any("TECHNICAL_EXIT dc_15m_low" in rr for rr in reasons), f"expected TECHNICAL_EXIT dc_15m_low in {reasons}"

def test_daytrade_target_exit_reason_includes_dc_high():
    npz = _npz_for_dc(tf="15m")
    # for target, price must go above dc_high * (1 - buf) 105*0.999=104.895, set price 106 at bar 150
    npz['close'][150] = 106.0
    npz['close_15m'][150] = 106.0
    cfg = v12.QuickConfig()
    cfg.MODE = 'crypto'
    cfg.DC_DAYTRADE_ENABLED = True
    cfg.DAYTRADE_DC_TARGET_TF = "15m"
    cfg.DAYTRADE_DC_TARGET_BUFFER_PCT = 0.10
    cfg.DAYTRADE_DC_STOP_TF = "OFF"
    cfg.TECHNICAL_DC_STOP_TF = "OFF"
    cfg.TECHNICAL_DC_TARGET_TF = "OFF"
    cfg.WT_15M_BOUNCE_OPEN_ENABLED = True
    cfg.COOLDOWN_BARS = 0
    cfg.MIN_HOLD_BARS = 0
    cfg.MIN_HOLD_BARS_BEFORE_EXIT = 0
    cfg.STRUCTURAL_EXIT_GATE_ENABLED = False
    cfg.NOLOSS_ENABLED = False
    r = v12.simulate_one(npz, "TEST3", True, cfg)
    assert r is not None and r['trades'] >= 1
    reasons = [t['exit_reason'] for t in r['ledger']]
    assert any("DAYTRADE_TARGET dc_15m_high" in rr for rr in reasons), f"expected DAYTRADE_TARGET dc_15m_high in {reasons}"
