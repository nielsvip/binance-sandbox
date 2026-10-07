"""Verification for vec_decisions.lh_ll_top_exit: default-inert, scalar<->vector
equivalence (0 mismatches, both sides, all modes), causality (prefix-stable),
determinism, zero-data safety, and a real-NPZ smoke report. No locked files used.
Run: python3 vec_decisions/test_lh_ll_top_exit.py"""
import os
import sys
import time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from vec_decisions.lh_ll_top_exit import parse_struct_tf, resolve_lh_ll_top_exit, check_lh_ll_top_exit, build_exit_mask, struct_armed
from vec_decisions.htf_causal_align import align_store

def _safe(npz, key, n, default=0.0):
    try:
        a = np.asarray(npz[key])
        if a.ndim == 1 and len(a) == n:
            return a
    except (KeyError, TypeError, ValueError):
        pass
    return np.full(n, default, dtype=float)

def _spec(**kw):
    base = {"LH_LL_TOP_EXIT_ENABLED": True, "LH_LL_TOP_EXIT_STRUCT_TF": "4h", "LH_LL_TOP_EXIT_STRUCT_MODE": "LH_LL", "LH_LL_TOP_EXIT_MODE": "EITHER", "LH_LL_TOP_EXIT_DC1H_BUFFER_PCT": 0.10, "LH_LL_TOP_EXIT_BOTH_TOL_PCT": 0.30, "LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM": False}
    base.update(kw)
    return resolve_lh_ll_top_exit(lambda k, d: base.get(k, d))

def t_parser():
    assert parse_struct_tf("OFF") == [] and parse_struct_tf(None) == [] and parse_struct_tf("") == []
    assert parse_struct_tf("4h") == ["4h"] and parse_struct_tf("4h,D") == ["4h", "D"]
    assert parse_struct_tf("D+4h") == ["D", "4h"] and parse_struct_tf("15m") == [] and parse_struct_tf("4h,4h") == ["4h"]
    assert resolve_lh_ll_top_exit(lambda k, d: d)["enabled"] is False
    print("parser/default-inert OK")

def t_synthetic():
    s = _spec(LH_LL_TOP_EXIT_STRUCT_TF="4h", LH_LL_TOP_EXIT_MODE="WT15M")
    ind = {"high_4h": 100.0, "high_4h_prev": 101.0, "low_4h": 90.0, "low_4h_prev": 89.0, "wt1_15m": 5.0, "wt2_15m": 6.0, "dc_high_1h": 200.0}
    f, r = check_lh_ll_top_exit(s, ind, 95.0, 96.0, 7.0, 6.0, True)
    assert f and "arm=4h_LH" in r and "leg=WT15M" in r, r
    f2, _ = check_lh_ll_top_exit(s, dict(ind, wt1_15m=7.0), 95.0, 96.0, 7.0, 6.0, True)
    assert not f2
    f3, _ = check_lh_ll_top_exit(s, dict(ind, high_4h=102.0, low_4h=88.0), 95.0, 96.0, 7.0, 6.0, True)
    assert f3  # LL arm (low 88 < 89) also fires under LH_LL
    s_ll = _spec(LH_LL_TOP_EXIT_STRUCT_TF="4h", LH_LL_TOP_EXIT_STRUCT_MODE="LL", LH_LL_TOP_EXIT_MODE="WT15M")
    f4, _ = check_lh_ll_top_exit(s_ll, ind, 95.0, 96.0, 7.0, 6.0, True)
    assert not f4  # LH only, LL required -> no arm (low 90 > 89)
    s_dc = _spec(LH_LL_TOP_EXIT_STRUCT_TF="4h", LH_LL_TOP_EXIT_MODE="DC1H")
    f5, r5 = check_lh_ll_top_exit(s_dc, dict(ind, dc_high_1h=95.05), 95.0, 96.0, 7.0, 6.0, True)
    assert f5 and "leg=DC1H" in r5, r5
    s_both = _spec(LH_LL_TOP_EXIT_STRUCT_TF="4h", LH_LL_TOP_EXIT_MODE="BOTH")
    f6, _ = check_lh_ll_top_exit(s_both, dict(ind, dc_high_1h=95.05), 95.0, 96.0, 7.0, 6.0, True)
    assert f6  # cross + within tol of edge
    f7, _ = check_lh_ll_top_exit(s_both, ind, 95.0, 96.0, 7.0, 6.0, True)
    assert not f7  # cross but far from edge -> BOTH holds
    sh = _spec(LH_LL_TOP_EXIT_STRUCT_TF="D", LH_LL_TOP_EXIT_MODE="EITHER")
    indh = {"high_D": 102.0, "high_D_prev": 101.0, "low_D": 90.0, "low_D_prev": 89.0, "wt1_15m": 6.0, "wt2_15m": 5.0, "dc_low_1h": 50.0}
    f8, r8 = check_lh_ll_top_exit(sh, indh, 95.0, 94.0, 4.0, 5.0, False)
    assert f8 and "arm=D_HH+HL" in r8, r8  # short mirrors LH->HH, LL->HL
    z = {"high_4h": 0.0, "high_4h_prev": 101.0, "low_4h": 90.0, "low_4h_prev": 89.0, "wt1_15m": 5.0, "wt2_15m": 6.0, "dc_high_1h": 200.0}
    assert check_lh_ll_top_exit(s, z, 95.0, 96.0, 7.0, 6.0, True) == (False, "")
    assert struct_armed(0, 1, 2, 3, True, "LH_LL") is False
    print("synthetic long/short/zero OK")

def _repo():
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

def _load():
    p = os.path.join(_repo(), "backtest_v8", "indicators", "1000000MOGUSDT.npz")
    assert os.path.exists(p), f"fixture missing: {p}"
    return align_store(dict(np.load(p, allow_pickle=True)))

def t_equivalence(store):
    n = len(store["timestamps"])
    sl = slice(n - 4000, n)
    sub = {k: (np.asarray(v)[sl] if isinstance(v, np.ndarray) and v.shape == (n,) else v) for k, v in store.items()}
    m = 4000
    tfs = ["4h", "D", "4h,D"]
    total = mism = fires = 0
    for is_long in (True, False):
        px = np.asarray(_safe(sub, "close", m, 0.0), dtype=float)
        if not bool((px > 0).any()):
            px = np.asarray(_safe(sub, "close_15m", m, 0.0), dtype=float)
        w1 = np.asarray(_safe(sub, "wt1_15m", m, 0.0), dtype=float)
        w2 = np.asarray(_safe(sub, "wt2_15m", m, 0.0), dtype=float)
        for tf in tfs:
            for smode in ("LH", "LL", "LH_LL", "LH_AND_LL"):
                for mode in ("WT15M", "DC1H", "EITHER", "BOTH"):
                    for pc in (False, True):
                        spec = _spec(LH_LL_TOP_EXIT_STRUCT_TF=tf, LH_LL_TOP_EXIT_STRUCT_MODE=smode, LH_LL_TOP_EXIT_MODE=mode, LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM=pc)
                        vec = build_exit_mask(sub, m, is_long, spec, _safe)
                        fires += int(vec.sum())
                        for i in range(m):
                            ind = {}
                            for t in spec["tfs"]:
                                for suf in ("", "_prev"):
                                    for hl in ("high", "low"):
                                        ind[f"{hl}_{t}{suf}"] = float(_safe(sub, f"{hl}_{t}{suf}", m, 0.0)[i])
                            ind["wt1_15m"] = float(w1[i])
                            ind["wt2_15m"] = float(w2[i])
                            ind["dc_high_1h"] = float(_safe(sub, "dc_high_1h", m, 0.0)[i])
                            ind["dc_low_1h"] = float(_safe(sub, "dc_low_1h", m, 0.0)[i])
                            s_fire, _ = check_lh_ll_top_exit(spec, ind, float(px[i]), float(px[i - 1]) if i else 0.0, float(w1[i - 1]) if i else 0.0, float(w2[i - 1]) if i else 0.0, is_long)
                            total += 1
                            if bool(s_fire) != bool(vec[i]):
                                mism += 1
                                if mism < 4:
                                    print("MISM", is_long, tf, smode, mode, pc, i, s_fire, bool(vec[i]))
    print(f"equivalence: {total} scalar-vs-vector checks, {mism} mismatches, {fires} vec fires")
    assert mism == 0 and fires > 0

def t_causal_determinist(store):
    n = len(store["timestamps"])
    for is_long in (True, False):
        spec = _spec(LH_LL_TOP_EXIT_STRUCT_TF="4h,D", LH_LL_TOP_EXIT_MODE="EITHER")
        t0 = time.time()
        full = build_exit_mask(store, n, is_long, spec, _safe)
        dt = time.time() - t0
        again = build_exit_mask(store, n, is_long, spec, _safe)
        assert np.array_equal(full, again), "non-deterministic"
        half = n // 2
        trunc = {k: (np.asarray(v)[:half] if isinstance(v, np.ndarray) and v.shape == (n,) else v) for k, v in store.items()}
        part = build_exit_mask(trunc, half, is_long, spec, _safe)
        assert np.array_equal(part, full[:half]), "lookahead: prefix changed under truncation"
        off = build_exit_mask(store, n, is_long, resolve_lh_ll_top_exit(lambda k, d: d), _safe)
        assert not bool(off.any()), "default must be inert"
        print(f"{'LONG' if is_long else 'SHORT'}: n={n} fires={int(full.sum())} ({100*full.mean():.3f}%) build={dt*1000:.1f}ms causal+deterministic+inert OK")

def t_smoke(store):
    n = len(store["timestamps"])
    print("sym=1000000MOGUSDT n=%d mark=%s" % (n, store.get("_htf_causal_align")))
    for is_long in (True, False):
        for tf in ("4h", "D", "4h,D"):
            for mode in ("WT15M", "DC1H", "EITHER", "BOTH"):
                spec = _spec(LH_LL_TOP_EXIT_STRUCT_TF=tf, LH_LL_TOP_EXIT_MODE=mode)
                v = build_exit_mask(store, n, is_long, spec, _safe)
                print(f"  {'LONG' if is_long else 'SHORT':5s} tf={tf:4s} mode={mode:5s} fires={int(v.sum()):5d} ({100*v.mean():.3f}%)")

def t_engine_wiring():
    import v12_quick_engine as V
    import config as C
    import config_tradier as CT
    from tools.opt.v12_pilot import evaluate_sanitized as ES, prepare_batch, evaluate_prepared_sanitized as EPS
    qc, cc, tc = V.QuickConfig(), C.Config(), CT.TradierConfig()
    for cfg in (qc, cc, tc):
        assert getattr(cfg, "LH_LL_TOP_EXIT_ENABLED") is False
        assert getattr(cfg, "LH_LL_TOP_EXIT_STRUCT_TF") == "OFF"
    assert "LH_LL_TOP_EXIT_ENABLED" in V.AUTO_WIRED_PARAMS
    ss, b = "", {}
    for cand in ("GALAUSDT_LONG", "AAPL_LONG", "MSTR_LONG"):
        try:
            r = ES(cand, {}, 30)
        except Exception:
            continue
        if r.get("trades", 0) >= 30:
            ss, b = cand, r
            break
    assert b and b["trades"] >= 30, "no viable engine fixture (need >=30 trades)"
    dflt = {"LH_LL_TOP_EXIT_ENABLED": False, "LH_LL_TOP_EXIT_STRUCT_TF": "OFF", "LH_LL_TOP_EXIT_STRUCT_MODE": "LH_LL", "LH_LL_TOP_EXIT_MODE": "EITHER", "LH_LL_TOP_EXIT_DC1H_BUFFER_PCT": 0.10, "LH_LL_TOP_EXIT_BOTH_TOL_PCT": 0.30, "LH_LL_TOP_EXIT_REQUIRE_PRICE_CONFIRM": False}
    r0 = ES(ss, dflt, 30)
    assert abs(r0["gain_pct"] - b["gain_pct"]) < 1e-9 and r0["trades"] == b["trades"], "default flip must be exactly 0"
    armed = dict(dflt, LH_LL_TOP_EXIT_ENABLED=True, LH_LL_TOP_EXIT_STRUCT_TF="4h")
    prep = prepare_batch(ss, 30)
    rb = EPS(prep, {}, 30, include_ledger=True)
    ra = EPS(prep, armed, 30, include_ledger=True)
    lb = [t for t in (rb.get("ledger") or []) if t.get("type") == "CLOSE"]
    la = [t for t in (ra.get("ledger") or []) if t.get("type") == "CLOSE"]
    fires = [t for t in la if "LH_LL_TOP_EXIT" in str(t.get("exit_reason") or t.get("reason"))]
    assert fires, "enabled exit never fired through the engine"
    cb = {(t.get("bar_entry"), t.get("bar_exit")) for t in lb}
    ca = {(t.get("bar_entry"), t.get("bar_exit")) for t in la}
    assert cb != ca, "ledger identical despite firing (fabrication guard)"
    print(f"engine wiring OK: base {b['gain_pct']:.2f}/{b['trades']}tr, idempotent 0, armed dGain={ra['gain_pct']-rb['gain_pct']:+.2f}, {len(fires)} LHLL closes, ledger changed")

def main():
    t_parser()
    t_synthetic()
    store = _load()
    t_equivalence(store)
    t_causal_determinist(store)
    t_smoke(store)
    t_engine_wiring()
    print("ALL LH_LL_TOP_EXIT TESTS PASSED")

if __name__ == "__main__":
    main()
