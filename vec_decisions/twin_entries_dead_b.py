"""Shared live/vec predicates for dead-entries pack B (2026-10-04 wiring mandate).

Three families, each an ENABLED + TF/PCT pair (all default inert):
  STOCH_XTREME_ENTRY/EXIT  stoch K extreme on TF (ENTRY_CONFIRMATION_GATES / EXIT_VELOCITY).
  SMFI_DIV_ENTRY/EXIT      smart-money-flow divergence flags on TF.
  VWAP_STRETCH_ENTRY/EXIT  |px-vwap|/vwap distance vs PCT.

Semantics (no threshold switch exists in config/template, so extremes are module
constants documented here, not swept values):
  ENTRY = reversal trigger, OR-ed into fresh entries (force-open mirror):
    LONG  stoch K_TF<=20 / SHORT stoch K_TF>=80
    LONG  smfi bull_div / SHORT smfi bear_div
    LONG  stretched BELOW vwap by>=PCT / SHORT stretched ABOVE vwap by>=PCT
  EXIT = opposing-extreme close, OR-ed into exits:
    LONG  stoch K_TF>=80 / SHORT stoch K_TF<=20
    LONG  smfi bear_div / SHORT smfi bull_div
    LONG  stretched ABOVE vwap by>=PCT / SHORT stretched BELOW vwap by>=PCT

Data keys verified 2026-10-04 in this worktree:
  live crypto+stocks indicators carry stoch_k_{tf} (alias k_{tf}) and
  smfi_bull_div_{tf} / smfi_bear_div_{tf} for tf in 3m/15m/1h/4h/D (tradier also
  1m/5m); vwap via vwap_npz (fallback vwap).
  NPZ carries stoch_k_{tf} for 15m/1h/4h/D/W/M + base stoch_k, and vwap_D.
  NPZ does NOT carry smfi_* (neither precompute emits it): the smfi vec masks
  read smfi_{bull,bear}_div_{tf} with inert-0 default, i.e. honest non-binding
  until precompute emits them (hook_spec precompute entry, NEEDS-OPERATOR-DECISION).
Parity notes (measured, not assumed):
  TF 3m/5m/1m vec leg uses the base-TF stoch_k key (no stoch_k_3m in NPZ);
  template only offers 4h/D/15m/1h so swept values are exact.
  W/M TF resolves disabled: live has no W/M stoch/smfi, and a vec-only signal
  would be a lie (SWITCH_BIBLE rule 2 / BACKTEST_BIBLE 19).
  Live vwap_npz (today-session 15m VWAP) vs NPZ vwap_D (daily VWAP broadcast):
  same construction, different bar source.
Fail-open: missing/NaN data never fires (stoch level default 50 is neutral;
smfi flags must read exactly 0/1; vwap/px must be >0).
"""
import numpy as np

STOCH_XTREME_OVERSOLD = 20.0
STOCH_XTREME_OVERBOUGHT = 80.0
_STOCH_EXACT_TFS = ("15m", "1h", "4h", "D")
_STOCH_NEAR_TFS = ("1m", "3m", "5m")
_SMFI_TFS = ("1m", "3m", "5m", "15m", "1h", "4h", "D")


def _norm_tf(tf):
    t = str(tf or "").strip()
    if not t or t.upper() == "OFF":
        return ""
    if t in ("D", "W", "M"):
        return t
    return t.lower()


def _stoch_fields(tf):
    if tf in _STOCH_EXACT_TFS:
        return f"stoch_k_{tf}", f"stoch_k_{tf}"
    if tf in _STOCH_NEAR_TFS:
        return f"stoch_k_{tf}", "stoch_k"
    return "", ""


def resolve_stoch_xtreme_entry(get):
    if not bool(get("STOCH_XTREME_ENTRY_ENABLED", False)):
        return {"enabled": False}
    tf = _norm_tf(get("STOCH_XTREME_ENTRY_TF", "1h"))
    live_f, vec_f = _stoch_fields(tf)
    if not live_f:
        return {"enabled": False}
    return {"enabled": True, "tf": tf, "live_field": live_f, "vec_field": vec_f}


def resolve_stoch_xtreme_exit(get):
    if not bool(get("STOCH_XTREME_EXIT_ENABLED", False)):
        return {"enabled": False}
    tf = _norm_tf(get("STOCH_XTREME_EXIT_TF", "1h"))
    live_f, vec_f = _stoch_fields(tf)
    if not live_f:
        return {"enabled": False}
    return {"enabled": True, "tf": tf, "live_field": live_f, "vec_field": vec_f}


def resolve_smfi_div_entry(get):
    if not bool(get("SMFI_DIV_ENTRY_ENABLED", False)):
        return {"enabled": False}
    tf = _norm_tf(get("SMFI_DIV_ENTRY_TF", "1h"))
    if tf not in _SMFI_TFS:
        return {"enabled": False}
    return {"enabled": True, "tf": tf, "live_field_long": f"smfi_bull_div_{tf}", "live_field_short": f"smfi_bear_div_{tf}", "vec_field_long": f"smfi_bull_div_{tf}", "vec_field_short": f"smfi_bear_div_{tf}"}


def resolve_smfi_div_exit(get):
    if not bool(get("SMFI_DIV_EXIT_ENABLED", False)):
        return {"enabled": False}
    tf = _norm_tf(get("SMFI_DIV_EXIT_TF", "1h"))
    if tf not in _SMFI_TFS:
        return {"enabled": False}
    return {"enabled": True, "tf": tf, "live_field_long": f"smfi_bull_div_{tf}", "live_field_short": f"smfi_bear_div_{tf}", "vec_field_long": f"smfi_bull_div_{tf}", "vec_field_short": f"smfi_bear_div_{tf}"}


def resolve_vwap_stretch_entry(get):
    if not bool(get("VWAP_STRETCH_ENTRY_ENABLED", False)):
        return {"enabled": False}
    try:
        pct = float(get("VWAP_STRETCH_ENTRY_PCT", 1.0))
    except (TypeError, ValueError):
        return {"enabled": False}
    if not pct > 0:
        return {"enabled": False}
    return {"enabled": True, "pct": pct}


def resolve_vwap_stretch_exit(get):
    if not bool(get("VWAP_STRETCH_EXIT_ENABLED", False)):
        return {"enabled": False}
    try:
        pct = float(get("VWAP_STRETCH_EXIT_PCT", 1.0))
    except (TypeError, ValueError):
        return {"enabled": False}
    if not pct > 0:
        return {"enabled": False}
    return {"enabled": True, "pct": pct}


def _num(x):
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v:
        return None
    return v


def stoch_xtreme_entry_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    k = _num(level(spec["live_field"]))
    if k is None:
        return (False, "")
    if is_long and k <= STOCH_XTREME_OVERSOLD:
        return (True, f"STOCH_XTREME_ENTRY LONG k_{spec['tf']}={k:.1f}<={STOCH_XTREME_OVERSOLD:.0f}")
    if not is_long and k >= STOCH_XTREME_OVERBOUGHT:
        return (True, f"STOCH_XTREME_ENTRY SHORT k_{spec['tf']}={k:.1f}>={STOCH_XTREME_OVERBOUGHT:.0f}")
    return (False, "")


def stoch_xtreme_exit_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    k = _num(level(spec["live_field"]))
    if k is None:
        return (False, "")
    if is_long and k >= STOCH_XTREME_OVERBOUGHT:
        return (True, f"STOCH_XTREME_EXIT LONG k_{spec['tf']}={k:.1f}>={STOCH_XTREME_OVERBOUGHT:.0f}")
    if not is_long and k <= STOCH_XTREME_OVERSOLD:
        return (True, f"STOCH_XTREME_EXIT SHORT k_{spec['tf']}={k:.1f}<={STOCH_XTREME_OVERSOLD:.0f}")
    return (False, "")


def _flag_on(level, field):
    v = _num(level(field))
    return v is not None and 0.5 < v <= 1.5


def smfi_div_entry_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    if is_long and _flag_on(level, spec["live_field_long"]):
        return (True, f"SMFI_DIV_ENTRY LONG bull_div_{spec['tf']}")
    if not is_long and _flag_on(level, spec["live_field_short"]):
        return (True, f"SMFI_DIV_ENTRY SHORT bear_div_{spec['tf']}")
    return (False, "")


def smfi_div_exit_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    if is_long and _flag_on(level, spec["live_field_short"]):
        return (True, f"SMFI_DIV_EXIT LONG bear_div_{spec['tf']}")
    if not is_long and _flag_on(level, spec["live_field_long"]):
        return (True, f"SMFI_DIV_EXIT SHORT bull_div_{spec['tf']}")
    return (False, "")


def _stretch_below(vwap, px):
    return (vwap - px) / vwap * 100.0


def vwap_stretch_entry_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    vwap = _num(level("vwap"))
    px = _num(px)
    if vwap is None or px is None or vwap <= 0 or px <= 0:
        return (False, "")
    pct = spec["pct"]
    if is_long and _stretch_below(vwap, px) >= pct:
        return (True, f"VWAP_STRETCH_ENTRY LONG {pct:g}pct_below px={px:.4f}_vwap={vwap:.4f}")
    if not is_long and -_stretch_below(vwap, px) >= pct:
        return (True, f"VWAP_STRETCH_ENTRY SHORT {pct:g}pct_above px={px:.4f}_vwap={vwap:.4f}")
    return (False, "")


def vwap_stretch_exit_fire(spec, is_long, level, px):
    if not spec.get("enabled"):
        return (False, "")
    vwap = _num(level("vwap"))
    px = _num(px)
    if vwap is None or px is None or vwap <= 0 or px <= 0:
        return (False, "")
    pct = spec["pct"]
    if is_long and -_stretch_below(vwap, px) >= pct:
        return (True, f"VWAP_STRETCH_EXIT LONG {pct:g}pct_above px={px:.4f}_vwap={vwap:.4f}")
    if not is_long and _stretch_below(vwap, px) >= pct:
        return (True, f"VWAP_STRETCH_EXIT SHORT {pct:g}pct_below px={px:.4f}_vwap={vwap:.4f}")
    return (False, "")


def stoch_xtreme_entry_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "STOCH_XTREME_ENTRY_ENABLED", False)):
        return None
    _, vec_f = _stoch_fields(_norm_tf(getattr(cfg, "STOCH_XTREME_ENTRY_TF", "1h")))
    if not vec_f:
        return None
    k = np.asarray(_safe(npz, vec_f, n, 50.0), dtype=float)
    if is_long:
        return k <= STOCH_XTREME_OVERSOLD
    return k >= STOCH_XTREME_OVERBOUGHT


def stoch_xtreme_exit_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "STOCH_XTREME_EXIT_ENABLED", False)):
        return None
    _, vec_f = _stoch_fields(_norm_tf(getattr(cfg, "STOCH_XTREME_EXIT_TF", "1h")))
    if not vec_f:
        return None
    k = np.asarray(_safe(npz, vec_f, n, 50.0), dtype=float)
    if is_long:
        return k >= STOCH_XTREME_OVERBOUGHT
    return k <= STOCH_XTREME_OVERSOLD


def smfi_div_entry_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "SMFI_DIV_ENTRY_ENABLED", False)):
        return None
    tf = _norm_tf(getattr(cfg, "SMFI_DIV_ENTRY_TF", "1h"))
    if tf not in _SMFI_TFS:
        return None
    f = f"smfi_bull_div_{tf}" if is_long else f"smfi_bear_div_{tf}"
    v = np.asarray(_safe(npz, f, n, 0.0), dtype=float)
    return (v > 0.5) & (v <= 1.5)


def smfi_div_exit_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "SMFI_DIV_EXIT_ENABLED", False)):
        return None
    tf = _norm_tf(getattr(cfg, "SMFI_DIV_EXIT_TF", "1h"))
    if tf not in _SMFI_TFS:
        return None
    f = f"smfi_bear_div_{tf}" if is_long else f"smfi_bull_div_{tf}"
    v = np.asarray(_safe(npz, f, n, 0.0), dtype=float)
    return (v > 0.5) & (v <= 1.5)


def _vwap_vec_masks(npz, n, cfg, close, _safe, pct):
    vwap = np.asarray(_safe(npz, "vwap_D", n, 0.0), dtype=float)
    px = np.asarray(close, dtype=float)
    ok = (vwap > 0) & (px > 0)
    stretch_below = np.where(ok, (vwap - px) / np.maximum(vwap, 1e-9) * 100.0, 0.0)
    return ok, stretch_below >= pct, -stretch_below >= pct


def vwap_stretch_entry_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "VWAP_STRETCH_ENTRY_ENABLED", False)):
        return None
    try:
        pct = float(getattr(cfg, "VWAP_STRETCH_ENTRY_PCT", 1.0))
    except (TypeError, ValueError):
        return None
    if not pct > 0:
        return None
    ok, below, above = _vwap_vec_masks(npz, n, cfg, close, _safe, pct)
    return ok & (below if is_long else above)


def vwap_stretch_exit_vec(npz, n, is_long, cfg, close, _safe):
    if not bool(getattr(cfg, "VWAP_STRETCH_EXIT_ENABLED", False)):
        return None
    try:
        pct = float(getattr(cfg, "VWAP_STRETCH_EXIT_PCT", 1.0))
    except (TypeError, ValueError):
        return None
    if not pct > 0:
        return None
    ok, below, above = _vwap_vec_masks(npz, n, cfg, close, _safe, pct)
    return ok & (above if is_long else below)
