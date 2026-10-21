"""USER 2026-10-21: a TIM-out-of-band or non-positive-gain final is NEVER QUALIFIED and NEVER new_best.

Rescinds the 2026-10-16 -inf gain floor and defaults the 2026-10-13 TIM publish law OFF.
A TIM-only miss runs repair -> REDO (BEST_EFFORT at best); gain<=0 runs repair -> REDO -> IMPOSSIBLE.
Shapes pinned: TIM 5.87 (RLCUSDT_SHORT) and gain -48.59 (the quarantined board)."""
import pathlib

import v15_pilot as P

import tools.v15_suspect_row_rerun as SRR

OK = {"valid": True, "trades": 40, "tim_pct": 35.0, "gain_pct": 12.0, "max_dd_pct": 8.0}
TIM_MISS = dict(OK, tim_pct=5.87)
LOSS = dict(OK, gain_pct=-48.59)
ZERO = dict(OK, gain_pct=0.0)


def test_tim_only_miss_never_qualifies():
    ok, r = P._qualifies_30d(TIM_MISS, -100.0)
    assert not ok and any(x.startswith("TIM ") for x in r)


def test_tim_only_miss_stays_near_miss_lane():
    assert P._nearmiss_30d(TIM_MISS, -100.0)[0] is True


def test_negative_gain_never_qualifies():
    ok, r = P._qualifies_30d(LOSS, -100.0)
    assert not ok and any(x.startswith("gain ") for x in r)
    assert P._nearmiss_30d(LOSS, -100.0) == (False, [])


def test_zero_gain_never_qualifies():
    assert P._qualifies_30d(ZERO, -100.0)[0] is False
    assert P._nearmiss_30d(ZERO, -100.0) == (False, [])


def test_good_board_still_qualifies():
    assert P._qualifies_30d(OK, -100.0) == (True, [])


def test_sidecar_mirror_refuses_loss_and_tim_miss():
    assert SRR.qualifies(LOSS)[0] is False
    assert SRR.qualifies(TIM_MISS)[0] is False
    assert SRR.qualifies(OK)[0] is True


def test_safe_defaults_pinned_in_source():
    src = pathlib.Path(P.__file__).read_text()
    assert 'os.environ.get("V15_TIM_PUBLISH_LAW", "0")' in src
    assert 'os.environ.get("V15_QUAL_GAIN_MIN", "0")' in src
