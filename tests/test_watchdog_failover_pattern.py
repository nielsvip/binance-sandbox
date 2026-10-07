"""Failover-pattern regression (USER 2026-10-07 incident: men unmanaged 00:01-01:14Z, S1 doubles 01:11-01:55Z).

Static pins + real regex behavior. No repo imports (no openpyxl on Mac).
"""
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WD = (ROOT / "run_with_watchdog.sh").read_text()
COPILOT = (ROOT / "ez_copilot.py").read_text()
HEARTBEAT = (ROOT / "mac_live_heartbeat.py").read_text()

OLD_PATTERN = "ez_manage.py.*men"
NEW_PATTERN = "python.*ez_manage.py.*me[n]"

WORKER_U = "/opt/anaconda3/envs/binance_env/bin/python -u ez_manage.py --account men"
WORKER_NOU = "/home/niels/.conda/envs/binance_env/bin/python ez_manage.py --account men"
SUPERVISOR = "bash /home/niels/binance/run_with_watchdog.sh ez_manage.py --account men"
PROBE = "bash -c pgrep -f \"python.*ez_manage.py.*me[n]\" | head"
COPILOT_PROC = "/home/niels/.conda/envs/binance_env/bin/python -u /home/niels/binance/ez_copilot.py"


def test_old_pattern_matched_supervisor_bug_demo():
    assert re.search(OLD_PATTERN, SUPERVISOR)


def test_new_pattern_matches_workers_only():
    for cmd in (WORKER_U, WORKER_NOU):
        assert re.search(NEW_PATTERN, cmd), cmd
    for cmd in (SUPERVISOR, PROBE, COPILOT_PROC):
        assert not re.search(NEW_PATTERN, cmd), cmd


def test_watchdog_failover_pins():
    assert "${arg%?}[$a_last]" in WD
    assert "python.*$SCRIPT" in WD
    assert "/home/niels/binance/.venv/bin/python" in WD
    assert "/home/niels/.conda/envs/binance_env/bin/python" in WD


def test_copilot_trader_gate_pins():
    assert "_s1_trading_forbidden" in COPILOT
    assert "S1_LIVE_TRADING_FORBIDDEN" in COPILOT
    assert '("ez_manage.py", "tradier_manage.py")' in COPILOT
    assert "tradier_positions.py" not in COPILOT.split("_S1_FORBIDDEN_TRADERS")[1].split("\n")[0]


def test_heartbeat_bracket_pins():
    assert "{acct[:-1]}[{acct[-1]}]" in HEARTBEAT
    assert 'f"python -u ez_manage.py --account {acct}")' not in HEARTBEAT
