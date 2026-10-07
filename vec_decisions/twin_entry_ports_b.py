"""Entry-ports-B live twins (2026-10-04 wiring mandate).

Shared pure predicates for 20 entry/exit switches. Every function is a real
predicate (BIBLE 19: no stubs, no `and False`); all config/indicator keys are
string literals. Each takes ``get`` (callable ``(key, default)`` — live passes
a per-sym getter), ``is_long``, and the live indicator snapshot ``ind``.
Fail-open: any exception or missing data returns no-fire.

Source map (vec = v12_quick_engine.py, live = tradier_manage.py):
  WT_15M_BOUNCE_OPEN_ENABLED + BB_MIN/BB_MAX/VOLUME_MODE/HIGH_1H_GT_PREV/
    LOW_1H_GT_PREV/REL_VOL_GT_1: vec compute_entry_signals 9736-9814,
    stocks-live _shared_direct_entry_claim 1298-1356.
  WT_ENTRY_ENABLED: vec 8473-8478, stocks-live 13226-13234.
  STOCH_ENTRY_ENABLED: vec 8466-8471, stocks-live 13198-13208.
  SATOSHIT_ENTRY_ENABLED: vec 8688-8703 (1h/3m keys), stocks-live 12571-12600
    + 13057-13090 (15m keys) — twin ports the STOCKS-LIVE 15m variant (all keys
    exist in ez snapshots; vec 1h keys ha_streak_1h have no live producer).
  MFI_ENTRY_ENABLED: vec AND-gate 9111-9118 (mfi_1h < 60 / > 40). Default True.
  SMA200_DIST_ENTRY_ENABLED: vec OR-block 8602-8605 (sma_200_1h, thr -3.0).
    Default True. Stocks-live is a 4h veto at -10 (different semantics).
  EMA20_SLOPE_ENTRY_ENABLED: vec OR-block 8593-8596. Default True. No genuine
    stocks-live (20837 is a dynamic-key telemetry read, not logic).
  BB_PCTB_ENTRY_ENABLED: vec 8629-8630, stocks-live 13025-13034.
  VWAP_BOUNCE_ENTRY_ENABLED + VWAP_BOUNCE_DIST_PCT: vec 8683-8686. No genuine
    stocks-live (16286 is `and False` dead code). ez has no vwap key at all.
  BAND_ARROW_ENABLED: NO functioning vec path (engine reads DEAD, module
    missing) — twin ports stocks-live band_arrow_score 10147-10168 verbatim.
  WT_DC_DETAILED_SCORER_ENABLED: vec 9424-9426 via wt_dc_entry_scorer_vec,
    stocks-live 12671-12672 via wt_dc_entry_scorer.score_entry(detailed=True).
    Twin calls the same shared scorer + 43 threshold + TF adjust.
  WT_CROSSUNDER_FINAL_ENABLED (EXIT, both venues): vec compute_exit_signals
    9955-9959 inline (module missing, so inline IS vec truth). No live either
    venue (16297 is `and False` dead code). Default True both venues.
  BB_BOUNCE_ENTRY_TF (both venues): vec 8899-8913 pct-b reclaim. No live
    either venue. Dormant until bb_pct_b_{TF}_prev pipeline keys exist.

Documented live-vs-vec deltas (direction-consistent, measured-not-assumed):
  D1 HTF: vec/tradier read wt_cross_rising_1h/4h arrays; ez snapshots lack
    them (0 refs) so the twin falls back to the ez-native level proxy
    wt1_TF > wt2_TF (same idiom as the WT_3M force-open HTF filter).
  D2 Volume: vec reads relative_volume_15m primary + 1h fallback; tradier-live
    reads 1h primary (user 2026-09-07). Twin mirrors vec (15m + 1h fallback).
  D3 Explicit-0: twin honors explicit 0 thresholds (vec-faithful); tradier-live
    `or <default>` idiom coerces 0 -> default.
  D4 SATOSHIT: live 15m votes vs vec 1h/3m votes (pre-existing stocks delta);
    SHORT HTF gate is mfi_D >= 30 both sides (live) vs vec mfi_D <= 70 short.
  D5 Prev-bar keys (wt1_15m_prev, dc_*_1h_prev, wt1_3m_prev, wt1_5m_prev) default
    to current value when absent -> cross/edge predicates no-fire (safe).
  D6 BB_BOUNCE prev keys (bb_pct_b_{TF}_prev) exist on NEITHER venue (0 refs)
    -> twin is dormant-safe until the pipeline produces them.
"""

def _fsafe(value, default):
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _f(ind, key, default=0.0):
    try:
        return _fsafe((ind or {}).get(key, default), default)
    except Exception:
        return default


def _b(ind, key, default=False):
    try:
        value = (ind or {}).get(key, default)
        if isinstance(value, str):
            return value.strip().upper() in ("1", "TRUE", "YES", "Y", "T")
        return bool(value)
    except Exception:
        return default


def _cf(get, key, default):
    try:
        value = get(key, default)
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def wt_15m_bounce(get, is_long, ind):
    try:
        if not bool(get("WT_15M_BOUNCE_OPEN_ENABLED", False)):
            return False, ""
        ind = ind or {}
        bb_min = _cf(get, "WT_15M_BOUNCE_BB_MIN", 0.05)
        bb_max = _cf(get, "WT_15M_BOUNCE_BB_MAX", 0.95)
        req_both = bool(get("WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False))
        w1 = _f(ind, "wt1_15m")
        w2 = _f(ind, "wt2_15m")
        w1p = _f(ind, "wt1_15m_prev", w1)
        w2p = _f(ind, "wt2_15m_prev", w2)
        up = (w1p <= w2p) and (w1 > w2)
        down = (w1p >= w2p) and (w1 < w2)
        cross = up if is_long else down
        still = (w1 > w2) if is_long else (w1 < w2)
        bb = _f(ind, "bb_pct_b_15m", 0.5)
        bb_ok = (bb >= bb_min) and (bb <= bb_max)
        if ind.get("wt_cross_rising_1h", None) is None:
            r1 = _f(ind, "wt1_1h") > _f(ind, "wt2_1h")
        else:
            r1 = _b(ind, "wt_cross_rising_1h")
        if ind.get("wt_cross_rising_4h", None) is None:
            r4 = _f(ind, "wt1_4h") > _f(ind, "wt2_4h")
        else:
            r4 = _b(ind, "wt_cross_rising_4h")
        h1 = r1 if is_long else (not r1)
        h4 = r4 if is_long else (not r4)
        htf_ok = (h1 and h4) if req_both else (h1 or h4)
        hl_on = bool(get("WT_15M_BOUNCE_FILTER_HL_ENABLED", False) or get("WT_15M_BOUNCE_LOW_1H_GT_PREV", False))
        hh_on = bool(get("WT_15M_BOUNCE_FILTER_HH_ENABLED", False) or get("WT_15M_BOUNCE_HIGH_1H_GT_PREV", False))
        hl_ok, hh_ok = True, True
        if hl_on or hh_on:
            dl = _f(ind, "dc_low_1h")
            dlp = _f(ind, "dc_low_1h_prev", dl)
            dh = _f(ind, "dc_high_1h")
            dhp = _f(ind, "dc_high_1h_prev", dh)
            if hl_on:
                hl_ok = dl > dlp
            if hh_on:
                hh_ok = dh > dhp
            mode = str(get("WT_15M_BOUNCE_FILTER_MODE", "AND") or "AND").upper()
            if mode == "OR":
                if hl_on and not hh_on:
                    hlhh = hl_ok
                elif hh_on and not hl_on:
                    hlhh = hh_ok
                else:
                    hlhh = hl_ok or hh_ok
            else:
                hlhh = hl_ok and hh_ok
        else:
            hlhh = True
        trigger = still if (hl_on or hh_on) else cross
        vol_on = bool(get("WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False) or get("WT_15M_BOUNCE_REL_VOL_GT_1", False))
        vol_ok = True
        if vol_on:
            vmode = str(get("WT_15M_BOUNCE_VOLUME_MODE", "relvol") or "relvol").lower()
            vthr = _cf(get, "WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0)
            if vmode == "relvol":
                rel = ind.get("relative_volume_15m", None)
                if rel is None:
                    rel = _f(ind, "relative_volume_1h", 1.0)
                else:
                    rel = _fsafe(rel, 1.0)
                vol_ok = rel > vthr
            elif vmode == "ema":
                vol = ind.get("volume_15m", None)
                sma = ind.get("volume_sma_15m", None)
                if vol is None or sma is None:
                    vol = _f(ind, "volume_1h")
                    sma = _f(ind, "volume_sma_1h")
                else:
                    vol = _fsafe(vol, 0.0)
                    sma = _fsafe(sma, 0.0)
                sma_s = sma if sma != 0 else 1.0
                vol_ok = vol > (sma_s * vthr)
            else:
                vol = ind.get("volume_15m", None)
                sma = ind.get("volume_sma_15m", None)
                if vol is None or sma is None:
                    vol = _f(ind, "volume_1h")
                    sma = _f(ind, "volume_sma_1h")
                else:
                    vol = _fsafe(vol, 0.0)
                    sma = _fsafe(sma, 0.0)
                sma_s = sma if sma != 0 else 1.0
                vol_ok = vol > sma_s
        fire = bool(trigger and bb_ok and htf_ok and hlhh and vol_ok)
        if not fire:
            return False, ""
        return True, f"WT_15M_BOUNCE_{'L' if is_long else 'S'}_still={int(still)}_cross={int(cross)}_bb={bb:.2f}_hlhh={int(hlhh)}_vol={int(vol_ok)}"
    except Exception:
        return False, ""


def stoch_entry(get, is_long, ind):
    try:
        if not bool(get("STOCH_ENTRY_ENABLED", False)):
            return False, ""
        ind = ind or {}
        k = ind.get("k_1h", None)
        if k is None:
            k = _f(ind, "stoch_k_1h", 50.0)
        else:
            k = _fsafe(k, 50.0)
        d = ind.get("d_1h", None)
        if d is None:
            d = _f(ind, "stoch_d_1h", 50.0)
        else:
            d = _fsafe(d, 50.0)
        fire = (k < 30 and k > d) if is_long else (k > 70 and k < d)
        if not fire:
            return False, ""
        return True, f"STOCH_ENTRY_{'L' if is_long else 'S'}_k={k:.0f}"
    except Exception:
        return False, ""


def wt_entry(get, is_long, ind):
    try:
        if not bool(get("WT_ENTRY_ENABLED", False)):
            return False, ""
        ind = ind or {}
        w1 = ind.get("wt1_1h", None)
        if w1 is None:
            w1 = _f(ind, "wt1_15m")
        else:
            w1 = _fsafe(w1, 0.0)
        w2 = ind.get("wt2_1h", None)
        if w2 is None:
            w2 = _f(ind, "wt2_15m")
        else:
            w2 = _fsafe(w2, 0.0)
        fire = (w1 < -50 and w1 > w2) if is_long else (w1 > 50 and w1 < w2)
        if not fire:
            return False, ""
        return True, f"WT_ENTRY_{'L' if is_long else 'S'}_w1={w1:.1f}"
    except Exception:
        return False, ""


def ema20_slope_entry(get, is_long, ind):
    try:
        if not bool(get("EMA20_SLOPE_ENTRY_ENABLED", True)):
            return False, ""
        ind = ind or {}
        e = _f(ind, "ema_20_1h")
        p = _f(ind, "ema_20_1h_prev")
        slope = (e - p) / max(p, 1e-9) * 100 if p > 0 else 0.0
        thr = _cf(get, "EMA20_SLOPE_SHORT_THRESHOLD_1H", 0.05)
        fire = (slope > thr) if is_long else (slope < -thr)
        if not fire:
            return False, ""
        return True, f"EMA20_SLOPE_{'L' if is_long else 'S'}_sl={slope:.3f}"
    except Exception:
        return False, ""


def sma200_dist_entry(get, is_long, ind):
    try:
        if not bool(get("SMA200_DIST_ENTRY_ENABLED", True)):
            return False, ""
        ind = ind or {}
        px = _f(ind, "current_price")
        sma = _f(ind, "sma_200_1h")
        dist = (px - sma) / sma * 100 if sma > 0 else 0.0
        thr = _cf(get, "SMA200_DIST_LONG_THRESHOLD", -3.0)
        fire = (dist < thr) if is_long else (dist > -thr)
        if not fire:
            return False, ""
        return True, f"SMA200_DIST_{'L' if is_long else 'S'}_d={dist:.2f}"
    except Exception:
        return False, ""


def bb_pctb_entry(get, is_long, ind):
    try:
        if not bool(get("BB_PCTB_ENTRY_ENABLED", False)):
            return False, ""
        ind = ind or {}
        b = _f(ind, "bb_pct_b_1h", 0.5)
        lt = _cf(get, "BB_ENTRY_LONG_THRESHOLD", -0.2)
        st = _cf(get, "BB_ENTRY_SHORT_THRESHOLD", 1.0)
        fire = (b < lt) if is_long else (b > st)
        if not fire:
            return False, ""
        return True, f"BB_PCTB_ENTRY(bb={b:.3f}{'<'+str(lt) if is_long else '>'+str(st)})"
    except Exception:
        return False, ""


def vwap_bounce_entry(get, is_long, ind):
    try:
        if not bool(get("VWAP_BOUNCE_ENTRY_ENABLED", True)):
            return False, ""
        ind = ind or {}
        px = _f(ind, "current_price")
        v = ind.get("vwap_D", None)
        if v is None:
            v = _f(ind, "vwap")
        else:
            v = _fsafe(v, 0.0)
        if v <= 0 or px <= 0:
            return False, ""
        d = _cf(get, "VWAP_BOUNCE_DIST_PCT", 0.3)
        near = abs(px - v) / max(v, 1e-9) * 100 <= d
        fire = near and ((px > v) if is_long else (px < v))
        if not fire:
            return False, ""
        return True, f"VWAP_BOUNCE_{'L' if is_long else 'S'}_d={abs(px - v) / v * 100:.3f}"
    except Exception:
        return False, ""


def satoshit_entry(get, is_long, ind):
    try:
        if not bool(get("SATOSHIT_ENTRY_ENABLED", False)):
            return False, ""
        ind = ind or {}
        rsi = _f(ind, "rsi_15m", 50.0)
        k = _f(ind, "stoch_k_15m", 50.0)
        mfi = _f(ind, "mfi_15m", 50.0)
        bb = _f(ind, "bb_pct_b_1h", 0.5)
        ha = str(ind.get("ha_15m", "neutral"))
        mfi_d = _f(ind, "mfi_D", 50.0)
        rvol = _f(ind, "relative_volume_1h", 1.0)
        min_votes = int(_cf(get, "SATOSHIT_MIN_VOTES_TRADIER", _cf(get, "SATOSHIT_MIN_VOTES", 3)))
        if is_long:
            ha_val = -1 if ha == "red" else (1 if ha == "green" else 0)
            votes = int(rsi < _cf(get, "SATOSHIT_LONG_RSI_MAX_TRADIER", 50.0))
            votes += int(bb < _cf(get, "SATOSHIT_LONG_BB_PCTB_MAX", 0.5))
            votes += int(ha_val < int(_cf(get, "SATOSHIT_LONG_HA_STREAK_MAX", 1)))
            votes += int(k < _cf(get, "SATOSHIT_LONG_STOCH_K_MAX_TRADIER", 60.0))
            votes += int(mfi < _cf(get, "SATOSHIT_LONG_MFI_MAX_TRADIER", 60.0))
        else:
            ha_val = 1 if ha == "green" else (-1 if ha == "red" else 0)
            votes = int(rsi > _cf(get, "SATOSHIT_SHORT_RSI_MIN_TRADIER", 55.0))
            votes += int(bb > _cf(get, "SATOSHIT_SHORT_BB_PCTB_MIN", 0.55))
            votes += int(ha_val > int(_cf(get, "SATOSHIT_SHORT_HA_STREAK_MIN", 0)))
            votes += int(k > _cf(get, "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER", 50.0))
            votes += int(mfi > _cf(get, "SATOSHIT_SHORT_MFI_MIN_TRADIER", 50.0))
        htf_ok = (mfi_d >= _cf(get, "SATOSHIT_HTF_MFI_D_MIN_TRADIER", 30.0)) and (rvol >= _cf(get, "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER", 0.3))
        if votes >= min_votes and htf_ok:
            return True, f"SATOSHIT_{'LONG' if is_long else 'SHORT'}_v{votes}of5_R{rsi:.0f}K{k:.0f}"
        return False, ""
    except Exception:
        return False, ""


def _band_arrow_tf_keys(ind, tf):
    if tf == "D":
        return ind.get("lrL_slope_D", None), ind.get("lrL_pct_b_D", None)
    if tf == "4h":
        return ind.get("lrL_slope_4h", None), ind.get("lrL_pct_b_4h", None)
    if tf == "1h":
        return ind.get("lrL_slope_1h", None), ind.get("lrL_pct_b_1h", None)
    if tf == "15m":
        return ind.get("lrL_slope_15m", None), ind.get("lrL_pct_b_15m", None)
    if tf == "5m":
        return ind.get("lrL_slope_5m", None), ind.get("lrL_pct_b_5m", None)
    if tf == "3m":
        return ind.get("lrL_slope_3m", None), ind.get("lrL_pct_b_3m", None)
    return None, None


def band_arrow_entry(get, is_long, ind):
    try:
        if not bool(get("BAND_ARROW_ENABLED", False)):
            return False, ""
        ind = ind or {}
        tfs_raw = str(get("BAND_ARROW_ENTRY_TFS", "D,4h,1h") or "D,4h,1h")
        tfs = [t.strip() for t in tfs_raw.split(",") if t.strip()]
        deadband = _cf(get, "BAND_ARROW_SLOPE_DEADBAND", 0.0)
        for tf in tfs:
            sl, pb = _band_arrow_tf_keys(ind, tf)
            if sl is None or pb is None:
                continue
            sl_f = _fsafe(sl, 0.0)
            pb_f = _fsafe(pb, 0.5)
            if is_long and sl_f > deadband and pb_f < 0.5:
                return True, f"BAND_ARROW_{tf}_sl{sl_f:.2f}_pb{pb_f:.2f}"
            if not is_long and sl_f < -deadband and pb_f > 0.5:
                return True, f"BAND_ARROW_{tf}_sl{sl_f:.2f}_pb{pb_f:.2f}"
        return False, ""
    except Exception:
        return False, ""


def mfi_gate(get, is_long, ind):
    try:
        if not bool(get("MFI_ENTRY_ENABLED", True)):
            return True
        ind = ind or {}
        m = _f(ind, "mfi_1h", 50.0)
        if is_long:
            return m < _cf(get, "MFI_ENTRY_LONG_MAX", 60.0)
        return m > _cf(get, "MFI_ENTRY_SHORT_MIN", 40.0)
    except Exception:
        return True


def wt_dc_detailed_entry(get, is_long, ind, px=0.0):
    try:
        if not bool(get("WT_DC_DETAILED_SCORER_ENABLED", False)):
            return False, ""
        try:
            from wt_dc_entry_scorer import score_entry as _se
        except Exception:
            return False, ""
        score, _sreason = _se(ind or {}, is_long, _fsafe(px, 0.0), detailed=True)
        thr = _cf(get, "WT_DC_DETAILED_ENTRY_THRESHOLD", 43.0)
        tf = str(get("WT_DC_TF_ENTRY", "1h") or "1h").lower()
        if tf == "15m":
            thr = max(20.0, thr - 10)
        elif tf == "4h":
            thr = min(85.0, thr + 10)
        elif tf == "d":
            thr = min(85.0, thr + 15)
        if float(score) >= thr:
            return True, f"WT_DC_DETAILED_{'L' if is_long else 'S'}_sc{float(score):.0f}_thr{thr:.0f}"
        return False, ""
    except Exception:
        return False, ""


def wt_crossunder_final_exit(get, is_long, ind, base_tf="3m"):
    try:
        if not bool(get("WT_CROSSUNDER_FINAL_ENABLED", True)):
            return False, ""
        ind = ind or {}
        if str(base_tf) == "5m":
            w1 = _f(ind, "wt1_5m")
            w2 = _f(ind, "wt2_5m")
            w1p = _f(ind, "wt1_5m_prev", w1)
            kk = ind.get("stoch_k_5m", None)
            if kk is None:
                kk = _f(ind, "k_5m", 50.0)
            else:
                kk = _fsafe(kk, 50.0)
        else:
            w1 = _f(ind, "wt1_3m")
            w2 = _f(ind, "wt2_3m")
            w1p = _f(ind, "wt1_3m_prev", w1)
            kk = ind.get("stoch_k_3m", None)
            if kk is None:
                kk = _f(ind, "k_3m", 50.0)
            else:
                kk = _fsafe(kk, 50.0)
        if is_long:
            fire = (w1p >= w2) and (w1 < w2) and (kk >= 70)
        else:
            fire = (w1p <= w2) and (w1 > w2) and (kk <= 30)
        if not fire:
            return False, ""
        return True, f"WT_CROSSUNDER_FINAL_{'L' if is_long else 'S'}_k={kk:.0f}"
    except Exception:
        return False, ""


def bb_bounce_entry(get, is_long, ind):
    try:
        tf = str(get("BB_BOUNCE_ENTRY_TF", "OFF") or "OFF")
        if tf not in ("15m", "1h", "4h", "D"):
            return False, ""
        ind = ind or {}
        if tf == "15m":
            pct = _f(ind, "bb_pct_b_15m", 0.5)
            prev = _f(ind, "bb_pct_b_15m_prev", pct)
            lower = _f(ind, "bb_lower_15m")
            upper = _f(ind, "bb_upper_15m")
        elif tf == "1h":
            pct = _f(ind, "bb_pct_b_1h", 0.5)
            prev = _f(ind, "bb_pct_b_1h_prev", pct)
            lower = _f(ind, "bb_lower_1h")
            upper = _f(ind, "bb_upper_1h")
        elif tf == "4h":
            pct = _f(ind, "bb_pct_b_4h", 0.5)
            prev = _f(ind, "bb_pct_b_4h_prev", pct)
            lower = _f(ind, "bb_lower_4h")
            upper = _f(ind, "bb_upper_4h")
        else:
            pct = _f(ind, "bb_pct_b_D", 0.5)
            prev = _f(ind, "bb_pct_b_D_prev", pct)
            lower = _f(ind, "bb_lower_D")
            upper = _f(ind, "bb_upper_D")
        if is_long:
            fire = (prev < 0.20) and (pct > 0.25) and (lower > 0)
        else:
            fire = (prev > 0.80) and (pct < 0.75) and (upper > 0)
        if not fire:
            return False, ""
        return True, f"BB_BOUNCE_ENTRY_{tf}_{'L' if is_long else 'S'}_pct={pct:.2f}"
    except Exception:
        return False, ""
