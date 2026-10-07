"""band_ladder_mult_vec — STAGED pure port of live tradier_manage.band_ladder_mult.

STATUS: STAGED PURE PORT ONLY — NO CALL SITE. The vec engine models no
LR-band arrow entries (LR_BAND_ENTRY_ENABLED is LIVE_ONLY both venues), so
there is no honest call site where this sizing multiplier could apply.
Staging the predicate alone would be an unwired twin; wiring it to a
non-LR entry would be fabrication (§19). Land a call site only together
with a vec LR-band arrow entry family.

Covers the LIVE_ONLY stocks knobs that bind ONLY through band_ladder_mult:
  LR_BAND_LADDER_BASIS ('band' default / 'slope' re-anchor, §16.62A)
  LR_BAND_LADDER_BELOW_BOTTOM_MULT / _ABOVE_TOP_MULT / _CENTER
    (scan-miss LIVE: read via the local `g()` lambda, invisible to the
    AST scan — bible rows say DEAD/DEAD, actually LIVE_ONLY stocks)
plus the shared TF_BOTTOM/TF_TOP/BOTTOM_MULT/TOP_MULT/BASE_UNIT/CAPACITY/MODE
reads (already twinned on the ordinary-parity path).

Math delegates to ordinary_ladder_contract.ladder_multiplier (identical
code, not a re-implementation — same pattern as lr_band_ladder_aug).
"""
from __future__ import annotations


def band_ladder_mult(pct_b, cfg, tf="D"):
    """Size multiplier for a green arrow at band position pct_b. Never raises."""
    try:
        pb = float(pct_b)
    except (TypeError, ValueError):
        return 0.0
    if pb != pb:
        return 0.0

    def g(k, d):
        try:
            return float(getattr(cfg, k, d))
        except (TypeError, ValueError):
            return float(d)

    def tf_val(mapname, scalar_key, dflt):
        try:
            m = getattr(cfg, mapname, None) or {}
            if tf in m:
                return float(m[tf])
        except Exception:
            pass
        return g(scalar_key, dflt)

    below = g("LR_BAND_LADDER_BELOW_BOTTOM_MULT", 0.0)
    bottom = tf_val("LR_BAND_LADDER_TF_BOTTOM", "LR_BAND_LADDER_BOTTOM_MULT", 10.0)
    top = tf_val("LR_BAND_LADDER_TF_TOP", "LR_BAND_LADDER_TOP_MULT", 3.0)
    above = g("LR_BAND_LADDER_ABOVE_TOP_MULT", -1.0)
    if above < 0:
        above = top
    base = max(1e-9, g("LR_BAND_LADDER_BASE_UNIT_USD", 2000.0))
    capacity = max(0.0, g("LR_BAND_LADDER_CAPACITY_USD", 16000.0))
    basis = str(getattr(cfg, "LR_BAND_LADDER_BASIS", "band")).lower()
    if basis == "slope" and pb >= 0.0:
        pb = max(0.0, (pb - 0.5) / 0.5)
    import ordinary_ladder_contract as C
    return C.ladder_multiplier(
        pb,
        bottom,
        top,
        str(getattr(cfg, "LR_BAND_LADDER_MODE", "linear")),
        max_multiplier=capacity / base,
        center=g("LR_BAND_LADDER_CENTER", 0.5),
        below_bottom=below,
        above_top=above,
    )
