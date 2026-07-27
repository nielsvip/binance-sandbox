from tools import run_hao_short_exposure_phase4 as base
from tools import run_hao_short_exposure_phase4_confirm as confirm


def test_confirmation_registry_is_bounded_and_preserves_source() -> None:
    rows = confirm.CONFIRMATION_POLICIES
    assert len(rows) == 5
    assert rows[0] == base.EXPOSURE_POLICIES[0]
    assert tuple(x.impulse_stage_target_mult for x in rows[1:]) == (
        7.125,
        7.25,
        7.375,
        7.5,
    )
    assert all(x.resting_reclaim for x in rows[1:])
    assert all(x.impulse_add_mult == 1.0 for x in rows[1:])


def test_configure_changes_only_contract_and_registry(monkeypatch) -> None:
    prior_contract = base.CONTRACT
    prior_registry = base.EXPOSURE_POLICIES
    try:
        confirm.configure()
        assert base.CONTRACT == confirm.CONTRACT
        assert base.EXPOSURE_POLICIES == confirm.CONFIRMATION_POLICIES
        assert base.EXIT_FAMILIES == (
            "EXIT_E02_DONCHIAN",
            "EXIT_E05_DIVERGENCE_RETEST",
        )
    finally:
        monkeypatch.setattr(base, "CONTRACT", prior_contract)
        monkeypatch.setattr(base, "EXPOSURE_POLICIES", prior_registry)
