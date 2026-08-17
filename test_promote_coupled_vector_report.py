from datetime import datetime, timedelta, timezone
import json

import pytest

from tools import promote_coupled_vector_report as promote


def _pair(tmp_path, hashes, *, campaign=promote.CURRENT_CAMPAIGN_NAME,
          generated=None, completed=2, current=2, stale=0):
    generated = generated or datetime.now(timezone.utc).isoformat()
    payload = {
        "schema": "coupled-vector-progress-v1",
        "generated_utc": generated,
        "campaign_s1": f"/home/niels/binance-sandbox/data/reports/vec_research/{campaign}",
        "planned_keys": 77,
        "completed_receipts": completed,
        "current_source_receipts": current,
        "stale_source_receipts": stale,
        "result_producing_engine_hashes": hashes,
    }
    incoming_json = tmp_path / "incoming.json"
    incoming_md = tmp_path / "incoming.md"
    incoming_json.write_text(json.dumps(payload))
    incoming_md.write_text(f"{generated}\n{payload['campaign_s1']}\n")
    return incoming_json, incoming_md


def test_promotes_only_current_source_complete_r6_pair(tmp_path, monkeypatch):
    hashes = {"executor_sha256": "current"}
    monkeypatch.setattr(promote, "current_engine_hashes", lambda _root: hashes)
    incoming_json, incoming_md = _pair(tmp_path, hashes)
    destination_json = tmp_path / "current.json"
    destination_md = tmp_path / "current.md"
    result = promote.promote(tmp_path, incoming_json, incoming_md,
                             destination_json, destination_md)
    assert result["status"] == "PROMOTED_CURRENT_R6_COMPACT_REPORT"
    assert json.loads(destination_json.read_text())["completed_receipts"] == 2
    second = promote.promote(tmp_path, incoming_json, incoming_md,
                             destination_json, destination_md)
    assert second["status"] == "ALREADY_CURRENT_R6_COMPACT_REPORT"


@pytest.mark.parametrize("kwargs,reason", [
    ({"campaign": "coupled_qualification_r5_dc4h_parity_20260803T1420Z"}, "OBSOLETE_CAMPAIGN"),
    ({"current": 1, "stale": 1}, "STALE_RESULT_PRODUCING_RECEIPTS"),
])
def test_refuses_obsolete_or_stale_report(tmp_path, monkeypatch, kwargs, reason):
    hashes = {"executor_sha256": "current"}
    monkeypatch.setattr(promote, "current_engine_hashes", lambda _root: hashes)
    incoming_json, incoming_md = _pair(tmp_path, hashes, **kwargs)
    with pytest.raises(ValueError, match=reason):
        promote.validate(tmp_path, incoming_json, incoming_md)


def test_refuses_older_report(tmp_path, monkeypatch):
    hashes = {"executor_sha256": "current"}
    monkeypatch.setattr(promote, "current_engine_hashes", lambda _root: hashes)
    now = datetime.now(timezone.utc)
    incoming_json, incoming_md = _pair(tmp_path, hashes, generated=now.isoformat())
    canonical = tmp_path / "canonical.json"
    canonical.write_text(json.dumps({"generated_utc": (now + timedelta(seconds=1)).isoformat()}))
    with pytest.raises(ValueError, match="REPORT_NOT_NEWER"):
        promote.validate(tmp_path, incoming_json, incoming_md, canonical)
