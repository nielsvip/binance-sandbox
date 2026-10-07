"""Any NPZ smoke — function must produce numbers for any randomly tried NPZ."""
import random
from pathlib import Path
import v12_quick_engine as V
from tools.opt.evaluate_v12 import _exact_30d_slice
from tools.dc_simple_8_sweep import _get_template_baseline

def _eval_any(sym, is_long=True):
    # Try crypto then tradier
    for mode in ["crypto", "tradier"]:
        stores = V.load_npz(mode, [sym], "2024-01-01")
        npz = stores.get(sym)
        if npz is not None and len(npz.get("close", [])) >= 100:
            crypto = mode == "crypto"
            sliced, _ = _exact_30d_slice(npz, crypto, 30)
            tmpl = _get_template_baseline(crypto, is_long)
            base_ov = dict(tmpl)
            base_ov["DC_DAYTRADE_ENABLED"] = False
            for name, extra in [
                ("BB_ENTRY 15m", {"BB_SQUEEZE_ENTRY_ENABLED": True}),
                ("WT_DC 15m thr20", {"WT_DC_ENABLED": True, "WT_DC_TF_ENTRY": "15m", "WT_DC_ENTRY_THRESHOLD": 20}),
                ("WT 15m", {"WT_LOWER_CROSS_EXIT_TF": "15m"}),
                ("STOP 0.25 15m", {"TECHNICAL_DC_STOP_TF": "15m", "TECHNICAL_DC_STOP_BUFFER_PCT": 0.25}),
            ]:
                cfg = V.QuickConfig()
                if not crypto:
                    cfg.apply_tradier_defaults()
                for k,v in {**base_ov, **extra}.items():
                    setattr(cfg, k, v)
                cfg.MODE = "crypto" if crypto else "tradier"
                r = V.simulate_one(sliced, sym, is_long, cfg)
                assert r is not None, f"{sym} {name} no result"
                assert "gain_pct_2000norm" in r and "trades" in r
                assert not (r["gain_pct_2000norm"] != r["gain_pct_2000norm"])  # not NaN
                assert r["trades"] >= 0
            return True
    return False

def test_any_npz_random_smoke():
    NPZ_DIR = Path("/Users/niels/Documents/binance/backtest_v8/indicators")
    all_npz = list(NPZ_DIR.glob("*.npz"))
    # Try random until we have 5 valid (skip corrupt)
    random.seed(0)
    valid_syms = []
    for p in random.sample(all_npz, 20):
        sym = p.stem
        if _eval_any(sym, True):
            valid_syms.append(sym)
            if len(valid_syms) >= 5:
                break
    assert len(valid_syms) >= 5, "not enough valid NPZ"
    for sym in valid_syms:
        _eval_any(sym, False)
