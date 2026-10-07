"""One-symside vec-vs-live verification worker (runs on S5).

Reads one manifest entry, evaluates the FINAL override set through BOTH
engines on the same frozen 30d window, applies the pilot-identical parity
gate plus promotion gates, and writes one atomic result JSON.

Vector: tools.opt.v12_pilot.evaluate_sanitized (same fn family as the pilot
final-fresh re-anchor). Live: backtest_v12_engine.run_one (real
process_position path, _assert_live_path guarded). No fallbacks, no
fabrication: exceptions/timeouts are recorded, never zero-filled.
"""
import hashlib
import json
import os
import socket
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("V12_NPZ_CACHE", "32")

ENGINE_FILES = ("backtest_v12_engine.py", "v12_quick_engine.py", "ez_manage.py", "tradier_manage.py", "config.py", "config_tradier.py")
PROV_FILES = ("data/hourly_reconfig/trb/active_config.json", "data/hourly_reconfig/per_sym_active_config.json", "data/cat_side_defaults_4.json", "data/cat_side_promotions.json", "data/sweep_cat_overrides.json")


def md5_of(path):
    try:
        h = hashlib.md5()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def parity_ok(live, vec):
    if not live.get("valid"):
        return False, f"live invalid: {live.get('invalid_reason')}"
    if not vec.get("valid"):
        return False, f"vector invalid: {vec.get('invalid_reason')}"
    lt = int(live.get("trades") or 0)
    vt = int(vec.get("trades") or 0)
    if lt == 0 or vt == 0:
        return False, f"zero trades live={lt} vec={vt}"
    ratio = vt / lt if lt else 0
    if not (0.80 <= ratio <= 1.25):
        return False, f"trade-count ratio {ratio:.2f} out of 0.80..1.25 (live {lt} vec {vt})"
    lg = float(live.get("gain_pct") or 0)
    vg = float(vec.get("gain_pct") or 0)
    if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
        return False, f"gain mismatch live {lg:.4f} vec {vg:.4f}"
    return True, "parity ok"


def qual_gates(m, bh):
    gates = {}
    gates["valid"] = bool(m.get("valid"))
    try:
        tim = float(m.get("tim_pct"))
        gates["tim_20_80"] = 20.0 <= tim <= 80.0
    except (TypeError, ValueError):
        gates["tim_20_80"] = False
    try:
        gates["dd_le_30"] = float(m.get("max_dd_pct")) <= 30.0
    except (TypeError, ValueError):
        gates["dd_le_30"] = False
    try:
        gates["trades_ge_30"] = int(m.get("trades") or 0) >= 30
    except (TypeError, ValueError):
        gates["trades_ge_30"] = False
    try:
        gates["sharpe_gt_0p2"] = float(m.get("pool_sharpe")) > 0.2
    except (TypeError, ValueError):
        gates["sharpe_gt_0p2"] = False
    try:
        g = float(m.get("gain_pct"))
        gates["gain_ge_0"] = g >= 0
        gates["gain_ge_bh"] = (bh is not None) and (g >= float(bh))
    except (TypeError, ValueError):
        gates["gain_ge_0"] = False
        gates["gain_ge_bh"] = False
    return gates


def _npz_stat(root, symside):
    if symside.endswith("_LONG"):
        sym = symside[:-5]
    elif symside.endswith("_SHORT"):
        sym = symside[:-6]
    else:
        sym = symside
    p = os.path.join(root, "backtest_v8", "indicators", sym + ".npz")
    try:
        st = os.stat(p)
        return {"mtime": int(st.st_mtime), "size": st.st_size}
    except OSError:
        return None


def slim(m):
    keep = ("symside", "valid", "invalid_reason", "gain_pct", "gain_per_mo", "bh_pct", "bh_per_mo", "delta_vs_bh", "delta_per_mo", "max_dd_pct", "tim_pct", "wr_pct", "pool_sharpe", "trades", "closes_per_month", "avg_gain_trade", "years", "window_days", "capital_usd", "pnl_usd", "beats_bh", "score")
    return {k: m.get(k) for k in keep if k in m}


def _jsonable(o):
    if o is None or isinstance(o, (bool, int, str)):
        return o
    if isinstance(o, float):
        return o
    if isinstance(o, dict):
        return {str(k): _jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_jsonable(v) for v in o]
    try:
        import numbers
        if isinstance(o, numbers.Real):
            return float(o)
    except Exception:
        pass
    return str(o)[:200]


def _live_child(symside, overrides, window, force_real, res_path, err_path):
    import sys as _sys
    err = None
    try:
        err = open(err_path, "w")
        _sys.stderr = err
    except OSError:
        pass
    def _mark(msg):
        try:
            if err is not None:
                err.write(f"[child-mark] {msg}\n")
                err.flush()
        except BaseException:
            pass
    _mark(f"start argv={_sys.argv[:3]} pid={os.getpid()}")
    if force_real:
        os.environ["V8_FORCE_REAL"] = "1"
    try:
        import v12_quick_engine as _VQ_first  # noqa: F401 — pilot-fidelity import order (vec engine loaded before scalar)
        _mark("v12 imported")
        import backtest_v12_engine as B
        _mark("backtest_v12 imported")
        live = B.run_one(symside, overrides, window_days=window)
        _mark(f"run_one returned trades={(live or {}).get('trades')}")
        live.pop("ledger", None)
        live.pop("overrides", None)
        live["child_rc"] = 0
    except BaseException as ex:
        import traceback
        live = {"symside": symside, "valid": False, "invalid_reason": f"live {type(ex).__name__}: {ex}"[:200], "trace": traceback.format_exc()[:800] if isinstance(ex, Exception) else f"base-exception {type(ex).__name__}", "gain_pct": None, "trades": 0, "child_rc": 0}
        _mark(f"caught {type(ex).__name__}")
    try:
        with open(res_path + ".tmp", "w") as f:
            json.dump(_jsonable(live), f)
        os.replace(res_path + ".tmp", res_path)
        _mark("result written")
    except BaseException as ex:
        _mark(f"WRITE FAILED {type(ex).__name__}: {ex}"[:200])
    try:
        if err is not None:
            err.close()
    except BaseException:
        pass


def _run_live_child(symside, overrides, window, force_real, timeout_s=3500):
    import multiprocessing as mp
    import tempfile
    tag = f"s5live_{symside}_{os.getpid()}"
    err_path = os.path.join(tempfile.gettempdir(), tag + ".err")
    res_path = os.path.join(tempfile.gettempdir(), tag + ".json")
    for p in (err_path, res_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    ctx = mp.get_context("spawn")
    proc = ctx.Process(target=_live_child, args=(symside, dict(overrides), window, force_real, res_path, err_path))
    proc.start()
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if os.path.exists(res_path):
            break
        if not proc.is_alive():
            break
        time.sleep(5)
    if not os.path.exists(res_path) and proc.is_alive():
        proc.terminate()
        proc.join(30)
        if proc.is_alive():
            try:
                proc.kill()
            except Exception:
                pass
            proc.join(30)
        return {"symside": symside, "valid": False, "invalid_reason": f"live timeout {timeout_s}s (child terminated)", "gain_pct": None, "trades": 0, "child_rc": "timeout"}
    proc.join(60)
    rc = proc.exitcode
    if not os.path.exists(res_path):
        tail = ""
        try:
            with open(err_path) as f:
                tail = f.read()[-800:]
        except OSError:
            pass
        for p in (err_path, res_path):
            try:
                if os.path.exists(p):
                    os.remove(p)
            except OSError:
                pass
        return {"symside": symside, "valid": False, "invalid_reason": f"live hard exit rc={rc} (engine killed the process; vec above is intact)", "trace": tail, "gain_pct": None, "trades": 0, "child_rc": rc}
    try:
        with open(res_path) as f:
            live = json.load(f)
    except BaseException as ex:
        return {"symside": symside, "valid": False, "invalid_reason": f"live result unreadable: {type(ex).__name__}", "gain_pct": None, "trades": 0, "child_rc": rc}
    for p in (err_path, res_path):
        try:
            if os.path.exists(p):
                os.remove(p)
        except OSError:
            pass
    live["child_rc"] = rc
    return live


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--entry", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--force-real", action="store_true", help="V8_FORCE_REAL=1: skip AUTO_VECTOR union (diagnostic arm)")
    args = ap.parse_args()
    if args.force_real:
        os.environ["V8_FORCE_REAL"] = "1"
    with open(args.entry) as f:
        e = json.load(f)
    symside = e["symside"]
    window = int(e.get("window_days") or 30)
    overrides = dict(e.get("overrides") or {})
    bh = e.get("bh")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    res = {"symside": symside, "window_days": window, "host": socket.gethostname(), "force_real": bool(args.force_real), "v8_override_file_set": bool(os.environ.get("V8_OVERRIDE_FILE")), "engine_md5": {n: md5_of(os.path.join(root, n)) for n in ENGINE_FILES}, "prov_md5": {n: md5_of(os.path.join(root, n)) for n in PROV_FILES}, "vec_fn": "prepare_batch+evaluate_prepared_sanitized over {bolds(cat)+cumulative} (pilot base replication)", "live_fn": "backtest_v12_engine.run_one over {cumulative} (pilot H-identical, no bolds)"}
    def _write(obj):
        tmp = args.out + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, args.out)
    base0 = symside.split("_")[0].upper()
    cat = ("CRYPTO" if base0.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "STOCKS") + ("_SHORT" if symside.endswith("_SHORT") else "_LONG")
    res["cat_side"] = cat
    vec_ov = dict(overrides)
    try:
        _bp = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(args.entry))), "bolds.json")
        _bolds = (json.load(open(_bp)).get("cats") or {}).get(cat, {})
        vec_ov = {**_bolds, **vec_ov}
        res["bolds_n"] = len(_bolds)
        res["bolds_md5"] = md5_of(_bp)
    except BaseException as ex:
        res["bolds_n"] = 0
        res["bolds_error"] = f"{type(ex).__name__}: {ex}"[:120]
    t0 = time.time()
    try:
        from tools.opt.v12_pilot import evaluate_prepared_sanitized as ES
        from tools.opt.v12_pilot import prepare_batch
        prep = prepare_batch(symside, window)
        vec = ES(prep, vec_ov, window)
    except BaseException as ex:
        import traceback
        vec = {"symside": symside, "valid": False, "invalid_reason": f"vec {type(ex).__name__}: {ex}"[:200], "trace": traceback.format_exc()[:800] if isinstance(ex, Exception) else f"base-exception {type(ex).__name__}", "gain_pct": None, "trades": 0}
    res["vec_s"] = round(time.time() - t0, 1)
    res["npz_pre_vec"] = _npz_stat(root, symside)
    res["vec"] = slim(vec)
    res["vec"]["invalid_reason_full"] = vec.get("invalid_reason")
    res["vec"]["trace"] = vec.get("trace")
    res["live"] = None
    res["live_pending"] = True
    res["manifest"] = {k: e.get(k) for k in ("bh", "baseline_gain", "cumulative_gain", "final_gain_fresh_vec", "overrides_n", "done_n", "file")}
    _write(res)
    res["npz_pre_live"] = _npz_stat(root, symside)
    t0 = time.time()
    live = _run_live_child(symside, overrides, window, args.force_real)
    res["live_s"] = round(time.time() - t0, 1)
    res["npz_post_live"] = _npz_stat(root, symside)
    res["live_pending"] = False
    res["live"] = slim(live)
    res["live"]["invalid_reason_full"] = live.get("invalid_reason")
    res["live"]["trace"] = live.get("trace")
    res["live"]["child_rc"] = live.get("child_rc")
    try:
        ok, reason = parity_ok(live, vec)
    except BaseException as ex:
        ok, reason = False, f"parity crashed {type(ex).__name__}: {ex}"[:120]
    res["parity"] = {"ok": ok, "reason": reason}
    try:
        res["qual_vec"] = qual_gates(vec, bh)
        res["qual_live"] = qual_gates(live, bh)
    except BaseException as ex:
        res["qual_error"] = f"{type(ex).__name__}: {ex}"[:120]
    _write(res)
    print(f"{symside} vec={vec.get('gain_pct')}t{vec.get('trades')} live={live.get('gain_pct')}t{live.get('trades')} parity={ok}:{reason} vec_s={res['vec_s']} live_s={res['live_s']}", flush=True)


if __name__ == "__main__":
    main()
