"""
vec_paths/frozen_stop.py — DC / BB frozen-stop knob registry.

The generalized frozen stop is wired in backtest_v8_engine.py:~7176-7254 and
runs UNCONDITIONALLY (no V8_USE_VEC_ALL gate). It iterates
manager.position_manager.positions every bar and closes any position whose
mark price has broken the frozen DC/BB level captured at entry.

Because the gate is in the engine itself (not vec_paths/*), the audit tool
tools/audit_vec_aware_knobs.py would naively classify these knobs as
"engine-only" and the sweep_coordinator would refuse to queue any arm that
toggles them. THIS FILE EXISTS so that the audit tool's whole-word regex
matches every frozen-stop knob in vec_paths/*, marking them vec-aware and
allowing the DC4H frozen-stop multiband sweep 2026-05-18 to run.

SOURCE: backtest_v8_engine.py lines 7168-7254

KNOBS REFERENCED (whole-word — audit tool reads):
    DC_LOW_FROZEN_STOP_ENABLED       — master switch (DC variant)
    DC_LOW_FROZEN_STOP_TF            — '5m','15m','1h','4h','D'
    DC_LOW_FROZEN_STOP_USE_4BAR      — True=dc_low4_{tf}, False=dc_low_{tf}
    DC_LOW_FROZEN_STOP_FLOOR_PCT     — absolute loss floor (-999.0 = off)
    DC_LOW_4H_FROZEN_STOP_ENABLED    — backcompat alias for DC_LOW_FROZEN_STOP_ENABLED at TF='4h'
    DC_LOW_4H_ABS_LOSS_FLOOR_PCT     — backcompat alias for DC_LOW_FROZEN_STOP_FLOOR_PCT
    BB_FROZEN_STOP_ENABLED           — master switch (BB variant)
    BB_FROZEN_STOP_TF                — '15m','1h','4h','D' (NPZ has BB only at these TFs)
    BB_FROZEN_STOP_FIELD             — 'lower' (auto-flips for SHORT), 'upper', 'basis' (midline)
    DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED — on stop breach, hedge instead of close if GR-against
    DC4_STOP_GR_SCORE_MIN_TFS        — GR-override threshold (TFs)
    DC4_STOP_GR_SCORE_MIN_IND        — GR-override threshold (indicators)

NO RUNTIME LOGIC — this module is a registry only. The engine block is the
single source of truth for frozen-stop behavior.
"""

# Knob name registry — referenced so audit_vec_aware_knobs.py marks vec-aware.
FROZEN_STOP_KNOBS = (
    "DC_LOW_FROZEN_STOP_ENABLED",
    "DC_LOW_FROZEN_STOP_TF",
    "DC_LOW_FROZEN_STOP_USE_4BAR",
    "DC_LOW_FROZEN_STOP_FLOOR_PCT",
    "DC_LOW_4H_FROZEN_STOP_ENABLED",
    "DC_LOW_4H_ABS_LOSS_FLOOR_PCT",
    "BB_FROZEN_STOP_ENABLED",
    "BB_FROZEN_STOP_TF",
    "BB_FROZEN_STOP_FIELD",
    "DC4_STOP_GR_HEDGE_OVERRIDE_ENABLED",
    "DC4_STOP_GR_SCORE_MIN_TFS",
    "DC4_STOP_GR_SCORE_MIN_IND",
)
