"""stdev_macro — macro top/bottom gates driven by the auto-tuned BB %B
already precomputed in NPZ via ez_indicators.bb_auto_tune.

REVISION 2026-05-17 — pivoted from a custom macro_z computation to consume
the existing auto-tuned bb_pct_b_{D,4h,1h} fields. Rationale: bb_auto_tune
already sweeps σ from 1.5→3.5 per (symbol, TF) to maximize balanced upper +
lower band touches — i.e., the σ that "touches the max amount of bars" in
both directions. bb_pct_b_D > 0.95 means price is at the auto-tuned upper
band where historical D-highs cluster; bb_pct_b_D < 0.05 the mirror. This
IS the macro top/bottom signal; macro_z duplicated the work less precisely.

Hierarchy:
  - bb_auto_tune (in ez_indicators.bb_auto_tune) — picks σ per sym/TF for
    maximum balanced band touches. Writes bb_upper/bb_lower/bb_pct_b to NPZ
    via backtest_v8_precompute.py:818-831.
  - This module reads bb_pct_b_D, bb_pct_b_4h, bb_pct_b_1h from the per-bar
    indicators dict and derives a 5-state classifier.
  - Downstream gates (entry_veto / r4_exit / hedge_trigger_boost) consult
    the classifier behind their own *_ENABLED flags.

Default-OFF, additive. Never overrides an existing decision path.
"""
from __future__ import annotations
import math
from typing import Any


# Auto-tuned BB %B thresholds (price normalized to [0,1] across the auto-tuned
# band; 0 = at lower band, 1 = at upper band). Defaults are conservative.
# Strong = "near the auto-tuned extreme where historical highs/lows cluster".
DEFAULT_TOP_PCTB = 0.90
DEFAULT_STRONG_TOP_PCTB = 0.97
DEFAULT_BOT_PCTB = 0.10
DEFAULT_STRONG_BOT_PCTB = 0.03
DEFAULT_TFS = ("D", "4h")  # primary macro TFs; 1h optional confirmation

STATE_STRONG_TOP = "STRONG_TOP"
STATE_TOP = "TOP"
STATE_MID = "MID"
STATE_BOT = "BOT"
STATE_STRONG_BOT = "STRONG_BOT"


def _read_thresholds(config_obj):
    if config_obj is None:
        return DEFAULT_TOP_PCTB, DEFAULT_STRONG_TOP_PCTB, DEFAULT_BOT_PCTB, DEFAULT_STRONG_BOT_PCTB
    return (
        float(getattr(config_obj, "STDEV_MACRO_TOP_PCTB", DEFAULT_TOP_PCTB)),
        float(getattr(config_obj, "STDEV_MACRO_STRONG_TOP_PCTB", DEFAULT_STRONG_TOP_PCTB)),
        float(getattr(config_obj, "STDEV_MACRO_BOT_PCTB", DEFAULT_BOT_PCTB)),
        float(getattr(config_obj, "STDEV_MACRO_STRONG_BOT_PCTB", DEFAULT_STRONG_BOT_PCTB)),
    )


def derive_state(pctb_d: float, pctb_4h: float, top: float = DEFAULT_TOP_PCTB,
                 strong_top: float = DEFAULT_STRONG_TOP_PCTB,
                 bot: float = DEFAULT_BOT_PCTB,
                 strong_bot: float = DEFAULT_STRONG_BOT_PCTB) -> str:
    """Derive 5-state macro classifier from auto-tuned bb_pct_b on D + 4h.
    Opposite-sign extremes (D top while 4h bot, or vice versa) → MID (conflict).
    """
    if not math.isfinite(pctb_d):
        pctb_d = 0.5
    if not math.isfinite(pctb_4h):
        pctb_4h = 0.5
    d_top = pctb_d > top
    d_bot = pctb_d < bot
    h4_top = pctb_4h > top
    h4_bot = pctb_4h < bot
    # Conflict between TFs → MID
    if (d_top and h4_bot) or (d_bot and h4_top):
        return STATE_MID
    if pctb_d > strong_top and pctb_4h > top:
        return STATE_STRONG_TOP
    if pctb_d < strong_bot and pctb_4h < bot:
        return STATE_STRONG_BOT
    if d_top or h4_top:
        return STATE_TOP
    if d_bot or h4_bot:
        return STATE_BOT
    return STATE_MID


def compute_stdev_macro_state(indicators: dict[str, Any], config_obj=None) -> dict[str, Any]:
    """Read auto-tuned bb_pct_b_{D,4h,1h} from indicators (precomputed in NPZ
    via backtest_v8_precompute.py:818 → ez_indicators.bb_auto_tune) and derive
    a 5-state macro classifier.

    Missing fields fail-open: bb_pct_b defaults to 0.5 → state = MID → all
    gates no-op. Thresholds read from config_obj so sweeps can vary them.
    """
    pctb_d = _to_float(indicators.get("bb_pct_b_D", 0.5))
    pctb_4h = _to_float(indicators.get("bb_pct_b_4h", 0.5))
    pctb_1h = _to_float(indicators.get("bb_pct_b_1h", 0.5))
    top, strong_top, bot, strong_bot = _read_thresholds(config_obj)
    state = derive_state(pctb_d, pctb_4h, top=top, strong_top=strong_top, bot=bot, strong_bot=strong_bot)
    # data_present heuristic: at least one of D/4h has a non-default value (precompute fills 0.5 for warmup).
    data_present = (pctb_d != 0.5) or (pctb_4h != 0.5)
    return {
        "bb_pct_b_D": pctb_d,
        "bb_pct_b_4h": pctb_4h,
        "bb_pct_b_1h": pctb_1h,
        "macro_state": state,
        "is_strong_top": state == STATE_STRONG_TOP,
        "is_top": state in (STATE_TOP, STATE_STRONG_TOP),
        "is_strong_bot": state == STATE_STRONG_BOT,
        "is_bot": state in (STATE_BOT, STATE_STRONG_BOT),
        "data_present": data_present,
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
    _ = compute_stdev_macro_state  # type-hint only
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

    Uses auto-tuned bb_pct_b_D in the state_dict (which read it from
    indicators). Optionally requires wt_4h flip against the position for
    confirmation — defaults True (more conservative).
    """
    if not getattr(config_obj, "STDEV_MACRO_R4_EXIT_ENABLED", False):
        return False, ""
    if not state_dict.get("data_present"):
        return False, ""
    require_ltf_flip = getattr(config_obj, "STDEV_MACRO_R4_REQUIRE_LTF_FLIP", True)
    side_u = (side or "").upper()
    wt1_4h = _to_float(indicators.get("wt1_4h"))
    wt2_4h = _to_float(indicators.get("wt2_4h"))
    pctb_d = state_dict.get("bb_pct_b_D", 0.5)
    if side_u == "LONG" and state_dict.get("is_strong_top"):
        if require_ltf_flip and not (wt1_4h < wt2_4h):
            return False, ""
        return True, f"R4_STDEV_MACRO_TOP_bbpctbD={pctb_d:.2f}"
    if side_u == "SHORT" and state_dict.get("is_strong_bot"):
        if require_ltf_flip and not (wt1_4h > wt2_4h):
            return False, ""
        return True, f"R4_STDEV_MACRO_BOT_bbpctbD={pctb_d:.2f}"
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
