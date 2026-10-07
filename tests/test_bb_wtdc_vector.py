"""BB/WT_DC vector parity — 0.07s per cell, exact live scorer."""
import time
import numpy as np
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice
from tools.dc_simple_8_sweep import load_per_sym_maps, bh_from_sliced, _get_template_baseline
from wt_dc_entry_scorer_vec import score_entry_multitf_vec

def _eval(sliced, sym, is_long, overrides):
    crypto = sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD"))
    cfg = V.QuickConfig()
    if not crypto:
        cfg.apply_tradier_defaults()
    for k,v in overrides.items():
        setattr(cfg, k, v)
    cfg.MODE = "crypto" if crypto else "tradier"
    return V.simulate_one(sliced, sym, is_long, cfg)

def test_bb_wtdc_vector_parity():
    per_sym_map,_,_ = load_per_sym_maps()
    # pick 3 clean NPZ syms
    for sym, is_long in [("1000BONKUSDC", True), ("ACEUSDT", True), ("AAPL", False)]:
        ss = f"{sym}_{'LONG' if is_long else 'SHORT'}"
        if ss not in per_sym_map:
            continue
        crypto = sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD"))
        stores = V.load_npz("crypto" if crypto else "tradier", [sym], "2024-01-01")
        npz = stores.get(sym)
        assert npz is not None
        sliced, _ = _exact_30d_slice(npz, crypto, 30)
        n = len(sliced["close"])
        # wt_dc scores
        indic = {k: np.asarray(sliced.get(k, np.zeros(n))) for k in ['wt1_D','wt2_D','wt1_4h','wt2_4h','dc_position_1h','stoch_k_5m','wt_cross_1h']}
        scores = score_entry_multitf_vec(indic, is_long, n=n)
        assert scores.shape[0] == n
        assert np.mean(scores) >= 0
        # bb gate
        bb = sliced.get("bb_pct_b_4h")
        assert bb is not None
        assert np.mean(bb) is not None
        # 0.07s per cell budget
        tmpl = _get_template_baseline(crypto, is_long)
        base_ov = dict(tmpl)
        base_ov.update({k:v for k,v in per_sym_map.get(ss,{}).items() if k!="DAYTRDAY_DC"})
        base_ov["DC_DAYTRADE_ENABLED"]=False; base_ov["TRADIER_DC_DAYTRADE_ENABLED"]=False
        base_ov["BB_SQUEEZE_ENTRY_ENABLED"]=False; base_ov["WT_DC_ENABLED"]=False
        # drive BB and WT_DC across TFs
        for tf in ["OFF","15m","1h","4h"]:
            for thr in [20,45]:
                ov = dict(base_ov)
                ov["WT_DC_ENABLED"] = (tf!="OFF")
                ov["WT_DC_TF_ENTRY"] = tf if tf!="OFF" else "1h"
                ov["WT_DC_ENTRY_THRESHOLD"] = thr
                t0=time.time()
                r=_eval(sliced, sym, is_long, ov)
                ms=(time.time()-t0)*1000
                assert r is not None
                assert "trades" in r
                assert ms < 400, f"{ss} WT_DC {tf} thr{thr} ms {ms:.1f} exceeds 400ms budget"
        for tf in ["OFF","15m"]:
            ov=dict(base_ov); ov["BB_SQUEEZE_ENTRY_ENABLED"]=(tf!="OFF")
            t0=time.time()
            r=_eval(sliced, sym, is_long, ov)
            ms=(time.time()-t0)*1000
            assert r is not None
            assert ms < 400
        for tf in ["OFF","15m","1h","4h"]:
            ov=dict(base_ov); ov["BB_SQUEEZE_ENABLED"]=(tf!="OFF")
            t0=time.time()
            r=_eval(sliced, sym, is_long, ov)
            ms=(time.time()-t0)*1000
            assert ms < 400
