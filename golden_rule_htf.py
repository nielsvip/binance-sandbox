"""
golden_rule_htf.py — Multi-timeframe indicator confirmation gate.

The GOLDEN RULE: a trade signal only fires when at least MIN_TFS timeframes
each show at least MIN_IND bullish (or bearish for shorts/exits) indicators.

11 indicators checked per TF (skipped gracefully if field missing):
  1. WT      — wt1 > wt2 (bullish) / wt1 < wt2 (bearish)
  2. RSI     — rsi > 50 (bullish) / rsi < 50 (bearish)  [short-TF signal]
  3. MFI     — mfi > 50 (bullish) / mfi < 50 (bearish)  [long-TF signal]
  4. DC      — room-to-run mode: dc_pos < 0.65 (not extended) / > 0.35
               breakout mode:    dc_pos >= 0.65 (extended/breaking out) / <= 0.35
  5. BB      — room-to-run mode: bb_pct_b < 0.75 (not at top of band) / > 0.25
               breakout mode:    bb_pct_b >= 0.75 (above upper band) / <= 0.25
  6. RVOL    — relative_volume > 1.0 (above-avg volume = conviction, both dirs)
  7. K       — stoch_k < 80 (not overbought, long) / stoch_k > 20 (not oversold, short)
  8. ADX     — adx > 20 (trending environment, direction-agnostic — counts for both LONG/SHORT)  [2026-05-17 USER add]
  9. MACD_H  — macd_hist > 0 (bullish) / macd_hist < 0 (bearish)                                [2026-05-17 USER add]
 10. HA      — ha_color > 0 (green = bullish) / ha_color < 0 (red = bearish)                    [2026-05-17 USER add]
 11. K>D     — stoch_k > stoch_d (bullish K-cross) / stoch_k < stoch_d (bearish)                [2026-05-17 USER add]

Two DC/BB semantic modes:
  invert_dc_bb=False (default, room-to-run): DC/BB not extended = bullish.
    Use for reentry, standard confirmation gates.
  invert_dc_bb=True (breakout mode): DC/BB EXTENDED = bullish (price broke the channel/band).
    Use for GOLDEN_RULE loop entries — GR fires ON breakouts, so extension confirms the signal.
    Without this flag, all GR entries see DC/BB as "bearish" (extended), leaving only WT/RSI/MFI/RVOL/K
    to satisfy MIN_IND — and since WT is already required by the GR trigger, MIN_TFS=1 ≡ MIN_TFS=2.

TFs checked:
  crypto : 3m, 15m, 1h, 4h, D, W  (W added 2026-05-17 per USER mandate — was 5 TFs, now 6)
  tradier: 5m, 15m, 1h, 4h, D, W

Usage:
  from golden_rule_htf import score_entry_htf, score_exit_htf

  # Standard (room-to-run):
  passes, n_tfs, detail = score_entry_htf(indicators, is_long, mode='tradier',
                                           min_tfs=2, min_ind=2)
  # Breakout GR path:
  passes, n_tfs, detail = score_entry_htf(indicators, is_long, mode='crypto',
                                           min_tfs=2, min_ind=3, invert_dc_bb=True)
  if not passes:
      return "NO_ACTION", f"GOLDEN_RULE_HTF {detail}", 0.0, 0.0
"""

from __future__ import annotations
from typing import Any

_CRYPTO_TFS = ["3m", "15m", "1h", "4h", "D", "W"]  # 2026-05-17 USER: +W (was 5 TFs)
_TRADIER_TFS = ["5m", "15m", "1h", "4h", "D", "W"]

_DC_EXTENDED_LONG = 0.65
_DC_EXTENDED_SHORT = 0.35
_BB_EXTENDED_LONG = 0.75
_BB_EXTENDED_SHORT = 0.25


def _ind_score(
    ind: dict,
    tf: str,
    is_long: bool,
    px: float,
    invert_dc_bb: bool = False,
    dc_threshold: float = 0.0,
    bb_threshold: float = 0.0,
) -> tuple[int, str]:
    """Return (n_bullish_indicators, detail_str) for a single timeframe.

    invert_dc_bb=True: use breakout semantics for DC/BB (extension = bullish).
    invert_dc_bb=False (default): use room-to-run semantics (not extended = bullish).
    dc_threshold / bb_threshold: override module-level _DC/_BB_EXTENDED constants (0 = use default).
    Short thresholds are derived as 1 - long_threshold (symmetric channel).
    """
    score = 0
    parts = []

    wt1 = float(ind.get(f"wt1_{tf}") or 0)
    wt2 = float(ind.get(f"wt2_{tf}") or 0)
    if wt1 != 0 or wt2 != 0:
        ok = wt1 > wt2 if is_long else wt1 < wt2
        score += int(ok)
        parts.append(f"WT{'✓' if ok else '✗'}")

    rsi = float(ind.get(f"rsi_{tf}") if ind.get(f"rsi_{tf}") is not None else -1)
    if rsi >= 0:
        ok = rsi > 50 if is_long else rsi < 50
        score += int(ok)
        parts.append(f"RSI{'✓' if ok else '✗'}{rsi:.0f}")

    mfi = float(ind.get(f"mfi_{tf}") if ind.get(f"mfi_{tf}") is not None else -1)
    if mfi >= 0:
        ok = mfi > 50 if is_long else mfi < 50
        score += int(ok)
        parts.append(f"MFI{'✓' if ok else '✗'}{mfi:.0f}")

    dc_pos = float(ind.get(f"dc_position_{tf}") if ind.get(f"dc_position_{tf}") is not None else -1)
    if dc_pos < 0:
        dc_h = float(ind.get(f"dc_high_{tf}") or 0)
        dc_l = float(ind.get(f"dc_low_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if dc_h > dc_l > 0 and ref_px > 0:
            dc_pos = (ref_px - dc_l) / (dc_h - dc_l)
    _dc_l = dc_threshold if dc_threshold > 0 else _DC_EXTENDED_LONG
    _dc_s = 1.0 - _dc_l
    if dc_pos >= 0:
        if invert_dc_bb:
            ok = dc_pos >= _dc_l if is_long else dc_pos <= _dc_s
        else:
            ok = dc_pos < _dc_l if is_long else dc_pos > _dc_s
        score += int(ok)
        parts.append(f"DC{'✓' if ok else '✗'}{dc_pos:.2f}")

    bb_pctb = float(ind.get(f"bb_pct_b_{tf}") if ind.get(f"bb_pct_b_{tf}") is not None else -1)
    if bb_pctb < 0:
        bb_u = float(ind.get(f"bb_upper_{tf}") or 0)
        bb_l = float(ind.get(f"bb_lower_{tf}") or 0)
        ref_px = px or float(ind.get(f"close_{tf}") or 0)
        if bb_u > bb_l > 0 and ref_px > 0:
            bb_pctb = (ref_px - bb_l) / (bb_u - bb_l)
    _bb_l = bb_threshold if bb_threshold > 0 else _BB_EXTENDED_LONG
    _bb_s = 1.0 - _bb_l
    if bb_pctb >= 0:
        if invert_dc_bb:
            ok = bb_pctb >= _bb_l if is_long else bb_pctb <= _bb_s
        else:
            ok = bb_pctb < _bb_l if is_long else bb_pctb > _bb_s
        score += int(ok)
        parts.append(f"BB{'✓' if ok else '✗'}{bb_pctb:.2f}")

    rvol = float(ind.get(f"relative_volume_{tf}") if ind.get(f"relative_volume_{tf}") is not None else -1)
    if rvol >= 0:
        ok = rvol > 1.0
        score += int(ok)
        parts.append(f"RVOL{'✓' if ok else '✗'}{rvol:.2f}")

    stk = float(ind.get(f"stoch_k_{tf}") if ind.get(f"stoch_k_{tf}") is not None else -1)
    if stk >= 0:
        ok = stk < 80.0 if is_long else stk > 20.0
        score += int(ok)
        parts.append(f"K{'✓' if ok else '✗'}{stk:.0f}")

    # === USER 2026-05-17 expansion: ADX, MACD_HIST, HA, K-vs-D ===
    adx = float(ind.get(f"adx_{tf}") if ind.get(f"adx_{tf}") is not None else -1)
    if adx > 0:
        ok = adx > 20.0
        score += int(ok)
        parts.append(f"ADX{'✓' if ok else '✗'}{adx:.0f}")

    _mh_raw = ind.get(f"macd_hist_{tf}")
    if _mh_raw is not None:
        try:
            mh = float(_mh_raw)
            if mh != 0.0:
                ok = mh > 0 if is_long else mh < 0
                score += int(ok)
                parts.append(f"MH{'✓' if ok else '✗'}{mh:.4f}")
        except (TypeError, ValueError):
            pass

    _ha_raw = ind.get(f"ha_color_{tf}")
    if _ha_raw is not None:
        try:
            ha = float(_ha_raw)
            if ha != 0.0:
                ok = ha > 0 if is_long else ha < 0
                score += int(ok)
                parts.append(f"HA{'✓' if ok else '✗'}{int(ha)}")
        except (TypeError, ValueError):
            _ha_s = str(_ha_raw).lower()
            if _ha_s in ("green", "red"):
                ok = (_ha_s == "green") if is_long else (_ha_s == "red")
                score += int(ok)
                parts.append(f"HA{'✓' if ok else '✗'}{_ha_s}")

    std = float(ind.get(f"stoch_d_{tf}") if ind.get(f"stoch_d_{tf}") is not None else -1)
    if std >= 0 and stk >= 0:
        ok = stk > std if is_long else stk < std
        score += int(ok)
        parts.append(f"K>D{'✓' if ok else '✗'}{stk:.0f}/{std:.0f}")

    return score, "|".join(parts)


def _check_activation(
    ind: dict,
    activation_tfs: list,
    is_long: bool,
    px: float,
    dc_thr: float,
    bb_thr: float,
) -> tuple[bool, str]:
    """USER 2026-05-18 activation gate: at least 1 activation TF must show breakout.
    Breakout = bb_pctb crosses extended threshold OR dc_position crosses extended threshold.
    Cheap check (~2 fields × len(activation_tfs)) — runs BEFORE the per-TF score scan.
    """
    _bb_lvl = bb_thr if bb_thr > 0 else _BB_EXTENDED_LONG
    _dc_lvl = dc_thr if dc_thr > 0 else _DC_EXTENDED_LONG
    _bb_s = 1.0 - _bb_lvl
    _dc_s = 1.0 - _dc_lvl
    parts = []
    for tf in activation_tfs:
        bb = ind.get(f"bb_pct_b_{tf}")
        dc = ind.get(f"dc_position_{tf}")
        try:
            bb_v = float(bb) if bb is not None else -1.0
            dc_v = float(dc) if dc is not None else -1.0
        except (TypeError, ValueError):
            bb_v, dc_v = -1.0, -1.0
        bb_ok = (bb_v >= _bb_lvl) if is_long else (0 <= bb_v <= _bb_s)
        dc_ok = (dc_v >= _dc_lvl) if is_long else (0 <= dc_v <= _dc_s)
        tf_active = bb_ok or dc_ok
        parts.append(f"{tf}:bb{bb_v:.2f}{'✓' if bb_ok else '✗'}/dc{dc_v:.2f}{'✓' if dc_ok else '✗'}")
        if tf_active:
            return True, f"ACT_OK[{','.join(parts)}]"
    return False, f"NO_ACTIVATION[{','.join(parts)}]"


def _run_gate(
    ind: dict,
    is_long: bool,
    mode: str,
    min_tfs: int,
    min_ind: int,
    px: float = 0.0,
    invert_dc_bb: bool = False,
) -> tuple[bool, int, str]:
    """Core gate shared by entry and exit checks.

    2026-05-18 USER MANDATE: activation/entry TF split (was: all TFs scored equally).
    When config.GOLDEN_RULE_REQUIRE_ACTIVATION=True, runs a 2-stage gate:
      Stage 1 (activation): at least 1 TF in GOLDEN_RULE_ACTIVATION_TF_LIST (default ['D','4h'])
                            must show breakout (bb_pctb or dc_pos crosses extended threshold).
      Stage 2 (entry):      score per-TF on GOLDEN_RULE_ENTRY_TF_LIST (default ['1h','15m','3m'])
                            using existing MIN_TFS x MIN_IND logic.
    Without activation, gate returns (False, 0, NO_ACTIVATION) — even if entry TFs are bullish.
    This mirrors STDEV_BREAKOUT_HTF_LIST + STDEV_BREAKOUT_RETEST_TF_LIST structure.

    2026-05-12 USER MANDATE: alternate TOTAL-VOTE-SCORE mode (legacy, still supported).
    When config.GR_TOTAL_VOTE_SCORE_MIN > 0, switch to the multiplicative score gate.

    Legacy MIN_TFS × MIN_IND binary gate kept for backward compat (when activation+vote both off).
    """
    # Mode-aware config read: tradier overrides land on tradier_manage.config (an instance),
    # not the config MODULE. Crypto needs Config() instance for dataclass List fields
    # (field(default_factory=...) doesn't resolve at class level).
    try:
        if mode == "tradier":
            import tradier_manage as _tm_src
            _cfg_obj = _tm_src.config
        else:
            import config as _cfg_mod
            _cfg_obj = getattr(_cfg_mod, "_GR_HTF_CFG_SINGLETON", None)
            if _cfg_obj is None:
                _cfg_obj = _cfg_mod.Config()
                _cfg_mod._GR_HTF_CFG_SINGLETON = _cfg_obj   # cache for next call
        _vote_min = int(getattr(_cfg_obj, 'GR_TOTAL_VOTE_SCORE_MIN', 0) or 0)
        _dc_thr = float(getattr(_cfg_obj, 'GR_DC_EXTENDED_LONG', 0) or 0)
        _bb_thr = float(getattr(_cfg_obj, 'GR_BB_EXTENDED_LONG', 0) or 0)
        _require_act = bool(getattr(_cfg_obj, 'GOLDEN_RULE_REQUIRE_ACTIVATION', False))
        _act_list = list(getattr(_cfg_obj, 'GOLDEN_RULE_ACTIVATION_TF_LIST', None) or [])
        _entry_list = list(getattr(_cfg_obj, 'GOLDEN_RULE_ENTRY_TF_LIST', None) or [])
    except Exception:
        _vote_min = 0
        _dc_thr = 0.0
        _bb_thr = 0.0
        _require_act = False
        _act_list = []
        _entry_list = []
    tfs_default = _TRADIER_TFS if mode == "tradier" else _CRYPTO_TFS

    # === USER 2026-05-18 activation/entry split (when REQUIRE_ACTIVATION=True) ===
    if _require_act and _act_list:
        act_ok, act_detail = _check_activation(ind, _act_list, is_long, px, _dc_thr, _bb_thr)
        if not act_ok:
            return False, 0, act_detail
        # Activation confirmed — score only entry TFs (cheaper, semantically correct).
        tfs = _entry_list if _entry_list else tfs_default
    else:
        tfs = tfs_default

    # === legacy total-vote-score gate ===
    if _vote_min > 0:
        total = 0
        parts = []
        for tf in tfs:
            n, _ = _ind_score(ind, tf, is_long, px, invert_dc_bb, _dc_thr, _bb_thr)
            total += n
            parts.append(f"{tf}:{n}")
        passes = total >= _vote_min
        _act_tag = "+act" if _require_act else ""
        return passes, total, f"vote_total={total}/{_vote_min}req{_act_tag} [{' '.join(parts)}]"
    # === LEGACY MIN_TFS × MIN_IND binary gate ===
    if min_tfs <= 0:
        return True, 0, "GATE_OFF"
    confirmed = 0
    parts = []
    for tf in tfs:
        n, detail = _ind_score(ind, tf, is_long, px, invert_dc_bb, _dc_thr, _bb_thr)
        ok = n >= min_ind
        if ok:
            confirmed += 1
        parts.append(f"{tf}:{n}/{min_ind}{'✓' if ok else ''}")
    passes = confirmed >= min_tfs
    _act_tag = "+act" if _require_act else ""
    summary = f"tfs={confirmed}/{min_tfs}req{_act_tag} [{' '.join(parts)}]"
    return passes, confirmed, summary


def score_entry_htf(
    indicators: dict,
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    current_price: float = 0.0,
    invert_dc_bb: bool = False,
) -> tuple[bool, int, str]:
    """
    Entry confirmation: requires MIN_TFS timeframes to each have MIN_IND bullish indicators.
    Returns (passes, n_confirmed_tfs, detail).

    Set invert_dc_bb=True for GOLDEN_RULE breakout entries: DC/BB extension = bullish
    (price broke the channel/band = confirms momentum). Without this, all GR breakout
    entries see DC/BB as "bearish" (extended) so only WT counts, making MIN_TFS=1==MIN_TFS=5.
    """
    return _run_gate(indicators, is_long, mode, min_tfs, min_ind, current_price, invert_dc_bb)


def score_exit_htf(
    indicators: dict,
    is_long: bool,
    mode: str = "tradier",
    min_tfs: int = 2,
    min_ind: int = 2,
    current_price: float = 0.0,
    invert_dc_bb: bool = False,
) -> tuple[bool, int, str]:
    """
    Exit confirmation: requires MIN_TFS timeframes to show BEARISH signals (opposite of position).
    Returns (should_exit, n_confirmed_tfs, detail).
    """
    return _run_gate(indicators, not is_long, mode, min_tfs, min_ind, current_price, invert_dc_bb)
