#!/usr/bin/env python3
"""v15_augment_probe — measured counterfactuals for the AUGMENT / partial-REDUCE / REENTRY families of one board.

USER 2026-10-20: "SHOP_LONG is completely skipping all augment functions — if it wasn't it would multiply its gains,
please revise". This tool answers that with numbers from the REAL engine only (lifecycle_pilot.evaluate_month ->
v12_quick_engine.simulate_one, the same ledger path the chart and the board header are drawn from):

  * BASE  = the board's own set (progress JSON cumulative_overrides) on a FROZEN copy of its NPZ (copied once at
            start so a 15-min NPZ refresh cannot move base and variants apart mid-run).
  * one family at a time: augment sources (venue live sources, then vector-only sources), UAG trigger ladder,
            per-position augment caps 1/2/3, partial reduces, reentry.

Augment caps: MAX_AUGMENTS_PER_POSITION was deleted on every surface 2026-10-15 (USER "delete the switch completely");
no engine reads it. The cap rows below are a MEASUREMENT-ONLY monkeypatch of
vec_decisions.ported_stateful_augment.masks (the engine's existing `_sa_cap` hook, v12_quick_engine.py ~17926) inside
this process. Nothing is written to any config, set, template or engine.

Nothing is promoted. Output: JSON (per variant gain/trades/TIM/DD/WR + augment/reduce/reentry counts by source).

USAGE (S1 crypto / S2 stocks, from ~/binance-sandbox):
    python -u tools/v15_augment_probe.py --progress ~/v15_run33_20261019/progress/SHOP_LONG_v14_progress.json \
        --frozen-dir ~/aug_probe_20261020/npz --out ~/aug_probe_20261020/SHOP_LONG.json
"""
import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_NPZ_CACHE", "4")

_CAP = {"value": None}


def _install_cap_hook():
    import numpy as np
    import vec_decisions.ported_stateful_augment as psa

    orig = psa.masks

    def masks(npz, n, is_long, cfg, _safe, close):
        out = dict(orig(npz, n, is_long, cfg, _safe, close) or {})
        if _CAP["value"] is not None:
            out["PROBE_AUGMENT_CAP"] = (np.ones(n, dtype=bool), {"augment_count_max": int(_CAP["value"])})
        return out

    psa.masks = masks


def _install_frozen_npz(frozen_dir: Path, symbol: str):
    import numpy as np
    import v12_quick_engine as V
    from tools.opt import evaluate_v12 as E

    src = None
    for prefix in ("backtest_v8", "backtest_v7"):
        p = ROOT / prefix / "indicators" / f"{symbol}.npz"
        if p.exists():
            src = p
            break
    if src is None:
        raise SystemExit(f"no NPZ for {symbol}")
    frozen_dir.mkdir(parents=True, exist_ok=True)
    dst = frozen_dir / f"{symbol}.npz"
    if not dst.exists():
        shutil.copy2(src, dst)
    orig_load = V.load_npz

    def load_npz(mode, symbols, start_date, npz_dir=""):
        return orig_load(mode, symbols, start_date, npz_dir=str(frozen_dir))

    V.load_npz = load_npz
    orig_start = E._frozen_npz_start_date

    def start_date(sym, window_days, offset_days=0):
        from datetime import datetime, timezone

        p = frozen_dir / f"{sym}.npz"
        if not p.exists():
            return orig_start(sym, window_days, offset_days)
        with np.load(p, allow_pickle=True) as z:
            ts = np.asarray(z["timestamps"], dtype="float64")
        ts = ts[np.isfinite(ts) & (ts > 0)]
        end = float(ts.max()) - offset_days * 86400.0
        return datetime.fromtimestamp(end - window_days * 86400.0, tz=timezone.utc).strftime("%Y-%m-%d")

    E._frozen_npz_start_date = start_date
    import hashlib

    return hashlib.md5(dst.read_bytes()).hexdigest()


def _src_of(reason: str) -> str:
    r = str(reason or "").upper()
    for tok in ("UAG_LADDER", "PULLBACK_AUG", "DC_TIER", "TRAILING_AUG", "LR_BAND_LADDER", "WT_D_BOUNCE", "AUGMENT_AT_LOSS", "HAIKU", "DD_BOUNCE", "FAST_RISER", "AIR_CARRY", "VEC_AUGMENT_SIG", "PPL", "PARTIAL_PROFIT", "REGIME", "HLR", "QUICK_REDUCE", "DC_BREACH", "AUGMENT_FALLBACK"):
        if tok in r:
            return tok
    return r.split(" ")[0][:28] or "?"


def ev(symside, ov, window_days):
    from tools.opt.lifecycle_pilot import evaluate_month
    from metrics_guard import realised_closes

    t0 = time.time()
    r = evaluate_month(symside, dict(ov), include_ledger=True, window_days=window_days) or {}
    led = r.get("execution_ledger") or []
    closes = realised_closes(led)
    aug = [x for x in led if isinstance(x, dict) and x.get("type") == "AUGMENT"]
    red = [x for x in led if isinstance(x, dict) and x.get("type") == "REDUCE" and x.get("pnl_dollars") is None]
    reent = [x for x in closes if "REENTR" in str(x.get("entry_reason", "")).upper()]
    by = {}
    for x in aug:
        k = "AUG:" + _src_of(x.get("reason"))
        by[k] = by.get(k, 0) + 1
    for x in red:
        k = "RED:" + _src_of(x.get("reason"))
        by[k] = by.get(k, 0) + 1
    return {
        "gain_pct": round(float(r.get("gain_pct") or 0.0), 4),
        "trades": len(closes),
        "tim_pct": round(float(r.get("tim_pct") or 0.0), 2),
        "max_dd_pct": round(float(r.get("max_dd_pct") or 0.0), 2),
        "bh_pct": round(float(r.get("bh_pct") or 0.0), 2),
        "wr_pct": round(float(r.get("wr_pct") or 0.0), 1),
        "n_augments": len(aug),
        "n_partial_reduces": len(red),
        "n_reentries": len(reent),
        "by_source": by,
        "valid": bool(r.get("valid")),
        "invalid_reason": r.get("invalid_reason", ""),
        "secs": round(time.time() - t0, 1),
    }


def _flip(base, k, default):
    return not bool(base.get(k, default))


def variants(base, is_stock):
    """(family, label, overrides, env, cap). One family at a time; every override is an existing QuickConfig field."""
    V = []
    A = "AUGMENT"
    if is_stock:
        live = {"STOCK_LIVE_AUGMENT_ENABLED": True}
        V.append((A, "live: DC_TIER (STOCK_LIVE_AUGMENT_ENABLED)", live, {}, None))
        V.append((A, "live: DC_TIER + TRAILING_AUG", dict(live, TRAILING_AUG_ENABLED_TRADIER=True), {}, None))
        V.append((A, "live: DC_TIER + TRAILING + WT_D_BOUNCE", dict(live, TRAILING_AUG_ENABLED_TRADIER=True, WT_D_BOUNCE_AUG_ENABLED=True), {}, None))
        for c in (1, 2, 3):
            V.append((A, f"live all sources, cap {c}/position", dict(live, TRAILING_AUG_ENABLED_TRADIER=True, WT_D_BOUNCE_AUG_ENABLED=True), {}, c))
        vo = {"STOCK_LIVE_AUGMENT_ONLY": False}
        env = {"V12_STOCK_LIVE_AUGMENT_GUARD": "0"}
        for mg in (2.5, 3.0, 4.5):
            V.append((A, f"vec-only: UAG ladder+pullback trigger {mg}", dict(vo, MIN_GAIN_TO_BUY_AGGRESSIVELY=mg, AUGMENT_MIN_GAIN_PCT=0.0), env, None))
        for c in (1, 2, 3):
            V.append((A, f"vec-only: UAG 2.5, cap {c}/position", dict(vo, MIN_GAIN_TO_BUY_AGGRESSIVELY=2.5, AUGMENT_MIN_GAIN_PCT=0.0), env, c))
        V.append((A, "vec-only: + PYRAMID", dict(vo, PYRAMID_ENABLED=True), env, None))
        V.append((A, "vec-only: + BOUNCE_AUGMENT", dict(vo, BOUNCE_AUGMENT_ENABLED=True), env, None))
        V.append((A, "vec-only + live: UAG 2.5 + DC_TIER + TRAILING", dict(vo, MIN_GAIN_TO_BUY_AGGRESSIVELY=2.5, AUGMENT_MIN_GAIN_PCT=0.0, STOCK_LIVE_AUGMENT_ENABLED=True, TRAILING_AUG_ENABLED_TRADIER=True), env, None))
    else:
        for mg in (2.5, 3.0, 4.5):
            V.append((A, f"UAG ladder+pullback trigger {mg}", {"MIN_GAIN_TO_BUY_AGGRESSIVELY": mg, "AUGMENT_MIN_GAIN_PCT": 0.0}, {}, None))
        for c in (1, 2, 3):
            V.append((A, f"UAG 2.5, cap {c}/position", {"MIN_GAIN_TO_BUY_AGGRESSIVELY": 2.5, "AUGMENT_MIN_GAIN_PCT": 0.0}, {}, c))
        V.append((A, "UAG gate OFF (UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED=False)", {"UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": False}, {}, None))
        V.append((A, "+ PYRAMID", {"PYRAMID_ENABLED": True}, {}, None))
        V.append((A, "+ BOUNCE_AUGMENT", {"BOUNCE_AUGMENT_ENABLED": True}, {}, None))
        V.append((A, "+ FAST_RISER_DOUBLE 1h", {"FAST_RISER_DOUBLE_ENABLED": True, "FAST_RISER_FILTER_TF": "1h"}, {}, None))
        V.append((A, "+ DD_BOUNCE", {"DD_BOUNCE_ENABLED": True}, {}, None))
        V.append((A, "flip HAIKU_WINNER_AUGMENT_ENABLED", {"HAIKU_WINNER_AUGMENT_ENABLED": _flip(base, "HAIKU_WINNER_AUGMENT_ENABLED", False)}, {}, None))
        V.append((A, "+ AUGMENT_AT_LOSS (HL/LH only)", {"AUGMENT_AT_LOSS_ENABLED": True}, {}, None))
    V.append((A, "+ AUGMENT_INTO_REENTRY", {"AUGMENT_INTO_REENTRY_ENABLED": True}, {}, None))
    V.append((A, "ABLATION_DISABLE_AUGMENTATION (signal sources off)", {"ABLATION_DISABLE_AUGMENTATION": True}, {}, None))
    R = "REDUCE"
    V.append((R, "flip PARTIAL_PROFIT_LOCK_ENABLED", {"PARTIAL_PROFIT_LOCK_ENABLED": _flip(base, "PARTIAL_PROFIT_LOCK_ENABLED", False)}, {}, None))
    V.append((R, "REGIME_GATE_ENABLED=True (regime partial reduces)", {"REGIME_GATE_ENABLED": True}, {}, None))
    V.append((R, "flip HLR_TOP_EXIT_ENABLED (+sanctioned)", {"HLR_TOP_EXIT_ENABLED": _flip(base, "HLR_TOP_EXIT_ENABLED", False), "HLR_TOP_EXIT_LIVE_SANCTIONED": True}, {}, None))
    V.append((R, "flip EXIT_DC_BREACH_REDUCE_ENABLED", {"EXIT_DC_BREACH_REDUCE_ENABLED": _flip(base, "EXIT_DC_BREACH_REDUCE_ENABLED", False)}, {}, None))
    V.append((R, "flip MANAGE_REDUCE", {"MANAGE_REDUCE": _flip(base, "MANAGE_REDUCE", True)}, {}, None))
    T = "REENTRY"
    V.append((T, "ABLATION_DISABLE_REENTRY=True (value of reentry)", {"ABLATION_DISABLE_REENTRY": True}, {}, None))
    V.append((T, "flip REENTRY_MANDATORY", {"REENTRY_MANDATORY": _flip(base, "REENTRY_MANDATORY", True)}, {}, None))
    V.append((T, "flip REENTRY_APPLY_ENTRY_GATES_ENABLED", {"REENTRY_APPLY_ENTRY_GATES_ENABLED": _flip(base, "REENTRY_APPLY_ENTRY_GATES_ENABLED", False)}, {}, None))
    V.append((T, "flip REENTRY_CONFIRMATION_GATES_ENABLED", {"REENTRY_CONFIRMATION_GATES_ENABLED": _flip(base, "REENTRY_CONFIRMATION_GATES_ENABLED", True)}, {}, None))
    V.append((T, "REENTRY_PULL1..4 ON", {"REENTRY_PULL1_ENABLED": True, "REENTRY_PULL2_ENABLED": True, "REENTRY_PULL3_ENABLED": True, "REENTRY_PULL4_ENABLED": True}, {}, None))
    V.append((T, "REENTRY_SMA200_GR_CONTINUATION ON", {"REENTRY_SMA200_GR_CONTINUATION_ENABLED": True}, {}, None))
    V.append((T, "REENTRY_K_RESET_GR ON", {"REENTRY_K_RESET_GR_ENABLED": True}, {}, None))
    return V


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--progress", required=True, help="board progress JSON (cumulative_overrides = the published set)")
    ap.add_argument("--frozen-dir", required=True)
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", default="", help="comma list of families (AUGMENT,REDUCE,REENTRY)")
    a = ap.parse_args()
    prog = json.loads(Path(a.progress).expanduser().read_text())
    ss = prog["symside"]
    base_ov = dict(prog.get("cumulative_overrides") or {})
    symbol = ss.rsplit("_", 1)[0]
    is_stock = not any(symbol.endswith(s) for s in ("USDT", "USDC", "BUSD", "FDUSD"))
    _install_cap_hook()
    npz_md5 = _install_frozen_npz(Path(a.frozen_dir).expanduser(), symbol)
    rows = []

    def run(fam, label, ov, env, cap):
        saved = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        _CAP["value"] = cap
        try:
            m = ev(ss, dict(base_ov, **ov), a.window_days)
        finally:
            _CAP["value"] = None
            for k, v in saved.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        row = {"family": fam, "label": label, "overrides": ov, "env": env, "cap": cap, **m}
        rows.append(row)
        print(f"{fam:8s} {label:58s} gain {m['gain_pct']:+8.2f}  tr {m['trades']:4d}  TIM {m['tim_pct']:5.1f}  DD {m['max_dd_pct']:5.2f}  WR {m['wr_pct']:5.1f}  aug {m['n_augments']:3d}  red {m['n_partial_reduces']:3d}  reent {m['n_reentries']:3d}  {m['secs']}s", flush=True)
        return row

    base = run("BASE", "published set, frozen NPZ", {}, {}, None)
    fams = set(x for x in a.only.split(",") if x)
    for fam, label, ov, env, cap in variants(base_ov, is_stock):
        if fams and fam not in fams:
            continue
        run(fam, label, ov, env, cap)
    for r in rows:
        r["delta_vs_base"] = round(r["gain_pct"] - base["gain_pct"], 4)
    out = Path(a.out).expanduser()
    out.parent.mkdir(parents=True, exist_ok=True)
    import hashlib

    eng = hashlib.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()
    out.write_text(json.dumps({"sym_side": ss, "published_final_gain": prog.get("final_gain"), "published_trades": prog.get("final_trades"), "published_bh": prog.get("bh"), "published_npz_md5": (prog.get("npz_id") or {}).get("md5"), "frozen_npz_md5": npz_md5, "engine_md5": eng, "window_days": a.window_days, "measured_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), "base_set_values": {k: base_ov.get(k, "<absent>") for k in ("MAX_AUGMENTS_PER_POSITION", "STOCK_LIVE_AUGMENT_ONLY", "STOCK_LIVE_AUGMENT_ENABLED", "BB4H_BREAKOUT_LADDER_ENABLED", "DC_TIER_AUG_ENABLED", "MIN_GAIN_TO_BUY_AGGRESSIVELY", "AUGMENT_MIN_GAIN_PCT", "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED", "PYRAMID_ENABLED", "TRAILING_AUG_ENABLED_TRADIER", "PARTIAL_PROFIT_LOCK_ENABLED", "REENTRY_MANDATORY")}, "rows": rows}, indent=1, default=str))
    print(f"[augment_probe] {ss} base {base['gain_pct']:+.2f} (published {prog.get('final_gain')}) npz {npz_md5[:8]} (published {(prog.get('npz_id') or {}).get('md5','')[:8]}) -> {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
