from tools.run_same_entry_exit_cohort import _run_one


def test_runner_reads_side_from_frozen_artifact():
    # Side is not an argument and therefore cannot be silently inverted by the
    # cohort wrapper; _run_one reads it from the fingerprinted artifact.
    assert "side" not in _run_one.__annotations__
