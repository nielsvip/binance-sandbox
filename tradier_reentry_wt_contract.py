"""Pure WT confirmation contract for mandatory Tradier re-entry.

This is intentionally opt-in.  It is used by the exact backtest path and the
live manager, but the live default remains disabled until matrix evidence
supports promotion.
"""

from __future__ import annotations

from typing import Any, Mapping, Tuple


def _float(indicators: Mapping[str, Any], *keys: str, default: float = 0.0) -> float:
    for key in keys:
        value = indicators.get(key)
        if value is not None:
            try:
                return float(value)
            except (TypeError, ValueError):
                pass
    return default


def mandatory_reentry_wt_gate(
    indicators: Mapping[str, Any],
    is_long: bool,
    *,
    enabled: bool,
    tf_mode: str = "5m_or_15m",
    min_tfs: int = 1,
    require_flip: bool = True,
    velocity_ratio: float = 0.90,
    min_velocity: float = 0.0,
) -> Tuple[bool, str]:
    """Return whether a price-cross re-entry has directional WT momentum.

    A qualifying timeframe must be on the position's side, have a fresh WT
    flip when ``require_flip`` is enabled, and have favorable velocity that is
    not decelerating relative to the prior completed bar.
    """
    if not enabled:
        return True, "DISABLED"

    mode = str(tf_mode or "5m_or_15m").lower().replace(" ", "")
    if mode in {"5m", "5m_only"}:
        tfs = ("5m",)
    elif mode in {"15m", "15m_only"}:
        tfs = ("15m",)
    else:
        tfs = ("5m", "15m")

    ratio = max(0.0, float(velocity_ratio))
    min_v = max(0.0, float(min_velocity))
    details = []
    qualifying = 0
    for tf in tfs:
        w1 = _float(indicators, f"wt1_{tf}")
        w2 = _float(indicators, f"wt2_{tf}")
        w1_prev = _float(indicators, f"wt1_{tf}_prev", f"wt1_prev_{tf}", default=w2)
        w2_prev = _float(indicators, f"wt2_{tf}_prev", f"wt2_prev_{tf}", default=w2)
        favorable = w1 > w2 if is_long else w1 < w2
        was_favorable = w1_prev > w2_prev if is_long else w1_prev < w2_prev
        explicit_flip = bool(
            indicators.get(
                f"wt_cross_bull_{tf}" if is_long else f"wt_cross_bear_{tf}",
                False,
            )
        )
        flipped = explicit_flip or (favorable and not was_favorable)

        velocity = _float(indicators, f"wt_velocity_{tf}")
        velocity_prev = _float(
            indicators,
            f"wt_velocity_{tf}_prev",
            f"wt_velocity_prev_{tf}",
            default=velocity,
        )
        favorable_velocity = velocity >= min_v if is_long else velocity <= -min_v
        not_slowing = abs(velocity) >= max(min_v, abs(velocity_prev) * ratio)
        ok = favorable and (flipped if require_flip else True) and favorable_velocity and not_slowing
        if ok:
            qualifying += 1
        details.append(
            f"{tf}:fav={int(favorable)},flip={int(flipped)},"
            f"vel={velocity:.3f},prev={velocity_prev:.3f},ok={int(ok)}"
        )

    required = max(1, min(int(min_tfs), len(tfs)))
    passed = qualifying >= required
    return passed, f"qualifying={qualifying}/{required};" + ";".join(details)
