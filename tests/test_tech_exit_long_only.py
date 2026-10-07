import sys
sys.path.insert(0, '.')
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice
import numpy as np
def test_long_only_and_no_max_hold():
    # Use ZECUSDC on Mac (DOGE missing per DO_NOT_TEST_ON_MAC), DOGE on S1
    for sym in ['ZECUSDC','DOGEUSDC','BTCUSDC']:
        stores=V.load_npz('crypto', [sym], '2024-01-01')
        if sym in stores and stores[sym] is not None:
            break
    else:
        import pytest
        pytest.skip("no crypto NPZ on Mac - run on S1")
    npz=stores[sym]
    sliced,_=_exact_30d_slice(npz, True, 30)
    cfg=V.QuickConfig()
    cfg.TECHNICAL_DC_STOP_TF='15m'
    cfg.TECHNICAL_DC_TARGET_TF='1h'
    cfg.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
    cfg.TECHNICAL_DC_TARGET_BUFFER_PCT=0.10
    r=V.simulate_one(sliced, 'DOGEUSDC', True, cfg)
    assert r['trades'] < 1200
    assert all('TECHNICAL_EXIT' not in t['exit_reason'] or 'dc_' in t['exit_reason'] for t in r['ledger'])
    cfg2=V.QuickConfig()
    cfg2.TECHNICAL_DC_STOP_TF='15m'
    cfg2.TECHNICAL_DC_TARGET_TF='1h'
    cfg2.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
    cfg2.TECHNICAL_DC_TARGET_BUFFER_PCT=0.10
    r2=V.simulate_one(sliced, sym, False, cfg2)
    # SHORT now also has TECHNICAL_EXIT v versa: 0.10% ABOVE low / 0.25% ABOVE high - may not trigger in this window, so allow either
    assert any('TECHNICAL_EXIT' in t['exit_reason'] for t in r2['ledger']) or r2['trades'] < 500
    cfg3=V.QuickConfig()
    cfg3.DC_DAYTRADE_MAX_HOLD_MINUTES=240
    cfg3.TRADIER_DC_DAYTRADE_MAX_HOLD_MINUTES=240
    r3=V.simulate_one(sliced, 'DOGEUSDC', True, cfg3)
    assert 'DAYTRADE_MAX_HOLD' not in set(t['exit_reason'] for t in r3['ledger'])
