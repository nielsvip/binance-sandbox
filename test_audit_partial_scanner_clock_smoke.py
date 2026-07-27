from tools.audit_partial_scanner_clock_smoke import run


def test_standalone_clock_smoke_passes():
    receipt = run()
    assert receipt["status"] == "PASS"
    assert receipt["terminal_liquidation_booking_count"] == 1
    assert receipt["terminal_duplicate_diagnosis"] == "DISPROVED"
