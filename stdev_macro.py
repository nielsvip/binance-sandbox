"""stdev_macro — long-window standard-deviation displacement on D/W/M.

Purpose: detect real macro tops/bottoms on D, W, M timeframes via z-score of
log(close) vs rolling mean. ENTIRELY SEPARATE from Bollinger Bands. BB is a
short-window (20-bar) breakout envelope; macro_z is long-window cycle
position. Two different questions, two different fields.

Single source of truth for BOTH live (ez_manage / tradier_manage) and
backtest (backtest_v8_engine / v8_vec_sweep). Live reads indicators dict;
backtest reads NPZ. The compute_stdev_macro_state() function below normalises
both into the same dict so every gate consumer sees identical fields.

Additive layer ONLY. Every gate downstream must:
  - default to OFF behind its own *_ENABLED flag
  - never replace or disable an existing decision path
  - never silently override BB-breakout logic (BB serves a different purpose)
  - never bypass NOLOSS without an explicit bypass-list entry
"""
from __future__ import annotations
import math
from typing import Any


DEFAULT_WINDOWS = {"D": 200, "W": 52, "M": 24}

STATE_STRONG_TOP = "STRONG_TOP"
STATE_TOP = "TOP"
STATE_MID = "MID"
STATE_BOT = "BOT"
STATE_STRONG_BOT = "STRONG_BOT"

STRONG_THRESHOLD = 2.5
MODERATE_THRESHOLD = 1.5


def compute_z_scalar(log_close: float, mean: float, std: float) -> float:
    if std is None or not math.isfinite(std) or std < 1e-9:
        return 0.0
    if not math.isfinite(log_close) or not math.isfinite(mean):
        return 0.0
    return (log_close - mean) / std


def derive_state(z_d: float, z_w: float) -> str:
    if not math.isfinite(z_d):
        z_d = 0.0
    if not math.isfinite(z_w):
        z_w = 0.0
    top_any = z_d > MODERATE_THRESHOLD or z_w > MODERATE_THRESHOLD
    bot_any = z_d < -MODERATE_THRESHOLD or z_w < -MODERATE_THRESHOLD
    if top_any and bot_any:
        return STATE_MID
    if z_d > STRONG_THRESHOLD and z_w > MODERATE_THRESHOLD:
        return STATE_STRONG_TOP
    if z_d < -STRONG_THRESHOLD and z_w < -MODERATE_THRESHOLD:
        return STATE_STRONG_BOT
    if top_any:
        return STATE_TOP
    if bot_any:
        return STATE_BOT
    return STATE_MID


def compute_stdev_macro_state(indicators: dict[str, Any]) -> dict[str, Any]:
    """Read precomputed macro_z_{D,W,M} from indicators and derive state.

    indicators is the per-bar dict the engine and live system both assemble.
    Missing fields fail-open: state = MID, never blocks anything.
    """
    z_d = _to_float(indicators.get("macro_z_D"))
    z_w = _to_float(indicators.get("macro_z_W"))
    z_m = _to_float(indicators.get("macro_z_M"))
    state = derive_state(z_d, z_w)
    return {
        "macro_z_D": z_d,
        "macro_z_W": z_w,
        "macro_z_M": z_m,
        "macro_state": state,
        "is_strong_top": state == STATE_STRONG_TOP,
        "is_top": state in (STATE_TOP, STATE_STRONG_TOP),
        "is_strong_bot": state == STATE_STRONG_BOT,
        "is_bot": state in (STATE_BOT, STATE_STRONG_BOT),
        "data_present": math.isfinite(z_d) and math.isfinite(z_w) and (z_d != 0.0 or z_w != 0.0),
    }


def _to_float(v) -> float:
    if v is None:
        return 0.0
    try:
        f = float(v)
        if not math.isfinite(f):
            return 0.0
        return f
    except (TypeError, ValueError):
        return 0.0


def entry_veto(side: str, state_dict: dict, config_obj) -> tuple[bool, str]:
    """Block OPEN/AUGMENT when at macro extreme on same side.

    Returns (block, reason). block=False means do nothing (fail open).
    Additive: never blocks based on BB. Never blocks when data missing.
    """
    if not getattr(config_obj, "STDEV_MACRO_ENTRY_VETO_ENABLED", False):
        return False, ""
    if not state_dict.get("data_present"):
        return False, ""
    side_u = (side or "").upper()
    if side_u == "LONG" and state_dict.get("is_strong_top"):
        return True, "STDEV_MACRO_ENTRY_VETO_STRONG_TOP"
    if side_u == "SHORT" and state_dict.get("is_strong_bot"):
        return True, "STDEV_MACRO_ENTRY_VETO_STRONG_BOT"
    return False, ""


def augment_veto(side: str, state_dict: dict, config_obj) -> tuple[bool, str]:
    """Block AUGMENT only (not OPEN-on-empty) at macro extreme on same side.

    Distinct from entry_veto so OPEN-on-empty (incl. BB breakouts from flat)
    is never blocked. Caller must ensure existing_position when invoking.
    """
    if not getattr(config_obj, "STDEV_MACRO_AUGMENT_VETO_ENABLED", False):
        return False, ""
    if not state_dict.get("data_present"):
        return False, ""
    side_u = (side or "").upper()
    if side_u == "LONG" and state_dict.get("is_top"):
        return True, "STDEV_MACRO_AUGMENT_VETO_TOP"
    if side_u == "SHORT" and state_dict.get("is_bot"):
        return True, "STDEV_MACRO_AUGMENT_VETO_BOT"
    return False, ""


def entry_size_boost(side: str, state_dict: dict, config_obj) -> float:
    """Size multiplier when entering AGAINST macro extreme (mean-revert)."""
    if not getattr(config_obj, "STDEV_MACRO_ENTRY_BOOST_ENABLED", False):
        return 1.0
    if not state_dict.get("data_present"):
        return 1.0
    boost = float(getattr(config_obj, "STDEV_MACRO_ENTRY_BOOST_MULT", 1.3))
    side_u = (side or "").upper()
    if side_u == "LONG" and state_dict.get("is_strong_bot"):
        return boost
    if side_u == "SHORT" and state_dict.get("is_strong_top"):
        return boost
    return 1.0


def r4_exit(side: str, state_dict: dict, indicators: dict, config_obj) -> tuple[bool, str]:
    """Exit trigger after R1/R2/R3 when held into a macro extreme that
    matches an LTF momentum flip. Returns (close, reason).

    Additive: this is a NEW reason, NOT a replacement for R1/R2/R3.
    Must be evaluated AFTER R1/R2/R3 in process_position.
    Bypass list entries (config flags below) must be added before this
    can close at a loss; otherwise NOLOSS_GATE keeps it as HOLD.
    """
    if not getattr(config_obj, "STDEV_MACRO_R4_EXIT_ENABLED", False):
        return False, ""
    if not state_dict.get("data_present"):
        return False, ""
    require_ltf_flip = getattr(config_obj, "STDEV_MACRO_R4_REQUIRE_LTF_FLIP", True)
    side_u = (side or "").upper()
    wt1_4h = _to_float(indicators.get("wt1_4h"))
    wt2_4h = _to_float(indicators.get("wt2_4h"))
    if side_u == "LONG" and state_dict.get("is_strong_top"):
        if require_ltf_flip and not (wt1_4h < wt2_4h):
            return False, ""
        return True, "R4_STDEV_MACRO_TOP"
    if side_u == "SHORT" and state_dict.get("is_strong_bot"):
        if require_ltf_flip and not (wt1_4h > wt2_4h):
            return False, ""
        return True, "R4_STDEV_MACRO_BOT"
    return False, ""


def hedge_trigger_boost(origin_side: str, state_dict: dict, config_obj) -> tuple[bool, str]:
    """Additional OBLIGATORY_HEDGE trigger when origin position is held
    AGAINST a macro extreme. Returns (fire, reason).

    Additive: this is an EXTRA trigger path. Existing hedge triggers are
    untouched. Only fires when origin side and macro extreme conflict.
    """
    if not getattr(config_obj, "STDEV_MACRO_HEDGE_BOOST_ENABLED", False):
        return False, ""
    if not state_dict.get("data_present"):
        return False, ""
    origin_u = (origin_side or "").upper()
    if origin_u == "LONG" and state_dict.get("is_strong_top"):
        return True, "STDEV_MACRO_HEDGE_BOOST_TOP"
    if origin_u == "SHORT" and state_dict.get("is_strong_bot"):
        return True, "STDEV_MACRO_HEDGE_BOOST_BOT"
    return False, ""
