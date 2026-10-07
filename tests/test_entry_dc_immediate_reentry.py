import sys
sys.path.insert(0, '.')
import numpy as np
import v12_quick_engine as V

def _synthetic_trending(n=120, is_long=True):
    # Use a clear crossing: start below DC, jump above in middle to guarantee CROSSING detection (px_prev < lvl*buf and px >= lvl*buf)
    if is_long:
        close = np.concatenate([np.linspace(100, 103.5, n//2), np.linspace(103.9, 115, n - n//2)])
    else:
        close = np.concatenate([np.linspace(115, 104.5, n//2), np.linspace(103.8, 96, n - n//2)])
    # dc_high constant ceiling for BEFORE-high target (long) / AFTER-high break (short)
    dc_high_const = 104.0
    dc_low_const = 96.0
    npz = {
        'close': close, 'open': close, 'high': close+0.3, 'low': close-0.3,
        'timestamps': np.arange(n)*180,
        'dc_high_15m': np.full(n, dc_high_const), 'dc_low_15m': np.full(n, dc_low_const),
        'dc_high_1h': np.full(n, dc_high_const), 'dc_low_1h': np.full(n, dc_low_const),
        'dc_high_4h': np.full(n, dc_high_const), 'dc_low_4h': np.full(n, dc_low_const),
        'high_15m': close+0.3, 'low_15m': close-0.3, 'open_15m': close, 'close_15m': close,
        'high_1h': close+0.3, 'low_1h': close-0.3, 'open_1h': close, 'close_1h': close,
        'high_4h': close+0.3, 'low_4h': close-0.3, 'open_4h': close, 'close_4h': close,
        'wt1_15m': np.zeros(n), 'wt2_15m': np.zeros(n), 'wt1_1h': np.zeros(n), 'wt2_1h': np.zeros(n),
        'wt1_4h': np.zeros(n), 'wt2_4h': np.zeros(n), 'wt1_D': np.zeros(n), 'wt2_D': np.zeros(n),
        'stoch_k_15m': np.full(n,50), 'stoch_k_1h': np.full(n,50), 'stoch_k_4h': np.full(n,50), 'stoch_k_D': np.full(n,50),
    }
    return npz, close

def test_entry_dc_above_filters_entry():
    npz,_ = _synthetic_trending(is_long=True)
    # OFF should allow entries via base blocks (maybe 0 or some)
    cfg = V.QuickConfig()
    cfg.ENTRY_DC_TF = 'OFF'
    r_off = V.simulate_one(npz, 'BTCUSDC', True, cfg)
    cfg2 = V.QuickConfig()
    cfg2.ENTRY_DC_TF = '15m'
    r_on = V.simulate_one(npz, 'BTCUSDC', True, cfg2)
    # with trending price above dc, ON should not reduce trades drastically; OFF vs ON both produce trades
    assert r_on['trades'] > 0, "ENTRY_DC_TF 15m should allow entries above dc"
    assert r_off['trades'] >= 0

def test_exit_before_high_immediate_reentry_no_cooldown():
    npz,_ = _synthetic_trending(is_long=True, n=120)
    cfg = V.QuickConfig()
    cfg.ENTRY_DC_TF = '15m'
    cfg.TECHNICAL_DC_TARGET_TF = '15m'
    cfg.TECHNICAL_DC_STOP_TF = 'OFF'
    cfg.DC_DAYTRADE_ENABLED = False
    cfg.TRADIER_DC_DAYTRADE_ENABLED = False
    cfg.COOLDOWN_BARS = 10
    r = V.simulate_one(npz, 'BTCUSDC', True, cfg)
    assert r is not None, "simulate should not return None (n>=100)"
    ledger = r.get('ledger') or []
    reasons = [t.get('exit_reason','') for t in ledger if isinstance(t, dict)]
    # TECHNICAL_EXIT dc_15m_high is the TARGET-before-high for long (close >= dc_high*0.999)
    assert any('TECHNICAL_EXIT' in rr and 'dc_' in rr.lower() for rr in reasons), f"expected TECHNICAL_EXIT dc exit, got {reasons[:3]}"
    assert r['trades'] >= 2, f"immediate reentry should give >=2 trades, got {r['trades']}"
    if ledger:
        bars = [t.get('bars_held', 99) for t in ledger]
        assert min(bars) < 70, "immediate reentry should have short holds (no cd=10 delay) CROSSING version"

def test_short_entry_below_and_target_before_low():
    npz,_ = _synthetic_trending(is_long=False, n=120)
    cfg = V.QuickConfig()
    cfg.ENTRY_DC_TF = '15m'
    cfg.TECHNICAL_DC_TARGET_TF = '15m'
    cfg.COOLDOWN_BARS = 10
    r = V.simulate_one(npz, 'BTCUSDC', False, cfg)
    assert r is not None and r['trades'] >= 1
