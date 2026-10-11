"""Guardian sidecar paths (USER 2026-10-11 fix-as-discovered follow-up): the live daemon runs old rotation-blind code and cannot be safely bounced from here, so a fresh fixed-guardian sweep must be runnable side-by-side — custom --out/--state/--jsonl that never touch the daemon's files. Live-file non-interference is structural (overrides replace every module-const write) and verified manually per run; these tests pin the override plumbing.
"""
import json
from tools.forward_parity import live_guardian as G


def test_defaults_point_at_live_paths():
    g = G.Guardian(dry=True)
    assert g._status == G.STATUS and g._state_p == G.STATE and g._jsonl == G.JSONL


def test_alert_honors_custom_jsonl(tmp_path):
    jp = tmp_path / "a.jsonl"
    g = G.Guardian(dry=True, status_path=tmp_path / "s.json", state_path=tmp_path / "st.json", jsonl_path=jp)
    g.alert("TEST_KIND", "tst:KEY", "evidence-body", "INFO", dedupe_s=0)
    rows = jp.read_text().strip().splitlines()
    assert len(rows) == 1
    rec = json.loads(rows[0])
    assert rec["kind"] == "TEST_KIND" and rec["key"] == "tst:KEY"


def test_sweep_writes_custom_status_and_state(tmp_path):
    sp, stp = tmp_path / "status.json", tmp_path / "state.json"
    g = G.Guardian(dry=True, status_path=sp, state_path=stp, jsonl_path=tmp_path / "a.jsonl")
    summary = g.sweep()
    assert set(summary["accounts"]) >= {"men", "fin", "inf", "flz", "ang"}
    disk = json.loads(sp.read_text())
    assert disk["ts"] == summary["ts"]
    assert "interventions" in json.loads(stp.read_text())
