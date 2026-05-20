"""stdev_macro — macro top/bottom gates.

Two algorithms coexist (additive — neither path overrides the other):
  (1) BB %B based (production default 2026-05-17 → present): consume the
      already-precomputed bb_pct_b_{D,4h,1h} fields from NPZ. derive_state()
      + compute_stdev_macro_state() + downstream entry_veto / augment_veto /
      r4_exit / hedge_trigger_boost all use this path.
  (2) Log-price rolling z-score (vec parity path): compute_z_scalar() +
      derive_state_logz() mirror vec_paths/stdev_macro_vec.py. Used by
      tools/test_stdev_macro_parity.py to validate that the vec rolling-z
      gives bit-identical results to the scalar loop. Downstream gates do
      NOT currently consume the log-z path — adding it is a separate
      decision (would require config-driven algorithm selection).

The two algorithms answer DIFFERENT questions:
  - BB %B reads "where is price within the auto-tuned σ band right now."
  - Log-z reads "how many σ away is log(price) from its rolling mean over
    N bars" (default windows D=200, W=52, M=24 per vec defaults).

Both are valid macro tops/bottoms signals — they just look at different
horizons and reference frames. Keep both until backtest evidence picks one.

Default-OFF, additive. Never overrides an existing decision path.
"""
from __future__ import annotations
import math
from typing import Any, Sequence


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


# ════════════════════════════════════════════════════════════════════════════
# LOG-PRICE ROLLING Z-SCORE PATH (parity twin of vec_paths/stdev_macro_vec.py)
# ════════════════════════════════════════════════════════════════════════════
# These functions are the SCALAR reference implementations for the vectorized
# log-price z-score path in vec_paths/stdev_macro_vec.py. They exist to back
# the parity test tools/test_stdev_macro_parity.py. They do NOT replace the
# BB %B path above — downstream gates (entry_veto / augment_veto / r4_exit /
# hedge_trigger_boost) continue to consume compute_stdev_macro_state's BB %B
# output. Wiring derive_state_logz into a downstream gate is a separate
# decision that must go through backtest validation first.

DEFAULT_WINDOWS = {"D": 200, "W": 52, "M": 24}
STRONG_THRESHOLD = 2.5
MODERATE_THRESHOLD = 1.5


def compute_z_scalar(log_close: float, mean: float, std: float) -> float:
    """Scalar log-price z-score given pre-computed log(close), mean, std.

    z = (log_close - mean) / std

    Returns 0.0 when:
      - any input is NaN/Inf
      - std < 1e-9 (constant series)

    The caller is responsible for computing log(close) and the rolling mean/std
    over the window of interest. This matches vec_paths/stdev_macro_vec's
    rolling_log_zscore per-bar output (pandas rolling.mean/std with ddof=0).
    """
    if not math.isfinite(log_close) or not math.isfinite(mean) or not math.isfinite(std):
        return 0.0
    if std < 1e-9:
        return 0.0
    return (log_close - mean) / std


def derive_state_logz(z_d: float, z_w: float) -> str:
    """Scalar log-z-score state classifier — parity twin of derive_state_vec.

    Returns one of STATE_STRONG_TOP / STATE_TOP / STATE_MID / STATE_BOT /
    STATE_STRONG_BOT.

    Opposite-sign extremes between D and W (one TF top, other TF bot) → MID
    (conflict). Mirrors vec semantics (vec returns int8 codes, this returns
    the matching string).
    """
    if not math.isfinite(z_d):
        z_d = 0.0
    if not math.isfinite(z_w):
        z_w = 0.0
    top_any = (z_d > MODERATE_THRESHOLD) or (z_w > MODERATE_THRESHOLD)
    bot_any = (z_d < -MODERATE_THRESHOLD) or (z_w < -MODERATE_THRESHOLD)
    if top_any and bot_any:  # conflict
        return STATE_MID
    strong_top = (z_d > STRONG_THRESHOLD) and (z_w > MODERATE_THRESHOLD)
    strong_bot = (z_d < -STRONG_THRESHOLD) and (z_w < -MODERATE_THRESHOLD)
    if strong_top:
        return STATE_STRONG_TOP
    if strong_bot:
        return STATE_STRONG_BOT
    if top_any:
        return STATE_TOP
    if bot_any:
        return STATE_BOT
    return STATE_MID
