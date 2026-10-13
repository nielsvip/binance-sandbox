#!/usr/bin/env python3
"""V17 AUGMENT_INTO_REENTRY parity proof (USER 2026-10-13: "completely wired ... v12_quick_engine, backtest_v12_engine and all the
live scripts"). Run on a worker with REAL NPZs (s2 ~/binance-sandbox; Mac NPZs are truncated).

  identity  For each sym_side x knob-set: v12_quick_engine.simulate_one (evaluate_sanitized, include_ledger) is the truth. Every vec
            OPEN is replayed through live_twins/reentry_augment.py (the function ez_manage.execute_now / tradier_manage.execute_now /
            backtest_v12_engine call) on the vec's own ledger before that bar (+ the NPZ 15m low/high for HL_REF='low') and the
            carry decision + multiple must be identical. Exercises HL_REF entry/low, WINDOW_SEC, NO_PRIOR_AUGMENT on/off, LONG/SHORT.
  scalar    vec leg (simulate_one) vs scalar leg (backtest_v12_engine.run_one, the real live process_position/execute paths) on the
            same frozen NPZ with the set: round trips matched by entry time (tools/v15_trade_parity) and, on matched OPENs, the carry
            multiple compared (native mode: the live ' |AIR_CARRY<m>' tag; VEC_EXACT mode: live qty*px / START_POSITION_SIZE - 1).

Usage: python3 tools/air_parity_check.py identity --sym-sides ADAUSDC_LONG,DOGEUSDC_SHORT [--window-days 30] [--out f.json]
       python3 tools/air_parity_check.py scan --limit 60            (vec-only: sym_sides with the most carried opens)
       python3 tools/air_parity_check.py scalar --sym-side X_LONG [--knobs base] [--mode native|vec_exact]
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")

from live_twins import reentry_augment as RA  # noqa: E402

KNOB_SETS = {
    "base": {"AUGMENT_INTO_REENTRY_ENABLED": True},
    "hl_low": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_HL_REF": "low"},
    "window_1h": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_WINDOW_SEC": 3600.0},
    "window_0": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_WINDOW_SEC": 0.0},
    "prior_aug_ok": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_NO_PRIOR_AUGMENT": False, "MIN_GAIN_TO_BUY_AGGRESSIVELY": 0.3, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": False},
    "prior_aug_block": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_NO_PRIOR_AUGMENT": True, "MIN_GAIN_TO_BUY_AGGRESSIVELY": 0.3, "UNIVERSAL_AUGMENT_GAIN_GATE_ENABLED": False},
    "prior_aug_ok_atloss": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_NO_PRIOR_AUGMENT": False, "AUGMENT_AT_LOSS_ENABLED": True},
    "prior_aug_block_atloss": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_NO_PRIOR_AUGMENT": True, "AUGMENT_AT_LOSS_ENABLED": True},
    "no_hl_mult_half": {"AUGMENT_INTO_REENTRY_ENABLED": True, "REENTRY_AUGMENT_REQUIRE_HIGHER_LOW": False, "REENTRY_AUGMENT_SIZE_MULT": 0.5},
}


def _ts(x):
    return RA.epoch(x)


def npz_bars(sym: str, npz_dir: str | None = None):
    import numpy as np

    p = pathlib.Path(npz_dir or os.environ.get("TRADE_PARITY_NPZ_DIR") or (ROOT / "backtest_v8" / "indicators")) / f"{sym}.npz"
    with np.load(p, allow_pickle=True) as z:
        ts = np.asarray(z["timestamps"], dtype="float64")
        lo = np.asarray(z["low_15m"] if "low_15m" in z.files else z["low"], dtype="float64")
        hi = np.asarray(z["high_15m"] if "high_15m" in z.files else z["high"], dtype="float64")
    if ts.size and float(np.nanmax(ts)) > 1e11:
        ts = ts / 1000.0
    n = min(ts.size, lo.size, hi.size)
    return list(zip(ts[:n].tolist(), lo[:n].tolist(), hi[:n].tolist()))


def vec_run(ss: str, ov: dict, window_days: int):
    from tools.opt.v12_pilot import evaluate_sanitized

    return evaluate_sanitized(ss, dict(ov), window_days=window_days, include_ledger=True)


def vec_log_rows(res: dict):
    """vec execution ledger -> ordered rows {ts, action, price, reason, carry, qty} (CLOSE = realised full-close rows)."""
    out = []
    for e in res.get("execution_ledger") or []:
        t = str(e.get("type") or "").upper()
        if "bar_exit" in e and "pnl_dollars" in e:
            if str(e.get("reason") or e.get("exit_reason") or "").startswith("FINAL_MTM"):
                continue
            out.append({"ts": _ts(e.get("ts")), "action": "CLOSE", "price": float(e.get("exit_price") or e.get("price") or 0.0), "reason": str(e.get("exit_reason") or e.get("reason") or "")})
        elif t in ("OPEN", "AUGMENT", "REDUCE"):
            out.append({"ts": _ts(e.get("ts")), "action": t, "price": float(e.get("price") or 0.0), "reason": str(e.get("reason") or ""), "carry": float(e.get("carry_aug_mult") or 0.0), "carry_qty": float(e.get("carry_aug_qty") or 0.0), "qty": float(e.get("qty") or 0.0)})
    return out


def identity(ss: str, knob: str, window_days: int) -> dict:
    sym, side = ss.rsplit("_", 1)
    is_long = side == "LONG"
    ov = dict(KNOB_SETS[knob])
    res = vec_run(ss, ov, window_days)
    rows = vec_log_rows(res)
    from tools.opt.evaluate_v12 import build_cfg_npz

    cfg, _npz, _s, _l, mode, _tok = build_cfg_npz(ss, dict(ov), window_days)
    get = lambda k, d=None: getattr(cfg, k, RA.DEFAULTS.get(k, d))  # noqa: E731  (the effective QuickConfig the vec simulated)
    whole = mode == "tradier"
    cap = float((getattr(cfg, "MAX_SYMBOL_VALUE_TRADIER", 0.0) if whole else getattr(cfg, "MAX_POSITION_SIZE", 0.0)) or 0.0)
    bars = None
    if str(get("REENTRY_AUGMENT_HL_REF")).lower() == "low":
        bars = npz_bars(sym)
    n_open = n_carry_vec = n_carry_fn = agree = 0
    mism = []
    native = _native_fn(mode, cfg, sym, side, bars) if os.environ.get("AIR_IDENTITY_NATIVE", "1") == "1" else None
    n_native_agree = 0
    native_mism = []
    for i, r in enumerate(rows):
        if r["action"] != "OPEN" or r["reason"] == "SEED_BH":
            continue
        n_open += 1
        log = rows[:i]
        prev = RA.prev_trade(log)
        adv = None
        if bars is not None and prev and RA.as_bool(get("REENTRY_AUGMENT_REQUIRE_HIGHER_LOW", True), True):
            adv = RA.adverse_extreme(bars, prev["entry_ts"], prev["close_ts"], is_long)
        m_fn, why = RA.carry_mult(get, is_long, r["price"], r["ts"], prev, adv)
        qty0 = r["qty"] - r["carry_qty"]
        m_vec = r["carry"]
        _q, _add, m_eff = RA.sized(qty0, m_fn, r["price"], cap, whole)  # same cap / whole-share rule live applies
        n_carry_vec += m_vec > 0
        n_carry_fn += m_eff > 0
        ok = abs(m_vec - m_eff) <= 1e-6 * max(1.0, m_eff)
        agree += ok
        if not ok:
            mism.append({"ts": r["ts"], "px": r["price"], "vec_mult": m_vec, "fn_mult_capped": m_eff, "fn_mult_raw": m_fn, "fn_why": why, "qty0_usd": qty0 * r["price"], "cap_usd": cap})
        if native is not None:
            # the REAL live function (ez_manage._air_native_carry / tradier_manage._air_native_carry_t) on the same state
            nq, nreason, ninfo = native(r["price"], qty0, r["ts"], log)
            n_m = (nq / qty0 - 1.0) if qty0 > 0 else 0.0
            n_ok = abs(m_vec - n_m) <= 1e-6 * max(1.0, n_m) and ((m_vec > 0) == (RA.CARRY_TAG in str(nreason)))
            n_native_agree += n_ok
            if not n_ok:
                native_mism.append({"ts": r["ts"], "vec_mult": m_vec, "native_mult": n_m, "native_why": ninfo.get("why"), "reason": str(nreason)[-24:]})
    return {"sym_side": ss, "knob": knob, "overrides": ov, "valid": res.get("valid"), "trades": res.get("trades"), "gain_pct": res.get("gain_pct"),
            "n_open": n_open, "n_augment_events": sum(1 for x in rows if x["action"] == "AUGMENT"), "n_carry_vec": int(n_carry_vec), "n_carry_fn": int(n_carry_fn), "decision_agree": agree,
            "cap_usd": cap, "identical": agree == n_open, "mismatches": mism[:20],
            "native_fn": (None if native is None else ("ez_manage._air_native_carry" if mode != "tradier" else "tradier_manage._air_native_carry_t")),
            "native_agree": n_native_agree if native is not None else None, "native_identical": (n_native_agree == n_open) if native is not None else None, "native_mismatches": native_mism[:20]}


def _native_fn(mode, cfg, sym, side, bars):
    """Bind the live native function to the vec's effective config (per-sym lookup -> cfg) with the live kill switch on.
    Stocks: the live-only account/HARD symbol caps are neutralised (1e12) so only the vec cap (MAX_SYMBOL_VALUE_TRADIER) binds."""
    if mode != "tradier":
        import ez_manage as E

        def _call(px, qty0, ts, log):
            saved = (E._psym_get, getattr(E.config, RA.LIVE_KEY, None), E._AIR_BARS_LOADER)
            try:
                E._psym_get = lambda s, sd, k, d: getattr(cfg, k, d)
                setattr(E.config, RA.LIVE_KEY, True)
                E._AIR_BARS_LOADER = (lambda s, sd: bars) if bars is not None else (lambda s, sd: None)
                return E._air_native_carry(sym, side, px, qty0, "VEC_OPEN", ts, log)
            finally:
                E._psym_get, _lv, E._AIR_BARS_LOADER = saved
                setattr(E.config, RA.LIVE_KEY, _lv)
        return _call
    import tradier_manage as TM

    def _call_t(px, qty0, ts, log):
        saved = (TM._cfg, getattr(TM.config, RA.LIVE_KEY, None), getattr(TM.config, "TRB_MAX_SYMBOL_VALUE", None), TM._AIR_BARS_LOADER)
        try:
            TM._cfg = lambda k, d=None, a=None, s=None, sd=None, **kw: (1e12 if k == "HARD_MAX_SYMBOL_VALUE_TRADIER" else getattr(cfg, k, d))
            setattr(TM.config, RA.LIVE_KEY, True)
            setattr(TM.config, "TRB_MAX_SYMBOL_VALUE", 1e12)
            TM._AIR_BARS_LOADER = (lambda s, sd: bars) if bars is not None else (lambda s, sd: None)
            return TM._air_native_carry_t("trb", sym, side, px, qty0, "VEC_OPEN", ts, log, 0.0)
        finally:
            TM._cfg, _lv, _tc, TM._AIR_BARS_LOADER = saved
            setattr(TM.config, RA.LIVE_KEY, _lv)
            setattr(TM.config, "TRB_MAX_SYMBOL_VALUE", _tc)
    return _call_t


def scan(limit: int, window_days: int) -> list:
    d = pathlib.Path(os.environ.get("TRADE_PARITY_NPZ_DIR") or (ROOT / "backtest_v8" / "indicators"))
    syms = sorted(p.stem for p in d.glob("*.npz"))
    out = []
    t0 = time.time()
    for s in syms:
        for side in ("LONG", "SHORT"):
            ss = f"{s}_{side}"
            try:
                res = vec_run(ss, KNOB_SETS["base"], window_days)
            except Exception as e:  # noqa: BLE001
                continue
            rows = vec_log_rows(res)
            n = sum(1 for r in rows if r["action"] == "OPEN" and r.get("carry", 0) > 0)
            out.append({"ss": ss, "carried": n, "opens": sum(1 for r in rows if r["action"] == "OPEN"), "trades": res.get("trades"), "valid": res.get("valid")})
        if len(out) >= 2 * limit or time.time() - t0 > 900:
            break
    return sorted(out, key=lambda x: -x["carried"])


def scalar(ss: str, knob: str, window_days: int, mode: str) -> dict:
    os.environ["TRADE_PARITY_VEC_EXACT"] = "1" if mode == "vec_exact" else "0"
    os.environ.setdefault("TEST_RATE_GUARD_MIN_PER_DAY", "0")  # RateGuard low-rate sys.exit is a harness guard, not a result (as tools/v15_trade_parity main)
    import tools.v15_trade_parity as T

    T.harness_env(ss)
    os.chdir(ROOT)
    os.environ.setdefault("BASE_PATH", str(ROOT))
    T.freeze_npz_dir()
    base_set = {} if os.environ.get("AIR_SCALAR_NO_SET") == "1" else (T.load_overrides(ss, os.environ.get("AIR_SCALAR_SET_JSON")) or {})
    ov = {**dict(base_set), **dict(KNOB_SETS[knob])}  # the sym_side's own final set (as tools/v15_trade_parity) + the AIR knobs
    side = ss.rsplit("_", 1)[1]
    is_long = side == "LONG"
    vec = vec_run(ss, ov, window_days)
    import backtest_v12_engine as B

    live_extra = {}
    if mode == "vec_exact":
        live_extra = {"PARITY_VEC_EXACT_MODE": True, "PARITY_VEC_EXACT_FAMILIES": "ENTRY,EXIT,AUGMENT", "VEC_DRIVEN_ENABLED": False, "VEC_DRIVEN_NATIVE_ENTRY_BLOCK_ALL": False, "PARITY_VEC_EXACT_SOURCE": "npz"}
    t0 = time.time()
    live = T._run_live(B, ss, {**ov, **live_extra}, window_days)
    secs = time.time() - t0
    vt = T.round_trips(T.vec_events(vec), is_long)
    lev, _drop = T.live_events(live, side)
    lt = T.round_trips(lev, is_long)
    m = T.match(vt, lt, 30 * 60.0)
    vrows = [r for r in vec_log_rows(vec) if r["action"] == "OPEN"]
    lopens = [e for e in lev if str(e.get("action") or e.get("type") or "").upper() in ("OPEN", "REENTRY", "QUICK_OPEN")]
    try:
        import config as _c
        sps = float(getattr(B.config, "START_POSITION_SIZE", getattr(_c.Config, "START_POSITION_SIZE", 0.0)) or 0.0)
    except Exception:
        sps = 0.0
    pairs = []
    for p in m["pairs"]:
        ve = min(vrows, key=lambda r: abs((r["ts"] or 0) - p["vec"]["entry_ts"]), default=None)
        le = min(lopens, key=lambda e: abs((_ts(e.get("ts") or e.get("timestamp")) or 0) - p["live"]["entry_ts"]), default=None)
        if not ve or not le:
            continue
        lr = str(le.get("reason") or "")
        if RA.CARRY_TAG in lr:
            try:
                lm = float(lr.split(RA.CARRY_TAG, 1)[1][:5])
            except Exception:
                lm = -1.0
            src = "native_tag"
        elif mode == "vec_exact" and sps > 0:
            lq = float(le.get("qty") or le.get("quantity") or 0.0)
            lpx = float(le.get("price") or 0.0)
            lm = round(lq * lpx / sps - 1.0, 3) if lq and lpx else -1.0
            src = "vec_exact_qty"
        else:
            lm, src = 0.0, "native_untagged"
        pairs.append({"entry_ts": p["vec"]["entry_ts"], "vec_mult": round(ve.get("carry", 0.0), 3), "live_mult": lm, "src": src,
                      "agree": abs(round(ve.get("carry", 0.0), 3) - lm) <= 0.002, "live_reason": lr[:90]})
    n_carry_pairs = sum(1 for x in pairs if x["vec_mult"] > 0 or x["live_mult"] > 0)
    return {"sym_side": ss, "knob": knob, "mode": mode, "window_days": window_days, "secs_live": round(secs, 1), "n_set_overrides": len(base_set),
            "vec": {k: vec.get(k) for k in ("trades", "gain_pct", "valid")}, "live": {k: live.get(k) for k in ("trades", "gain_pct", "valid", "invalid_reason")},
            "vec_trips": len(vt), "live_trips": len(lt), "matched": len(m["pairs"]),
            "vec_match_rate": round(len(m["pairs"]) / len(vt), 3) if vt else None, "live_match_rate": round(len(m["pairs"]) / len(lt), 3) if lt else None,
            "matched_opens_compared": len(pairs), "matched_opens_with_carry": n_carry_pairs, "carry_agree": sum(1 for x in pairs if x["agree"]),
            "carry_disagree": [x for x in pairs if not x["agree"]][:25], "carry_examples": [x for x in pairs if x["vec_mult"] > 0][:10],
            "vec_carried_opens_total": sum(1 for r in vrows if r.get("carry", 0) > 0), "live_tagged_opens_total": sum(1 for e in lopens if RA.CARRY_TAG in str(e.get("reason") or ""))}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["identity", "scan", "scalar"])
    ap.add_argument("--sym-sides", default="")
    ap.add_argument("--sym-side", default="")
    ap.add_argument("--knobs", default=",".join(KNOB_SETS))
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--limit", type=int, default=60)
    ap.add_argument("--mode", default="native", choices=["native", "vec_exact"])
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.cmd == "identity":
        rep = [identity(ss, k, a.window_days) for ss in a.sym_sides.split(",") if ss for k in a.knobs.split(",") if k]
        print(json.dumps([{k: r[k] for k in ("sym_side", "knob", "n_open", "n_augment_events", "n_carry_vec", "n_carry_fn", "decision_agree", "identical", "native_agree", "native_identical")} for r in rep]))
    elif a.cmd == "scan":
        rep = scan(a.limit, a.window_days)
        print(json.dumps(rep[:40]))
    else:
        rep = scalar(a.sym_side, a.knobs.split(",")[0], a.window_days, a.mode)
        print("AIR_SCALAR " + json.dumps({k: rep[k] for k in ("sym_side", "knob", "mode", "n_set_overrides", "vec_trips", "live_trips", "matched", "matched_opens_compared", "matched_opens_with_carry", "carry_agree", "vec_carried_opens_total", "live_tagged_opens_total", "secs_live")}))
    if a.out:
        pathlib.Path(a.out).write_text(json.dumps(rep, indent=1, default=str))


if __name__ == "__main__":
    main()
