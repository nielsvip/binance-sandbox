"""Vec-parity matching: slow-pipeline fills (1800s window) + same-reason backstop (3600s)."""
import time
from datetime import datetime, timezone
from tools.forward_parity import live_guardian as lg


def _mk(monkeypatch, tmp_path, log_lines):
    monkeypatch.setattr(lg, "ROOT", tmp_path)
    monkeypatch.setattr(lg, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(lg, "STATE", tmp_path / "state.json")
    monkeypatch.setattr(lg, "REP", tmp_path / "rep")
    monkeypatch.setattr(lg, "JSONL", tmp_path / "guardian.jsonl")
    (tmp_path / "logs").mkdir(parents=True)
    (tmp_path / "logs" / "ez_manage_fin.log").write_text("\n".join(log_lines) + "\n")
    (tmp_path / "logs" / "ez_manage_fin_stderr.log").write_text("")
    return lg.Guardian(dry=True)


def _logline(t, acts):
    s = datetime.fromtimestamp(t, tz=timezone.utc).strftime("10 %H:%M:%S")
    return f"{s} - INFO - [fin] [VEC_EXACT] XRPUSDC_LONG bar={int(t)} k=1 acts={acts} eval_ms=5"


def _fills(ft, reason="B_KZONE |VEC_EXACT", typ="AUGMENT"):
    return {"fin:XRPUSDC_LONG": [{"t": ft, "type": typ, "qty": 3.8, "price": 1.4, "reason": reason}]}


def test_slow_fill_within_1800_matches(tmp_path, monkeypatch):
    now = time.time()
    g = _mk(monkeypatch, tmp_path, [_logline(now - 900, "[('OPEN', 'B_KZONE')]")])
    out = g.check_vec_parity("fin", _fills(now - 60), now - 6 * 3600)
    assert out["vec_exact_fills_without_decision"] == 0
    assert [a for a in g.alerts if a["kind"] == "FILL_WITHOUT_VEC_DECISION"] == []


def test_reason_backstop_clears_2000s_late_fill(tmp_path, monkeypatch):
    now = time.time()
    g = _mk(monkeypatch, tmp_path, [_logline(now - 2000, "[('OPEN', 'B_KZONE')]")])
    out = g.check_vec_parity("fin", _fills(now - 60), now - 6 * 3600)
    assert out["vec_exact_fills_without_decision"] == 0
    assert out["matched_by_reason"] >= 1


def test_true_orphan_still_flags(tmp_path, monkeypatch):
    now = time.time()
    g = _mk(monkeypatch, tmp_path, [_logline(now - 5000, "[('OPEN', 'B12')]")])
    out = g.check_vec_parity("fin", _fills(now - 60), now - 6 * 3600)
    assert out["vec_exact_fills_without_decision"] == 1
    assert len([a for a in g.alerts if a["kind"] == "FILL_WITHOUT_VEC_DECISION"]) == 1
