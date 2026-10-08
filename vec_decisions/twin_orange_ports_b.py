"""twin_orange_ports_b.py — crypto-live scalar twins for 48 orange-filter switches.

Each function mirrors its vector predicate (v12_quick_engine) / stocks-live twin
(tradier_manage) in scalar form over a live-style indicators dict. Contract:

- Signature: fn(indicators, is_long, cfg) -> Optional[bool].
- True/False = filter slice passes/fires (entry-gate slices return pass=True to
  allow; exit slices return fire=True to exit; documented per function).
- None = inapplicable: wrong side for a side-specific filter, parent enable OFF,
  or an inert default (e.g. threshold<0, MODE mismatch).
- Missing indicator keys fail open exactly where vec fails open (documented);
  where vec would produce no fire (e.g. missing timestamps), twin returns False.
- cfg may be an object (getattr) or a Mapping (.get). Every config read uses a
  literal key and the value is USED in the returned predicate (BIBLE 19: no
  `_ = getattr` scaffolding, no `and False`).

Per-switch status (see STATUS): ALREADY-WIRED (ez_satoshit._cfg honors the
_TRADIER key today; only the config.py attribute is absent), NO-CRYPTO-GAP
(vec reads the key in tradier MODE only; no crypto effect anywhere), or
NEEDS-OPERATOR-DECISION (no mappable ez crypto parent; twin provided for
formula agreement, no hook emitted).
"""
from __future__ import annotations

from typing import Any, Callable, Mapping, Optional


def _num(m: Mapping[str, Any] | None, key: str, default: float = 0.0) -> float:
    try:
        v = (m or {}).get(key, default)
        if v is None or v == "":
            return default
        f = float(v)
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _bol(m: Mapping[str, Any] | None, key: str, default: bool = False) -> bool:
    v = (m or {}).get(key, default)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "bull", "green", "up", "rising")
    return bool(v)


def _txt(m: Mapping[str, Any] | None, key: str, default: str = "") -> str:
    v = (m or {}).get(key, default)
    return str(v) if v is not None else default


def _raw(cfg: Any, key: str, default: Any) -> Any:
    if isinstance(cfg, Mapping):
        v = cfg.get(key, default)
        return default if v is None else v
    v = getattr(cfg, key, default)
    return default if v is None else v


def _gf(cfg: Any, key: str, default: float) -> float:
    try:
        f = float(_raw(cfg, key, default))
        return f if f == f else default
    except (TypeError, ValueError):
        return default


def _gi(cfg: Any, key: str, default: int) -> int:
    try:
        return int(float(_raw(cfg, key, default)))
    except (TypeError, ValueError):
        return default


def _gb(cfg: Any, key: str, default: bool) -> bool:
    v = _raw(cfg, key, default)
    if isinstance(v, str):
        return v.strip().lower() in ("1", "true", "yes", "y", "on")
    return bool(v)


def _gs(cfg: Any, key: str, default: str) -> str:
    v = _raw(cfg, key, default)
    return str(v) if v is not None else default


# ── SATOSHIT exit votes (vec v12:9995-10008; stocks-live: dead `and False`
# stub only — no real stocks implementation; ez crypto exit is cross-based
# with hardcoded levels, so these are NEEDS-OPERATOR-DECISION). ──

def _satoshit_exit_votes(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    if not _gb(cfg, "SATOSHIT_EXIT_ENABLED", False):
        return None
    rsi = _num(ind, "rsi_1h", 50.0)
    k = _num(ind, "stoch_k", 50.0)
    need = _gi(cfg, "SATOSHIT_MIN_VOTES_TRADIER", _gi(cfg, "SATOSHIT_MIN_VOTES", 3))
    if is_long:
        rsi_hit = rsi >= _gf(cfg, "SATOSHIT_EXIT_LONG_RSI_MIN_TRADIER", 55.0)
        stoch_hit = k >= _gf(cfg, "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER", 60.0)
    else:
        rsi_hit = rsi <= _gf(cfg, "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER", 42.0)
        stoch_hit = k <= _gf(cfg, "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER", 50.0)
    if need >= 3:
        return bool(rsi_hit and stoch_hit)
    return bool(rsi_hit or stoch_hit)


def satoshit_exit_long_stoch_k_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Exit-fire slice (True=exit). Long only; None when parent off / short."""
    if not is_long:
        return None
    return _satoshit_exit_votes(ind, True, cfg)


def satoshit_exit_short_rsi_max_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Exit-fire slice (True=exit). Short only; None when parent off / long."""
    if is_long:
        return None
    return _satoshit_exit_votes(ind, False, cfg)


def satoshit_exit_short_stoch_k_max_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Exit-fire slice (True=exit). Short only; None when parent off / long."""
    if is_long:
        return None
    return _satoshit_exit_votes(ind, False, cfg)


# ── SATOSHIT entry votes (vec v12:8691-8704; stocks tradier:12581+/13069+;
# crypto ez_satoshit.satoshit_entry_signal — ALREADY-WIRED via _cfg TRADIER-
# first; twin mirrors the LIVE crypto formula: rsi/stoch/mfi on 15m).
# DIVERGENCE (REPORT): vec short HTF uses mfi_D <= 100-MIN; both lives use
# mfi_D >= MIN for both sides. Twin follows live. ──

def satoshit_htf_mfi_d_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """HTF pass slice (True=pass). Both sides, live-faithful (>= MIN)."""
    del is_long
    return _num(ind, "mfi_D", 50.0) >= _gf(cfg, "SATOSHIT_HTF_MFI_D_MIN_TRADIER", _gf(cfg, "SATOSHIT_HTF_MFI_D_MIN", 30.0))


def satoshit_htf_rvol_1h_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """HTF pass slice (True=pass). Both sides."""
    del is_long
    return _num(ind, "relative_volume_1h", 1.0) >= _gf(cfg, "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER", _gf(cfg, "SATOSHIT_HTF_RVOL_1H_MIN", 0.3))


def satoshit_long_mfi_max_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Long only."""
    if not is_long:
        return None
    return _num(ind, "mfi_15m", 50.0) < _gf(cfg, "SATOSHIT_LONG_MFI_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_MFI_MAX", 60.0))


def satoshit_long_rsi_max_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Long only."""
    if not is_long:
        return None
    return _num(ind, "rsi_15m", 50.0) < _gf(cfg, "SATOSHIT_LONG_RSI_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_RSI_MAX", 50.0))


def satoshit_long_stoch_k_max_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Long only."""
    if not is_long:
        return None
    return _num(ind, "stoch_k_15m", 50.0) < _gf(cfg, "SATOSHIT_LONG_STOCH_K_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_STOCH_K_MAX", 60.0))


def _satoshit_ha_val(ind: Mapping[str, Any] | None, is_long: bool) -> int:
    raw = (ind or {}).get("ha_15m", "neutral")
    if isinstance(raw, (int, float)):
        s = "green" if raw > 0 else ("red" if raw < 0 else "neutral")
    else:
        s = str(raw) if raw else "neutral"
    if is_long:
        return -1 if s == "red" else (1 if s == "green" else 0)
    return 1 if s == "green" else (-1 if s == "red" else 0)


def _satoshit_votes(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> int:
    rsi = _num(ind, "rsi_15m", 50.0)
    k = _num(ind, "stoch_k_15m", 50.0)
    mfi = _num(ind, "mfi_15m", 50.0)
    bb = _num(ind, "bb_pct_b_1h", 0.5)
    ha = _satoshit_ha_val(ind, is_long)
    if is_long:
        v_rsi = int(rsi < _gf(cfg, "SATOSHIT_LONG_RSI_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_RSI_MAX", 50.0)))
        v_bb = int(bb < _gf(cfg, "SATOSHIT_LONG_BB_PCTB_MAX", 0.50))
        v_ha = int(ha < _gi(cfg, "SATOSHIT_LONG_HA_STREAK_MAX", 1))
        v_k = int(k < _gf(cfg, "SATOSHIT_LONG_STOCH_K_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_STOCH_K_MAX", 60.0)))
        v_mfi = int(mfi < _gf(cfg, "SATOSHIT_LONG_MFI_MAX_TRADIER", _gf(cfg, "SATOSHIT_LONG_MFI_MAX", 60.0)))
    else:
        v_rsi = int(rsi > _gf(cfg, "SATOSHIT_SHORT_RSI_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_RSI_MIN", 55.0)))
        v_bb = int(bb > _gf(cfg, "SATOSHIT_SHORT_BB_PCTB_MIN", 0.55))
        v_ha = int(ha > _gi(cfg, "SATOSHIT_SHORT_HA_STREAK_MIN", 0))
        v_k = int(k > _gf(cfg, "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_STOCH_K_MIN", 50.0)))
        v_mfi = int(mfi > _gf(cfg, "SATOSHIT_SHORT_MFI_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_MFI_MIN", 50.0)))
    return v_rsi + v_bb + v_ha + v_k + v_mfi


def satoshit_min_votes_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Quorum slice (True=quorum met). Both sides; full 5-vote count."""
    need = _gi(cfg, "SATOSHIT_MIN_VOTES_TRADIER", _gi(cfg, "SATOSHIT_MIN_VOTES", 3))
    return _satoshit_votes(ind, is_long, cfg) >= need


def satoshit_short_mfi_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Short only."""
    if is_long:
        return None
    return _num(ind, "mfi_15m", 50.0) > _gf(cfg, "SATOSHIT_SHORT_MFI_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_MFI_MIN", 50.0))


def satoshit_short_rsi_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Short only."""
    if is_long:
        return None
    return _num(ind, "rsi_15m", 50.0) > _gf(cfg, "SATOSHIT_SHORT_RSI_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_RSI_MIN", 55.0))


def satoshit_short_stoch_k_min_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Vote slice (True=vote). Short only."""
    if is_long:
        return None
    return _num(ind, "stoch_k_15m", 50.0) > _gf(cfg, "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER", _gf(cfg, "SATOSHIT_SHORT_STOCH_K_MIN", 50.0))


# ── SMA200 distance (vec v12:8602-8605 B_SMA200DIST; stocks-live: dead stub;
# no ez parent → NEEDS-OPERATOR-DECISION). Block-fire slice (True=block). ──

def sma200_dist_long_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides (short mirrors -thr)."""
    if not _gb(cfg, "SMA200_DIST_ENTRY_ENABLED", False):
        return None
    sma = _num(ind, "sma_200_1h", 0.0)
    close = _num(ind, "close", _num(ind, "current_price", 0.0))
    dist = 0.0 if sma <= 0 else (close - sma) / sma * 100.0
    thr = _gf(cfg, "SMA200_DIST_LONG_THRESHOLD", -3.0)
    if is_long:
        return bool(dist < thr)
    return bool(dist > -thr)


# ── STRENGTH filter (vec v12:9139-9156 weighted score; stocks-live real via
# WT-gap proxy tradier:15382-15392; no ez parent → NEEDS-OPERATOR-DECISION).
# Pass slice (True=pass). Scalar follows the stocks-live proxy (vec block
# weights are not reconstructible bar-side). ──

def _strength_pass(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    del is_long
    if not _gb(cfg, "STRENGTH_FILTER_ENABLED", True):
        return None
    gap = abs(_num(ind, "wt1_1h", 0.0) - _num(ind, "wt2_1h", 0.0))
    return bool(gap >= _gf(cfg, "STRENGTH_MIN_SCORE", 5.0) * 0.8)


def strength_filter_enabled(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Both sides."""
    return _strength_pass(ind, is_long, cfg)


def strength_min_score(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Both sides."""
    return _strength_pass(ind, is_long, cfg)


# ── TF alignment total (vec v12:9251-9259, tradier MODE only; stocks-live
# dead stub; no ez parent → NO-CRYPTO-GAP + NEEDS-OPERATOR-DECISION).
# Pass slice (True=pass). ──

def tf_alignment_min_total(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Tradier MODE only; else None."""
    if not _gb(cfg, "BACKTEST_VALIDATED_GATES_TRADIER", False):
        return None
    if _gs(cfg, "MODE", "crypto") != "tradier":
        return None
    w1 = _num(ind, "wt1_1h", 0.0)
    w2 = _num(ind, "wt2_1h", 0.0)
    h1 = _num(ind, "wt1_4h", 0.0)
    h2 = _num(ind, "wt2_4h", 0.0)
    d1 = _num(ind, "wt1_D", 0.0)
    d2 = _num(ind, "wt2_D", 0.0)
    if is_long:
        cnt = int(w1 > w2) + int(h1 > h2) + int(d1 > d2)
    else:
        cnt = int(w1 < w2) + int(h1 < h2) + int(d1 < d2)
    total = _gi(cfg, "TF_ALIGNMENT_MIN_TOTAL", 4)
    need = max(1, min(3, total // 4)) if total > 0 else 1
    return bool(cnt >= need)


# ── TF focus (vec v12:8749-8761 computes focus_mult then DROPS it — verified
# dead store, no ledger effect; stocks-live pure reads. Twin exposes the
# computed alignment predicate for the operator; no hook. NEEDS-OPERATOR.) ──

def _tf_focus_aligned(ind: Mapping[str, Any] | None, is_long: bool) -> bool:
    w1 = _num(ind, "wt1_1h", 0.0)
    w2 = _num(ind, "wt2_1h", 0.0)
    h1 = _num(ind, "wt1_4h", 0.0)
    h2 = _num(ind, "wt2_4h", 0.0)
    if is_long:
        return bool((w1 > w2) and (h1 > h2))
    return bool((w1 < w2) and (h1 < h2))


def tf_focus_entry_hard_gate(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Alignment slice (True=1h&4h aligned). None when gate off."""
    if not _gb(cfg, "TF_FOCUS_ENTRY_HARD_GATE", False):
        return None
    return _tf_focus_aligned(ind, is_long)


def tf_focus_weight(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Bonus slice (True=focus bonus > 0). None when gate off."""
    if not _gb(cfg, "TF_FOCUS_ENTRY_HARD_GATE", False):
        return None
    return bool(_tf_focus_aligned(ind, is_long) and _gf(cfg, "TF_FOCUS_WEIGHT", 8.0) > 0)


# ── HTF alignment TF selectors (vec v12:9054-9070; stocks-live real
# tradier:15332-15351; no ez HTF_ALIGNMENT parent → NEEDS-OPERATOR-DECISION).
# Pass slice (True=pass). ──

def _htf_alignment_pass(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    if not _gb(cfg, "HTF_ALIGNMENT_ENABLED", True):
        return None
    htf1 = _gs(cfg, "TF_HTF1", "1h")
    htf3 = _gs(cfg, "TF_HTF3", "D")
    w1 = _num(ind, "wt1_1h", 0.0)
    w2 = _num(ind, "wt2_1h", 0.0)
    a1 = _num(ind, f"wt1_{htf1}", 0.0)
    a2 = _num(ind, f"wt2_{htf1}", 0.0)
    b1 = _num(ind, f"wt1_{htf3}", 0.0)
    b2 = _num(ind, f"wt2_{htf3}", 0.0)
    if is_long:
        cnt = int(w1 > w2) + int(a1 > a2) + int(b1 > b2)
    else:
        cnt = int(w1 < w2) + int(a1 < a2) + int(b1 < b2)
    return bool(cnt >= _gi(cfg, "HTF_MIN_ALIGNED", 1))


def tf_htf1(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Both sides."""
    return _htf_alignment_pass(ind, is_long, cfg)


def tf_htf3(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Both sides."""
    return _htf_alignment_pass(ind, is_long, cfg)


# ── DC daytrade position threshold (vec v12:8667-8673 B_DAYTRADE; crypto leg
# uses DC_POSITION_ENTRY_THRESHOLD, TRADIER_ leg tradier MODE only;
# stocks-live dead stub → NO-CRYPTO-GAP for this key (the missing crypto
# B_DAYTRADE entry parent is a separate REPORT item, not this filter).
# Block-fire slice (True=block fires). ──

def tradier_dc_position_entry_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides."""
    if not (_gb(cfg, "DC_DAYTRADE_ENABLED", False) or _gb(cfg, "TRADIER_DC_DAYTRADE_ENABLED", False)):
        return None
    thr = _gf(cfg, "TRADIER_DC_POSITION_ENTRY_THRESHOLD", 0.25)
    pos = _num(ind, "dc_position_15m", 0.5)
    base = (pos < thr) if is_long else (pos > (1.0 - thr))
    if _gb(cfg, "TRADIER_DC_DAYTRADE_REQUIRE_1H_EXPANSION", True):
        w = _num(ind, "dc_width_1h", 0.0)
        wp = (ind or {}).get("dc_width_1h_prev", None)
        if wp is None:
            exp = True
        else:
            try:
                exp = bool(w > float(wp))
            except (TypeError, ValueError):
                exp = True
        return bool(base and exp)
    return bool(base)


# ── Tradier entry-score MFI_D veto (vec v12:9276-9280, tradier MODE only;
# stocks-live dead stub; no ez parent → NO-CRYPTO-GAP + NEEDS-OPERATOR).
# Pass slice (True=pass). ──

def tradier_entry_score_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Tradier MODE + thr>=24; else None."""
    if _gs(cfg, "MODE", "crypto") != "tradier":
        return None
    if _gf(cfg, "TRADIER_ENTRY_SCORE_THRESHOLD", 30) < 24:
        return None
    m = _num(ind, "mfi_D", 50.0)
    if is_long:
        return bool(m >= 40)
    return bool(m <= 60)


# ── First-hour momentum (vec v12 B_FHMOMENTUM inline; live threaded 10-06:
# ez:46510 zero-scan + tr:14155 entry chain via twin_gates_sizing_a fh core).
# Block-fire slice (True=block fires). ──

def _fh_momentum_fires(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    tradier = _gs(cfg, "MODE", "crypto") == "tradier"
    on = _gb(cfg, "TRADIER_FH_MOMENTUM_ENABLED", False) if tradier else _gb(cfg, "FH_MOMENTUM_ENABLED", False)
    if not on:
        return None
    ts = _num(ind, "timestamps", 0.0)
    if ts <= 0:
        return False
    mins_since_open = ((int(ts) % 86400) - (13 * 3600 + 30 * 60)) / 60.0
    window = _gf(cfg, "TRADIER_FH_MOMENTUM_WINDOW_MINUTES", 60)
    in_window = 0 <= mins_since_open <= window
    open_d = _num(ind, "open_D", 0.0)
    close = _num(ind, "close", _num(ind, "current_price", 0.0))
    move = 0.0 if open_d <= 0 else (close - open_d) / open_d * 100.0
    min_move = _gf(cfg, "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT", 0.5)
    moved = (move >= min_move) if is_long else (move <= -min_move)
    confirm = True
    if _gb(cfg, "TRADIER_FH_MOMENTUM_DC_CONFIRM", True):
        dc_max = _gf(cfg, "TRADIER_FH_MOMENTUM_DC_MAX_LONG", 0.33)
        _fh_tf = str(_gs(cfg, "FH_MOMENTUM_FILTER_TF", "15m") or "15m").lower()
        if _fh_tf != "off":
            if _fh_tf not in ("15m", "1h", "4h", "d"):
                _fh_tf = "15m"
            pos = _num(ind, "dc_position_%s" % _fh_tf, 0.5)
            confirm = confirm and ((pos <= dc_max) if is_long else (pos >= (1.0 - dc_max)))
    if _gb(cfg, "TRADIER_FH_MOMENTUM_MFI_CONFIRM", True):
        mfi_min = _gf(cfg, "TRADIER_FH_MOMENTUM_MFI_MIN", 55.0)
        mfi = _num(ind, "mfi_1h", 50.0)
        confirm = confirm and ((mfi >= mfi_min) if is_long else (mfi <= (100.0 - mfi_min)))
    return bool(in_window and moved and confirm)


def tradier_fh_momentum_dc_max_long(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides."""
    return _fh_momentum_fires(ind, is_long, cfg)


def tradier_fh_momentum_mfi_min(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides."""
    return _fh_momentum_fires(ind, is_long, cfg)


def tradier_fh_momentum_min_move_pct(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides."""
    return _fh_momentum_fires(ind, is_long, cfg)


def tradier_fh_momentum_window_minutes(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Both sides."""
    return _fh_momentum_fires(ind, is_long, cfg)


# ── K-zone (vec v12:8422-8425 B_KZONE; crypto leg uses K_ZONE_* base keys,
# TRADIER_ legs tradier MODE only; stocks-live dead stubs; ez crypto parent
# reads base keys (ez:459-479) → these venue variants are NO-CRYPTO-GAP).
# Block-fire slice (True=block fires). ──

def tradier_k_zone_long_threshold_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Long only."""
    if not _gb(cfg, "K_ZONE_ENTRY_ENABLED", False):
        return None
    if not is_long:
        return None
    return bool(_num(ind, "k_3m", 50.0) < _gf(cfg, "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER", 35))


def tradier_k_zone_short_threshold_tradier(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Short only."""
    if not _gb(cfg, "K_ZONE_ENTRY_ENABLED", False):
        return None
    if is_long:
        return None
    return bool(_num(ind, "k_3m", 50.0) > _gf(cfg, "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER", 65))


# ── Tradier RSI short (vec v12:8787-8792, tradier MODE only; stocks-live
# dead stub; no ez parent → NO-CRYPTO-GAP + NEEDS-OPERATOR-DECISION).
# Block-fire slice (True=block fires). ──

def tradier_rsi_short_rel_volume_min(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Block-fire slice (True=block fires). Tradier MODE only; else None."""
    if _gs(cfg, "MODE", "crypto") != "tradier":
        return None
    rsi = _num(ind, "rsi_1h", 50.0)
    if is_long:
        return bool(rsi < _gf(cfg, "TRADIER_RSI_ENTRY_LONG_TRADIER", -1.0))
    return bool((rsi > _gf(cfg, "TRADIER_RSI_ENTRY_SHORT_TRADIER", 70.0)) and (_num(ind, "relative_volume_1h", 1.0) >= _gf(cfg, "TRADIER_RSI_SHORT_REL_VOLUME_MIN", 2.4)))


# ── VWAP filter (vec v12:9119-9123; stocks-live real tradier:27754/28119;
# no ez parent → NEEDS-OPERATOR-DECISION). Pass slice (True=pass). ──

def vwap_filter_enabled(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Fails open when VWAP<=0. None when off."""
    if not _gb(cfg, "VWAP_FILTER_ENABLED", False):
        return None
    v = _num(ind, "vwap_D", 0.0)
    if v <= 0:
        v = _num(ind, "vwap", 0.0)
    if v <= 0:
        return True
    c = _num(ind, "close", _num(ind, "current_price", 0.0))
    if is_long:
        return bool(c > v)
    return bool(c < v)


# ── Winner-trail peak erosion (vec v12:12492+13741; stocks-live dead stub;
# no ez parent → NEEDS-OPERATOR-DECISION). Exit-fire slice (True=exit). ──

def win_trail_erosion_pct(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Exit-fire slice (True=exit). Both sides. None when pct<=0."""
    del is_long
    e = _gf(cfg, "WIN_TRAIL_EROSION_PCT", 0.0)
    if e <= 0:
        return None
    peak = _num(ind, "peak_pnl_pct", 0.0)
    live = _num(ind, "live_pnl_pct", _num(ind, "gain_pct", 0.0))
    if peak <= 0:
        return False
    ec = (ind or {}).get("erosion_confirm", None)
    if ec is not None and not bool(ec):
        return False
    return bool((peak - live) >= peak * e)


# ── WT 15m bounce (vec v12:9736-9808; stocks-live real tradier:1297-1358;
# ez parent ez:59763-59802 is a dead `_ = (...)` stub → NEEDS-OPERATOR).
# Eligibility slice (True=eligible). Mirrors vec incl. 15m-primary volume
# with 1h fallback (stocks-live uses 1h-primary — REPORT divergence). ──

def _b15_eligible(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    if not _gb(cfg, "WT_15M_BOUNCE_OPEN_ENABLED", False):
        return None
    w1 = _num(ind, "wt1_15m", 0.0)
    w2 = _num(ind, "wt2_15m", 0.0)
    w1p = _num(ind, "wt1_15m_prev", w1)
    w2p = _num(ind, "wt2_15m_prev", w2)
    up = (w1p <= w2p) and (w1 > w2)
    down = (w1p >= w2p) and (w1 < w2)
    cross = up if is_long else down
    still = (w1 > w2) if is_long else (w1 < w2)
    bb = _num(ind, "bb_pct_b_15m", 0.5)
    bb_ok = (_gf(cfg, "WT_15M_BOUNCE_BB_MIN", 0.05) <= bb <= _gf(cfg, "WT_15M_BOUNCE_BB_MAX", 0.95))
    r1 = _bol(ind, "wt_cross_rising_1h", False)
    r4 = _bol(ind, "wt_cross_rising_4h", False)
    req_both = _gb(cfg, "WT_15M_BOUNCE_REQUIRE_BOTH_HTF", False)
    if is_long:
        htf_ok = (r4 and r1) if req_both else (r4 or r1)
    else:
        htf_ok = ((not r4) and (not r1)) if req_both else ((not r4) or (not r1))
    hl = _gb(cfg, "WT_15M_BOUNCE_FILTER_HL_ENABLED", False) or _gb(cfg, "WT_15M_BOUNCE_LOW_1H_GT_PREV", False)
    hh = _gb(cfg, "WT_15M_BOUNCE_FILTER_HH_ENABLED", False) or _gb(cfg, "WT_15M_BOUNCE_HIGH_1H_GT_PREV", False)
    if hl or hh:
        dl = _num(ind, "dc_low_1h", 0.0)
        dlp = _num(ind, "dc_low_1h_prev", dl)
        dh = _num(ind, "dc_high_1h", 0.0)
        dhp = _num(ind, "dc_high_1h_prev", dh)
        hl_ok = (dl > dlp) if hl else True
        hh_ok = (dh > dhp) if hh else True
        mode = _gs(cfg, "WT_15M_BOUNCE_FILTER_MODE", "AND").upper()
        if mode == "OR":
            if hl and not hh:
                hlhh_ok = hl_ok
            elif hh and not hl:
                hlhh_ok = hh_ok
            else:
                hlhh_ok = bool(hl_ok or hh_ok)
        else:
            hlhh_ok = bool(hl_ok and hh_ok)
    else:
        hlhh_ok = True
    trig = still if (hl or hh) else cross
    von = _gb(cfg, "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED", False) or _gb(cfg, "WT_15M_BOUNCE_REL_VOL_GT_1", False)
    if von:
        vmode = _gs(cfg, "WT_15M_BOUNCE_VOLUME_MODE", "relvol").lower()
        vthr = _gf(cfg, "WT_15M_BOUNCE_VOLUME_THRESHOLD", 1.0)
        if vmode == "relvol":
            rel = (ind or {}).get("relative_volume_15m", None)
            if rel is None:
                rel = _num(ind, "relative_volume_1h", 1.0)
            else:
                try:
                    rel = float(rel)
                except (TypeError, ValueError):
                    rel = 1.0
            vol_ok = bool(rel > vthr)
        else:
            v = _num(ind, "volume_15m", 0.0)
            s = _num(ind, "volume_sma_15m", 0.0)
            if v == 0 or s == 0:
                v = _num(ind, "volume_1h", 0.0)
                s = _num(ind, "volume_sma_1h", 0.0)
            s = s if s != 0 else 1.0
            vol_ok = bool(v > s * vthr)
    else:
        vol_ok = True
    return bool(trig and bb_ok and htf_ok and hlhh_ok and vol_ok)


def wt_15m_bounce_filter_hh_enabled(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


def wt_15m_bounce_filter_hl_enabled(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


def wt_15m_bounce_filter_mode(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


def wt_15m_bounce_require_both_htf(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


def wt_15m_bounce_volume_filter_enabled(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


def wt_15m_bounce_volume_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). Both sides."""
    return _b15_eligible(ind, is_long, cfg)


# ── WT_DC TF-expanded pack (vec v12:9313-9385, ANDed into _base_entry at
# v12:9594 with NO WT_DC_ENABLED master — always on in vec; no ez WT_DC
# scorer parent → NEEDS-OPERATOR-DECISION). Pass slices (True=pass).
# NOTE: stocks-live assigns DC_POS/STOCH thresholds (tradier:12778-12781)
# but never uses them (assigned-only); vec DOES apply them. ──

def _dc_key(tf: str) -> str:
    return "dc_position_D" if tf == "d" else f"dc_position_{tf}"


def wt_dc_dc_pos_threshold_long(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Long only; missing key fails open."""
    if not is_long:
        return None
    tf = _gs(cfg, "WT_DC_DC_TF", "1h").lower()
    if tf not in ("15m", "1h", "4h", "d"):
        return True
    key = _dc_key(tf)
    if key not in (ind or {}):
        return True
    return bool(_num(ind, key, 0.5) < _gf(cfg, "WT_DC_DC_POS_THRESHOLD_LONG", 0.50))


def wt_dc_dc_pos_threshold_short(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Short only; missing key fails open."""
    if is_long:
        return None
    tf = _gs(cfg, "WT_DC_DC_TF", "1h").lower()
    if tf not in ("15m", "1h", "4h", "d"):
        return True
    key = _dc_key(tf)
    if key not in (ind or {}):
        return True
    return bool(_num(ind, key, 0.5) > _gf(cfg, "WT_DC_DC_POS_THRESHOLD_SHORT", 0.50))


def _wtdc_tf_adjusted(thr: float, tf: str) -> float:
    if tf == "15m":
        return max(20.0, thr - 10.0)
    if tf == "4h":
        return min(85.0, thr + 10.0)
    if tf == "d":
        return min(85.0, thr + 15.0)
    return thr


def wt_dc_detailed_entry_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice: wtdc_score >= TF-adjusted thr. None unless detailed on."""
    del is_long
    if not _gb(cfg, "WT_DC_ENABLED", False):
        return None
    if not _gb(cfg, "WT_DC_DETAILED_SCORER_ENABLED", False):
        return None
    thr = _wtdc_tf_adjusted(_gf(cfg, "WT_DC_DETAILED_ENTRY_THRESHOLD", 43.0), _gs(cfg, "WT_DC_TF_ENTRY", "1h").lower())
    return bool(_num(ind, "wtdc_score", 0.0) >= thr)


def _wtdc_cross(val: Any) -> str:
    if isinstance(val, str):
        t = val.strip().upper()
        if t in ("BULL", "BEAR", "NONE"):
            return t
        try:
            num = float(t)
        except (TypeError, ValueError):
            return ""
    else:
        try:
            num = float(val)
        except (TypeError, ValueError):
            return ""
    if num != num:
        return ""
    return "BULL" if num > 0 else ("BEAR" if num < 0 else "NONE")


def wt_dc_direct_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Eligibility slice (True=eligible). thr<0 → True (vec '-1=no block').

    Score = 25*D + 25*4h + 30*cross_1h + 10*dc + 10*stoch (wt_dc_contract).
    Live default call passes -1 for align (live _config would raise; caught
    upstream); twin treats out-of-range align as 0 and unknown htf gate as
    none — REPORT deviations, fails open by design.
    """
    thr = _gf(cfg, "WT_DC_DIRECT_THRESHOLD", -1.0)
    if thr < 0:
        return True
    d1 = _num(ind, "wt1_D", 0.0)
    d2 = _num(ind, "wt2_D", 0.0)
    h1 = _num(ind, "wt1_4h", 0.0)
    h2 = _num(ind, "wt2_4h", 0.0)
    dc = _num(ind, "dc_position_1h", 0.5)
    sk = _num(ind, "stoch_k_5m", _num(ind, "k_5m", _num(ind, "stoch_k", 50.0)))
    cross = _wtdc_cross((ind or {}).get("wt_cross_1h", "NONE"))
    if is_long:
        score = 25.0 * (d1 > d2) + 25.0 * (h1 > h2) + 30.0 * (cross == "BULL") + 10.0 * (dc < 0.5) + 10.0 * (sk < 40.0)
    else:
        score = 25.0 * (d1 < d2) + 25.0 * (h1 < h2) + 30.0 * (cross == "BEAR") + 10.0 * (dc > 0.5) + 10.0 * (sk > 60.0)
    eligible = bool(score >= thr)
    gate = _gs(cfg, "WT_DC_DIRECT_HTF_GATE", "none").lower()
    if gate == "1h":
        g1 = _num(ind, "wt1_1h", 0.0)
        g2 = _num(ind, "wt2_1h", 0.0)
        eligible = eligible and ((g1 >= g2) if is_long else (g1 <= g2))
    elif gate in ("4h", "4h_d"):
        eligible = eligible and ((h1 >= h2) if is_long else (h1 <= h2))
        if gate == "4h_d":
            eligible = eligible and ((d1 >= d2) if is_long else (d1 <= d2))
    req = _gi(cfg, "WT_DC_DIRECT_HTF_ALIGN_REQUIRED", 0)
    if req < 0 or req > 3:
        req = 0
    if req > 0:
        g1 = _num(ind, "wt1_1h", 0.0)
        g2 = _num(ind, "wt2_1h", 0.0)
        if is_long:
            aligned = int(g1 > g2) + int(h1 > h2) + int(d1 > d2)
        else:
            aligned = int(g1 < g2) + int(h1 < h2) + int(d1 < d2)
        eligible = eligible and (aligned >= req)
    sg = _gf(cfg, "WT_DC_DIRECT_COMBINED_STOCH_GATE", 100.0)
    if sg < 100.0:
        eligible = eligible and ((sk < sg) if is_long else (sk > 100.0 - sg))
    return bool(eligible)


def wt_dc_entry_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice: wtdc_score >= TF-adjusted thr. None unless simple on."""
    del is_long
    if not _gb(cfg, "WT_DC_ENABLED", False):
        return None
    if _gb(cfg, "WT_DC_DETAILED_SCORER_ENABLED", False):
        return None
    thr = _wtdc_tf_adjusted(_gf(cfg, "WT_DC_ENTRY_THRESHOLD", 45.0), _gs(cfg, "WT_DC_TF_ENTRY", "1h").lower())
    return bool(_num(ind, "wtdc_score", 0.0) >= thr)


def wt_dc_htf_gate(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=HTF not against). Short forced 4h_d; none→4h_d."""
    if not is_long:
        gate = "4h_d"
    else:
        gate = _gs(cfg, "WT_DC_HTF_GATE", "none").lower()
        if gate == "none":
            gate = "4h_d"
    w1 = _num(ind, "wt1_1h", 0.0)
    w2 = _num(ind, "wt2_1h", 0.0)
    h1 = _num(ind, "wt1_4h", 0.0)
    h2 = _num(ind, "wt2_4h", 0.0)
    d1 = _num(ind, "wt1_D", 0.0)
    d2 = _num(ind, "wt2_D", 0.0)
    if is_long:
        a1, a4, ad = (w1 < w2), (h1 < h2), (d1 < d2)
    else:
        a1, a4, ad = (w1 > w2), (h1 > h2), (d1 > d2)
    if gate == "1h":
        return bool(not a1)
    if gate == "4h":
        return bool(not a4)
    if gate == "4h_d":
        return bool((not a4) and (not ad))
    return True


def _wtdc_tf_label_ok(ind: Mapping[str, Any] | None, is_long: bool, label: str) -> bool:
    if label in ("none", "off", ""):
        return True
    key1 = "wt1_D" if label.upper() == "D" else f"wt1_{label}"
    key2 = "wt2_D" if label.upper() == "D" else f"wt2_{label}"
    if key1 not in (ind or {}) and key2 not in (ind or {}):
        return True
    a1 = _num(ind, key1, 0.0)
    a2 = _num(ind, key2, 0.0)
    if a1 == 0 and a2 == 0:
        return True
    return bool((a1 > a2) if is_long else (a1 < a2))


def wt_dc_htf_gate_mode(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice for the expanded HTF1/HTF2 gate. True at default TFs."""
    htf = _gs(cfg, "WT_DC_TF_HTF", "4h").lower()
    htf2 = _gs(cfg, "WT_DC_TF_HTF2", "D").lower()
    if htf == "4h" and htf2 == "d":
        return True
    ok1 = _wtdc_tf_label_ok(ind, is_long, htf)
    ok2 = _wtdc_tf_label_ok(ind, is_long, htf2)
    mode = _gs(cfg, "WT_DC_HTF_GATE_MODE", "AND").upper()
    stack = _gs(cfg, "MODE", "crypto") == "tradier" and _gb(cfg, "STOCKS_LIVE_ENTRY_STACK_ENABLED", False)
    if stack:
        return bool((ok1 or ok2) if mode == "AND" else (ok1 and ok2))
    return bool((ok1 and ok2) if mode == "AND" else (ok1 or ok2))


def wt_dc_k5m_min_short_hard(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Short only gate; long passes. None if off."""
    if not _gb(cfg, "WT_DC_K5M_HARD_ENABLED", False):
        return None
    if is_long:
        return True
    if "stoch_k_5m" in (ind or {}):
        k = _num(ind, "stoch_k_5m", 50.0)
    else:
        k = _num(ind, "stoch_k_15m", 50.0)
    return bool(k >= _gf(cfg, "WT_DC_K5M_MIN_SHORT_HARD", 20.0))


def wt_dc_stoch_threshold_long(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Long only; missing key fails open."""
    if not is_long:
        return None
    stf = _gs(cfg, "WT_DC_STOCH_TF", "5m").lower()
    if stf in ("5m", "3m"):
        stf = "15m"
    if stf not in ("5m", "15m", "1h", "4h"):
        return True
    key = f"stoch_k_{stf}"
    if key not in (ind or {}):
        return True
    return bool(_num(ind, key, 50.0) < _gf(cfg, "WT_DC_STOCH_THRESHOLD_LONG", 40.0))


def wt_dc_stoch_threshold_short(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Pass slice (True=pass). Short only; missing key fails open."""
    if is_long:
        return None
    stf = _gs(cfg, "WT_DC_STOCH_TF", "5m").lower()
    if stf in ("5m", "3m"):
        stf = "15m"
    if stf not in ("5m", "15m", "1h", "4h"):
        return True
    key = f"stoch_k_{stf}"
    if key not in (ind or {}):
        return True
    return bool(_num(ind, key, 50.0) > _gf(cfg, "WT_DC_STOCH_THRESHOLD_SHORT", 60.0))


# ── WT velocity decay exit (vec v12:9901-9913; stocks-live real
# tradier:20233-20243; no ez parent → NEEDS-OPERATOR-DECISION).
# Exit-fire slice (True=exit). ──

def wt_vel_decay_threshold(ind: Mapping[str, Any] | None, is_long: bool, cfg: Any) -> Optional[bool]:
    """Exit-fire slice (True=exit). Both sides. None when parent off."""
    if not _gb(cfg, "WT_VEL_DECAY_EXIT_ENABLED", True):
        return None
    v1 = _num(ind, "wt_velocity_1h", 0.0)
    v1p = _num(ind, "wt_velocity_1h_prev", v1)
    v3 = _num(ind, "wt_velocity_3m", _num(ind, "wt_velocity", 0.0))
    thr = _gf(cfg, "WT_VEL_DECAY_THRESHOLD", 1.0)
    if is_long:
        return bool((v1p > thr * 2) and (v1 < thr) and (v3 < v1p * 0.5))
    return bool((v1p < -thr * 2) and (v1 > -thr) and (v3 > v1p * 0.5))


FILTERS = (
    "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER",
    "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER",
    "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER",
    "SATOSHIT_HTF_MFI_D_MIN_TRADIER",
    "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER",
    "SATOSHIT_LONG_MFI_MAX_TRADIER",
    "SATOSHIT_LONG_RSI_MAX_TRADIER",
    "SATOSHIT_LONG_STOCH_K_MAX_TRADIER",
    "SATOSHIT_MIN_VOTES_TRADIER",
    "SATOSHIT_SHORT_MFI_MIN_TRADIER",
    "SATOSHIT_SHORT_RSI_MIN_TRADIER",
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER",
    "SMA200_DIST_LONG_THRESHOLD",
    "STRENGTH_FILTER_ENABLED",
    "STRENGTH_MIN_SCORE",
    "TF_ALIGNMENT_MIN_TOTAL",
    "TF_FOCUS_ENTRY_HARD_GATE",
    "TF_FOCUS_WEIGHT",
    "TF_HTF1",
    "TF_HTF3",
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD",
    "TRADIER_ENTRY_SCORE_THRESHOLD",
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG",
    "TRADIER_FH_MOMENTUM_MFI_MIN",
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT",
    "TRADIER_FH_MOMENTUM_WINDOW_MINUTES",
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER",
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER",
    "TRADIER_RSI_SHORT_REL_VOLUME_MIN",
    "VWAP_FILTER_ENABLED",
    "WIN_TRAIL_EROSION_PCT",
    "WT_15M_BOUNCE_FILTER_HH_ENABLED",
    "WT_15M_BOUNCE_FILTER_HL_ENABLED",
    "WT_15M_BOUNCE_FILTER_MODE",
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF",
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED",
    "WT_15M_BOUNCE_VOLUME_THRESHOLD",
    "WT_DC_DC_POS_THRESHOLD_LONG",
    "WT_DC_DC_POS_THRESHOLD_SHORT",
    "WT_DC_DETAILED_ENTRY_THRESHOLD",
    "WT_DC_DIRECT_THRESHOLD",
    "WT_DC_ENTRY_THRESHOLD",
    "WT_DC_HTF_GATE",
    "WT_DC_HTF_GATE_MODE",
    "WT_DC_K5M_MIN_SHORT_HARD",
    "WT_DC_STOCH_THRESHOLD_LONG",
    "WT_DC_STOCH_THRESHOLD_SHORT",
    "WT_VEL_DECAY_THRESHOLD",
)

DEFAULTS = {
    "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER": 60.0,
    "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER": 42.0,
    "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER": 50.0,
    "SATOSHIT_HTF_MFI_D_MIN_TRADIER": 30.0,
    "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER": 0.3,
    "SATOSHIT_LONG_MFI_MAX_TRADIER": 60.0,
    "SATOSHIT_LONG_RSI_MAX_TRADIER": 50.0,
    "SATOSHIT_LONG_STOCH_K_MAX_TRADIER": 60.0,
    "SATOSHIT_MIN_VOTES_TRADIER": 3,
    "SATOSHIT_SHORT_MFI_MIN_TRADIER": 50.0,
    "SATOSHIT_SHORT_RSI_MIN_TRADIER": 55.0,
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER": 50.0,
    "SMA200_DIST_LONG_THRESHOLD": -3.0,
    "STRENGTH_FILTER_ENABLED": True,
    "STRENGTH_MIN_SCORE": 5.0,
    "TF_ALIGNMENT_MIN_TOTAL": 4,
    "TF_FOCUS_ENTRY_HARD_GATE": False,
    "TF_FOCUS_WEIGHT": 8.0,
    "TF_HTF1": "1h",
    "TF_HTF3": "D",
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": 0.25,
    "TRADIER_ENTRY_SCORE_THRESHOLD": 30,
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": 0.33,
    "TRADIER_FH_MOMENTUM_MFI_MIN": 55.0,
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": 0.5,
    "TRADIER_FH_MOMENTUM_WINDOW_MINUTES": 60,
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": 35,
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": 65,
    "TRADIER_RSI_SHORT_REL_VOLUME_MIN": 2.4,
    "VWAP_FILTER_ENABLED": False,
    "WIN_TRAIL_EROSION_PCT": 0.0,
    "WT_15M_BOUNCE_FILTER_HH_ENABLED": False,
    "WT_15M_BOUNCE_FILTER_HL_ENABLED": False,
    "WT_15M_BOUNCE_FILTER_MODE": "AND",
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": False,
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": False,
    "WT_15M_BOUNCE_VOLUME_THRESHOLD": 1.0,
    "WT_DC_DC_POS_THRESHOLD_LONG": 0.50,
    "WT_DC_DC_POS_THRESHOLD_SHORT": 0.50,
    "WT_DC_DETAILED_ENTRY_THRESHOLD": 43.0,
    "WT_DC_DIRECT_THRESHOLD": -1.0,
    "WT_DC_ENTRY_THRESHOLD": 45.0,
    "WT_DC_HTF_GATE": "4h_D",
    "WT_DC_HTF_GATE_MODE": "AND",
    "WT_DC_K5M_MIN_SHORT_HARD": 20.0,
    "WT_DC_STOCH_THRESHOLD_LONG": 40.0,
    "WT_DC_STOCH_THRESHOLD_SHORT": 60.0,
    "WT_VEL_DECAY_THRESHOLD": 1.0,
}

STATUS = {
    "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER": "NEEDS-OPERATOR-DECISION",
    "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER": "NEEDS-OPERATOR-DECISION",
    "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER": "NEEDS-OPERATOR-DECISION",
    "SATOSHIT_HTF_MFI_D_MIN_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_LONG_MFI_MAX_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_LONG_RSI_MAX_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_LONG_STOCH_K_MAX_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_MIN_VOTES_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_SHORT_MFI_MIN_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_SHORT_RSI_MIN_TRADIER": "ALREADY-WIRED",
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER": "ALREADY-WIRED",
    "SMA200_DIST_LONG_THRESHOLD": "NEEDS-OPERATOR-DECISION",
    "STRENGTH_FILTER_ENABLED": "NEEDS-OPERATOR-DECISION",
    "STRENGTH_MIN_SCORE": "NEEDS-OPERATOR-DECISION",
    "TF_ALIGNMENT_MIN_TOTAL": "NO-CRYPTO-GAP",
    "TF_FOCUS_ENTRY_HARD_GATE": "NEEDS-OPERATOR-DECISION",
    "TF_FOCUS_WEIGHT": "NEEDS-OPERATOR-DECISION",
    "TF_HTF1": "NEEDS-OPERATOR-DECISION",
    "TF_HTF3": "NEEDS-OPERATOR-DECISION",
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": "NO-CRYPTO-GAP",
    "TRADIER_ENTRY_SCORE_THRESHOLD": "NO-CRYPTO-GAP",
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": "NEEDS-OPERATOR-DECISION",
    "TRADIER_FH_MOMENTUM_MFI_MIN": "NEEDS-OPERATOR-DECISION",
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": "NEEDS-OPERATOR-DECISION",
    "TRADIER_FH_MOMENTUM_WINDOW_MINUTES": "NEEDS-OPERATOR-DECISION",
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": "NO-CRYPTO-GAP",
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": "NO-CRYPTO-GAP",
    "TRADIER_RSI_SHORT_REL_VOLUME_MIN": "NO-CRYPTO-GAP",
    "VWAP_FILTER_ENABLED": "NEEDS-OPERATOR-DECISION",
    "WIN_TRAIL_EROSION_PCT": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_FILTER_HH_ENABLED": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_FILTER_HL_ENABLED": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_FILTER_MODE": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": "NEEDS-OPERATOR-DECISION",
    "WT_15M_BOUNCE_VOLUME_THRESHOLD": "NEEDS-OPERATOR-DECISION",
    "WT_DC_DC_POS_THRESHOLD_LONG": "NEEDS-OPERATOR-DECISION",
    "WT_DC_DC_POS_THRESHOLD_SHORT": "NEEDS-OPERATOR-DECISION",
    "WT_DC_DETAILED_ENTRY_THRESHOLD": "NEEDS-OPERATOR-DECISION",
    "WT_DC_DIRECT_THRESHOLD": "NEEDS-OPERATOR-DECISION",
    "WT_DC_ENTRY_THRESHOLD": "NEEDS-OPERATOR-DECISION",
    "WT_DC_HTF_GATE": "NEEDS-OPERATOR-DECISION",
    "WT_DC_HTF_GATE_MODE": "NEEDS-OPERATOR-DECISION",
    "WT_DC_K5M_MIN_SHORT_HARD": "NEEDS-OPERATOR-DECISION",
    "WT_DC_STOCH_THRESHOLD_LONG": "NEEDS-OPERATOR-DECISION",
    "WT_DC_STOCH_THRESHOLD_SHORT": "NEEDS-OPERATOR-DECISION",
    "WT_VEL_DECAY_THRESHOLD": "NEEDS-OPERATOR-DECISION",
}

_GET = {
    "SATOSHIT_EXIT_LONG_STOCH_K_MIN_TRADIER": satoshit_exit_long_stoch_k_min_tradier,
    "SATOSHIT_EXIT_SHORT_RSI_MAX_TRADIER": satoshit_exit_short_rsi_max_tradier,
    "SATOSHIT_EXIT_SHORT_STOCH_K_MAX_TRADIER": satoshit_exit_short_stoch_k_max_tradier,
    "SATOSHIT_HTF_MFI_D_MIN_TRADIER": satoshit_htf_mfi_d_min_tradier,
    "SATOSHIT_HTF_RVOL_1H_MIN_TRADIER": satoshit_htf_rvol_1h_min_tradier,
    "SATOSHIT_LONG_MFI_MAX_TRADIER": satoshit_long_mfi_max_tradier,
    "SATOSHIT_LONG_RSI_MAX_TRADIER": satoshit_long_rsi_max_tradier,
    "SATOSHIT_LONG_STOCH_K_MAX_TRADIER": satoshit_long_stoch_k_max_tradier,
    "SATOSHIT_MIN_VOTES_TRADIER": satoshit_min_votes_tradier,
    "SATOSHIT_SHORT_MFI_MIN_TRADIER": satoshit_short_mfi_min_tradier,
    "SATOSHIT_SHORT_RSI_MIN_TRADIER": satoshit_short_rsi_min_tradier,
    "SATOSHIT_SHORT_STOCH_K_MIN_TRADIER": satoshit_short_stoch_k_min_tradier,
    "SMA200_DIST_LONG_THRESHOLD": sma200_dist_long_threshold,
    "STRENGTH_FILTER_ENABLED": strength_filter_enabled,
    "STRENGTH_MIN_SCORE": strength_min_score,
    "TF_ALIGNMENT_MIN_TOTAL": tf_alignment_min_total,
    "TF_FOCUS_ENTRY_HARD_GATE": tf_focus_entry_hard_gate,
    "TF_FOCUS_WEIGHT": tf_focus_weight,
    "TF_HTF1": tf_htf1,
    "TF_HTF3": tf_htf3,
    "TRADIER_DC_POSITION_ENTRY_THRESHOLD": tradier_dc_position_entry_threshold,
    "TRADIER_ENTRY_SCORE_THRESHOLD": tradier_entry_score_threshold,
    "TRADIER_FH_MOMENTUM_DC_MAX_LONG": tradier_fh_momentum_dc_max_long,
    "TRADIER_FH_MOMENTUM_MFI_MIN": tradier_fh_momentum_mfi_min,
    "TRADIER_FH_MOMENTUM_MIN_MOVE_PCT": tradier_fh_momentum_min_move_pct,
    "TRADIER_FH_MOMENTUM_WINDOW_MINUTES": tradier_fh_momentum_window_minutes,
    "TRADIER_K_ZONE_LONG_THRESHOLD_TRADIER": tradier_k_zone_long_threshold_tradier,
    "TRADIER_K_ZONE_SHORT_THRESHOLD_TRADIER": tradier_k_zone_short_threshold_tradier,
    "TRADIER_RSI_SHORT_REL_VOLUME_MIN": tradier_rsi_short_rel_volume_min,
    "VWAP_FILTER_ENABLED": vwap_filter_enabled,
    "WIN_TRAIL_EROSION_PCT": win_trail_erosion_pct,
    "WT_15M_BOUNCE_FILTER_HH_ENABLED": wt_15m_bounce_filter_hh_enabled,
    "WT_15M_BOUNCE_FILTER_HL_ENABLED": wt_15m_bounce_filter_hl_enabled,
    "WT_15M_BOUNCE_FILTER_MODE": wt_15m_bounce_filter_mode,
    "WT_15M_BOUNCE_REQUIRE_BOTH_HTF": wt_15m_bounce_require_both_htf,
    "WT_15M_BOUNCE_VOLUME_FILTER_ENABLED": wt_15m_bounce_volume_filter_enabled,
    "WT_15M_BOUNCE_VOLUME_THRESHOLD": wt_15m_bounce_volume_threshold,
    "WT_DC_DC_POS_THRESHOLD_LONG": wt_dc_dc_pos_threshold_long,
    "WT_DC_DC_POS_THRESHOLD_SHORT": wt_dc_dc_pos_threshold_short,
    "WT_DC_DETAILED_ENTRY_THRESHOLD": wt_dc_detailed_entry_threshold,
    "WT_DC_DIRECT_THRESHOLD": wt_dc_direct_threshold,
    "WT_DC_ENTRY_THRESHOLD": wt_dc_entry_threshold,
    "WT_DC_HTF_GATE": wt_dc_htf_gate,
    "WT_DC_HTF_GATE_MODE": wt_dc_htf_gate_mode,
    "WT_DC_K5M_MIN_SHORT_HARD": wt_dc_k5m_min_short_hard,
    "WT_DC_STOCH_THRESHOLD_LONG": wt_dc_stoch_threshold_long,
    "WT_DC_STOCH_THRESHOLD_SHORT": wt_dc_stoch_threshold_short,
    "WT_VEL_DECAY_THRESHOLD": wt_vel_decay_threshold,
}


def get(name: str) -> Callable[[Mapping[str, Any] | None, bool, Any], Optional[bool]]:
    """Return the twin predicate for a filter switch name (KeyError if unknown)."""
    return _GET[name]
