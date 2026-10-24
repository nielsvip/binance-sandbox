"""No dead paths: switches with no real vectorized path must stay blocked from testing.

USER 2026-10-24 ("make sure NO dead paths remain also outside of augment"): every
name below was dataflow-verified this session — no v12 body reads outside the dead
ranges, no twin-module caller, no live consumer (or live-infra/global only, which a
single-symbol board can never move). Testing them measures board noise (+/-0.48
gain) and can promote values that change live protection with zero evidence (the
2026-10-15 EXIT_R1_R2_FILTER_TF accident). Blocking stops future promotions; live
behaviour is unchanged by every entry. Remove a name only when a real vec predicate
+ live consumer land (bible §18: investigate, never guess).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v15_pilot as P  # noqa: E402

BLOCKED = [
    # Fully dead — zero vec reads, zero live consumers.
    "ULTIMATE_DC_4H_STOP_ENABLED",  # safety: kill-switch for the unconditional 4h hard stop
    "HARD_AUGMENT_LOCK_SEC", "HARD_REDUCE_LOCK_SEC", "ABSOLUTE_OPEN_LOCK_SEC",  # NAME SPLIT, real knobs are _SECONDS
    "REENTRY_TIER2_MAX_MINUTES", "REENTRY_TIER2_MAX_MINUTES_TRADIER",  # retired
    "STALE_HOLD_ENABLED", "BALANCE_FLOOR_USD",
    "DC_DAYTRADE_STOP_PCT",  # vec assigns daytrade_stop, never reads it
    "AUGMENT_WT_4H_BOUNCE_ENABLED",  # vec reads are dead code after _batch1 early return
    "AUGMENTED_DC_BREAK_ENABLED",  # vec imports the module, never calls it
    "REGIME_RANGING_EXIT_GAIN_MIN", "REGIME_TRENDING_EXIT_GAIN_MIN",
    "GR_FILTER_VEC_MIN_TFS", "STDEV_SUPPRESS_EARLY_EXIT", "REVERSE_ON_EXIT_ENABLED",
    "WORKER_INSTANCE_ID", "WORKER_TOTAL_INSTANCES",
    # Live-infra / execution plumbing — board can never move them.
    "EXECUTE_NOW_MAX_MARK_AGE_S", "EZ_MANAGE_THROTTLER_RATE",
    "OPEN_RATE_BREAKER_ENABLED", "OPEN_RATE_MAX",
    # Live-runs-unmeasured — real live consumers the vector never models.
    "HTF_TREND_VETO_BYPASS_ENABLED", "HTF_TREND_VETO_BYPASS_REASONS",
    "MARKET_QUALITY_SCORE_ENABLED", "BEAR_MARKET_MODE_TRADIER",
    "REENTRY_TIER2_MIN_MINUTES_TRADIER",  # live tradier cooldown param, no vec twin (unblock only with one)
    "HTF_GATE_BYPASS_RZ",
    "HIGH_GAIN_AUGMENTATION_MIN_SIZE",  # sizing floor stays live's job
]

# Verified wired on BOTH sides this session — must stay testable (blocking a real
# lever is as harmful as testing a dead one).
TESTABLE = [
    "AUGMENT_FALLBACK_GAIN_PCT",  # v12 fallback-reduce hook via twin_reentry_staged
    "AUGMENT_BOUNCE_MIN_GAIN_PCT", "AUGMENT_BREAKOUT_MIN_GAIN_PCT",  # always-dispatch typed tiers
    "DC_DAYTRADE_TARGET_PCT",  # vec assigns AND twin/live consume it
    "AUGMENT_BULL_KILL_ENABLED",  # vec signal + live twin callers both venues
    "HAIKU_WINNER_AUGMENT_ENABLED",  # vec lane + live per-sym parity gate
]


def test_verified_dead_switches_stay_blocked():
    for sw in BLOCKED:
        for tab in ("AUGMENT_TREND", "AUGMENT_RISK_SIZING", "EXIT_STRUCTURAL", "GLOBAL_RISK_GATES"):
            assert P.promotion_block_reason(sw, tab) != "", f"{sw} must stay blocked until genuinely wired"


def test_verified_wired_switches_stay_testable():
    for sw in TESTABLE:
        assert P.promotion_block_reason(sw, None) == "", f"{sw} is wired both sides and must be tested"
