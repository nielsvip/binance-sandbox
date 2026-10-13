"""V17 self-improvement wiring pins (bible §93): every learning output now has a consumer."""
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def test_invention_reads_real_autopsy_keys(tmp_path):
    from tools import v15_invention_loop as IL
    (tmp_path / "X_SHORT_autopsy.json").write_text(json.dumps({"symside": "X_SHORT", "missing_entry_filters": [{"feature": "wt_15m", "op": ">", "thr": 60, "at": "entry", "bad_removed": 4}], "missing_exit_confirmations": [{"feature": "k_1h", "op": "<", "thr": 20, "at": "exit"}]}))
    obs = IL.scan_autopsy([str(tmp_path)])
    assert len(obs) == 2
    assert all("GENERIC" not in o["sig"][2] for o in obs), [o["sig"] for o in obs]
    assert obs[0]["score"] >= 4


def test_invention_default_progress_dirs(tmp_path):
    from tools import v15_invention_loop as IL
    (tmp_path / "v15_run99" / "progress").mkdir(parents=True)
    dirs = IL._progress_dirs(str(tmp_path), str(tmp_path))
    assert dirs[0].endswith("v15_run99/progress")
    assert IL._progress_dirs(str(tmp_path), str(tmp_path), "/x") == ["/x"]
    src = (ROOT / "tools" / "v15_invention_loop.py").read_text()
    assert '"v15_diag_repair", "*.json"' in src


def test_endgame_knowledge_persistent_and_counts_negatives(tmp_path, monkeypatch):
    monkeypatch.setenv("V15_KNOWLEDGE_DIR", str(tmp_path))
    import v15_pilot as P
    p = P._endgame_knowledge_path("ABCUSDT_SHORT")
    assert p.parent == tmp_path and p.name == "endgame_knowledge_CRYPTO_SHORT.json"
    k = P._endgame_merge_knowledge({}, [("TRY:F1", -0.5)], [("F2", -0.1), ("F3", 0.4)], [], 0.0, False, True)
    assert k["keys"]["F2"]["pos"] == 0 and k["keys"]["F3"]["pos"] == 1 and k["keys"]["TRY:F1"]["sum"] == -0.5
    assert "PROGRESS_DIR / \"endgame_knowledge.json\"" not in (ROOT / "v15_pilot.py").read_text()


def test_profile_ignores_route_labels():
    src = (ROOT / "tools" / "v15_switch_causal_profile.py").read_text()
    assert '"ROUTE", "RECHECK"' in src


def test_missed_trend_priors_folded_and_chain_step():
    src = (ROOT / "tools" / "v15_avg_delta_rebuild.py").read_text()
    assert "_missed_trend_priors.json" in src and "V15_MISSED_TREND_PRIORS" in src
    ch = (ROOT / "tools" / "v15_daily_chain_s1.sh").read_text()
    assert "tools/v15_route_round.py merge" in ch and "v15_invention_loop.py mine --write" in ch and "data/route_knowledge/CRYPTO_LONG.json" in ch
    sch = (ROOT / "tools" / "v15_fleet_scheduler.py").read_text()
    assert "V15_SCOUT=1 V15_ROUTE=1" in sch


def test_route_report_cli(tmp_path):
    from tools import v15_route_round as R
    R.save_board("CRYPTO_LONG", {"symside": "A_LONG", "key": "k", "moves": {"ENTRY_LH_TRIGGER_ENABLED=True": {"step": "S6_ROWLESS", "delta": 0.5, "promoted": True}}, "unmask": {}, "masked_by": {}, "lift": 0.5}, tmp_path)
    R.save_board("CRYPTO_LONG", {"symside": "B_LONG", "key": "k", "moves": {"ENTRY_LH_TRIGGER_ENABLED=True": {"step": "S6_ROWLESS", "delta": 0.3, "promoted": True}}, "unmask": {}, "masked_by": {}, "lift": 0.3}, tmp_path)
    env = dict(os.environ, V15_KNOWLEDGE_DIR=str(tmp_path))
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "v15_route_round.py"), "report", "--out", str(tmp_path / "r.md")], capture_output=True, text=True, env=env, timeout=60)
    assert out.returncode == 0, out.stderr
    txt = (tmp_path / "r.md").read_text()
    assert "SWITCH-ADD PLAN" in txt and "ENTRY_LH_TRIGGER_ENABLED=True" in txt.split("SWITCH-ADD PLAN")[1]
