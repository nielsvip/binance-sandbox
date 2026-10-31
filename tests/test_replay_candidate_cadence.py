from pathlib import Path

from tools.replay_candidate_cadence import flat_candidate_due, flat_candidate_period


def test_default_flat_candidate_loop_evaluates_every_bar(monkeypatch):
    monkeypatch.delenv("V12_FLAT_CANDIDATE_EVERY", raising=False)
    every = flat_candidate_period()
    assert every == 1
    assert [step for step in range(8) if flat_candidate_due(step, every)] == list(range(8))


def test_explicit_flat_candidate_throttle_is_preserved_and_reportable():
    every = flat_candidate_period("3")
    assert every == 3
    assert [step for step in range(8) if flat_candidate_due(step, every)] == [0, 3, 6]


def test_invalid_flat_candidate_period_is_clamped_to_one():
    assert flat_candidate_period("0") == 1
    assert flat_candidate_period("") == 1


def test_tradier_flat_candidate_selection_loop_uses_the_shared_cadence():
    source = Path("backtest_v12_engine.py").read_text()
    assert "if flat_candidate_due(step, _flat_candidate_every_t):" in source
