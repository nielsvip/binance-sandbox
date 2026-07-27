from tools import ingest_regime_entry_exit_beam as ingest


def test_exit_family_mapping_is_registered():
    assert (
        ingest.beam.EXIT_TO_PATH["BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED"]
        == "BOTTOM_A_PROTECTIVE_TRAIL"
    )
