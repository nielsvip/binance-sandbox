import json
from pathlib import Path

from tools.analyze_algo_structure_campaign import build_report


def test_real_job450_report_covers_all_four_arms_when_available():
    path = Path("/private/tmp/job450_algo_structure_summary.json")
    if not path.exists():
        return
    payload = json.loads(path.read_text())
    if not Path(payload["symbols"][0]["artifact"]).exists():
        return
    report = build_report(payload)
    assert "20 aligned keys × 4" in report
    assert "Exact arm aggregates" in report
    assert "| MU LONG |" in report
