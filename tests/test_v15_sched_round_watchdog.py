"""USER 2026-10-15 (06.1): round watchdog ignores UNREACHABLE + drained hosts.

The test executes the exact python check shipped inside tools/v15_round_watchdog.sh
(extracted between PY-CHECK-START / PY-CHECK-END, stdin = last-3 tick lines) so the
test pins the production logic, not a copy of it.
"""

import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent


def _snippet():
    txt = (ROOT / "tools" / "v15_round_watchdog.sh").read_text()
    return txt.split("# PY-CHECK-START")[1].split("# PY-CHECK-END")[0]


def _run(ticks, drain_hosts=(), env_drain="", workdir=None):
    d = pathlib.Path(workdir) if workdir else None
    dd = d / "data" if d else None
    if dd:
        dd.mkdir(parents=True, exist_ok=True)
        (dd / "fleet_drain_hosts.json").write_text(json.dumps({"hosts": sorted(drain_hosts)}))
    env = dict(os.environ, V15_SCHED_DRAIN_HOSTS=env_drain)
    p = subprocess.run([sys.executable, "-c", _snippet()], input="\n".join(json.dumps(t) for t in ticks),
                       capture_output=True, text=True, cwd=str(d or ROOT), env=env, timeout=60)
    assert p.returncode == 0, p.stderr
    return int(p.stdout.strip())


def _tick(hosts, launched=()):
    return {"now": "2026-10-15T00:00:00Z", "hosts": hosts, "launched": list(launched)}


def _idle():
    return {"idle_reason": "no eligible ready symbol for this host", "slots": "0/4"}


def test_ignores_unreachable_and_drained(tmp_path):
    ticks = [_tick({"s1": _idle(), "s2": _idle(), "s5": "UNREACHABLE", "s6": "UNREACHABLE"}) for _ in range(3)]
    assert _run(ticks, drain_hosts=("s5", "s6"), workdir=tmp_path) == 3


def test_all_drained_is_never_a_strike(tmp_path):
    ticks = [_tick({"s5": "UNREACHABLE", "s6": {"draining": True}}) for _ in range(3)]
    assert _run(ticks, drain_hosts=("s5", "s6"), workdir=tmp_path) == 0


def test_real_blocker_still_vetoes(tmp_path):
    disk = dict(_idle(), idle_reason="disk guard")
    ticks = [_tick({"s1": disk, "s2": _idle(), "s5": "UNREACHABLE"}) for _ in range(3)]
    assert _run(ticks, drain_hosts=("s5",), workdir=tmp_path) == 0


def test_launch_vetoes(tmp_path):
    ticks = [_tick({"s1": _idle(), "s2": _idle()}, launched=["AAPL_LONG|30D"]) for _ in range(3)]
    assert _run(ticks, workdir=tmp_path) == 0


def test_env_drain_list_honoured(tmp_path):
    ticks = [_tick({"s1": _idle(), "s5": "UNREACHABLE"}) for _ in range(3)]
    assert _run(ticks, env_drain="s5", workdir=tmp_path) == 3
