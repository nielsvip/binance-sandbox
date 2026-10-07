#!/usr/bin/env python3
"""dc_simple_8_sweep — 8-simple DC calcs per sym_side from OLD BEST baselines.

User 2026-09-26 mandate: USE OLD SIMPLE TEMPLATE NOT NEW HUGE ONE.
- Baseline = per_sym_active_config.json (crypto) + per_sym_active_config_stocks.json (stocks)
  which ARE the BEST cumulative overrides (OLD simple, ~90 keys, proven). If missing key,
  use TEMPLATE_{cat}_{side} defaults = QuickConfig() with venue defaults (OFF/OFF).
- Then SIMPLY CHANGE THE EXIT to each of 4 options THEN THE ENTRY = 8 options only:
  OFF/15m/1h/4h, buffers 0.25% stop / 0.10% target, vector 0.07s each, NOT a trillion.
- EXIT = TECHNICAL_DC (stop+target share TF) — the technical channel exit
- ENTRY (daytrade) = DAYTRADE_DC (stop+target share TF) — the intraday DC system
  Each variant sets BOTH stop+target TFs together (one EXIT system, one ENTRY system).
  Baseline recalc is SECONDS per sym_side then 8 deltas. NO huge sheet, NO 256 combos.
- NPZ: latest 30d on S1 (/home/niels/binance(-sandbox)/backtest_v8/indicators), same as v15_quick_dc_sweep.
- REAL NUMBERS only: reports gain/bh/trades/tim/delta per variant from v12_quick_engine.simulate_one.

NO LIES: every Sharpe/gain is from real trade-return list via metrics_guard-style, no synthetic.

Usage on S1:
  python3 tools/dc_simple_8_sweep.py --all                # 501 sym_sides
  python3 tools/dc_simple_8_sweep.py --sym BTCUSDC_LONG
  python3 tools/dc_simple_8_sweep.py --all --venue crypto --limit 5
  python3 tools/dc_simple_8_sweep.py --all --workers 8   # parallel

Output: data/reports/dc_simple_8_sweep.json + per_sym_active_config promotion for live.
"""
from __future__ import annotations
import argparse, json, sys, time, traceback
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# MULTI-TF per user 2026-09-26 fix: 4h alone ridiculous, try 15m AND 1h AND 4h etc. OFF + 7 combos of 15/1h/4h (OR across TFs). Fixed % ELIMINATED when DC active.
TFS_EXIT = ["OFF", "15m", "1h", "4h", "15m,1h", "15m,4h", "1h,4h", "15m,1h,4h"]
TFS_ENTRY = ["OFF", "15m", "1h", "4h", "15m,1h", "15m,4h", "1h,4h", "15m,1h,4h"]
# WT lower cross exit TFs per user 2026-09-27: lower wt+price cross (LONG wt down+price down vv SHORT)
TFS_WT = ["OFF", "15m", "1h", "4h"]
# backward alias for single-TF code paths
TFS = ["OFF", "15m", "1h", "4h"]
STOP_BUF = 0.25
TARGET_BUF = 0.10

def _load_template_defaults():
    """Latest TEMPLATE per cat_side — kindergarten/golden-rule baseline.
    Uses per_sym when present, else QuickConfig venue defaults as proxy for TEMPLATE
    (3m is IGNORED, not 15m)."""
    import v12_quick_engine as V
    defaults = {}
    for cat, is_long in [("crypto", True), ("crypto", False), ("stocks", True), ("stocks", False)]:
        cfg = V.QuickConfig()
        if cat == "stocks":
            cfg.apply_tradier_defaults()
        # Only keep DC-relevant + kindergarten filters that are in TEMPLATE sheets (avoid 3393 bloat)
        # Keep all uppercase but will be filtered by per_sym update (only per_sym keys override)
        d = {k: getattr(cfg, k) for k in dir(cfg) if k.isupper() and not k.startswith("_") and k in (
            "ENTRY_DC_TF","ENTRY_DC_BUFFER_PCT","TECHNICAL_DC_STOP_TF","TECHNICAL_DC_TARGET_TF",
            "TECHNICAL_DC_STOP_BUFFER_PCT","TECHNICAL_DC_TARGET_BUFFER_PCT",
            "DAYTRADE_DC_STOP_TF","DAYTRADE_DC_TARGET_TF","DAYTRADE_DC_STOP_BUFFER_PCT","DAYTRADE_DC_TARGET_BUFFER_PCT",
            "DC_DAYTRADE_ENABLED","TRADIER_DC_DAYTRADE_ENABLED","COOLDOWN_BARS")}
        defaults[(cat, is_long)] = d
    return defaults

def load_per_sym_maps():
    crypto_path = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
    stocks_path = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json"
    cmap = {}
    smap = {}
    if crypto_path.exists():
        try:
            d = json.loads(crypto_path.read_text())
            for k, v in d.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and "overrides" in v:
                    cmap[k] = v["overrides"]
                elif isinstance(v, dict):
                    cmap[k] = {kk: vv for kk, vv in v.items() if kk.isupper()}
        except Exception as e:
            print(f"[WARN] crypto per_sym load: {e}")
    if stocks_path.exists():
        try:
            d = json.loads(stocks_path.read_text())
            for k, v in d.items():
                if k.startswith("_"):
                    continue
                if isinstance(v, dict) and "overrides" in v:
                    smap[k] = v["overrides"]
                elif isinstance(v, dict):
                    smap[k] = {kk: vv for kk, vv in v.items() if kk.isupper()}
        except Exception as e:
            print(f"[WARN] stocks per_sym load: {e}")
    merged = {**cmap, **smap}
    print(f"[load] per_sym crypto {len(cmap)} stocks {len(smap)} merged {len(merged)}")
    return merged, cmap, smap

def eval_gain(npz, sym, is_long, overrides, window_days=30):
    import v12_quick_engine as V
    from tools.opt.evaluate_v12 import _exact_30d_slice
    crypto = sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD"))
    # symbols like AAPL/BTC are stocks vs crypto; USDT/USDC = crypto
    try:
        sliced, _ = _exact_30d_slice(npz, crypto, window_days)
    except Exception as e:
        return None, f"slice fail {e}"
    cfg = V.QuickConfig()
    if not crypto:
        cfg.apply_tradier_defaults()
    for k, v in overrides.items():
        try:
            if hasattr(cfg, k):
                cur = getattr(cfg, k)
                if isinstance(cur, bool):
                    setattr(cfg, k, bool(v))
                elif isinstance(cur, (int, float)) and not isinstance(cur, bool):
                    try:
                        setattr(cfg, k, type(cur)(v))
                    except:
                        setattr(cfg, k, v)
                else:
                    setattr(cfg, k, v)
            else:
                setattr(cfg, k, v)
        except:
            setattr(cfg, k, v)
    cfg.MODE = "crypto" if crypto else "tradier"
    r = V.simulate_one(sliced, sym, is_long, cfg)
    if r is None:
        return None, "simulate_one None"
    return r, None

def bh_from_sliced(sliced, is_long):
    import numpy as np
    try:
        c = np.asarray(sliced.get("close", []), dtype=float)
        c = c[np.isfinite(c) & (c > 0)]
        if len(c) >= 2:
            raw = (c[-1]-c[0])/c[0]*100
            return raw if is_long else max(-100, -raw)
    except:
        pass
    return None

_TEMPLATE_DEFAULTS = None
def _get_template_baseline(is_crypto: bool, is_long: bool):
    global _TEMPLATE_DEFAULTS
    if _TEMPLATE_DEFAULTS is None:
        _TEMPLATE_DEFAULTS = _load_template_defaults()
    cat = "crypto" if is_crypto else "stocks"
    return dict(_TEMPLATE_DEFAULTS.get((cat, is_long), {}))

def run_one(sym_side: str, per_sym_map: dict, window_days: int = 30):
    sym = sym_side[:-5] if sym_side.endswith("_LONG") else sym_side[:-6]
    side = "LONG" if sym_side.endswith("_LONG") else "SHORT"
    is_long = side == "LONG"
    crypto = sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD","USDT","USDC"))
    # baseline = per-category/side TEMPLATE (kindergarten/golden-rule filters) + per_sym BEST overrides
    # NEVER headless chicken: if per_sym missing, use latest TEMPLATE_CRYPTO/STOCKS_LONG/SHORT defaults
    tmpl_base = _get_template_baseline(crypto, is_long)
    overrides = dict(tmpl_base)
    # USER 2026-09-27: FORCE 9/21 KG 4h ON for this sweep - no testing, always on per user. Start from PREVIOUS BEST not BH.
    overrides["KINDERGARTEN_EMA_GATE_ENABLED"] = True
    overrides["KINDERGARTEN_FILTER_TF"] = "4h"
    overrides["KINDERGARTEN_CUMULATIVE_MODE"] = True
    overrides["KINDERGARTEN_CUMULATIVE_MIN_TFS"] = 1
    overrides["EMA_9_21_FILTER_ENABLED"] = True
    overrides["EMA_9_21_FILTER_FILTER_TF"] = "4h"
    # 3m is IGNORED in vector system (no fallback to 15m) — ensure any prior 3m is cleared to OFF
    overrides.update({k: v for k, v in per_sym_map.get(sym_side, {}).items() if k not in ("DAYTRDAY_DC",)})
    # re-force KG 4h ON even if per_sym had it OFF (user says always on)
    overrides["KINDERGARTEN_EMA_GATE_ENABLED"] = True
    overrides["KINDERGARTEN_FILTER_TF"] = "4h"
    overrides["KINDERGARTEN_CUMULATIVE_MODE"] = True
    overrides["EMA_9_21_FILTER_ENABLED"] = True
    overrides["EMA_9_21_FILTER_FILTER_TF"] = "4h"
    # force 3m OFF if previously set (vector ignores 3m)
    for k in list(overrides.keys()):
        if isinstance(overrides[k], str) and overrides[k] == "3m":
            overrides[k] = "OFF"
    has_prev = sym_side in per_sym_map and len(per_sym_map.get(sym_side, {})) > 0
    # also ensure if per_sym had 3m, it is now OFF
    if has_prev:
        for kk in ("TECHNICAL_DC_STOP_TF","TECHNICAL_DC_TARGET_TF","DAYTRADE_DC_STOP_TF","DAYTRADE_DC_TARGET_TF","ENTRY_DC_TF"):
            if overrides.get(kk) == "3m":
                overrides[kk] = "OFF"
    # load NPZ
    import v12_quick_engine as V
    from tools.opt.evaluate_v12 import _exact_30d_slice
    import numpy as np
    mode = "crypto" if crypto else "tradier"
    try:
        stores = V.load_npz(mode, [sym], "2024-01-01")
        npz = stores.get(sym) if isinstance(stores, dict) else stores
        if npz is None or len(npz.get("timestamps", [])) < 10:
            return {"sym_side": sym_side, "error": "npz missing", "has_prev": has_prev}
    except Exception as e:
        return {"sym_side": sym_side, "error": f"load_npz {e}", "has_prev": has_prev}
    # baseline
    base_r, err = eval_gain(npz, sym, is_long, overrides, window_days)
    if base_r is None:
        return {"sym_side": sym_side, "error": err, "has_prev": has_prev, "overrides_keys": len(overrides)}
    base_gain = base_r.get("gain_pct_2000norm", 0.0)
    try:
        sliced, _ = _exact_30d_slice(npz, crypto, window_days)
        base_bh = bh_from_sliced(sliced, is_long)
    except:
        base_bh = None
    # BEST BASELINE = per_sym previous best + TEMPLATE_DOGE_LONG (TEMPLATE_CRYPTO_LONG) defaults - NO BH FLOOR
    orig_base_gain = base_gain
    variants = []
    # 64 EXIT*ENTRY = 8 EXIT x 8 ENTRY combos (all DC 0.1/0.25) per user 64 not 8
    for tf_exit in TFS_EXIT:
        for tf_entry in TFS_ENTRY:
            ov = dict(overrides)
            ov["TECHNICAL_DC_STOP_TF"] = tf_exit
            ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = STOP_BUF
            ov["TECHNICAL_DC_TARGET_TF"] = tf_exit
            ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = TARGET_BUF
            ov["ENTRY_DC_TF"] = tf_entry
            ov["ENTRY_DC_BUFFER_PCT"] = TARGET_BUF
            r, _ = eval_gain(npz, sym, is_long, ov, window_days)
            if r is not None:
                g = r.get("gain_pct_2000norm", 0.0)
                label = f"{tf_exit.replace(',', '+') if tf_exit!='OFF' else 'OFF'}x{tf_entry.replace(',', '+') if tf_entry!='OFF' else 'OFF'}"
                variants.append({"kind": "EXIT_ENTRY", "tf": f"{tf_exit}/{tf_entry}", "variant": f"DC64_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # legacy 8 EXIT single (for keep_exit compatibility)
    for tf in TFS_EXIT:
        ov = dict(overrides)
        ov["TECHNICAL_DC_STOP_TF"] = tf
        ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = STOP_BUF
        ov["TECHNICAL_DC_TARGET_TF"] = tf
        ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = TARGET_BUF
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf.replace(',', '+') if tf != 'OFF' else 'OFF'
            variants.append({"kind": "EXIT", "tf": tf, "variant": f"TECH_EXIT_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # 8 ENTRY = ENTRY_DC multi-TF OR ABOVE low/high (long ABOVE, short BELOW) — per user 2026-09-26 clarification
    for tf in TFS_ENTRY:
        ov = dict(overrides)
        ov["ENTRY_DC_TF"] = tf
        ov["ENTRY_DC_BUFFER_PCT"] = TARGET_BUF  # 0.10% above
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf.replace(',', '+') if tf != 'OFF' else 'OFF'
            variants.append({"kind": "ENTRY", "tf": tf, "variant": f"ENTRY_ABOVE_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # WT LOWER CROSS EXIT — lower wt+price (LONG) / higher wt+price (SHORT) per user 2026-09-27; TF sweep OFF/15m/1h/4h as option in big WT TF run
    for tf in TFS_WT:
        ov = dict(overrides)
        ov["WT_LOWER_CROSS_EXIT_TF"] = tf
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf if tf != 'OFF' else 'OFF'
            variants.append({"kind": "WT", "tf": tf, "variant": f"WT_LOWER_CROSS_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # EMA_9_21 4h ON — test 1h, 4h, 1h+4h or both as you have to start from scratch again per user 2026-09-27
    for tf in ["OFF", "1h", "4h", "1h,4h"]:
        ov = dict(overrides)
        if tf == "OFF":
            ov["EMA_9_21_FILTER_ENABLED"] = False
            ov["EMA_9_21_FILTER_FILTER_TF"] = "4h"
        else:
            ov["EMA_9_21_FILTER_ENABLED"] = True
            ov["EMA_9_21_FILTER_FILTER_TF"] = tf
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf if tf != 'OFF' else 'OFF'
            variants.append({"kind": "EMA", "tf": tf, "variant": f"EMA9_21_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # BB and WT_DC from new templates — per user 2026-09-27 restart, add ALL BB and WT_DC options as well
    for tf in ["OFF", "15m", "1h", "4h"]:
        ov = dict(overrides)
        ov["BB_SQUEEZE_ENTRY_ENABLED"] = tf != "OFF"
        ov["BB_SQUEEZE_ENTRY_TF"] = tf  # 2026-09-28: TF-parametric detector (bb_upper/lower/pct_b_{tf}) + wired alignment
        ov["BB_SQUEEZE_EXIT_ENABLED"] = tf != "OFF"
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf if tf != 'OFF' else 'OFF'
            variants.append({"kind": "BB", "tf": tf, "variant": f"BB_SQUEEZE_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    for tf in ["OFF", "15m", "1h", "4h"]:
        ov = dict(overrides)
        ov["WT_DC_ENABLED"] = tf != "OFF"  # generic WT_DC flag
        ov["WT_DC_DETAILED_SCORER_ENABLED"] = tf != "OFF"  # 2026-09-28: slowdown/accel scorer (_score_long/_short), thr 43
        ov["WT_DC_TF_ENTRY"] = tf if tf != "OFF" else "1h"  # threshold shift per TF (15m -10 / 4h +10)
        ov["WT_DC_DC_TF"] = tf if tf != "OFF" else "1h"
        ov["WT_DC_TF_COMBO"] = tf if tf != "OFF" else "1h_4h_D"
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            label = tf if tf != 'OFF' else 'OFF'
            variants.append({"kind": "WTDC", "tf": tf, "variant": f"WT_DC_{label}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # Afternoon variants — 3m is IGNORED (OFF, no 15m fallback) per user; keep only 15m/1h combos for delta>=af check
    for variant, cfg_map in [
        ("AF_STOP_15m", {"TECHNICAL_DC_STOP_TF": "15m", "TECHNICAL_DC_TARGET_TF": "OFF"}),
        ("AF_STOP_1h", {"TECHNICAL_DC_STOP_TF": "1h", "TECHNICAL_DC_TARGET_TF": "OFF"}),
        ("AF_TARGET_15m", {"TECHNICAL_DC_STOP_TF": "OFF", "TECHNICAL_DC_TARGET_TF": "15m"}),
        ("AF_TARGET_1h", {"TECHNICAL_DC_STOP_TF": "OFF", "TECHNICAL_DC_TARGET_TF": "1h"}),
        ("AF_COMBO_15m_1h", {"TECHNICAL_DC_STOP_TF": "15m", "TECHNICAL_DC_TARGET_TF": "1h"}),
        ("AF_COMBO_1h_15m", {"TECHNICAL_DC_STOP_TF": "1h", "TECHNICAL_DC_TARGET_TF": "15m"}),
    ]:
        ov = dict(overrides)
        for k, v in cfg_map.items():
            ov[k] = v
            if "STOP" in k:
                ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = STOP_BUF
            if "TARGET" in k:
                ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = TARGET_BUF
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            variants.append({"kind": "AF", "tf": str(cfg_map), "variant": variant, "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2), "sharpe_per_trade": r.get("sharpe_per_trade", None)})
    # pick best per kind
    exit_vars = [v for v in variants if v["kind"]=="EXIT"]
    entry_vars = [v for v in variants if v["kind"]=="ENTRY"]
    best_exit = max(exit_vars, key=lambda x: x["delta"]) if exit_vars else None
    best_entry = max(entry_vars, key=lambda x: x["delta"]) if entry_vars else None
    best_overall = max(variants, key=lambda x: x["delta"]) if variants else None
    # also compute combined best_exit+best_entry if both positive
    combo = None
    if best_exit and best_entry and best_exit["delta"] > 1e-9 and best_entry["delta"] > 1e-9 and best_exit["tf"] != "OFF" and best_entry["tf"] != "OFF":
        ov = dict(overrides)
        ov["TECHNICAL_DC_STOP_TF"] = best_exit["tf"]; ov["TECHNICAL_DC_TARGET_TF"] = best_exit["tf"]
        ov["TECHNICAL_DC_STOP_BUFFER_PCT"] = STOP_BUF; ov["TECHNICAL_DC_TARGET_BUFFER_PCT"] = TARGET_BUF
        ov["ENTRY_DC_TF"] = best_entry["tf"]; ov["ENTRY_DC_BUFFER_PCT"] = TARGET_BUF
        r, _ = eval_gain(npz, sym, is_long, ov, window_days)
        if r is not None:
            g = r.get("gain_pct_2000norm", 0.0)
            combo = {"variant": f"COMBO_TECH_{best_exit['tf']}+ENTRY_{best_entry['tf']}", "gain": round(g,4), "delta": round(g-base_gain,4), "trades": r.get("trades",0), "tim": round(r.get("tim_pct",0),2)}
    result = {
        "sym_side": sym_side,
        "venue": mode,
        "has_prev": has_prev,
        "overrides_keys": len(overrides),
        "window_days": window_days,
        "base_gain": round(base_gain,4),
        "orig_base_gain": round(orig_base_gain,4),
        "base_bh": round(base_bh,4) if base_bh is not None else None,
        "bh_floor_applied": False,
        "base_trades": base_r.get("trades",0),
        "base_sharpe": base_r.get("sharpe_per_trade", None),
        "npz_bars": len(npz.get("timestamps", [])),
        "variants": variants,
        "best_exit": best_exit,
        "best_entry": best_entry,
        "best_overall": best_overall,
        "combo": combo,
        "keep_exit_tf": best_exit["tf"] if best_exit and best_exit["delta"] > 1e-9 else "OFF",
        "keep_entry_tf": best_entry["tf"] if best_entry and best_entry["delta"] > 1e-9 else "OFF",
        "keep_exit_delta": round(best_exit["delta"],4) if best_exit else 0,
        "keep_entry_delta": round(best_entry["delta"],4) if best_entry else 0,
    }
    return result

def main():
    p = argparse.ArgumentParser()
    p.add_argument("--sym", default="", help="single sym_side")
    p.add_argument("--all", action="store_true", help="run all per_sym keys")
    p.add_argument("--venue", choices=["crypto","stocks","both"], default="both")
    p.add_argument("--window", type=int, default=30)
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--workers", type=int, default=8)
    p.add_argument("--out", default="data/reports/dc_simple_8_sweep.json")
    args = p.parse_args()

    per_sym_map, cmap, smap = load_per_sym_maps()
    if args.sym:
        targets = [args.sym]
    elif args.all:
        # use per_sym_map keys as universe (OLD BEST simple template)
        # if empty, fallback to BEST xlsx scan
        targets = []
        for k in per_sym_map.keys():
            is_crypto = k.split("_")[0].upper().endswith("USDT") or k.endswith(("USDT_LONG","USDT_SHORT","USDC_LONG","USDC_SHORT","USD1_LONG","USD1_SHORT"))
            # simpler: check if sym part endswith USDT/USDC else stocks
            sym = k[:-5] if k.endswith("_LONG") else k[:-6]
            crypto_sym = sym.upper().endswith(("USDT","USDC","USD1","BUSD","FDUSD","TUSD"))
            if args.venue == "crypto" and not crypto_sym: continue
            if args.venue == "stocks" and crypto_sym: continue
            targets.append(k)
        targets = sorted(set(targets))
        if not targets:
            # fallback scan BEST dirs
            from pathlib import Path as _P
            BEST_DIRS = [ROOT/"SPREADSHEETS"/"BEST"/d for d in ("CRYPTO_LONG","CRYPTO_SHORT","STOCKS_LONG","STOCKS_SHORT")]
            s=set()
            for d in BEST_DIRS:
                if not d.exists(): continue
                for pth in d.glob("*.xlsx"):
                    name=pth.stem
                    if "_LONG_" in name: ss=name.split("_LONG_")[0]+"_LONG"
                    elif "_SHORT_" in name: ss=name.split("_SHORT_")[0]+"_SHORT"
                    else: continue
                    s.add(ss)
            targets=sorted(s)
        if args.limit:
            targets=targets[:args.limit]
        print(f"[targets] {len(targets)} sym_sides (venue={args.venue})")
    else:
        p.print_help(); return

    results=[]
    t0=time.time()
    if args.workers > 1 and len(targets) > 1:
        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            fut={ex.submit(run_one, ss, per_sym_map, args.window): ss for ss in targets}
            for f in as_completed(fut):
                ss=fut[f]
                try:
                    r=f.result()
                    results.append(r)
                    be=r.get("best_overall")
                    print(f"[{ss}] base {r.get('base_gain')} bh {r.get('base_bh')} trades {r.get('base_trades')} | best {be['variant'] if be else 'none'} delta {be['delta'] if be else 0:+.2f} keep_EXIT {r.get('keep_exit_tf')} keep_ENTRY {r.get('keep_entry_tf')} ({'prev' if r.get('has_prev') else 'TEMPLATE defaults'})")
                except Exception as e:
                    print(f"[{ss}] FAIL {e}")
                    traceback.print_exc()
                    results.append({"sym_side": ss, "error": str(e)})
    else:
        for ss in targets:
            r=run_one(ss, per_sym_map, args.window)
            results.append(r)
            be=r.get("best_overall")
            print(f"[{ss}] base {r.get('base_gain')} | best {be['variant'] if be else 'none'} delta {be['delta'] if be else 0} keep {r.get('keep_exit_tf')}/{r.get('keep_entry_tf')}")
    # sort by best delta
    results.sort(key=lambda x: x.get("best_overall",{}).get("delta", -1e9) if isinstance(x.get("best_overall"), dict) else -1e9, reverse=True)
    out=Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(results, indent=2))
    dt=time.time()-t0
    pos_exit=sum(1 for r in results if r.get("keep_exit_delta",0) > 1e-9)
    pos_entry=sum(1 for r in results if r.get("keep_entry_delta",0) > 1e-9)
    pos_any=sum(1 for r in results if r.get("best_overall") and r["best_overall"]["delta"] > 1e-9)
    print(f"[done] {len(results)} in {dt:.1f}s ({dt/max(1,len(results)):.2f}s/sym) wrote {out}")
    print(f"  EXIT positive {pos_exit}/{len(results)} ENTRY positive {pos_entry}/{len(results)} any positive {pos_any}/{len(results)}")
    if results:
        avg=max(0, sum(r.get("best_overall",{}).get("delta",0) for r in results if r.get("best_overall"))/max(1,len([r for r in results if r.get("best_overall")])))
        print(f"  avg best delta {avg:+.2f}pp (30d, 0.07s/vector, 1y walk-forward still needed before live)")
    # also handle WT and EMA best
    for r in results:
        wt_vars=[v for v in r.get("variants",[]) if v.get("kind")=="WT"]
        r["best_wt"]=max(wt_vars, key=lambda x: x["delta"]) if wt_vars else None
        r["keep_wt_tf"]=r["best_wt"]["tf"] if r["best_wt"] and r["best_wt"]["delta"]>1e-9 else "OFF"
        r["keep_wt_delta"]=round(r["best_wt"]["delta"],4) if r["best_wt"] else 0
        ema_vars=[v for v in r.get("variants",[]) if v.get("kind")=="EMA"]
        r["best_ema"]=max(ema_vars, key=lambda x: x["delta"]) if ema_vars else None
        r["keep_ema_tf"]=r["best_ema"]["tf"] if r["best_ema"] and r["best_ema"]["delta"]>1e-9 else "OFF"
        r["keep_ema_delta"]=round(r["best_ema"]["delta"],4) if r["best_ema"] else 0
    pos_wt=sum(1 for r in results if r.get("keep_wt_delta",0) > 1e-9)
    pos_ema=sum(1 for r in results if r.get("keep_ema_delta",0) > 1e-9)
    print(f"  WT positive {pos_wt}/{len(results)} EMA positive {pos_ema}/{len(results)}")
    # also write per_sym promotion helper
    promo={}
    for r in results:
        if r.get("error"): continue
        ss=r["sym_side"]
        promo[ss]={"TECHNICAL_DC_STOP_TF": r["keep_exit_tf"], "TECHNICAL_DC_TARGET_TF": r["keep_exit_tf"], "ENTRY_DC_TF": r["keep_entry_tf"], "ENTRY_DC_BUFFER_PCT": TARGET_BUF, "WT_LOWER_CROSS_EXIT_TF": r.get("keep_wt_tf","OFF"), "EMA_9_21_FILTER_ENABLED": r.get("keep_ema_tf","OFF") != "OFF", "EMA_9_21_FILTER_FILTER_TF": r.get("keep_ema_tf","OFF") if r.get("keep_ema_tf","OFF") != "OFF" else "4h", "exit_delta": r["keep_exit_delta"], "entry_delta": r["keep_entry_delta"], "wt_delta": r.get("keep_wt_delta",0), "ema_delta": r.get("keep_ema_delta",0), "base_gain": r["base_gain"], "best_variant": r.get("best_overall",{}).get("variant"), "best_wt_variant": r.get("best_wt",{}).get("variant") if r.get("best_wt") else None, "best_ema_variant": r.get("best_ema",{}).get("variant") if r.get("best_ema") else None}
    promo_path=out.with_name(out.stem+"_per_sym_promote.json")
    promo_path.write_text(json.dumps(promo, indent=2))
    print(f"  promote map {promo_path} ({len(promo)} keys)")

if __name__=="__main__":
    main()
