import json
from pathlib import Path

from tools.analyze_mtf_atr_trail_campaign import build_report


def test_real_job47_summary_builds_paired_noncausal_report():
    mtf_path = Path("/private/tmp/job47_mtf_atr_summary.json")
    bottom_path = Path("/private/tmp/job175_bottom_a_summary.json")
    if not mtf_path.exists() or not bottom_path.exists():
        return
    report = build_report(
        json.loads(mtf_path.read_text()),
        json.loads(bottom_path.read_text()),
    )
    assert "20 aligned keys × 40 arms" in report
    assert "not a causal incremental claim" in report
    assert "strict vector" in report
    assert "| MU LONG |" in report
