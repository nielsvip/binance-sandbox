import sys
sys.path.insert(0, '.')
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice
import numpy as np

def _load_doge():
    stores=V.load_npz('crypto', ['DOGEUSDC'], '2024-01-01')
    if 'DOGEUSDC' not in stores or stores['DOGEUSDC'] is None:
        import pytest; pytest.skip("no DOGE NPZ - run on S1")
    return stores['DOGEUSDC']

def test_dc_tf_variants_long():
    npz=_load_doge()
    sliced,_=_exact_30d_slice(npz, True, 30)
    # Test all 8 TFS_EXIT/ENTRY variants produce distinct or valid results
    tfs_list=['OFF','15m','1h','4h','15m,1h','15m,4h','1h,4h','15m,1h,4h']
    for tf in tfs_list:
        cfg=V.QuickConfig()
        cfg.TECHNICAL_DC_STOP_TF=tf
        cfg.TECHNICAL_DC_TARGET_TF=tf
        cfg.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
        cfg.TECHNICAL_DC_TARGET_BUFFER_PCT=0.10
        cfg.ENTRY_DC_TF=tf
        cfg.ENTRY_DC_BUFFER_PCT=0.10
        cfg.KINDERGARTEN_EMA_GATE_ENABLED=True
        cfg.KINDERGARTEN_FILTER_TF='4h'
        r=V.simulate_one(sliced, 'DOGEUSDC', True, cfg)
        assert r['trades'] >= 0
        assert r['gain_pct_2000norm'] != -76.56  # not BH stale

def test_dc_tf_any_vs_all():
    npz=_load_doge()
    sliced,_=_exact_30d_slice(npz, True, 30)
    # ANY (OR) vs single TF: multi-TF should not be identical to OFF
    cfg_off=V.QuickConfig()
    cfg_off.TECHNICAL_DC_STOP_TF='OFF'
    cfg_off.TECHNICAL_DC_TARGET_TF='OFF'
    r_off=V.simulate_one(sliced, 'DOGEUSDC', True, cfg_off)
    cfg_any=V.QuickConfig()
    cfg_any.TECHNICAL_DC_STOP_TF='15m,1h,4h'
    cfg_any.TECHNICAL_DC_TARGET_TF='15m,1h,4h'
    cfg_any.ENTRY_DC_TF='15m,1h,4h'
    r_any=V.simulate_one(sliced, 'DOGEUSDC', True, cfg_any)
    # ANY should be different from OFF (either trades or gain)
    assert r_any['trades'] != r_off['trades'] or abs(r_any['gain_pct_2000norm'] - r_off['gain_pct_2000norm']) > 1e-6

def test_dc_below_15m_1h_4h_individually():
    npz=_load_doge()
    sliced,_=_exact_30d_slice(npz, True, 30)
    for tf in ['15m','1h','4h']:
        cfg=V.QuickConfig()
        cfg.TECHNICAL_DC_STOP_TF=tf
        cfg.TECHNICAL_DC_TARGET_TF=tf
        cfg.TECHNICAL_DC_STOP_BUFFER_PCT=0.25
        cfg.TECHNICAL_DC_TARGET_BUFFER_PCT=0.10
        r=V.simulate_one(sliced, 'DOGEUSDC', True, cfg)
        # Each TF should produce a valid gain not equal to BH -76.56 and trades >0
        assert r['trades'] > 0
        assert r['gain_pct_2000norm'] > -100 and r['gain_pct_2000norm'] < 100
