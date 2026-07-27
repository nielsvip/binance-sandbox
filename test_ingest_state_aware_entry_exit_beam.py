from tools import ingest_state_aware_entry_exit_beam as ingest


def test_stage_and_extended_exit_mapping_are_explicit():
    assert ingest.STAGE == "VEC_STATE_AWARE_ENTRY_EXIT_BEAM_UNTOUCHED_OOS"
    assert (
        ingest.beam.EXIT_TO_PATH["BOTTOM_B_DELAYED_LOWER_TOP_EXTENDED"]
        == "BOTTOM_B_DELAYED_LOWER_TOP"
    )
