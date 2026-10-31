import json

import pytest

from tools.repair_replay_incident import quarantine_rows, sha

def target_for(raw, record, start, end):
    return {"file": {"current_file_sha256": sha(raw)}, "rows": [{"byte_start": start, "byte_end_exclusive": end, "raw_line_sha256": sha(raw[start:end + (end < len(raw))]), "canonical_record_sha256": sha(json.dumps(record, sort_keys=True, separators=(",", ":")).encode()), "record": record}]}

def test_quarantine_preserves_all_unrelated_bytes_and_diagnostic_text():
    original = b'{"real": 1}\n'
    record = {"mock": True, "reason": "VEC_LAST_REFUSE unchanged"}
    line = json.dumps(record).encode()
    tail = b'{"real": 2}\n'
    raw = original + line + b"\n" + tail
    target = target_for(raw, record, len(original), len(original) + len(line))
    assert quarantine_rows(raw, target) == original + tail
    assert target["rows"][0]["record"]["reason"] == "VEC_LAST_REFUSE unchanged"

def test_quarantine_fails_if_current_file_has_new_records():
    raw = b'{"mock": true}\n'
    target = target_for(raw, {"mock": True}, 0, len(raw) - 1)
    with pytest.raises(RuntimeError, match="changed"):
        quarantine_rows(raw + b'{"real": 1}\n', target)

def test_quarantine_requires_exact_reviewed_identity_and_line_boundaries():
    raw = b'{"mock": true}\n'
    target = target_for(raw, {"mock": True}, 0, len(raw) - 1)
    target["rows"][0]["record"] = {"mock": False}
    with pytest.raises(RuntimeError, match="bytes differ"):
        quarantine_rows(raw, target)
    target["rows"][0]["byte_start"] = 1
    with pytest.raises(RuntimeError, match="boundaries"):
        quarantine_rows(raw, target)
