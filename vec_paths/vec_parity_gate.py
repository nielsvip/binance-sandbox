"""vec_paths/vec_parity_gate.py — STRICT VEC PARITY allowlist.

Single source of truth for "which live entry/exit routes are ALSO achievable in
the vectorized backtest engine (v8_vec_sweep.py + vec_paths/)".

WHY THIS EXISTS (user mandate 2026-05-28):
    The vectorized per-symbol backtest can only produce a SMALL set of signal
    families (empirically 19 entry + 34 exit families — see
    data/_diagnostic/signal_parity_diff_post_b6.md). Live (ez_manage.py +
    ez_positions_quick.py) produces 500+. We cannot make the two emit bit-exact
    identical *decisions*, so the fallback is: gate live so it ONLY trades the
    switches the vectorized engine ALSO trades. Everything else is blocked. That
    yields the closest reproducible parity, and lets us A/B "parity (vec-only
    routes)" vs "full live routes".

HONEST LIMITATIONS (NO-LIES MANDATE):
    - This is STRATEGY-FAMILY parity, not bit-exact decision parity. A reason
      whose family is vec-achievable still passes even if the exact bar-level
      decision differs from vec.
    - Live and vec name the SAME strategy differently (live `R1_DC_LOW4_3M_EMERGENCY`
      <-> vec stub `RN_DC_LOWN_NM_EMERGENCY`; live `QUICK_SENTIMENT_CUT_GAIN` <->
      vec `SENTIMENT_CUT_GAIN`). The allowlist below maps each vec-achievable
      strategy to its LIVE raw reason substring(s). Mapping source:
      tools/signal_parity_diff.py output + a full grep of the live emit sites
      (see data/_diagnostic/vec_parity_allowlist_provenance.md).
    - WT_CROSSUNDER_FINAL / WT_CROSSOVER_FINAL are STOCKS-only in live
      (tradier_manage.py); they have NO crypto live emitter. Included for the
      stocks gate only.
    - The honest validation loop is SHADOW mode: run live with
      STRICT_VEC_PARITY_SHADOW=True (logs every order it WOULD block, blocks
      nothing) and inspect the logs before flipping STRICT_VEC_PARITY_MODE=True.

MATCHING: case-insensitive SUBSTRING containment. A live reason is vec-achievable
iff it contains at least one allowlist token. Tokens are chosen specific enough
that they do not collide with live-only families (verified against the top
live-only families in signal_parity_diff_post_b6.md).
"""
from __future__ import annotations

# ── ENTRY families the vectorized engine produces (live raw substrings) ──────
# Each token is the shortest stable LIVE substring that uniquely identifies a
# strategy the vec engine ALSO implements.
VEC_ENTRY_TOKENS = (
    "OPEN_STRONG_BUY",                       # vec QUICK_OPEN_STRONG_BUY (live QUICK_OPEN_STRONG_BUY / OPEN_STRONG_BUY_k...)
    "AUGMENT_STRONG_BUY",                     # vec QUICK_OPEN_STRONG_BUY (augment leg)
    "OPEN_STRONG_SELL",                       # vec QUICK_OPEN_STRONG_SELL
    "AUGMENT_STRONG_SELL",                    # vec QUICK_OPEN_STRONG_SELL (augment leg)
    "GOLDEN_RULE_LONG_mult",                  # vec GOLDEN_RULE_LONG_multN
    "GOLDEN_RULE_SHORT_mult",                 # vec GOLDEN_RULE_SHORT_multN
    "GUARANTEED_PRICE_CROSS_REENTRY",         # vec GUARANTEED_PRICE_CROSS_REENTRY_DISK_{LONG,SHORT}
    "WT_3M_FORCE_OPEN",                       # vec WT_NM_FORCE_OPEN_{LONG,SHORT} (3M->NM stub)
    "DIRECTION_FAVORABLE_REENTRY",            # vec DIRECTION_FAVORABLE_REENTRY
    "HEDGE_SAME_SYM_LAST_RESORT",             # vec QUICK_HEDGE_SAME_SYM_LAST_RESORT (live QUICK_-prefixed)
    "GUARANTEED_REENTRY",                     # vec REENTRY_TREND / all BN_* block-reentry families
)

# ── EXIT families the vectorized engine produces (live raw substrings) ───────
VEC_EXIT_TOKENS = (
    "R1_DC_LOW",                              # vec RN_DC_LOWN_NM_EMERGENCY (R1 emergency close)
    "R2_WT_VEL_SLOW",                         # vec RN_WT_VEL_SLOW_DECEL (R2 velocity slowdown)
    "WT_15M_VEL_SLOW",                        # legacy alias of R2
    "IN_GAIN_TREND_EXIT",                     # vec IN_GAIN_TREND_EXIT (+ MED/BIG winner tiers)
    "RIDICULOUS_HOLD",                        # vec RIDICULOUS_HOLD
    "RIDICULOUS_LOSS",                        # vec RIDICULOUS_LOSS
    "WT_4H_VEL_EXIT",                         # vec WT_NH_VEL_EXIT (4H->NH stub)
    "WT_NH_VEL_EXIT",                         # vec WT_NH_VEL_EXIT (in case live emits stub form)
    "PPL_TP",                                 # vec PPL_TP (partial-profit-lock take-profit)
    "PPL_SL",                                 # vec PPL_SL_CLOSE_* (partial-profit-lock stop)
    "DELTA_EXIT_speed",                       # vec DELTA_EXIT_speed (live DELTA_EXIT_speed_decay)
    "HLR_TOP_EXIT",                           # vec HLR_TOP_EXIT (live QUICK_-prefixed)
    "SENTIMENT_CUT_GAIN",                     # vec SENTIMENT_CUT_GAIN (live QUICK_SENTIMENT_CUT_GAIN)
    "DC_HOPELESS",                            # vec DC_HOPELESS_EXIT
    "CYCLE_TP",                               # vec QUICK_CYCLE_TP_STOCH_AGAINST (live QUICK_CYCLE_TP_*, CYCLE_TP_TIERED_*)
    "HEDGE_CLOSE_WT",                         # vec HEDGE_CLOSE_WT (live HEDGE_CLOSE_WTNMNH_*)
    "WT15M_AGAINST",                            # vec v12 scalar WT15M force-close (live process_position twin) — D4 Edit 6 2026-10-04
)

# Stocks-only — vec emits these but live only emits them in tradier_manage.py.
# Included so the same gate works if applied to trb/trc; harmless for crypto.
VEC_EXIT_TOKENS_STOCKS = (
    "WT_CROSSUNDER_FINAL",
    "WT_CROSSOVER_FINAL",
)

_ALL_TOKENS_UPPER = tuple(
    t.upper()
    for t in (VEC_ENTRY_TOKENS + VEC_EXIT_TOKENS + VEC_EXIT_TOKENS_STOCKS)
)


def is_vec_achievable(reason: str) -> bool:
    """True iff `reason` corresponds to a strategy the vectorized engine produces.

    Case-insensitive substring match against the curated allowlist. Empty/None
    reason -> False (vec always emits a named reason; an unnamed live order has
    no vec counterpart and is blocked in parity mode)."""
    if not reason:
        return False
    r = reason.upper()
    for token in _ALL_TOKENS_UPPER:
        if token in r:
            return True
    return False


def matched_token(reason: str) -> str:
    """Return the first allowlist token matched (for logging), or '' if none."""
    if not reason:
        return ""
    r = reason.upper()
    for token in _ALL_TOKENS_UPPER:
        if token in r:
            return token
    return ""
