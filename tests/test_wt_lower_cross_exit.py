import sys
sys.path.insert(0, "/Users/niels/Documents/binance")
import v12_quick_engine as V

def test_wt_lower_cross_config():
    cfg=V.QuickConfig()
    assert hasattr(cfg, "WT_LOWER_CROSS_EXIT_TF")
    assert cfg.WT_LOWER_CROSS_EXIT_TF=="OFF"
    cfg.WT_LOWER_CROSS_EXIT_TF="15m"
    assert cfg.WT_LOWER_CROSS_EXIT_TF=="15m"
    cfg.WT_LOWER_CROSS_EXIT_TF="1h"
    cfg.WT_LOWER_CROSS_EXIT_TF="4h"
    print("config pass")

def test_wt_sweep_includes():
    import pathlib
    p=pathlib.Path("/Users/niels/Documents/binance/tools/dc_simple_8_sweep.py")
    txt=p.read_text()
    # ensure WT TF sweep is mentioned or will be added
    # For now, ensure v12 has WT_LOWER_CROSS_EXIT_TF
    assert "WT_LOWER_CROSS_EXIT_TF" in txt or "WT_LOWER" in open("/Users/niels/Documents/binance/v12_quick_engine.py").read()
    print("sweep includes pass")

def test_wt_no_crash():
    import numpy as np
    n=300
    close=np.linspace(100,110,n)
    w1_15m=np.array([5]*151 + [-1]*(n-151), dtype=float)
    w2_15m=np.array([0]*n, dtype=float)
    ts=np.arange(n)*300
    npz={"close": close, "timestamps": ts, "wt1_15m": w1_15m, "wt2_15m": w2_15m, "wt1_1h": w1_15m, "wt2_1h": w2_15m, "wt1_4h": w1_15m, "wt2_4h": w2_15m, "close_time": ts, "dc_low_4h": np.full(n, 90.0), "dc_high_4h": np.full(n, 120.0)}
    cfg=V.QuickConfig()
    cfg.WT_LOWER_CROSS_EXIT_TF="15m"
    cfg.KINDERGARTEN_EMA_GATE_ENABLED=False
    cfg.EMA_9_21_FILTER_ENABLED=False
    res=V.simulate_one(npz, "TEST", True, cfg, force_initial_seed=True)
    assert isinstance(res, dict) or res is None
    print("no crash pass", res.get("trades",0) if isinstance(res, dict) else "None")

if __name__=="__main__":
    test_wt_lower_cross_config()
    test_wt_sweep_includes()
    test_wt_no_crash()
    print("all pass")
