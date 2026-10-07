"""ULTIMATE_DC_4H_HARD_STOP — durable regression for 2026-09-19 mandate.
No trade may be held through dc_low_4h (LONG) / dc_high_4h (SHORT) at any loss.
Gain / MIN_HOLD / NOLOSS / hedge-protect agnostic, HARD_STOP reason."""
import pathlib, openpyxl
import numpy as np
import config, config_tradier
import v12_quick_engine as vq

def _mk(n, close, dl, dh):
    ts = np.arange(1700000000, 1700000000 + n*180, 180)
    return {'close_3m': close, 'close_15m': close, 'close_5m': close, 'close': close,
            'dc_low_4h': dl, 'dc_high_4h': dh,
            'timestamps': ts, 'timestamp_3m': ts, 'timestamp_15m': ts,
            'wt1_3m': np.zeros(n), 'wt2_3m': np.zeros(n), 'wt1_15m': np.zeros(n), 'wt2_15m': np.zeros(n),
            'wt1_1h': np.zeros(n), 'wt2_1h': np.zeros(n), 'wt1_4h': np.zeros(n), 'wt2_4h': np.zeros(n),
            'wt1_D': np.zeros(n), 'wt2_D': np.zeros(n)}

def test_config_flag_enabled():
    assert getattr(config.Config(), 'ULTIMATE_DC_4H_STOP_ENABLED', False) is True
    assert getattr(config_tradier.TradierConfig(), 'ULTIMATE_DC_4H_STOP_ENABLED', False) is True

def test_live_reason_contains_hard_stop():
    for fname in ['ez_manage.py','ez_positions_quick.py','tradier_manage.py']:
        txt = pathlib.Path(fname).read_text()
        assert 'ULTIMATE_DC_4H_HARD_STOP' in txt, f"{fname} missing HARD_STOP"
    txt_ez = pathlib.Path('ez_manage.py').read_text()
    assert 'or "HARD_STOP" in _hpo_reason_up' in txt_ez

def test_v12_long_breach_is_hard_stop():
    n=150
    close=np.linspace(0.0285,0.0239,n)
    dl=np.full(n,0.027); dh=np.full(n,0.031)
    cfg=vq.QuickConfig(); cfg.MODE='crypto'; cfg.BASE_TF='3m'; cfg.START_POSITION_SIZE=500; cfg.COOLDOWN_BARS=0; cfg.MIN_HOLD_BARS=0; cfg.NOLOSS_ENABLED=True; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
    r=vq.simulate_one(_mk(n,close,dl,dh),'T_LONG',True,cfg, force_initial_seed=True)
    reasons=[t['exit_reason'] for t in r['ledger']]
    assert 'ULTIMATE_DC_4H_HARD_STOP' in reasons
    cross=int(np.where(close<=0.027)[0][0])
    first_ult=next(t for t in r['ledger'] if t['exit_reason']=='ULTIMATE_DC_4H_HARD_STOP')
    assert first_ult['bar_exit']==cross

def test_v12_short_breach_is_hard_stop():
    n=150
    close=np.linspace(0.020,0.025,n)
    cfg=vq.QuickConfig(); cfg.MODE='crypto'; cfg.BASE_TF='3m'; cfg.START_POSITION_SIZE=500; cfg.COOLDOWN_BARS=0; cfg.MIN_HOLD_BARS=0; cfg.NOLOSS_ENABLED=True; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
    r=vq.simulate_one(_mk(n,close,np.full(n,0.018),np.full(n,0.022)),'T_SHORT',False,cfg, force_initial_seed=True)
    assert 'ULTIMATE_DC_4H_HARD_STOP' in [t['exit_reason'] for t in r['ledger']]

def test_v12_no_breach_and_missing_dc_hold():
    n=150
    close=np.linspace(0.0285,0.0239,n)
    cfg=vq.QuickConfig(); cfg.MODE='crypto'; cfg.BASE_TF='3m'; cfg.START_POSITION_SIZE=500; cfg.COOLDOWN_BARS=0; cfg.MIN_HOLD_BARS=0; cfg.NOLOSS_ENABLED=True; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
    r=vq.simulate_one(_mk(n,close,np.full(n,0.010),np.full(n,0.05)),'T_NOB',True,cfg, force_initial_seed=True)
    assert 'ULTIMATE_DC_4H_HARD_STOP' not in [t['exit_reason'] for t in r['ledger']]
    r2=vq.simulate_one(_mk(n,close,np.zeros(n),np.zeros(n)),'T_MISS',True,cfg, force_initial_seed=True)
    assert 'ULTIMATE_DC_4H_HARD_STOP' not in [t['exit_reason'] for t in r2['ledger']]

def test_v12_min_hold_and_noloss_bypass():
    n=150
    close=np.linspace(0.0285,0.0239,n)
    dl=np.full(n,0.027); dh=np.full(n,0.031)
    cfg=vq.QuickConfig(); cfg.MODE='crypto'; cfg.BASE_TF='3m'; cfg.START_POSITION_SIZE=500; cfg.COOLDOWN_BARS=0; cfg.MIN_HOLD_BARS=100; cfg.NOLOSS_ENABLED=True; cfg.PROFIT_TARGET_ENABLED=False; cfg.STOP_LOSS_ENABLED=False
    r=vq.simulate_one(_mk(n,close,dl,dh),'T_MH',True,cfg, force_initial_seed=True)
    cross=int(np.where(close<=0.027)[0][0])
    first_ult=next(t for t in r['ledger'] if t['exit_reason']=='ULTIMATE_DC_4H_HARD_STOP')
    assert first_ult['bar_exit']==cross  # breach < MIN_HOLD still closes

def test_template_flags_present():
    for name in ["TEMPLATE.xlsx","TEMPLATE_CRYPTO_LONG.xlsx","TEMPLATE_CRYPTO_SHORT.xlsx","TEMPLATE_STOCKS_LONG.xlsx","TEMPLATE_STOCKS_SHORT.xlsx"]:
        p=pathlib.Path(f"SPREADSHEETS/{name}")
        # read_only=False avoids BadZipFile on concurrently-written large files
        wb=openpyxl.load_workbook(str(p), read_only=False, data_only=True)
        found=False
        for ws in wb.worksheets:
            for row in ws.iter_rows(min_row=1, max_row=ws.max_row):
                for c in row:
                    if c.value and "ULTIMATE_DC_4H_STOP_ENABLED" in str(c.value):
                        found=True; break
                if found: break
            if found: break
        assert found, f"{name} missing ULTIMATE flag"
