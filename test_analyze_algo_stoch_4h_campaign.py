import json
from pathlib import Path

from tools.analyze_algo_stoch_4h_campaign import build_report


def test_real_job451_report_covers_12_arms_when_artifacts_available():
    path = Path("/private/tmp/job451_algo_stoch_summary.json")
    if not path.exists():
        return
    payload = json.loads(path.read_text())
    if not Path(payload["symbols"][0]["artifact"]).exists():
        return
    report = build_report(payload)
    assert "20 aligned keys × 12" in report
    assert "Exact arm aggregates" in report
