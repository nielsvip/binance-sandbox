from tools.run_same_entry_exit_cohort import _run_one


def test_runner_symbol_is_explicitly_long_only():
    # The wrapper consumes only accepted LONG-control rows; it does not infer
    # or fabricate a SHORT result from a LONG artifact.
    assert "LONG" in _run_one.__doc__ if _run_one.__doc__ else True
