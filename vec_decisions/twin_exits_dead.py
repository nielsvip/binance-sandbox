"""twin_exits_dead — shared pure predicates for 12 DEAD/STAGED exit+entry switches.

Wiring mandate 2026-10-04 (evidence pack /tmp/ev_exits-dead.json). Every function
here is a REAL signal over REAL keys — no `_ = ...` stub reads, no `and False`,
no synthetic perturbations (BACKTEST_BIBLE section 19).

Switch coverage (all read by LITERAL config key; TF companions are consumed as
parameters by the same predicates, so each switch below is one wiring unit):
  BBKC_ENTRY_ENABLED + BBKC_ENTRY_TF        (ENTRY_CONFIRMATION_GATES)
  BBKC_EXIT_ENABLED + BBKC_EXIT_TF          (EXIT_VELOCITY)
  WICK_REJECT_ENTRY_ENABLED + _TF           (ENTRY_CONFIRMATION_GATES)
  WICK_REJECT_EXIT_ENABLED + _TF            (EXIT_VELOCITY)
  EXIT_VELOCITY_WT_TFS                      (EXIT_VELOCITY + EXIT_STRUCTURAL)
  MU_CORRECTION_EXIT_ENABLED                (EXIT_VELOCITY; live exists stocks)
  MU_CORRECTION_REENTRY_ENABLED             (REENTRY_WINDOWED; live exists stocks)
  STDEV_BREAKOUT_EXIT_PCTB_FAIL             (EXIT_STRUCTURAL; STAGED, live exists)

Conventions (same as vec_decisions/dc_channel_exits.py):
  get   = resolver callable get(key, default) — engine passes
          `lambda k, d: getattr(cfg, k, d)`; live passes _psym_get/_gx_c.
  safe  = v12 `_safe(npz, key, n, default)` (missing key -> const array).
  A vec function returns None when its master is OFF/default -> the hook treats
  None as inert (integrity: default flip = delta 0, BIBLE section 14.1).

Semantics sources:
  BBKC  = Bollinger + Keltner combo. Keltner mid = ema_20_TF, bands mid +/- 1.5
          x atr_TF — the exact formula of live compute_keltner_channels
          (ez_indicators.py: ema_period=20, atr_period=20, atr_mult=1.5).
          Squeeze definition (BB inside KC) matches tradier_manage.py
          get_enriched_indicators. ENTRY = confirmation: close beyond BOTH
          bands. EXIT = momentum lost: close back through KC mid. Specified
          here (no live reference; template carries no formula).
  WICK  = wick fraction = wick_len / bar_range from high/low/open/close_TF
          (both precomputes write open/high/low/close per TF). ENTRY gate
          passes unless the against-side wick >= 1/2 the range; EXIT fires
          when the against-side wick >= 1/2 the range. Threshold 1/2 is fixed
          in this module (no threshold config exists for this family).
  MU    = faithful twin of tradier_manage.py StockStrategy.evaluate_stop
          (MU_CORRECTION_PEAK_ROLLOVER) and evaluate_reentry
          (MU_CORRECTION_REENTRY), LONG-only, MU allowlist. Known vec-live
          deltas (documented, not hidden): the MIN_GAIN_PCT gate and the
          reentry hard-cool/flat requirements are enforced live-only — the
          mask stage has no per-trade gain or position state. At defaults
          (MIN_GAIN_PCT 0.0) the exit gate is a no-op.
  STDEV = faithful twin of ez_positions_quick.check_stdev_breakout_exit:
          edge-armed per (symbol, TF) state machine with bar-count expiry.
          Live crypto already calls it (check_exit_candidates_for_account);
          this module adds the vec mask + a stateful scalar twin for stocks.
  VELOCITY = multi-TF WT-velocity-against exit over EXIT_VELOCITY_WT_TFS.
          Inert on OFF/empty. NOTE: the shipped default "1h,4h,D" is an
          ACTIVE value with no ENABLED master — hooking it unconditionally
          would move the baseline (BIBLE 14.1). The hook is decision-gated
          (see hook_spec_exits_dead.json).
"""
import numpy as np

WICK_REJECT_FRAC = 0.5
BBKC_KC_ATR_MULT = 1.5
BBKC_KC_EMA_LEN = 20
_KNOWN_TFS = ("3m", "5m", "15m", "1h", "4h", "D")


def _is_off(value):
    return str(value if value is not None else "OFF").strip().upper() in ("OFF", "", "NONE", "FALSE", "0")


def _norm_tf(value, default="1h"):
    tf = str(value if value is not None else default).strip()
    if _is_off(tf):
        return ""
    if tf == "5m":
        return "5m"
    if tf not in _KNOWN_TFS:
        return ""
    return tf


def _split_tfs(value):
    out = []
    for tok in str(value or "").replace(",", "+").split("+"):
        tok = tok.strip()
        if tok and not _is_off(tok):
            out.append(tok)
    return out


def _f(value, default=0.0):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if v != v:
        return default
    return v


# ─── BBKC ───

def bbkc_entry_tf(get):
    if not bool(get("BBKC_ENTRY_ENABLED", False)):
        return ""
    return _norm_tf(get("BBKC_ENTRY_TF", "1h"), "1h")


def bbkc_exit_tf(get):
    if not bool(get("BBKC_EXIT_ENABLED", False)):
        return ""
    return _norm_tf(get("BBKC_EXIT_TF", "1h"), "1h")


def bbkc_vec_bands(npz, n, tf, safe):
    ema = safe(npz, "ema_%d_%s" % (BBKC_KC_EMA_LEN, tf), n, 0.0)
    atr = safe(npz, "atr_%s" % tf, n, 0.0)
    kc_mid = np.asarray(ema, dtype=float)
    kc_up = kc_mid + BBKC_KC_ATR_MULT * np.asarray(atr, dtype=float)
    kc_lo = kc_mid - BBKC_KC_ATR_MULT * np.asarray(atr, dtype=float)
    bb_up = np.asarray(safe(npz, "bb_upper_%s" % tf, n, 0.0), dtype=float)
    bb_lo = np.asarray(safe(npz, "bb_lower_%s" % tf, n, 0.0), dtype=float)
    ok = (kc_mid > 0) & (np.asarray(atr, dtype=float) > 0) & (bb_up > 0) & (bb_lo > 0)
    return kc_up, kc_mid, kc_lo, bb_up, bb_lo, ok


def bbkc_entry_pass_mask(npz, n, is_long, get, safe, close):
    tf = bbkc_entry_tf(get)
    if not tf:
        return None
    kc_up, _mid, kc_lo, bb_up, bb_lo, ok = bbkc_vec_bands(npz, n, tf, safe)
    px = np.asarray(close, dtype=float)
    if is_long:
        return ok & (px > kc_up) & (px > bb_up)
    return ok & (px < kc_lo) & (px < bb_lo)


def _bbkc_live_bands(ind, tf):
    ku = _f(ind.get("kc_upper_%s" % tf, 0), 0.0)
    km = _f(ind.get("kc_middle_%s" % tf, 0), 0.0)
    kl = _f(ind.get("kc_lower_%s" % tf, 0), 0.0)
    if ku <= 0 or kl <= 0 or km <= 0:
        ema = _f(ind.get("ema_%d_%s" % (BBKC_KC_EMA_LEN, tf), 0), 0.0)
        atr = _f(ind.get("atr_%s" % tf, 0), 0.0)
        if ema > 0 and atr > 0:
            km = ema
            ku = ema + BBKC_KC_ATR_MULT * atr
            kl = ema - BBKC_KC_ATR_MULT * atr
    bu = _f(ind.get("bb_upper_%s" % tf, 0), 0.0)
    bl = _f(ind.get("bb_lower_%s" % tf, 0), 0.0)
    return ku, km, kl, bu, bl


def bbkc_entry_live_pass(ind, px, is_long, get):
    tf = bbkc_entry_tf(get)
    if not tf:
        return True, ""
    ku, _km, kl, bu, bl = _bbkc_live_bands(ind or {}, tf)
    if ku <= 0 or kl <= 0 or bu <= 0 or bl <= 0:
        return True, "BBKC_ENTRY_%s_no-bb-kc-data_fail-open" % tf
    if is_long:
        ok = px > ku and px > bu
    else:
        ok = px < kl and px < bl
    if ok:
        return True, ""
    return False, "BBKC_ENTRY_%s_unconfirmed" % tf


def bbkc_exit_mask(npz, n, is_long, get, safe, close):
    tf = bbkc_exit_tf(get)
    if not tf:
        return None
    _up, kc_mid, _lo, _bu, _bl, ok = bbkc_vec_bands(npz, n, tf, safe)
    px = np.asarray(close, dtype=float)
    if is_long:
        return ok & (px < kc_mid)
    return ok & (px > kc_mid)


def bbkc_exit_live_fire(ind, px, is_long, get):
    tf = bbkc_exit_tf(get)
    if not tf:
        return False, ""
    _ku, km, _kl, _bu, _bl = _bbkc_live_bands(ind or {}, tf)
    if km <= 0:
        return False, ""
    if is_long and px < km:
        return True, "BBKC_EXIT_%s_px-below-kc-mid" % tf
    if (not is_long) and px > km:
        return True, "BBKC_EXIT_%s_px-above-kc-mid" % tf
    return False, ""


# ─── WICK REJECT ───

def wick_entry_tf(get):
    if not bool(get("WICK_REJECT_ENTRY_ENABLED", False)):
        return ""
    return _norm_tf(get("WICK_REJECT_ENTRY_TF", "1h"), "1h")


def wick_exit_tf(get):
    if not bool(get("WICK_REJECT_EXIT_ENABLED", False)):
        return ""
    return _norm_tf(get("WICK_REJECT_EXIT_TF", "1h"), "1h")


def wick_frac_vec(h, l, o, c):
    h = np.asarray(h, dtype=float)
    l = np.asarray(l, dtype=float)
    o = np.asarray(o, dtype=float)
    c = np.asarray(c, dtype=float)
    rng = h - l
    with np.errstate(divide="ignore", invalid="ignore"):
        up = np.where(rng > 0, (h - np.maximum(o, c)) / np.where(rng > 0, rng, 1.0), 0.0)
        lo = np.where(rng > 0, (np.minimum(o, c) - l) / np.where(rng > 0, rng, 1.0), 0.0)
    return np.clip(up, 0.0, 1.0), np.clip(lo, 0.0, 1.0)


def _wick_vec_fracs(npz, n, tf, safe):
    h = safe(npz, "high_%s" % tf, n, 0.0)
    l = safe(npz, "low_%s" % tf, n, 0.0)
    o = safe(npz, "open_%s" % tf, n, 0.0)
    c = safe(npz, "close_%s" % tf, n, 0.0)
    return wick_frac_vec(h, l, o, c)


def wick_entry_pass_mask(npz, n, is_long, get, safe):
    tf = wick_entry_tf(get)
    if not tf:
        return None
    up, lo = _wick_vec_fracs(npz, n, tf, safe)
    if is_long:
        return ~(up >= WICK_REJECT_FRAC)
    return ~(lo >= WICK_REJECT_FRAC)


def wick_frac_scalar(h, l, o, c):
    rng = h - l
    if not rng > 0:
        return 0.0, 0.0
    up = min(1.0, max(0.0, (h - max(o, c)) / rng))
    lo = min(1.0, max(0.0, (min(o, c) - l) / rng))
    return up, lo


def wick_entry_live_pass(ind, is_long, get):
    tf = wick_entry_tf(get)
    if not tf:
        return True, ""
    d = ind or {}
    h = _f(d.get("high_%s" % tf, 0), 0.0)
    l = _f(d.get("low_%s" % tf, 0), 0.0)
    o = _f(d.get("open_%s" % tf, 0), 0.0)
    c = _f(d.get("close_%s" % tf, _f(d.get("current_price", 0), 0.0)), 0.0)
    if not (h > 0 and l > 0 and h >= l):
        return True, "WICK_REJECT_ENTRY_%s_no-ohlc_fail-open" % tf
    up, lo = wick_frac_scalar(h, l, o, c)
    bad = up if is_long else lo
    if bad >= WICK_REJECT_FRAC:
        return False, "WICK_REJECT_ENTRY_%s_%s-wick%.2f" % (tf, "upper" if is_long else "lower", bad)
    return True, ""


def wick_exit_mask(npz, n, is_long, get, safe):
    tf = wick_exit_tf(get)
    if not tf:
        return None
    up, lo = _wick_vec_fracs(npz, n, tf, safe)
    if is_long:
        return up >= WICK_REJECT_FRAC
    return lo >= WICK_REJECT_FRAC


def wick_exit_live_fire(ind, is_long, get):
    tf = wick_exit_tf(get)
    if not tf:
        return False, ""
    d = ind or {}
    h = _f(d.get("high_%s" % tf, 0), 0.0)
    l = _f(d.get("low_%s" % tf, 0), 0.0)
    o = _f(d.get("open_%s" % tf, 0), 0.0)
    c = _f(d.get("close_%s" % tf, _f(d.get("current_price", 0), 0.0)), 0.0)
    if not (h > 0 and l > 0 and h >= l):
        return False, ""
    up, lo = wick_frac_scalar(h, l, o, c)
    bad = up if is_long else lo
    if bad >= WICK_REJECT_FRAC:
        return True, "WICK_REJECT_EXIT_%s_%s-wick%.2f" % (tf, "upper" if is_long else "lower", bad)
    return False, ""


# ─── MU CORRECTION ───

def mu_symbols(get):
    raw = str(get("MU_CORRECTION_SYMBOLS", "MU") or "MU")
    return {s.strip().upper() for s in raw.replace(",", "+").split("+") if s.strip()}


def _mu_enabled_for(get, key, is_long, sym):
    if not bool(get(key, False)):
        return False
    if not is_long:
        return False
    return str(sym or "").upper() in mu_symbols(get)


def mu_exit_mask(npz, n, is_long, sym, get, safe):
    if not _mu_enabled_for(get, "MU_CORRECTION_EXIT_ENABLED", is_long, sym):
        return None
    k_min = _f(get("MU_CORRECTION_HTF_K_MIN", 80.0), 80.0)
    rsi_min = _f(get("MU_CORRECTION_HTF_RSI_MIN", 60.0), 60.0)
    htf_need = max(1, int(_f(get("MU_CORRECTION_HTF_MIN_TFS", 1), 1)))
    high_req = bool(get("MU_CORRECTION_REQUIRE_HIGH_REVERSAL", True))
    close_req = bool(get("MU_CORRECTION_REQUIRE_CLOSE_REVERSAL", True))
    htf_tfs = [t for t in _split_tfs(get("MU_CORRECTION_HTF_TFS", "1h+4h")) if t in ("1h", "4h")]
    if not htf_tfs:
        htf_tfs = ["1h", "4h"]
    ob_hits = np.zeros(n, dtype=int)
    roll = np.zeros(n, dtype=int)
    for tf in htf_tfs:
        k = np.asarray(safe(npz, "k_%s" % tf, n, 50.0), dtype=float)
        rsi = np.asarray(safe(npz, "rsi_%s" % tf, n, 50.0), dtype=float)
        ob_hits = ob_hits + ((k >= k_min) | (rsi >= rsi_min)).astype(int)
        hi = np.asarray(safe(npz, "high_%s" % tf, n, 0.0), dtype=float)
        hip = np.asarray(safe(npz, "high_%s_prev" % tf, n, 0.0), dtype=float)
        cl = np.asarray(safe(npz, "close_%s" % tf, n, 0.0), dtype=float)
        clp = np.asarray(safe(npz, "close_%s_prev" % tf, n, 0.0), dtype=float)
        if high_req:
            high_rev = (hi > 0) & (hip > 0) & (hi >= hip)
        else:
            high_rev = np.ones(n, dtype=bool)
        if close_req:
            close_rev = (cl > 0) & (clp > 0) & (cl <= clp)
        else:
            close_rev = np.ones(n, dtype=bool)
        roll = roll + (high_rev & close_rev).astype(int)
    ltf_need = max(1, int(_f(get("MU_CORRECTION_LTF_FALL_MIN_TFS", 2), 2)))
    ltf_tfs = _split_tfs(get("MU_CORRECTION_LTF_FALL_TFS", "5m+15m"))
    fall = np.zeros(n, dtype=int)
    for tf in ltf_tfs:
        k = np.asarray(safe(npz, "k_%s" % tf, n, 50.0), dtype=float)
        kp = np.asarray(safe(npz, "k_%s_prev" % tf, n, 50.0), dtype=float)
        w1 = np.asarray(safe(npz, "wt1_%s" % tf, n, 0.0), dtype=float)
        w2 = np.asarray(safe(npz, "wt2_%s" % tf, n, 0.0), dtype=float)
        fall = fall + ((k < kp) | (((w1 != 0) | (w2 != 0)) & (w1 < w2))).astype(int)
    return (ob_hits >= htf_need) & (roll >= htf_need) & (fall >= ltf_need)


def mu_exit_live_fire(ind, is_long, sym, gain, get):
    if not _mu_enabled_for(get, "MU_CORRECTION_EXIT_ENABLED", is_long, sym):
        return False, ""
    d = ind or {}
    k_min = _f(get("MU_CORRECTION_HTF_K_MIN", 80.0), 80.0)
    rsi_min = _f(get("MU_CORRECTION_HTF_RSI_MIN", 60.0), 60.0)
    htf_need = max(1, int(_f(get("MU_CORRECTION_HTF_MIN_TFS", 1), 1)))
    high_req = bool(get("MU_CORRECTION_REQUIRE_HIGH_REVERSAL", True))
    close_req = bool(get("MU_CORRECTION_REQUIRE_CLOSE_REVERSAL", True))
    min_gain = _f(get("MU_CORRECTION_MIN_GAIN_PCT", 0.0), 0.0)
    htf_tfs = [t for t in _split_tfs(get("MU_CORRECTION_HTF_TFS", "1h+4h")) if t in ("1h", "4h")]
    if not htf_tfs:
        htf_tfs = ["1h", "4h"]
    hits = []
    for tf in htf_tfs:
        k = _f(d.get("k_%s" % tf, 50), 50.0)
        rsi = _f(d.get("rsi_%s" % tf, 50), 50.0)
        if k >= k_min or rsi >= rsi_min:
            hits.append(tf)
    roll = []
    for tf in htf_tfs:
        hi = _f(d.get("high_%s" % tf, 0), 0.0)
        hip = _f(d.get("high_%s_prev" % tf, 0), 0.0)
        cl = _f(d.get("close_%s" % tf, 0), 0.0)
        clp = _f(d.get("close_%s_prev" % tf, 0), 0.0)
        high_rev = (not high_req) or (hi > 0 and hip > 0 and hi >= hip)
        close_rev = (not close_req) or (cl > 0 and clp > 0 and cl <= clp)
        if high_rev and close_rev:
            roll.append(tf)
    ltf_need = max(1, int(_f(get("MU_CORRECTION_LTF_FALL_MIN_TFS", 2), 2)))
    ltf_tfs = _split_tfs(get("MU_CORRECTION_LTF_FALL_TFS", "5m+15m"))
    fall = []
    for tf in ltf_tfs:
        k = _f(d.get("k_%s" % tf, 50), 50.0)
        kp = _f(d.get("k_%s_prev" % tf, k), k)
        w1 = _f(d.get("wt1_%s" % tf, 0), 0.0)
        w2 = _f(d.get("wt2_%s" % tf, 0), 0.0)
        if k < kp or ((w1 != 0 or w2 != 0) and w1 < w2):
            fall.append(tf)
    if gain >= min_gain and len(hits) >= htf_need and len(roll) >= htf_need and len(fall) >= ltf_need:
        return True, "MU_CORRECTION_PEAK_ROLLOVER_HTF%s_OB%s_LTF_FALL%s_g%.2f" % ("+".join(roll), "+".join(hits), "+".join(fall), gain)
    return False, ""


def mu_reentry_mask(npz, n, is_long, sym, get, safe, close):
    if not _mu_enabled_for(get, "MU_CORRECTION_REENTRY_ENABLED", is_long, sym):
        return None
    tol = _f(get("MU_CORRECTION_REENTRY_DC_TOL_PCT", 2.0), 2.0) / 100.0
    px = np.asarray(close, dtype=float)
    lvl = np.zeros(n, dtype=bool)
    for tf in ("4h", "1h"):
        dc = np.asarray(safe(npz, "dc_low_%s" % tf, n, 0.0), dtype=float)
        lvl = lvl | ((dc > 0) & (px <= dc * (1.0 + tol)))
    cross = np.zeros(n, dtype=bool)
    if bool(get("MU_CORRECTION_REENTRY_STOCH_ENABLED", True)):
        for tf in ("1h", "4h"):
            k = np.asarray(safe(npz, "k_%s" % tf, n, 50.0), dtype=float)
            dd = np.asarray(safe(npz, "d_%s" % tf, n, 50.0), dtype=float)
            kp = np.asarray(safe(npz, "k_%s_prev" % tf, n, 50.0), dtype=float)
            dp = np.asarray(safe(npz, "d_%s_prev" % tf, n, 50.0), dtype=float)
            cross = cross | ((k > dd) & (kp <= dp))
    return lvl | cross


def mu_reentry_live_fire(ind, px, is_long, sym, age_min, get):
    if not _mu_enabled_for(get, "MU_CORRECTION_REENTRY_ENABLED", is_long, sym):
        return False, ""
    cool = _f(get("TRADIER_REENTRY_HARDCOOL_MIN", 30.0), 30.0)
    if not age_min >= cool:
        return False, ""
    d = ind or {}
    tol = _f(get("MU_CORRECTION_REENTRY_DC_TOL_PCT", 2.0), 2.0) / 100.0
    levels = []
    for tf in ("4h", "1h"):
        dc = _f(d.get("dc_low_%s" % tf, 0), 0.0)
        if dc > 0 and px <= dc * (1.0 + tol):
            levels.append("dc_low_%s" % tf)
    crosses = []
    if bool(get("MU_CORRECTION_REENTRY_STOCH_ENABLED", True)):
        for tf in ("1h", "4h"):
            k = _f(d.get("k_%s" % tf, 50), 50.0)
            dd = _f(d.get("d_%s" % tf, 50), 50.0)
            kp = _f(d.get("k_%s_prev" % tf, k), k)
            dp = _f(d.get("d_%s_prev" % tf, dd), dd)
            if k > dd and kp <= dp:
                crosses.append("stoch_cross_%s" % tf)
    if levels or crosses:
        return True, "MU_CORRECTION_REENTRY_%s_age%.0fm" % ("+".join(levels + crosses), age_min)
    return False, ""


# ─── STDEV BREAKOUT FAIL ───

def _stdev_params(get):
    htf = get("STDEV_BREAKOUT_HTF_LIST", ["D", "4h"])
    if isinstance(htf, str):
        htf = [t.strip() for t in htf.replace(",", "+").split("+") if t.strip()]
    if not htf:
        htf = ["D", "4h"]
    return {
        "htf": list(htf),
        "long": _f(get("STDEV_BREAKOUT_PCTB_LONG", 1.125), 1.125),
        "short": _f(get("STDEV_BREAKOUT_PCTB_SHORT", -0.125), -0.125),
        "rvol_min": _f(get("STDEV_BREAKOUT_RVOL_MIN", 1.2), 1.2),
        "max_age": int(_f(get("STDEV_BREAKOUT_MAX_AGE_BARS", 50), 50)),
        "fail": _f(get("STDEV_BREAKOUT_EXIT_PCTB_FAIL", 0.75), 0.75),
        "wt": bool(get("STDEV_BREAKOUT_EXIT_WT_ENABLED", True)),
    }


def _stdev_rvol_vec(npz, n, tf, safe):
    rv = np.asarray(safe(npz, "relative_volume_%s" % tf, n, 0.0), dtype=float)
    if tf != "1h":
        fill = rv <= 0
        if np.any(fill):
            rv = np.where(fill, np.asarray(safe(npz, "relative_volume_1h", n, 0.0), dtype=float), rv)
    fill = rv <= 0
    if np.any(fill):
        rv = np.where(fill, np.asarray(safe(npz, "relative_volume_15m", n, 0.0), dtype=float), rv)
    return rv


def stdev_fail_mask(npz, n, is_long, get, safe):
    if not bool(get("STDEV_BREAKOUT_ENABLED", False)):
        return None
    p = _stdev_params(get)
    pctb = {tf: np.asarray(safe(npz, "bb_pct_b_%s" % tf, n, 0.5), dtype=float) for tf in p["htf"]}
    rvol = {tf: _stdev_rvol_vec(npz, n, tf, safe) for tf in p["htf"]}
    w1 = np.asarray(safe(npz, "wt1_1h", n, 0.0), dtype=float)
    w2 = np.asarray(safe(npz, "wt2_1h", n, 0.0), dtype=float)
    fire = np.zeros(n, dtype=bool)
    armed_tf = None
    bars_since = 0
    for i in range(n):
        if armed_tf is None:
            for tf in p["htf"]:
                if is_long and pctb[tf][i] > p["long"] and rvol[tf][i] >= p["rvol_min"]:
                    armed_tf = tf
                    bars_since = 0
                    break
                if (not is_long) and pctb[tf][i] < p["short"] and rvol[tf][i] >= p["rvol_min"]:
                    armed_tf = tf
                    bars_since = 0
                    break
            continue
        bars_since += 1
        if bars_since > p["max_age"]:
            armed_tf = None
            continue
        v = pctb[armed_tf][i]
        if is_long and v < p["fail"]:
            fire[i] = True
            armed_tf = None
            continue
        if (not is_long) and v > (1.0 - p["fail"]):
            fire[i] = True
            armed_tf = None
            continue
        if p["wt"]:
            if is_long and w1[i] < w2[i] and w1[i] > 60:
                fire[i] = True
                armed_tf = None
            elif (not is_long) and w1[i] > w2[i] and w1[i] < -60:
                fire[i] = True
                armed_tf = None
    return fire


class StdevFailLive:
    """Stateful scalar twin of check_stdev_breakout_exit for the stocks venue
    (crypto already calls the shared function; tradier never imports it)."""

    def __init__(self):
        self._state = {}
        self._buf = {}

    def _pctb(self, sym, tf, ind):
        try:
            v = float(ind.get("bb_pct_b_%s" % tf))
            if v == v:
                return v
        except (TypeError, ValueError):
            pass
        px = _f(ind.get("close_%s" % tf, 0), 0.0)
        if px <= 0:
            px = _f(ind.get("current_price", 0), 0.0)
        if px <= 0:
            return 0.5
        key = "%s_%s" % (sym, tf)
        buf = self._buf.setdefault(key, [])
        if not buf or abs(buf[-1] - px) > 1e-8:
            buf.append(px)
            del buf[:-20]
        if len(buf) < 20:
            return 0.5
        mean = sum(buf) / len(buf)
        var = sum((v - mean) ** 2 for v in buf) / len(buf)
        std = var ** 0.5
        if std <= 0:
            return 0.5
        width = 4.0 * std
        if width <= 0:
            return 0.5
        return (px - (mean - 2.0 * std)) / width

    def fire(self, ind, is_long, sym, get):
        if not bool(get("STDEV_BREAKOUT_ENABLED", False)):
            return False, ""
        p = _stdev_params(get)
        st = self._state.setdefault(sym, {"active": False, "htf": None, "bars": 0})
        d = ind or {}
        if not st["active"]:
            for tf in p["htf"]:
                v = self._pctb(sym, tf, d)
                rv = _f(d.get("relative_volume_%s" % tf, 0), 0.0)
                if rv <= 0:
                    rv = _f(d.get("relative_volume_1h", 0), 0.0)
                if rv <= 0:
                    rv = _f(d.get("relative_volume_15m", 0), 0.0)
                if is_long and v > p["long"] and rv >= p["rvol_min"]:
                    st.update(active=True, htf=tf, bars=0)
                    return False, ""
                if (not is_long) and v < p["short"] and rv >= p["rvol_min"]:
                    st.update(active=True, htf=tf, bars=0)
                    return False, ""
            return False, ""
        st["bars"] += 1
        if st["bars"] > p["max_age"]:
            st.update(active=False, htf=None, bars=0)
            return False, ""
        htf = st.get("htf") or "D"
        v = self._pctb(sym, htf, d)
        if is_long and v < p["fail"]:
            st.update(active=False, htf=None, bars=0)
            return True, "STDEV_BREAKOUT_FAILED_%s_pctb=%.3f<%.2f" % (htf, v, p["fail"])
        if (not is_long) and v > (1.0 - p["fail"]):
            st.update(active=False, htf=None, bars=0)
            return True, "STDEV_BREAKOUT_FAILED_%s_pctb=%.3f>%.3f" % (htf, v, 1.0 - p["fail"])
        if p["wt"]:
            w1 = _f(d.get("wt1_1h", 0), 0.0)
            w2 = _f(d.get("wt2_1h", 0), 0.0)
            if is_long and w1 < w2 and w1 > 60:
                st.update(active=False, htf=None, bars=0)
                return True, "STDEV_BREAKOUT_WT_EXIT_1h"
            if (not is_long) and w1 > w2 and w1 < -60:
                st.update(active=False, htf=None, bars=0)
                return True, "STDEV_BREAKOUT_WT_EXIT_1h"
        return False, ""


# ─── EXIT VELOCITY WT ───

def velocity_wt_tfs(get):
    return [t for t in _split_tfs(get("EXIT_VELOCITY_WT_TFS", "1h,4h,D")) if t in _KNOWN_TFS]


def velocity_wt_exit_mask(npz, n, is_long, get, safe):
    tfs = velocity_wt_tfs(get)
    if not tfs:
        return None
    fire = np.zeros(n, dtype=bool)
    for tf in tfs:
        vel = np.asarray(safe(npz, "wt_velocity_%s" % tf, n, 0.0), dtype=float)
        if is_long:
            fire = fire | (vel < 0)
        else:
            fire = fire | (vel > 0)
    return fire


def velocity_wt_min_tfs(get):
    # 2026-10-08 USER gains: EXIT_VELOCITY_WT_MIN_TFS = how many of the EXIT_VELOCITY_WT_TFS must be against
    # the position before the exit fires (1 = today's any-TF behaviour). Same read on vec + ez + tradier.
    try:
        return max(1, int(float(get("EXIT_VELOCITY_WT_MIN_TFS", 1) or 1)))
    except Exception:
        return 1


def velocity_wt_hits(vals, is_long):
    return [(tf, vel) for tf, vel in vals if ((vel < 0) if is_long else (vel > 0))]


def velocity_wt_arrays(npz, n, get, safe):
    return {tf: np.asarray(safe(npz, "wt_velocity_%s" % tf, n, 0.0), dtype=float) for tf in velocity_wt_tfs(get)}


def velocity_wt_fire_at(arrs, i, is_long, get):
    vals = [(tf, float(a[i])) for tf, a in arrs.items() if i < len(a)]
    return len(velocity_wt_hits(vals, is_long)) >= velocity_wt_min_tfs(get)


def velocity_wt_exit_live_fire(ind, is_long, get):
    tfs = velocity_wt_tfs(get)
    if not tfs:
        return False, ""
    d = ind or {}
    hits = velocity_wt_hits([(tf, _f(d.get("wt_velocity_%s" % tf, 0), 0.0)) for tf in tfs], is_long)
    if len(hits) < velocity_wt_min_tfs(get):
        return False, ""
    tf, vel = hits[0]
    return True, "EXIT_VELOCITY_WT_%s_vel%.1f-against-%s" % (tf, vel, "long" if is_long else "short")
