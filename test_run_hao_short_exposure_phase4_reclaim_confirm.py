from tools import run_hao_short_exposure_phase4_reclaim_confirm as confirm


def test_reclaim_confirmation_is_bounded_and_preserves_source() -> None:
    assert len(confirm.POLICIES) == 4
    assert confirm.POLICIES[0].label == "P00_SOURCE_0875_C6"
    assert tuple(x.impulse_stage_target_mult for x in confirm.POLICIES[1:]) == (
        7.25,
        7.375,
        7.5,
    )
