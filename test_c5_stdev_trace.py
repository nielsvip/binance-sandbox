import json

import c5_stdev_trace


def test_stdev_trace_records_raw_sample_and_fire(tmp_path, monkeypatch):
    path = tmp_path / "stdev.json"
    monkeypatch.setenv("V8_STDEV_C5_TRACE_FILE", str(path))
    c5_stdev_trace._COUNTERS.clear()
    c5_stdev_trace._SAMPLES.clear()
    c5_stdev_trace.record_stdev_evaluation(
        path="STDEV_REJECT_EXIT",
        symbol="MU",
        side="LONG",
        timeframe="D",
        pct_b_previous=0.9,
        pct_b_current=0.5,
        wt_velocity_1h=-1.0,
        fire=True,
    )
    c5_stdev_trace._write_snapshot()
    payload = json.loads(path.read_text())
    assert payload["counters"]["STDEV_REJECT_EXIT:LONG:evaluated"] == 1
    assert payload["counters"]["STDEV_REJECT_EXIT:LONG:fired"] == 1
    assert payload["first_raw_samples"]["STDEV_REJECT_EXIT:LONG"][
        "pct_b_previous"
    ] == 0.9
