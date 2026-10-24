"""Augment gain paths: typed thresholds must be tested and enforced, never skipped or stubbed.

USER 2026-10-24 (witness ang:MASKUSDT_SHORT >8% with no augment): the WT-15m-crossup
augment tiers (1/1.5/2% gain) and the no-crossup obligatory tiers (2-3-4% gain) in
TEMPLATE_* never take effect. Measured dataflow 2026-10-24:

- AUGMENT_FALLBACK_GAIN_PCT sits in v15_pilot.DEAD_VEC_SWITCHES, so every one of its
  rows is skipped (MASKUSDT_SHORT: 5 rows, reason DEAD_VEC_PATH), while live
  evaluate_augmentation DOES consume it -> live runs an unmeasured value.
- AUGMENT_BOUNCE/BREAKOUT_MIN_GAIN_PCT only reach the vector behind
  AUGMENT_TYPED_MIN_GAIN_ENABLED (default False), but their rows (AUGMENT_TREND,
  filled 8th) are evaluated before the master row (AUGMENT_RISK_SIZING, filled 9th)
  with the master off -> deltas structurally ~0 -> never promote -> deadlock.
- AUGMENT_WT_4H_BOUNCE_ENABLED is pilot-testable, yet its only vector reads sit
  after _batch1_template_wiring's early return (dead code) -> fake-tested,
  promotable on board noise (+/-0.48).
- Live AUG_A-D (AUG_B WT-cross) tiers off GLOBAL MIN_GAIN=3.0, ignoring the board's
  AUGMENT_MIN_GAIN_PCT (MASKUSDT_SHORT board: 0.75); the UAGAIN choke generic tier
  is likewise global. Vector fires from 0.75%, live waits for 3%.
- Live "reads" of the typed thresholds are stubs: bool(threshold) + discarded
  position gain (ez_manage.py), never a comparison.

These tests pin the required end state: every typed augment gain switch is always
tested (obligatory, never dead-skipped while a real path exists), the vector
applies the best measured value without a master deadlock, and live resolves the
same tier per sym_side.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import v15_pilot as P  # noqa: E402
from vec_decisions import live_unw_gates as _lug  # noqa: E402


def _get(**kw):
    return lambda k, d=None: kw.get(k, d)


def test_bounce_and_breakout_thresholds_are_testable():
    for sw in ("AUGMENT_BOUNCE_MIN_GAIN_PCT", "AUGMENT_BREAKOUT_MIN_GAIN_PCT"):
        for tab in ("AUGMENT_TREND", "AUGMENT_RISK_SIZING"):
            assert P.promotion_block_reason(sw, tab) == "", f"{sw} on {tab} must be tested, never skipped"


def test_fallback_gain_threshold_is_testable():
    # Live consumes AUGMENT_FALLBACK_GAIN_PCT per sym_side (evaluate_augmentation via
    # twin_reentry_staged); the vector must model it, so the pilot must test it.
    assert P.promotion_block_reason("AUGMENT_FALLBACK_GAIN_PCT", "AUGMENT_RISK_SIZING") == ""


def test_wt4h_bounce_is_blocked_until_wired():
    # Its only vector reads are dead code after _batch1_template_wiring's early
    # return and live has no functional read: testing it measures noise, so the
    # pilot must block it (same class as EXIT_R1_R2_FILTER_TF) until wired.
    assert P.promotion_block_reason("AUGMENT_WT_4H_BOUNCE_ENABLED", "AUGMENT_RISK_SIZING") != ""


def test_typed_tier_applies_without_master_deadlock():
    # A bounce-type augment must tier off AUGMENT_BOUNCE_MIN_GAIN_PCT even when the
    # typed master was never promoted (the master row fills after the threshold
    # rows, so gating on it deadlocks the whole family at defaults forever).
    got = _lug.augment_tier_min_gain(
        _get(AUGMENT_BOUNCE_MIN_GAIN_PCT=1.0, AUGMENT_BREAKOUT_MIN_GAIN_PCT=2.0),
        "AUG_B_WT_CROSS_15m_g8.0%_wt=2/3",
        3.0,
    )
    assert got == 1.0, f"bounce augment must tier at 1.0, got {got}"
    got = _lug.augment_tier_min_gain(
        _get(AUGMENT_BOUNCE_MIN_GAIN_PCT=1.0, AUGMENT_BREAKOUT_MIN_GAIN_PCT=2.0),
        "AUG_A_BLOWPAST_g9.0%",
        3.0,
    )
    assert got == 2.0, f"breakout augment must tier at 2.0, got {got}"


def _ez_augment_fn_source():
    src = (ROOT / "ez_manage.py").read_text()
    start = src.index("async def evaluate_augmentation")
    end = src.index("async def evaluate_leaderboard_entry")
    return src[start:end]


def test_live_aug_tier_is_per_sym_not_global():
    # AUG_A-D must tier off the board's per-sym gain value: the generic tier feeds
    # from _psym_get, and the WT-cross / blowpast gates compare against the typed
    # per-sym tiers — never bare GLOBAL MIN_GAIN (a fail-open import fallback may
    # still name it, but no gate may compare against it).
    fn_src = _ez_augment_fn_source()
    assert "effective_min_gain(_tier_get)" in fn_src
    assert "_tier_get = lambda _k, _d=None: _psym_get(symbol" in fn_src
    assert "gain >= 3 * _blowpast_tier" in fn_src
    assert "gain >= _wtcross_tier" in fn_src
    # No gate may compare against GLOBAL MIN_GAIN; the single remaining naming is
    # the fail-open import fallback assignment (which no gate reads).
    for ln in fn_src.splitlines():
        if 'getattr(config, "MIN_GAIN"' in ln:
            assert "_generic_tier = safe_fetch_float" in ln, f"GLOBAL MIN_GAIN outside fail-open fallback: {ln.strip()}"


def test_live_typed_thresholds_compared_not_stubbed():
    # The typed thresholds must gate a real gain comparison; bool(threshold) with
    # a discarded position-gain read is a wiring-census stub, not logic.
    src = (ROOT / "ez_manage.py").read_text()
    assert 'if bool(getattr(config, "AUGMENT_BOUNCE_MIN_GAIN_PCT", False))' not in src
    assert 'if bool(getattr(config, "AUGMENT_BREAKOUT_MIN_GAIN_PCT", False))' not in src
