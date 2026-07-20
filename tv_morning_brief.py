#!/usr/bin/env python3
"""
tv_morning_brief.py
Reads tradier_indicators_latest.json, assesses WT/stoch/MFI across TFs,
and outputs BUY/SHORT/HOLD/AVOID recommendations to data/tv_morning_brief.json.
No API calls, no CDP, no Claude tokens. Runs in <1s.
"""
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
INDICATORS_FILE = BASE / "data" / "tradier" / "tradier_indicators_latest.json"
OUT_FILE = BASE / "data" / "tv_morning_brief.json"
LONG_FILE = BASE / "symbols_trb_long.json"
SHORT_FILE = BASE / "symbols_trb_short.json"

# TFs used for bias assessment
BIAS_TFS = ["1h", "4h", "D"]

# ── Signal helpers ──────────────────────────────────────────────────────────

def wt_dir(ind, tf):
    """Returns 'BULL', 'BEAR', or None."""
    cross = ind.get(f"wt_cross_{tf}")
    if cross in ("BULL", "BEAR"):
        return cross
    w1 = ind.get(f"wt1_{tf}")
    w2 = ind.get(f"wt2_{tf}")
    if isinstance(w1, float) and isinstance(w2, float):
        return "BULL" if w1 > w2 else "BEAR"
    return None

def stoch_status(k):
    """Returns 'OVERBOUGHT', 'OVERSOLD', or 'NEUTRAL'."""
    if k is None:
        return None
    if k > 80:
        return "OVERBOUGHT"
    if k < 20:
        return "OVERSOLD"
    return "NEUTRAL"

def mfi_status(mfi_val, side):
    """Returns 'CONFIRMING', 'WEAK', or None."""
    if mfi_val is None:
        return None
    if side == "long":
        return "CONFIRMING" if mfi_val >= 40 else "WEAK"
    else:
        return "CONFIRMING" if mfi_val <= 60 else "WEAK"

def accel_label(accel):
    if accel is None:
        return None
    if accel > 5:
        return "BUILDING"
    if accel < -5:
        return "FADING"
    return "FLAT"

# ── Per-symbol assessment ───────────────────────────────────────────────────

def assess(symbol, ind, side):
    """
    Returns a dict with:
      recommendation: BUY | SHORT | HOLD | AVOID
      confidence:     HIGH | MEDIUM | LOW
      tf_bias:        {tf: BULL|BEAR|?}
      stoch_status:   OVERBOUGHT | OVERSOLD | NEUTRAL
      mfi_status:     CONFIRMING | WEAK
      momentum:       BUILDING | FADING | FLAT
      cautions:       list of warning strings
      note:           one-line human reasoning
    """
    # Gather per-TF signals
    tf_bias = {}
    for tf in BIAS_TFS:
        tf_bias[tf] = wt_dir(ind, tf) or "?"

    bull_tfs = [tf for tf in BIAS_TFS if tf_bias[tf] == "BULL"]
    bear_tfs = [tf for tf in BIAS_TFS if tf_bias[tf] == "BEAR"]

    # Use 4h as primary stoch/MFI reference, fall back to 1h then D
    sk = ind.get("stoch_k_4h") or ind.get("stoch_k_1h") or ind.get("stoch_k_D")
    mfi_val = ind.get("mfi_4h") or ind.get("mfi_1h") or ind.get("mfi_D")
    sk_1h = ind.get("stoch_k_1h")
    accel_4h = ind.get("wt_acceleration_4h")

    sk_status = stoch_status(sk)
    mfi_st = mfi_status(mfi_val, side)
    momentum = accel_label(accel_4h)

    cautions = []

    # ── LONG-side logic ──
    if side == "long":
        if len(bull_tfs) >= 2:
            if sk_status == "OVERBOUGHT":
                cautions.append(f"Stoch overbought ({sk:.0f}) — wait for pullback")
                rec = "HOLD"
                conf = "LOW"
            elif mfi_st == "WEAK":
                cautions.append(f"MFI weak ({mfi_val:.0f}) — flow not confirming")
                rec = "HOLD"
                conf = "LOW"
            else:
                rec = "BUY"
                conf = "HIGH" if len(bull_tfs) == 3 else "MEDIUM"
        elif len(bull_tfs) == 1:
            if len(bear_tfs) >= 2:
                cautions.append("HTF bearish — wrong direction")
                rec = "AVOID"
                conf = "HIGH"
            else:
                cautions.append(f"Only {bull_tfs[0]} bullish — wait for alignment")
                rec = "HOLD"
                conf = "LOW"
        else:
            cautions.append("WT bearish across TFs — wrong side")
            rec = "AVOID"
            conf = "HIGH" if len(bear_tfs) == 3 else "MEDIUM"

        # Extra cautions
        if sk_1h and sk_1h < 20 and rec != "AVOID":
            cautions.append(f"1h stoch oversold ({sk_1h:.0f}) — bounce potential")
        if momentum == "FADING" and rec == "BUY":
            cautions.append("4h momentum fading — size down")

        # Note
        aligned_str = "+".join(bull_tfs) if bull_tfs else "none"
        note_parts = [f"WT {aligned_str} bullish" if bull_tfs else "WT bearish all TFs"]
        if sk is not None:
            note_parts.append(f"stoch {sk:.0f} ({sk_status or '?'})")
        if mfi_val is not None:
            note_parts.append(f"MFI {mfi_val:.0f} ({mfi_st or '?'})")
        if momentum:
            note_parts.append(f"momentum {momentum}")

    # ── SHORT-side logic ──
    else:
        if len(bear_tfs) >= 2:
            if sk_status == "OVERSOLD":
                cautions.append(f"Stoch oversold ({sk:.0f}) — risk of bounce")
                rec = "HOLD"
                conf = "LOW"
            elif mfi_st == "WEAK":
                cautions.append(f"MFI confirming ({mfi_val:.0f}) — flow still flowing down")
                rec = "SHORT"
                conf = "HIGH" if len(bear_tfs) == 3 else "MEDIUM"
            else:
                rec = "SHORT"
                conf = "HIGH" if len(bear_tfs) == 3 else "MEDIUM"
        elif len(bear_tfs) == 1:
            if len(bull_tfs) >= 2:
                cautions.append("HTF bullish — wrong direction for short")
                rec = "AVOID"
                conf = "HIGH"
            else:
                cautions.append(f"Only {bear_tfs[0]} bearish — wait for alignment")
                rec = "HOLD"
                conf = "LOW"
        else:
            cautions.append("WT bullish across TFs — wrong side to short")
            rec = "AVOID"
            conf = "HIGH" if len(bull_tfs) == 3 else "MEDIUM"

        if sk_1h and sk_1h > 80 and rec != "AVOID":
            cautions.append(f"1h stoch overbought ({sk_1h:.0f}) — short entry timing good")
        if momentum == "BUILDING" and rec == "SHORT":
            cautions.append("4h momentum still building — trail stop")

        aligned_str = "+".join(bear_tfs) if bear_tfs else "none"
        note_parts = [f"WT {aligned_str} bearish" if bear_tfs else "WT bullish all TFs"]
        if sk is not None:
            note_parts.append(f"stoch {sk:.0f} ({sk_status or '?'})")
        if mfi_val is not None:
            note_parts.append(f"MFI {mfi_val:.0f} ({mfi_st or '?'})")
        if momentum:
            note_parts.append(f"momentum {momentum}")

    # TF divergence note
    if len(bull_tfs) > 0 and len(bear_tfs) > 0:
        short_tfs_bear = [tf for tf in ["1h"] if tf_bias.get(tf) == "BEAR"]
        long_tfs_bull = [tf for tf in ["4h","D"] if tf_bias.get(tf) == "BULL"]
        if short_tfs_bear and long_tfs_bull and side == "long":
            cautions.append(f"LTF pullback ({'+'.join(short_tfs_bear)} bearish) within HTF uptrend — buy dip")

    return {
        "recommendation": rec,
        "confidence": conf,
        "tf_bias": tf_bias,
        "stoch_status": sk_status,
        "mfi_status": mfi_st,
        "momentum": momentum,
        "cautions": cautions,
        "note": ", ".join(note_parts),
    }

# ── Main ────────────────────────────────────────────────────────────────────

def main():
    if not INDICATORS_FILE.exists():
        print(f"ERROR: {INDICATORS_FILE} not found", file=sys.stderr)
        sys.exit(1)

    ind_data = json.loads(INDICATORS_FILE.read_text())
    long_syms = json.loads(LONG_FILE.read_text())
    short_syms = json.loads(SHORT_FILE.read_text())

    # Check data freshness
    file_age_min = (datetime.now(timezone.utc).timestamp() - INDICATORS_FILE.stat().st_mtime) / 60
    if file_age_min > 30:
        print(f"WARNING: indicators file is {file_age_min:.0f}min old", file=sys.stderr)

    assessments = {}
    buys, shorts, holds_long, holds_short, avoids = [], [], [], [], []

    for sym in long_syms:
        if sym not in ind_data:
            continue
        result = assess(sym, ind_data[sym], "long")
        assessments.setdefault(sym, {})["long"] = {"side": "long", **result}
        if result["recommendation"] == "BUY":
            buys.append((sym, result["confidence"], result))
        elif result["recommendation"] == "HOLD":
            holds_long.append((sym, result["confidence"], result))
        else:
            avoids.append((sym, result["confidence"], result))

    for sym in short_syms:
        if sym not in ind_data:
            continue
        result = assess(sym, ind_data[sym], "short")
        assessments.setdefault(sym, {})["short"] = {"side": "short", **result}
        if result["recommendation"] == "SHORT":
            shorts.append((sym, result["confidence"], result))
        elif result["recommendation"] == "HOLD":
            holds_short.append((sym, result["confidence"], result))
        else:
            avoids.append((sym, result["confidence"], result))

    # Sort by confidence (HIGH first)
    conf_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    buys.sort(key=lambda x: conf_order.get(x[1], 9))
    shorts.sort(key=lambda x: conf_order.get(x[1], 9))

    # Market bias
    n_buy = len(buys)
    n_short = len(shorts)
    if n_buy > n_short * 1.1:
        market_bias = "LONG"
    elif n_short > n_buy * 1.1:
        market_bias = "SHORT"
    else:
        market_bias = "NEUTRAL"

    # Top lists — name + confidence + one-line note
    def top_list(items, max_n=15):
        return [
            {"symbol": sym, "confidence": conf, "note": r["note"],
             "cautions": r["cautions"][:1]}
            for sym, conf, r in items[:max_n]
        ]

    output = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "data_age_minutes": round(file_age_min, 1),
        "symbols_scanned": len(assessments),
        "market_bias": market_bias,
        "summary": (
            f"{n_buy} BUY setups, {n_short} SHORT setups, "
            f"{len(holds_long)+len(holds_short)} HOLD, "
            f"{len(avoids)} AVOID — from {len(long_syms)+len(short_syms)} trb symbols"
        ),
        "top_buys": top_list(buys),
        "top_shorts": top_list(shorts),
        "top_holds": top_list(holds_long[:5] + holds_short[:5]),
        "assessments": assessments,
    }

    OUT_FILE.write_text(json.dumps(output, indent=2))
    print(f"tv-morning-brief: {n_buy} BUY, {n_short} SHORT, {len(holds_long)+len(holds_short)} HOLD, {len(avoids)} AVOID | bias={market_bias} | data_age={file_age_min:.0f}min")
    print(f"Wrote: {OUT_FILE}")
    try:
        cache_path = BASE / "data" / "tradier_exchange_cache.json"
        cache = json.loads(cache_path.read_text()) if cache_path.exists() else {}
        def format_wlt(symbols): return "\n".join([(f"{cache[s]}:{s}" if cache.get(s) else s) for s in symbols])
        desktop = Path("/Users/niels/Desktop")
        (desktop / "TRB_LONG.txt").write_text(format_wlt(long_syms))
        (desktop / "TRB_SHORT.txt").write_text(format_wlt(short_syms))
        (desktop / "TRB_LONG_BUY.txt").write_text(format_wlt([x[0] for x in buys]))
        (desktop / "TRB_SHORT_SHORT.txt").write_text(format_wlt([x[0] for x in shorts]))
        print("Wrote TradingView watchlists to Desktop.")
        import subprocess; subprocess.run([sys.executable, str(BASE / "generate_tv_dashboard.py")], check=False)
    except Exception as wlt_err: print(f"Error writing watchlists to Desktop: {wlt_err}", file=sys.stderr)

if __name__ == "__main__":
    main()
