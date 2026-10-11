"""DELTA_EXIT rich slowdown twin (USER 2026-10-10: wire the live-only sub-knobs completely).

LIVE SOURCE (read-only; wt_dc_delta.py is LOCKED): DeltaTracker.update() exit block
(~lines 540-850): speed_dead / decelerating / opposing / peak_decay / tf_lost predicates
+ accel + min_hold + dom-TF select + weakness>=2 / strong-directional + HTF slowdown veto
+ structural veto. Tracker bridge knobs consumed here: DELTA_EXIT_DECAY_RATIO,
DELTA_EXIT_ACCEL_THRESHOLD, DELTA_EXIT_OPPOSING_RATIO, DELTA_EXIT_MIN_TF_LOST,
DELTA_EXIT_MIN_HOLD, DELTA_EXIT_DOM_TF_ENABLED, DELTA_EXIT_TF,
DELTA_EXIT_REQUIRE_NONZERO_SCORE (ez_manage.py:13840, tradier_manage.py:35412+40840)
plus STRUCTURAL_EXIT_GATE_ENABLED (True crypto / False stocks, consulted like live).
REQUIRE_NONZERO_SCORE applies to stocks only (its only live consumer is
tradier_manage.py:35412; crypto has no such gate).

FAITHFULNESS CONTRACT (read before citing parity):
  - TF set = live bridge weights MINUS 3m (NPZ has no 3m keys): crypto
    {15m:1, 1h:3, 4h:2, D:1} (live adds 3m:1), stocks {1h:2, 4h:3, D:2} (no LTF at all).
  - 3m-only terms use the 15m micro substitution: DOM_TF=3m (live crypto default) reads
    15m speeds; wt1/dc falling/rising 3m terms read 15m. MFI uses mfi_15m (live's own
    fallback: mfi_3m<40 OR mfi_15m<40).
  - min_hold: live exit_min_hold_satisfied(None) is True (unknown age satisfies); the
    signal-level twin has no entry age, so it passes — identical to the live
    unknown-age path. A loop-level age mask is future work, not silent drift.
  - max_speed: live resets per position cycle (reset_position_state); the twin resets
    on entry_sig (passed in; expanding-max fallback when None).
  - Two-phase 3m exits + delayed-retest 5m machine: NOT twinned (no 3m/5m keys).
  - Stocks 45m post-reentry cooldown: NOT twinned (no reentry state at signal level).
  - DELTA_EXIT_TYPE / SCORE_BONUS / SPEED_DECAY / WT_CROSS / OVERRIDE_NOLOSS: dead in
    live too (stubs / `and False`) — no twin (twinning unwired-live knobs would
    fabricate behavior). They need a live spec first.
  - Pure units (accel, opposing, min_hold, structural veto) and the field lists are
    IMPORTED from wt_dc_delta, never copied — those units cannot drift.
  - GATE: v12 calls this twin only when DELTA_EXIT_SPEED_DECAY_VEC_ENABLED is True
    (default False both venues). Until then the legacy wt_against proxy fires and this
    module executes nowhere in production. Do NOT flip without a parity PASS.
"""
import numpy as np
from wt_dc_delta import WT_DELTA_FIELDS as _WT_FIELDS
from wt_dc_delta import DC_DELTA_FIELDS as _DC_FIELDS
from wt_dc_delta import DC_POSITION_FIELDS as _DC_POS_FIELDS
from wt_dc_delta import opposing_pressure_exceeds as _opp_exceeds
from wt_dc_delta import exit_acceleration_below as _accel_below
from wt_dc_delta import exit_min_hold_satisfied as _hold_ok
from wt_dc_delta import causal_z_speed_acceleration as _causal_accel
from wt_dc_delta import structural_exit_permitted as _struct_ok

_WT_INT_FIELDS = ("wt_bullish", "wt_cross_bull", "wt_cross_bear", "wt_momentum_state")
_CRYPTO_W = {"15m": 1.0, "1h": 3.0, "4h": 2.0, "D": 1.0}
_STOCK_W = {"1h": 2.0, "4h": 3.0, "D": 2.0}
_MICRO = "15m"


def _arr(npz, key, n):
    try:
        a = np.asarray(npz[key], dtype=np.float64)
    except Exception:
        return np.full(n, np.nan)
    if a.shape[0] < n:
        out = np.full(n, np.nan)
        out[n - a.shape[0]:] = a
        return out
    return a[-n:]


def _knobs(cfg, stocks):
    g = lambda k, d: getattr(cfg, k, d)
    return {
        "enabled": bool(g("DELTA_EXIT_ENABLED", True)),
        "decay_ratio": float(g("DELTA_EXIT_DECAY_RATIO", 0.30 if stocks else 0.90)),
        "accel_thr": float(g("DELTA_EXIT_ACCEL_THRESHOLD", -0.1)),
        "opp_ratio": float(g("DELTA_EXIT_OPPOSING_RATIO", 1.5)),
        "min_tf_lost": float(g("DELTA_EXIT_MIN_TF_LOST", 2 if stocks else 1)),
        "min_hold": float(g("DELTA_EXIT_MIN_HOLD", 4)),
        "dom_on": bool(g("DELTA_EXIT_DOM_TF_ENABLED", True)),
        "dom_tf": str(g("DELTA_EXIT_TF", "15m" if stocks else "3m")),
        "nonzero": bool(g("DELTA_EXIT_REQUIRE_NONZERO_SCORE", True)) and stocks,
        "accel_lb": int(g("DELTA_ACCEL_LOOKBACK", 5)),
        "z_win": int(g("DELTA_Z_WINDOW", 200)),
        "struct_gate": bool(g("STRUCTURAL_EXIT_GATE_ENABLED", False if stocks else True)),
    }


def prepare(npz, n, stocks):
    tw = _STOCK_W if stocks else _CRYPTO_W
    tfs = list(tw.keys())
    bull = {tf: np.zeros(n) for tf in tfs}
    bear = {tf: np.zeros(n) for tf in tfs}
    for tf in tfs:
        c = np.zeros(n)
        for f in list(_WT_FIELDS) + list(_DC_FIELDS) + list(_DC_POS_FIELDS) + list(_WT_INT_FIELDS):
            cur = _arr(npz, f"{f}_{tf}", n)
            prv = np.concatenate(([np.nan], cur[:-1]))
            ok = ~(np.isnan(cur) | np.isnan(prv))
            d = np.where(ok, cur - prv, 0.0)
            bull[tf] = bull[tf] + np.where(d > 0, d, 0.0)
            bear[tf] = bear[tf] + np.where(d < 0, -d, 0.0)
            c = c + ok.astype(float)
        nz = c > 0
        bull[tf][nz] /= c[nz]
        bear[tf][nz] /= c[nz]
    tot_bull = sum(bull[tf] * tw[tf] for tf in tfs)
    tot_bear = sum(bear[tf] * tw[tf] for tf in tfs)
    btc = sum((bull[tf] > 0.5).astype(int) for tf in tfs)
    brc = sum((bear[tf] > 0.5).astype(int) for tf in tfs)
    return {
        "tfs": tfs, "tw": tw, "bull": bull, "bear": bear, "tot_bull": tot_bull,
        "tot_bear": tot_bear, "btc": btc, "brc": brc,
        "wt1_1h": _arr(npz, "wt1_1h", n), "wt2_1h": _arr(npz, "wt2_1h", n),
        "wt1_4h": _arr(npz, "wt1_4h", n), "wt2_4h": _arr(npz, "wt2_4h", n),
        "wt1_D": _arr(npz, "wt1_D", n), "wt2_D": _arr(npz, "wt2_D", n),
        "wt1_m": _arr(npz, f"wt1_{_MICRO}", n), "dcp_m": _arr(npz, f"dc_position_{_MICRO}", n),
        "mfi_m": _arr(npz, "mfi_15m", n),
        "o_m": _arr(npz, f"open_{_MICRO}", n), "h_m": _arr(npz, f"high_{_MICRO}", n),
        "l_m": _arr(npz, f"low_{_MICRO}", n), "c_m": _arr(npz, f"close_{_MICRO}", n),
        "h_1h": _arr(npz, "high_1h", n), "l_1h": _arr(npz, "low_1h", n),
        "h_4h": _arr(npz, "high_4h", n), "l_4h": _arr(npz, "low_4h", n),
        "close": _arr(npz, "close", n),
    }


def rich_delta_exits(npz, n, is_long, cfg, entry_sig=None):
    stocks = str(getattr(cfg, "MODE", "crypto")) == "tradier"
    k = _knobs(cfg, stocks)
    fires = np.zeros(n, dtype=bool)
    reasons = [""] * n
    if not k["enabled"]:
        return fires, {"fires": 0, "knobs": k, "gap": "master-off"}
    p = prepare(npz, n, stocks)
    dom = k["dom_tf"]
    if k["dom_on"] and dom == "3m":
        dom = _MICRO  # 15m micro substitution (no 3m keys); documented contract
    use_dom = k["dom_on"] and dom in p["tfs"]
    _acc_state = {}
    max_spd = 0.0
    ps0, ps1 = 0.0, 0.0  # prev cur speeds (live _prev_cur_spd_{long,short}[_2])
    entry = np.asarray(entry_sig, dtype=bool) if entry_sig is not None else None
    for i in range(1, n):
        if entry is not None and bool(entry[i]):
            max_spd = 0.0  # live reset_position_state per position cycle
        ab = _causal_accel(_acc_state, "bull", float(p["tot_bull"][i]), append=True, lookback=k["accel_lb"], window=k["z_win"])
        ar = _causal_accel(_acc_state, "bear", float(p["tot_bear"][i]), append=True, lookback=k["accel_lb"], window=k["z_win"])
        if is_long:
            cur = float(p["bull"][dom][i]) if use_dom else float(p["tot_bull"][i])
            opp = float(p["bear"][dom][i]) if use_dom else float(p["tot_bear"][i])
            acc, tfc = ab, int(p["btc"][i])
        else:
            cur = float(p["bear"][dom][i]) if use_dom else float(p["tot_bear"][i])
            opp = float(p["bull"][dom][i]) if use_dom else float(p["tot_bull"][i])
            acc, tfc = ar, int(p["brc"][i])
        max_spd = max(max_spd, cur)
        acc_neg = _accel_below(acc, k["accel_thr"])
        hold_ok = _hold_ok(None, k["min_hold"])  # signal level: unknown age satisfies (live contract)
        speed_dead = cur < 0.5
        slowing_now = cur < ps0 * 0.7
        slowing_prev = ps0 < ps1 * 0.7 if ps1 > 0 else False
        decelerating = slowing_now and slowing_prev
        opposing = _opp_exceeds(opp, cur, k["opp_ratio"]) and acc_neg and hold_ok
        peak_decay = (cur < max_spd * (1 - k["decay_ratio"]) and acc_neg and hold_ok) if max_spd > 0 else False
        tf_lost = (tfc < k["min_tf_lost"] and acc_neg and hold_ok)
        mfi_low = bool(p["mfi_m"][i] < 40) if not np.isnan(p["mfi_m"][i]) else False
        w_now, w_prv = p["wt1_m"][i], p["wt1_m"][i - 1]
        d_now, d_prv = p["dcp_m"][i], p["dcp_m"][i - 1]
        if is_long:
            micro_fall = bool(w_now < w_prv) if not (np.isnan(w_now) or np.isnan(w_prv)) else False
            dc_fall = bool(d_now < d_prv) if not (np.isnan(d_now) or np.isnan(d_prv)) else False
        else:
            micro_fall = bool(w_now > w_prv) if not (np.isnan(w_now) or np.isnan(w_prv)) else False
            dc_fall = bool(d_now > d_prv) if not (np.isnan(d_now) or np.isnan(d_prv)) else False
        weak = sum([speed_dead, decelerating, peak_decay, opposing, tf_lost, mfi_low, micro_fall, dc_fall])
        strong = micro_fall or (dc_fall and micro_fall)
        fire = (weak >= 2) or strong
        if fire:
            if is_long:
                htf = int(p["wt1_1h"][i] > p["wt2_1h"][i]) + int(p["wt1_4h"][i] > p["wt2_4h"][i]) + int(p["wt1_D"][i] > p["wt2_D"][i])
            else:
                htf = int(p["wt1_1h"][i] < p["wt2_1h"][i]) + int(p["wt1_4h"][i] < p["wt2_4h"][i]) + int(p["wt1_D"][i] < p["wt2_D"][i])
            if htf >= 2:
                fire = False  # HTF_SLOWDOWN_VETO (sacred, live wt_dc_delta.py:703)
        if fire and k["nonzero"] and cur == 0.0 and opp == 0.0 and tfc == 0:
            fire = False  # DELTA_EXIT_REQUIRE_NONZERO_SCORE (stocks mandate 2026-06-02)
        if fire and k["struct_gate"]:
            ind = {
                "current_price": p["close"][i], f"open_{_MICRO}": p["o_m"][i],
                f"high_{_MICRO}": p["h_m"][i], f"high_{_MICRO}_prev": p["h_m"][i - 1],
                f"low_{_MICRO}": p["l_m"][i], f"low_{_MICRO}_prev": p["l_m"][i - 1],
                f"close_{_MICRO}": p["c_m"][i], "high_1h": p["h_1h"][i],
                "high_1h_prev": p["h_1h"][i - 1], "low_1h": p["l_1h"][i],
                "low_1h_prev": p["l_1h"][i - 1], "high_4h": p["h_4h"][i],
                "high_4h_prev": p["h_4h"][i - 1], "low_4h": p["l_4h"][i],
                "low_4h_prev": p["l_4h"][i - 1],
            }
            ok, _vr = _struct_ok(ind, bool(is_long), {"rz_ltf_micro": _MICRO})
            if not ok:
                fire = False  # STRUCTURAL_EXIT_GATE (mandate 2026-07-21)
        if fire:
            fires[i] = True
            tg = []
            if speed_dead:
                tg.append("DEAD")
            if decelerating:
                tg.append("DECEL")
            if peak_decay:
                tg.append("PEAK")
            if opposing:
                tg.append("OPP")
            if tf_lost:
                tg.append("TFLOST")
            reasons[i] = "|".join(tg) + f" cur={cur:.2f} max={max_spd:.2f}"
        ps1, ps0 = ps0, cur
    return fires, {"fires": int(fires.sum()), "knobs": k, "gap": "3m-branches+two-phase+cooldown untwinned (no 3m/5m keys, no reentry state)"}
