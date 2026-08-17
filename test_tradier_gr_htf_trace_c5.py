import json

from tradier_gr_htf_trace_c5 import (
    gr_htf_trace_snapshot,
    reset_gr_htf_trace,
    trace_gr_htf,
)


def test_trace_counts_each_handoff_and_side(monkeypatch):
    monkeypatch.delenv("V8_GR_HTF_TRACE_FILE", raising=False)
    reset_gr_htf_trace()
    trace_gr_htf(
        "scorer_called",
        path="GR_HTF_DIRECT_ENTRY",
        position_side="LONG",
    )
    trace_gr_htf(
        "candidate",
        path="GR_HTF_DIRECT_ENTRY",
        position_side="LONG",
    )
    snapshot = gr_htf_trace_snapshot()
    assert snapshot["scorer_called"] == 1
    assert snapshot["GR_HTF_DIRECT_ENTRY:candidate"] == 1
    assert snapshot["GR_HTF_DIRECT_ENTRY:LONG:candidate"] == 1


def test_trace_file_is_machine_readable(tmp_path, monkeypatch):
    path = tmp_path / "gr.jsonl"
    monkeypatch.setenv("V8_GR_HTF_TRACE_FILE", str(path))
    reset_gr_htf_trace()
    trace_gr_htf(
        "wrapper_blocked",
        path="GR_HTF_DIRECT_EXIT",
        position_side="SHORT",
        reason="BLOCKED_TEST",
    )
    event = json.loads(path.read_text())
    assert event["stage"] == "wrapper_blocked"
    assert event["reason"] == "BLOCKED_TEST"
