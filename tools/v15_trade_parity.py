#!/usr/bin/env python3
"""v15_trade_parity — TRADE-BY-TRADE parity between the vectorized sheet engine and the live call path (USER 2026-10-06:
"any vectorized trade is replicated in live … every trade in /SPREADSHEETS/ is actually performed (or filtered correctly)").

For one sym_side's final set (progress cumulative_overrides, or --overrides-json) both legs run on the SAME frozen window:
  vec  = tools.opt.v12_pilot.evaluate_sanitized(..., include_ledger=True)          (what the sheet measured)
  live = backtest_v12_engine.run_one(...)  -> execution_ledger                      (real ez_/tradier_ process_position)
Each ledger is folded into ROUND TRIPS (first OPEN while flat -> position flat again); trips are matched by entry time
(tolerance --tol-min, default 30 min = 2×15m bars) and same side. Output:
  matched (entry/exit time deltas, exit reason pair), VEC_ONLY trips (sheet trades live would not take — gap evidence,
  keyed by vec entry/exit reason family), LIVE_ONLY trips (live trades the sheet does not show), and a verdict:
  PASS iff vec_match_rate >= --min-match and live_match_rate >= --min-match (default 0.80) and both legs ran.
One JSON report per sym_side (--out-dir, default data/reports/trade_parity/) + one TRADE_PARITY line on stdout.
The gap register (tools/v15_trade_parity.py --aggregate DIR) ranks unmatched reason families across sym_sides by count
and |pnl| — that is the per-gap evidence list (USER rule: each gap closed in the direction the evidence favours).
"""
from __future__ import annotations

import argparse
import json
import os
import pathlib
import socket
import sys
import time
from collections import defaultdict

# the repo this script lives in (isolated copies must never import the sandbox's live modules); TRADE_PARITY_ROOT overrides
ROOT = pathlib.Path(os.environ.get("TRADE_PARITY_ROOT") or pathlib.Path(__file__).resolve().parents[1])

OPEN_ACTS = ("OPEN", "QUICK_OPEN", "REENTRY", "HEDGE_OPEN", "ENTRY")
ADD_ACTS = ("AUGMENT", "QUICK_AUGMENT")


def _ts(x) -> float:
    try:
        t = float(x or 0.0)
    except Exception:
        return 0.0
    return t / 1000.0 if t > 1e11 else t


def family(reason) -> str:
    """reason family: cut at the first ' (:|=@', then drop every '_'-token after the first that carries a digit
    (TFs, velocities, gains, prices, ages) so EXIT_VELOCITY_WT_4h_vel-8.3-against-long_g-0.38 -> EXIT_VELOCITY_WT."""
    s = str(reason or "").strip()
    for sep in (" ", "(", ":", "|", "=", "@"):
        s = s.split(sep, 1)[0]
    toks = [t for t in s.upper().split("_") if t != ""]
    if not toks:
        return "?"
    keep = [toks[0]] + [t for t in toks[1:] if not any(ch.isdigit() for ch in t) and t not in ("D", "W", "H", "M")]
    return "_".join(keep)[:48] or "?"


def _f(x) -> float:
    try:
        return float(x or 0.0)
    except Exception:
        return 0.0


def round_trips(events: list, is_long: bool | None = None) -> list:
    """events: [{ts, type|action, reason, qty, price, pnl_pct}] in time order -> trips
    [{entry_ts, exit_ts, entry_reason, exit_reason, n_aug, n_red, pnl_pct, pnl_px_pct}] (open trip at the end -> exit_ts None).
    pnl_pct = sum of the events' own pnl_pct (vec: % of deployed per close; scalar execution events usually carry none -> 0).
    pnl_px_pct (only when is_long is given and events carry qty+price) = realised price PnL / peak trip notional * 100,
    the comparable PnL for both legs (no fees)."""
    trips, cur = [], None
    sgn = None if is_long is None else (1.0 if is_long else -1.0)
    for e in sorted((x for x in events if isinstance(x, dict)), key=lambda x: _ts(x.get("ts"))):
        typ = str(e.get("type") or "").upper()
        act = str(e.get("action") or typ).upper()
        reason = e.get("reason") or e.get("entry_reason") or e.get("exit_reason") or ""
        is_open = typ == "OPEN" and not any(a in act for a in ADD_ACTS)
        is_add = typ == "AUGMENT" or any(a in act for a in ADD_ACTS)
        is_reduce = typ == "REDUCE" or ("REDUCE" in act and "CLOSE" not in act)
        is_close = typ == "CLOSE" and not is_reduce
        q, px = abs(_f(e.get("qty"))), _f(e.get("price"))
        if cur is None:
            if is_open or is_add:
                cur = {"entry_ts": _ts(e.get("ts")), "entry_reason": str(reason)[:120], "n_aug": 0, "n_red": 0, "exit_ts": None, "exit_reason": None, "pnl_pct": 0.0,
                       "entry_price": px or None, "_q": q, "_avg": px, "_peak": q * px, "_real": 0.0, "_px_ok": q > 0 and px > 0}
            continue
        if is_add or (is_open and cur is not None):
            cur["n_aug"] += 1
            if q > 0 and px > 0:
                nq = cur["_q"] + q
                cur["_avg"] = (cur["_avg"] * cur["_q"] + px * q) / nq if nq else px
                cur["_q"] = nq
                cur["_peak"] = max(cur["_peak"], nq * cur["_avg"])
            else:
                cur["_px_ok"] = False
        elif is_reduce or is_close:
            if is_reduce:
                cur["n_red"] += 1
            cur["pnl_pct"] += float(e.get("pnl_pct") or 0.0)
            qc = cur["_q"] if is_close else min(q, cur["_q"])
            if px > 0 and qc > 0 and sgn is not None:
                cur["_real"] += (px - cur["_avg"]) * qc * sgn
                cur["_q"] -= qc
            elif not px:
                cur["_px_ok"] = False
            if is_close:
                cur["exit_ts"] = _ts(e.get("ts"))
                cur["exit_reason"] = str(reason)[:120]
                cur["exit_price"] = px or None
                trips.append(cur)
                cur = None
    if cur is not None:
        trips.append(cur)
    for t in trips:
        ok = sgn is not None and t.pop("_px_ok") and t["_peak"] > 0 and t["exit_ts"] is not None
        t["pnl_px_pct"] = round(t["_real"] / t["_peak"] * 100.0, 4) if ok else None
        for k in ("_q", "_avg", "_peak", "_real"):
            t.pop(k, None)
        t.pop("_px_ok", None)
    return trips


def trip_pnl(t: dict) -> float:
    return float(t["pnl_px_pct"]) if t.get("pnl_px_pct") is not None else float(t.get("pnl_pct") or 0.0)


def vec_events(res: dict) -> list:
    """vec ledger -> event list. Prefer execution_ledger (OPEN/AUGMENT/REDUCE/CLOSE with reasons); else rebuild from CLOSE rows."""
    ex = (res or {}).get("execution_ledger") or []
    if ex:
        out = []
        for e in ex:
            t = str(e.get("type") or "").upper()
            out.append({"ts": e.get("ts"), "type": t, "action": t, "reason": e.get("reason") or e.get("entry_reason") or e.get("exit_reason"), "pnl_pct": e.get("pnl_pct"),
                        "qty": e.get("qty"), "price": e.get("price") or e.get("exit_price")})
        return out
    out = []
    for t in (res or {}).get("ledger") or []:
        if str(t.get("type") or "").upper() != "CLOSE":
            continue
        ets = t.get("entry_ts") or t.get("ts_entry")
        if ets is not None:
            out.append({"ts": ets, "type": "OPEN", "reason": t.get("entry_reason")})
        out.append({"ts": t.get("ts"), "type": "CLOSE", "reason": t.get("exit_reason") or t.get("reason"), "pnl_pct": t.get("pnl_pct")})
    return out


def live_events(live: dict, side: str | None) -> tuple:
    """scalar execution_ledger -> (events of the tested side only, n_dropped_other_side). Events without a position key are kept."""
    out, dropped = [], 0
    for e in (live or {}).get("execution_ledger") or []:
        pk = str(e.get("position_key") or e.get("position_side") or "").upper()
        if side and pk and ((side == "LONG" and pk.endswith("SHORT")) or (side == "SHORT" and pk.endswith("LONG"))):
            dropped += 1
            continue
        out.append(e)
    return out, dropped


def match(vtrips: list, ltrips: list, tol_s: float) -> dict:
    used, pairs = set(), []
    for vi, v in enumerate(vtrips):
        best = None
        for li, lt in enumerate(ltrips):
            if li in used:
                continue
            d = abs(lt["entry_ts"] - v["entry_ts"])
            if d <= tol_s and (best is None or d < best[0]):
                best = (d, li)
        if best is not None:
            used.add(best[1])
            lt = ltrips[best[1]]
            dx = None if (v["exit_ts"] is None or lt["exit_ts"] is None) else lt["exit_ts"] - v["exit_ts"]
            pairs.append({"vec": v, "live": lt, "d_entry_s": lt["entry_ts"] - v["entry_ts"], "d_exit_s": dx,
                          "exit_agree": dx is not None and abs(dx) <= tol_s})
    mv = {id(p["vec"]) for p in pairs}
    return {"pairs": pairs, "vec_only": [v for v in vtrips if id(v) not in mv], "live_only": [lt for i, lt in enumerate(ltrips) if i not in used]}


def summarize(vtrips, ltrips, m, min_match: float) -> dict:
    nv, nl, nm = len(vtrips), len(ltrips), len(m["pairs"])
    vr = nm / nv if nv else (1.0 if nl == 0 else 0.0)
    lr = nm / nl if nl else (1.0 if nv == 0 else 0.0)
    ex_ok = sum(1 for p in m["pairs"] if p["exit_agree"])
    gaps = defaultdict(lambda: {"n": 0, "pnl_pct": 0.0})
    for v in m["vec_only"]:
        g = gaps[f"VEC_ONLY entry:{family(v['entry_reason'])}"]
        g["n"] += 1
        g["pnl_pct"] += trip_pnl(v)
    for lt in m["live_only"]:
        g = gaps[f"LIVE_ONLY entry:{family(lt['entry_reason'])}"]
        g["n"] += 1
        g["pnl_pct"] += trip_pnl(lt)
    for p in m["pairs"]:
        if not p["exit_agree"]:
            g = gaps[f"EXIT_DIFF vec:{family(p['vec']['exit_reason'])} live:{family(p['live']['exit_reason'])}"]
            g["n"] += 1
            g["pnl_pct"] += trip_pnl(p["vec"]) - trip_pnl(p["live"])
    ok = nv + nl > 0 and vr >= min_match and lr >= min_match

    def _mix(trips, k):
        c = defaultdict(int)
        for t in trips:
            c[family(t.get(k))] += 1
        return dict(sorted(c.items(), key=lambda kv: -kv[1])[:25])

    de = sorted(p["d_entry_s"] for p in m["pairs"])
    return {"verdict": "PASS" if ok else "FAIL", "vec_trips": nv, "live_trips": nl, "matched": nm, "vec_match_rate": round(vr, 4),
            "live_match_rate": round(lr, 4), "exit_agree_rate": round(ex_ok / nm, 4) if nm else None,
            "median_d_entry_s": de[len(de) // 2] if de else None,
            "vec_pnl_sum": round(sum(trip_pnl(t) for t in vtrips), 3), "live_pnl_sum": round(sum(trip_pnl(t) for t in ltrips), 3),
            "mix": {"vec_entry": _mix(vtrips, "entry_reason"), "vec_exit": _mix(vtrips, "exit_reason"),
                    "live_entry": _mix(ltrips, "entry_reason"), "live_exit": _mix(ltrips, "exit_reason")},
            "gaps": dict(sorted(gaps.items(), key=lambda kv: -kv[1]["n"]))}


def load_overrides(symside: str, path: str | None) -> dict:
    if path:
        d = json.loads(pathlib.Path(path).read_text())
        return d.get("cumulative_overrides") or d.get("best_overrides") or d.get("overrides") or d
    pj = ROOT / "data" / "reports" / "lifecycle_pilot" / f"{symside}_v14_progress.json"
    if pj.exists():
        return json.loads(pj.read_text()).get("cumulative_overrides") or {}
    return {}


def freeze_npz_dir() -> str | None:
    """(c) TRADE_PARITY_NPZ_DIR: both legs read NPZs from a frozen dir (the S1->s2 sync rewrites backtest_v8/indicators during
    runs). vec: v12_quick_engine.load_npz default dir + evaluate_v12._frozen_npz_start_date anchor; live: V12_NPZ_DIR (engine
    get_npz_dir). Returns the dir or None."""
    d = os.environ.get("TRADE_PARITY_NPZ_DIR")
    if not d:
        return None
    os.environ["V12_NPZ_DIR"] = d
    import v12_quick_engine as _V
    from tools.opt import evaluate_v12 as _E
    if not getattr(_V.load_npz, "_tp_frozen", False):
        _orig = _V.load_npz

        def _load(mode, symbols, start_date, npz_dir=""):
            return _orig(mode, symbols, start_date, npz_dir or d)
        _load._tp_frozen = True
        _V.load_npz = _load

        def _start(sym, window_days, offset_days=0):
            from datetime import datetime, timezone
            import numpy as np
            p = pathlib.Path(d) / f"{sym}.npz"
            if not p.exists():
                return "1970-01-01"
            with np.load(p, allow_pickle=True) as z:
                k = "timestamps" if "timestamps" in z.files else next((x for x in ("timestamp_3m", "timestamp_5m") if x in z.files), None)
                if k is None:
                    return "1970-01-01"
                ts = np.asarray(z[k], dtype="float64")
            f = ts[np.isfinite(ts) & (ts > 0)]
            if f.size == 0:
                return "1970-01-01"
            e = float(f[-1]) / (1000.0 if float(f[-1]) > 1e11 else 1.0)
            return datetime.fromtimestamp(e - (max(0, int(window_days)) + max(0, int(offset_days))) * 86400.0, tz=timezone.utc).strftime("%Y-%m-%d")
        _E._frozen_npz_start_date = _start
    return d


def generic_window_slice(window_days: int) -> None:
    """evaluate_v12._exact_30d_slice silently maps any window other than 1/7/30/365 to 30 (crypto 30 calendar days, stocks 30
    sessions). Large parity runs 90D: crypto = N calendar days, stocks = N trading sessions, same shape as the 30D policy."""
    wd = int(window_days)
    if wd in (1, 7, 30) or wd >= 365:
        return
    from tools.opt import evaluate_v12 as _E
    if getattr(_E._exact_30d_slice, "_tp_generic", False):
        return
    _orig = _E._exact_30d_slice

    def _slice(npz, crypto, window_days=30):
        import numpy as np
        if int(window_days) != wd:
            return _orig(npz, crypto, window_days)
        raw = np.asarray(npz.get("timestamps", ()), dtype="float64")
        fin = np.flatnonzero(np.isfinite(raw) & (raw > 0))
        if fin.size < 2:
            raise ValueError("NPZ has no usable timestamps")
        sec = raw / (1000.0 if float(raw[fin[-1]]) > 1e11 else 1.0)
        right = int(fin[-1]) + 1
        if crypto:
            left = int(np.searchsorted(sec, float(sec[fin[-1]]) - wd * 86400.0, side="left"))
            pol = f"{wd}_calendar_days"
        else:
            days = sec.astype("datetime64[s]").astype("datetime64[D]")
            uniq = np.unique(days[fin])
            if len(uniq) < wd:
                raise ValueError(f"NPZ has only {len(uniq)} distinct stock sessions (need {wd})")
            left = int(np.searchsorted(days, uniq[-wd], side="left"))
            pol = f"{wd}_trading_sessions"
        n = len(raw)
        return {k: (v[left:right] if isinstance(v, np.ndarray) and v.ndim and len(v) == n else v) for k, v in npz.items()}, pol
    _slice._tp_generic = True
    _E._exact_30d_slice = _slice


SANDBOX_MARK = os.sep + "binance-sandbox" + os.sep


def assert_isolated() -> dict:
    """fail closed: every loaded project module must come from ROOT (no sandbox / other-copy imports)."""
    bad = {}
    root = str(ROOT.resolve()) + os.sep
    for name in ("backtest_v12_engine", "ez_manage", "ez_positions_quick", "tradier_manage", "tradier_vec_exact", "config", "config_tradier",
                 "v12_quick_engine", "live_twins.vec_exact", "tools.opt.evaluate_v12", "tools.opt.v12_pilot", "per_sym_store", "backtest_v8_harness"):
        m = sys.modules.get(name)
        f = getattr(m, "__file__", None) if m else None
        if f and not str(pathlib.Path(f).resolve()).startswith(root):
            bad[name] = f
    for name, m in list(sys.modules.items()):
        f = getattr(m, "__file__", None) or ""
        if SANDBOX_MARK in f and "site-packages" not in f and (os.sep + ".venv" + os.sep) not in f and not str(ROOT.resolve()).endswith("binance-sandbox"):
            bad[name] = f
    if bad:
        raise RuntimeError(f"NOT ISOLATED: modules outside TRADE_PARITY_ROOT {ROOT}: {dict(list(bad.items())[:8])}")
    return {"root": str(ROOT), "checked": True}


def harness_env(symside: str) -> None:
    """MUST run before backtest_v12_engine is imported (its module body applies overrides at import).
    V8_FORCE_REAL=1: no AUTO_VECTOR load — otherwise the UNION of every sym's hourly_reconfig/trb overrides (224 keys from 208
    syms; 89-119 keys absent from a given final set) is applied globally at import and contaminates the live leg.
    V8_RESEARCH_ALLOW_SIDE_KEYS=SS: run the tested side even when it is not in symbols_<acct>_<side>.json (the report records
    in_live_universe separately, so the universe gate is reported, not confused with decision logic)."""
    os.environ.setdefault("V8_FORCE_REAL", "1")
    # run_one forces V8_SWEEP_MODE=1, whose BH_FLOOR_REARM zeroes 11 stock entry gates (score/GR/HTF thresholds) in tm.config:
    # not live-faithful -> keep the live gates. Flat-key stock candidates every simulated bar (was every 3rd).
    os.environ.setdefault("V8_KEEP_ENTRY_GATES", "1")
    # USER RULING H1: the live leg holds only what the live scripts produce (no vec-predicate producers injected by the harness)
    os.environ.setdefault("V12_LIVE_ONLY_PRODUCERS", "1")
    # V8_PARITY_MODE=1 switches off the harness's own v8-only exit/hedge mirrors (RIDICULOUS_HOLD, UNDERWATER, GR_HTF_DIRECT,
    # OBLIGATORY_HEDGE, PPL bt, GOLDEN); V12_LIVE_ONLY_PRODUCERS gates the rest (HTF_AGAINST, MTF_ATR_TRAIL x2, BB_FROZEN, R4)
    os.environ.setdefault("V8_PARITY_MODE", "1")
    os.environ.setdefault("V8_KEEP_NPZ_COMPOSITE", "1")
    os.environ.setdefault("V12_REENTRY2_LIVE_CADENCE", "1")
    os.environ.setdefault("V12_TWIN_PREFIX_EVAL", "1")
    os.environ.setdefault("V12_LTF_FROM_15M", "1")
    if os.environ.get("TRADE_PARITY_VEC_EXACT", "1") == "1":
        os.environ.setdefault("V8_VEC_EXACT_INGEST", "1")
    os.environ.setdefault("V12_FLAT_CANDIDATE_EVERY", "1")
    # set precedence (default): the set reaches live per-sym readers as if PROMOTED (see backtest_v12_engine
    # _run_one_set_precedence_on). TRADE_PARITY_SET_PRECEDENCE=0 = 'store' mode: the live per_sym_store.db snapshot shadows
    # the set (= what live trades today, not what the sheet measured).
    if os.environ.get("TRADE_PARITY_SET_PRECEDENCE", "1") == "1":
        os.environ.setdefault("V12_RUN_ONE_SET_PRECEDENCE", "1")
    if os.environ.get("TRADE_PARITY_RESEARCH_ALLOW", "1") == "1":
        os.environ.setdefault("V8_RESEARCH_ALLOW_SIDE_KEYS", symside.upper())


def npz_stamp(symside: str, npz_dir: str | None = None) -> dict:
    """the frozen NPZ both legs read (not part of engine_md5: same code + different NPZ = different trades)."""
    import hashlib
    sym = symside.rsplit("_", 1)[0]
    p = (pathlib.Path(npz_dir) if npz_dir else ROOT / "backtest_v8" / "indicators") / f"{sym}.npz"
    try:
        st = p.stat()
        h = hashlib.md5()
        with open(p, "rb") as fh:
            for chunk in iter(lambda: fh.read(1 << 22), b""):
                h.update(chunk)
        return {"path": str(p), "md5": h.hexdigest(), "size": st.st_size, "mtime": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(st.st_mtime))}
    except Exception as e:
        return {"path": str(p), "error": str(e)[:80]}


def in_live_universe(symside: str) -> dict:
    sym, side = symside.rsplit("_", 1)
    cr = sym.upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD"))
    acct = "ang" if cr else "trb"
    p = ROOT / f"symbols_{acct}_{side.lower()}.json"
    try:
        lst = json.loads(p.read_text())
        return {"file": p.name, "in_universe": sym in lst}
    except Exception:
        return {"file": p.name, "in_universe": None}


def run(symside: str, ov: dict, window_days: int, tol_min: float, min_match: float) -> dict:
    harness_env(symside)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    os.environ.setdefault("BASE_PATH", str(ROOT))
    from tools.opt.v12_pilot import evaluate_sanitized
    frozen_dir = freeze_npz_dir()
    generic_window_slice(window_days)
    ov = dict(ov)
    both_notes = {}
    # H2 (USER RULING 2026-10-06): TRADE_PARITY_LIVE_ABLATION=live_both -> the LIVE config's ABLATION_DISABLE_* values
    # (config.py) go identically into BOTH legs (vec measured with what live really runs).
    if os.environ.get("TRADE_PARITY_LIVE_ABLATION") == "live_both":
        try:
            import config as _lc
            _abl = {k: getattr(_lc.Config, k) for k in dir(_lc.Config) if k.startswith("ABLATION_DISABLE_") and isinstance(getattr(_lc.Config, k), bool)}
            both_notes["ablation_live_values"] = _abl
            ov.update(_abl)
        except Exception as _e:
            both_notes["ablation_error"] = str(_e)[:120]
    # PARITY_VEC_EXACT_MODE passthrough (master that switches off non-vectorizable live functions; lanes B/C/D own it)
    # DIRECTOR 2026-10-06: verdicts run in the TARGET live configuration = PARITY_VEC_EXACT_MODE with FAMILIES=ENTRY,EXIT,AUGMENT
    # (native openers replaced by the vec twin; runbook data/parity/PARITY_VEC_EXACT_SWITCHOVER_RUNBOOK.md §3). Default ON;
    # TRADE_PARITY_VEC_EXACT=0 = labelled seam/native diagnostic. Live-leg only (the vec leg is the reference).
    vec_exact = os.environ.get("TRADE_PARITY_VEC_EXACT", "1") == "1"
    live_extra = {}
    if vec_exact:
        live_extra = {"PARITY_VEC_EXACT_MODE": True, "PARITY_VEC_EXACT_FAMILIES": os.environ.get("TRADE_PARITY_VEC_EXACT_FAMILIES", "ENTRY,EXIT,AUGMENT"),
                      "VEC_DRIVEN_ENABLED": False, "VEC_DRIVEN_NATIVE_ENTRY_BLOCK_ALL": False}
        both_notes["live_vec_exact"] = live_extra
    t0 = time.time()
    try:
        vec = evaluate_sanitized(symside, dict(ov), window_days=window_days, include_ledger=True)
    except Exception as e:
        vec = {"valid": False, "invalid_reason": f"vec {e}"[:150]}
    t1 = time.time()
    # (a) stocks loop 2026-10-06: re-pin ROOT first and fail closed if the engine/vec modules resolved anywhere else
    # (a sandbox tools/tools symlink once made isolated copies run the shared sandbox code).
    while str(ROOT) in sys.path:
        sys.path.remove(str(ROOT))
    sys.path.insert(0, str(ROOT))
    import backtest_v12_engine as B
    import tools.opt.v12_pilot as _vp
    for _m in (B, _vp):
        if not str(pathlib.Path(_m.__file__).resolve()).startswith(str(ROOT.resolve()) + os.sep):
            raise RuntimeError(f"module {_m.__name__} resolved outside TRADE_PARITY_ROOT: {_m.__file__} (ROOT {ROOT})")
    dropped_after_crash = []
    ov_live = {**dict(ov), **live_extra}
    live_scope = os.environ.get("TRADE_PARITY_LIVE_ABLATION", "set")
    if live_scope == "live":
        # live config.py ablation values (crypto: QUICK_ENTRY/EXIT, HEDGE, AUGMENTATION, REENTRY_ENFORCE... True) instead of the
        # sweep set's False (BIBLE §64 ablation exemption keeps backtest defaults False; EPQ reads them as GLOBAL config, so a
        # per-sym promotion of the set would not switch them off live).
        ov_live = {k: v for k, v in ov_live.items() if not str(k).startswith("ABLATION_DISABLE_")}
    live = _run_live(B, symside, ov_live, window_days)
    # a crash whose traceback names an override key (type problem the pre-apply guard did not catch) -> report it,
    # drop exactly those keys and retry ONCE, so one bad override never hides the whole live leg.
    if not live.get("valid") and str(live.get("invalid_reason", "")).startswith("run_one") and live.get("trace"):
        bad = [k for k in ov if len(k) > 6 and k in str(live["trace"])]
        if bad:
            dropped_after_crash = [{"key": k, "value": repr(ov[k])[:80], "crash": str(live.get("invalid_reason"))[:150]} for k in bad]
            live = _run_live(B, symside, {k: v for k, v in ov_live.items() if k not in bad}, window_days)
    t2 = time.time()
    isolation = assert_isolated()
    side = "SHORT" if symside.upper().endswith("_SHORT") else "LONG"
    is_long = side == "LONG"
    vt = round_trips(vec_events(vec), is_long)
    lev, dropped_side = live_events(live, side)
    lt = round_trips(lev, is_long)
    m = match(vt, lt, tol_min * 60.0)
    rep = summarize(vt, lt, m, min_match)
    if not live.get("execution_ledger"):
        rep["verdict"] = "UNAVAILABLE" if not live.get("valid") and not live.get("trades") else rep["verdict"]
    rep.update({"symside": symside, "window_days": window_days, "n_overrides": len(ov), "tol_min": tol_min,
                "host": socket.gethostname(), "live_universe": in_live_universe(symside), "isolation": isolation, "npz": npz_stamp(symside, frozen_dir), "npz_frozen_dir": frozen_dir, "mode_label": "VEC_EXACT" if vec_exact else "SEAM_DIAGNOSTIC", "live_ablation_scope": live_scope, "both_leg_overrides": both_notes, "n_overrides_live": len(ov_live), "engine_md5": engine_md5(), "env": {k: os.environ.get(k) for k in HARNESS_ENV},
                "vec": {k: vec.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid", "invalid_reason")},
                "live": {k: live.get(k) for k in ("gain_pct", "trades", "tim_pct", "max_dd_pct", "valid", "invalid_reason")},
                "live_trace": live.get("trace"), "precedence": "set" if os.environ.get("V12_RUN_ONE_SET_PRECEDENCE") == "1" else "store", "set_precedence": live.get("set_precedence"), "override_issues": live.get("override_issues") or [], "dropped_after_crash": dropped_after_crash,
                "live_events_other_side_dropped": dropped_side,
                "secs": {"vec": round(t1 - t0, 1), "live": round(t2 - t1, 1)},
                "vec_trips_all": vt[:1500], "live_trips_all": lt[:1500], "live_events_raw": lev[:4000],
                "vec_only": m["vec_only"][:400], "live_only": m["live_only"][:400],
                "exit_diffs": [p for p in m["pairs"] if not p["exit_agree"]][:400], "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
    return rep


HARNESS_ENV = ("V12_LTF_FROM_15M", "V12_TWIN_PREFIX_EVAL", "V12_REENTRY2_LIVE_CADENCE", "V8_KEEP_NPZ_COMPOSITE", "TRADE_PARITY_NPZ_DIR", "V8_VEC_EXACT_INGEST", "TRADE_PARITY_VEC_EXACT_FAMILIES", "V12_LIVE_ONLY_PRODUCERS", "V12_REAL_EXECUTE", "TRADE_PARITY_VEC_EXACT", "V8_KEEP_ENTRY_GATES", "V12_FLAT_CANDIDATE_EVERY", "PYTHONHASHSEED", "V12_RUN_ONE_SET_PRECEDENCE", "TRADE_PARITY_SET_PRECEDENCE", "V8_FORCE_REAL", "V8_RESEARCH_ALLOW_SIDE_KEYS", "TRADE_PARITY_LIVE_ABLATION", "TEST_RATE_GUARD_MIN_PER_DAY", "V8_PARITY_MODE", "V8_PRESERVE_DEBOUNCE", "V8_SIM_GAP_EXIT_HELD_THROUGH", "V12_F1_ENTRY_VET",
               "V12_F1_SINGLE_SIDE_PORTFOLIO_GATES_OFF", "V8_DECISION_ONLY", "V12_SIM_ALLOW_LIVE_MARKET_FILE", "V12_NPZ_CACHE")
ENGINE_FILES = ("v12_quick_engine.py", "backtest_v12_engine.py", "ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py")


def engine_md5() -> dict:
    """md5 of the files that decide both legs, on THIS host (+ composite of vec_decisions/*.py). Aggregate only same-set reports."""
    import hashlib
    out = {}
    for f in ENGINE_FILES:
        p = ROOT / f
        out[f] = hashlib.md5(p.read_bytes()).hexdigest() if p.exists() else None
    h = hashlib.md5()
    for p in sorted((ROOT / "vec_decisions").glob("*.py")):
        if not p.name.startswith("test_"):
            h.update(f"{p.name}={hashlib.md5(p.read_bytes()).hexdigest()}\n".encode())
    out["vec_decisions/*"] = h.hexdigest()
    out["set"] = hashlib.md5(json.dumps(out, sort_keys=True).encode()).hexdigest()[:12]
    return out


def _run_live(B, symside: str, ov: dict, window_days: int) -> dict:
    try:
        return B.run_one(symside, dict(ov), window_days=window_days)
    except BaseException as e:  # RateGuard sys.exit -> honest FAIL, never a silent gap
        import traceback
        return {"valid": False, "invalid_reason": f"live {type(e).__name__}: {e}"[:150], "trace": traceback.format_exc()[-3000:]}


def regap(r: dict) -> dict:
    """recompute the gap keys of a stored report with the current family() (reports keep the unmatched trips)."""
    gaps = defaultdict(lambda: {"n": 0, "pnl_pct": 0.0})
    for v in r.get("vec_only") or []:
        g = gaps[f"VEC_ONLY entry:{family(v.get('entry_reason'))}"]
        g["n"] += 1
        g["pnl_pct"] += trip_pnl(v)
    for lt in r.get("live_only") or []:
        g = gaps[f"LIVE_ONLY entry:{family(lt.get('entry_reason'))}"]
        g["n"] += 1
        g["pnl_pct"] += trip_pnl(lt)
    for p in r.get("exit_diffs") or []:
        g = gaps[f"EXIT_DIFF vec:{family(p['vec'].get('exit_reason'))} live:{family(p['live'].get('exit_reason'))}"]
        g["n"] += 1
        g["pnl_pct"] += trip_pnl(p["vec"]) - trip_pnl(p["live"])
    return dict(gaps)


def aggregate(d: str, md5_set: str | None = None) -> dict:
    """Gap register over one engine md5 set only (default: the set with the most reports; reports without a stamp are
    excluded and listed). Mixing engine eras would blame one era's gap on another's code."""
    reports = []
    for f in sorted(pathlib.Path(d).glob("*_trade_parity.json")):
        try:
            reports.append(json.loads(f.read_text()))
        except Exception:
            continue
    sets = defaultdict(list)
    for r in reports:
        sets[(r.get("engine_md5") or {}).get("set")].append(r.get("symside"))
    if md5_set is None:
        stamped = {k: v for k, v in sets.items() if k}
        md5_set = max(stamped, key=lambda k: len(stamped[k])) if stamped else None
    use = [r for r in reports if (r.get("engine_md5") or {}).get("set") == md5_set]
    reg = defaultdict(lambda: {"n": 0, "pnl_pct": 0.0, "symsides": set(), "abs_pnl": 0.0})
    verdicts = defaultdict(int)
    table = []
    for r in use:
        verdicts[r.get("verdict")] += 1
        table.append({k: r.get(k) for k in ("symside", "verdict", "vec_trips", "live_trips", "matched", "vec_match_rate", "live_match_rate", "exit_agree_rate",
                                            "median_d_entry_s", "vec_pnl_sum", "live_pnl_sum")} | {"vec": r.get("vec"), "live": r.get("live"),
                                                                                                "override_issues": len(r.get("override_issues") or []),
                                                                                                "dropped_after_crash": len(r.get("dropped_after_crash") or [])})
        gaps = r.get("gaps") or {}
        if "vec_only" in r and "live_only" in r:
            gaps = regap(r)
        for k, g in gaps.items():
            e = reg[k]
            e["n"] += g["n"]
            e["pnl_pct"] += g["pnl_pct"]
            e["abs_pnl"] += abs(g["pnl_pct"])
            e["symsides"].add(r.get("symside"))
    rows = sorted(({"gap": k, "n": v["n"], "n_symsides": len(v["symsides"]), "pnl_pct_sum": round(v["pnl_pct"], 3), "abs_pnl_sum": round(v["abs_pnl"], 3),
                    "symsides": sorted(v["symsides"])} for k, v in reg.items()), key=lambda x: (-x["n_symsides"], -x["n"]))
    return {"md5_set": md5_set, "engine_md5": (use[0].get("engine_md5") if use else None), "sets_seen": {str(k): v for k, v in sets.items()},
            "verdicts": dict(verdicts), "table": table, "register": rows}


def main():
    # backtest_v12_engine re-execs the process with PYTHONHASHSEED=0 on import: do it FIRST so the vec leg is not run twice
    if os.environ.get("PYTHONHASHSEED") != "0" and os.environ.get("V8_HASHSEED_LOCKED") != "1":
        os.environ["PYTHONHASHSEED"] = "0"
        sys.stdout.flush()
        os.execv(sys.executable, [sys.executable] + sys.argv)
    ap = argparse.ArgumentParser()
    ap.add_argument("--sym-side")
    ap.add_argument("--overrides-json")
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--tol-min", type=float, default=30.0)
    ap.add_argument("--min-match", type=float, default=0.80)
    ap.add_argument("--out-dir", default=str(ROOT / "data" / "reports" / "trade_parity"))
    ap.add_argument("--aggregate")
    ap.add_argument("--md5-set")
    a = ap.parse_args()
    if a.aggregate:
        print(json.dumps(aggregate(a.aggregate, a.md5_set), indent=1))
        return
    # RateGuard's projected-low-rate sys.exit is a test-harness guard, not a strategy result (enc. 04 §3 #5): off for trade parity
    os.environ.setdefault("TEST_RATE_GUARD_MIN_PER_DAY", "0")
    ov = load_overrides(a.sym_side, a.overrides_json)
    try:
        rep = run(a.sym_side, ov, a.window_days, a.tol_min, a.min_match)
    except Exception as e:
        rep = {"symside": a.sym_side, "verdict": "UNAVAILABLE", "error": str(e)[:200]}
    out = pathlib.Path(a.out_dir)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"{a.sym_side}_trade_parity.json").write_text(json.dumps(rep, indent=1, default=str))
    print(f"TRADE_PARITY {a.sym_side} {rep.get('verdict')} vec_trips={rep.get('vec_trips')} live_trips={rep.get('live_trips')} matched={rep.get('matched')} "
          f"vec_rate={rep.get('vec_match_rate')} live_rate={rep.get('live_match_rate')} exit_agree={rep.get('exit_agree_rate')} "
          f"top_gaps={list((rep.get('gaps') or {}).items())[:3]}", flush=True)


if __name__ == "__main__":
    main()
