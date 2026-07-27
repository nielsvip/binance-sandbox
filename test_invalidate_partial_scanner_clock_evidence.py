import json

from tools.invalidate_partial_scanner_clock_evidence import (
    INVALID_STATUS,
    find_reports,
)


def test_find_reports_targets_only_partial_and_e06(tmp_path):
    keep = tmp_path / "keep"
    bad = tmp_path / "bad"
    keep.mkdir()
    bad.mkdir()
    (keep / "result.json").write_text(json.dumps({"tier": "OTHER"}))
    (bad / "result.json").write_text(
        json.dumps({"tier": "VEC_RESEARCH_SAME_ENTRY_E06"})
    )
    assert find_reports(tmp_path) == [bad / "result.json"]
    assert "INVALIDATED" in INVALID_STATUS
