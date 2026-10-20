import importlib.util
import pathlib
import re

P = pathlib.Path(__file__).resolve().parents[1] / "tools" / "v15_warn_daemon.py"
spec = importlib.util.spec_from_file_location("v15_warn_daemon", P)
W = importlib.util.module_from_spec(spec)
spec.loader.exec_module(W)

LINE = '{"phase": "MUST", "label": "MUST:X=True", "sheet": "ENTRY", "ts": "t"}'


def out(delta_age, prog_age):
    return f"DELTA|{delta_age}|/h/v15_run33/progress/v15_delta_log/SHOP_LONG_jump.jsonl\n{LINE}\nPROG|{prog_age}|/h/p.json\n"


def test_not_stuck_while_delta_log_active():
    act = W.parse_activity(out(14, 300))
    assert act["age_s"] == 14 and act["phase"] == "MUST"
    assert not W.stuck_verdict(act, 120)
    assert "14 s ago" in W.describe_activity({**act, "host": "s1-int"})
    assert "MUST" in W.describe_activity({**act, "host": "s1-int"})


def test_not_stuck_when_only_progress_json_advances():
    assert not W.stuck_verdict(W.parse_activity(out(900, 30)), 120)


def test_stuck_when_idle():
    assert W.stuck_verdict(W.parse_activity(out(500, 700)), 120)


def test_stuck_when_no_files():
    assert W.stuck_verdict(W.parse_activity(""), 120)
    assert "no delta log" in W.describe_activity(None)


def test_herd_branch_gone():
    src = P.read_text()
    assert "herd daemon DOWN" not in src
    assert "restarting herd" not in src
    assert not hasattr(W, "auto_heal")
    assert not re.search(r"herd_ok", src)
