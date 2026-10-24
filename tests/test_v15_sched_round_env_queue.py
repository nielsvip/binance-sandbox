import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


S = _load("v15_fleet_scheduler")

PIN = {
    "round": "run36",
    "env": {"V18_CATCHUP": "0", "V15_BOARD_BUDGET": "12000"},
    "queue": [
        {"round": "run37", "env": {"V18_CATCHUP": "0", "V15_BOARD_BUDGET": "0"}},
        {"round": "run38", "env": {"V18_CATCHUP": "0", "V15_BOARD_BUDGET": "12000", "V15_SCHED_WINDOW_DAYS": "60"}},
    ],
}


def _pin(tmp_path):
    p = tmp_path / "v15_round_env.json"
    p.write_text(json.dumps(PIN))
    return p


def test_queue_entries_apply_only_to_their_own_round(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_ROUND_ENV", raising=False)
    p = _pin(tmp_path)
    # GATE_ENV_KEY_RE keeps only the pilot's own V15_* keys inline (pre-existing rule); V18_CATCHUP is read by _round_forces_slow instead
    assert S.round_env_inline("run36", p) == " V15_BOARD_BUDGET=12000"
    assert S.round_env_inline("run37", p) == " V15_BOARD_BUDGET=0"
    assert S.round_env_inline("run38", p) == " V15_BOARD_BUDGET=12000 V15_SCHED_WINDOW_DAYS=60"
    assert S.round_env_inline("run39", p) == ""  # nothing queued -> no pin, plain scheduler defaults
    assert S.round_env_inline(None, p) == ""


def test_slow_forced_reads_the_queue(tmp_path):
    p = _pin(tmp_path)
    assert S._round_forces_slow("run36", p) is True  # V18_CATCHUP=0 pins the slow rule
    assert S._round_forces_slow("run37", p) is True
    assert S._round_forces_slow("run39", p) is False
    assert S._round_forces_slow(None, p) is False


def test_window_hold_only_for_the_60d_round(tmp_path):
    p = _pin(tmp_path)
    assert S._round_window_days("run36", p) == 30 and S._round_window_ok("run36", p)
    assert S._round_window_days("run37", p) == 30 and S._round_window_ok("run37", p)
    assert S._round_window_days("run38", p) == 60 and not S._round_window_ok("run38", p)
    assert S._round_window_days("run99", p) == 30 and S._round_window_ok("run99", p)
    assert S._round_window_days("run36", tmp_path / "missing.json") == 30


def test_legacy_single_round_file_unchanged(tmp_path, monkeypatch):
    monkeypatch.delenv("V15_SCHED_ROUND_ENV", raising=False)
    p = tmp_path / "v15_round_env.json"
    p.write_text(json.dumps({"round": "run33", "env": {"V18_CATCHUP": "1", "V15_X": "7"}}))
    assert S.round_env_inline("run33", p) == " V15_X=7"
    assert S.round_env_inline("run34", p) == ""
    assert S._round_forces_slow("run33", p) is False
    assert S._round_window_ok("run33", p)


def test_launch_guards_carry_the_window_hold():
    src = (ROOT / "tools" / "v15_fleet_scheduler.py").read_text()
    assert "win_ok = _round_window_ok(_round_id_of(cur_pdir))" in src
    assert "or not swap_ok or not disk_ok or not win_ok):" in src
    assert "swap_ok and disk_ok and win_ok and new_launched < args.max_launch" in src
    assert "ROUND-WINDOW HOLD" in src
