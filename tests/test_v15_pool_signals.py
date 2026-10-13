"""2026-10-13 AMZN_LONG RUN-FAILED: the combo engine's own fork pool was terminated at the end of search(); the workers had
inherited the pilot's SIGTERM handler (4x "[shutdown] signal 15"), the next pilot pool submit failed and pinpoint aborted.
Law: only a signal delivered to the PARENT pid sets the shutdown flag; pool workers die SIG_DFL; the combo engine never forks
its own pool inside the pilot."""
import multiprocessing as mp
import os
import signal
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.environ.setdefault("V15_ALLOW_MAC", "1")

import v15_pilot as P  # noqa: E402
from tools import v17_combo_engine as CE  # noqa: E402

SRC = (ROOT / "v15_pilot.py").read_text()


def _child(init):
    if init is not None:
        init()
    time.sleep(30)


@pytest.fixture
def pilot_handler():
    old_t, old_i = signal.getsignal(signal.SIGTERM), signal.getsignal(signal.SIGINT)
    P._MAIN_PID["v"] = os.getpid()
    P._SHUTDOWN["v"] = False
    signal.signal(signal.SIGTERM, P._sig_shutdown)
    yield
    signal.signal(signal.SIGTERM, old_t)
    signal.signal(signal.SIGINT, old_i)
    P._SHUTDOWN["v"] = False
    P._MAIN_PID["v"] = None


@pytest.mark.parametrize("init", [None, P._pool_worker_sig_default])
def test_worker_teardown_never_trips_parent_flag(pilot_handler, init):
    # init=None: a library pool WITHOUT our initializer (inherits the handler) -> the parent-pid check alone must hold
    procs = [mp.get_context("fork").Process(target=_child, args=(init,)) for _ in range(2)]
    for p in procs:
        p.start()
    time.sleep(0.3)
    for p in procs:
        p.terminate()
    for p in procs:
        p.join(10)
    assert all(p.exitcode == -signal.SIGTERM for p in procs), [p.exitcode for p in procs]  # died the default way
    assert P._SHUTDOWN["v"] is False  # parent flag untouched


def test_parent_signal_sets_flag(pilot_handler):
    os.kill(os.getpid(), signal.SIGTERM)
    time.sleep(0.05)
    assert P._SHUTDOWN["v"] is True


def test_pools_reset_signals_and_combo_uses_pilot_evaluator(monkeypatch):
    assert 'initializer=_pool_worker_sig_default' in SRC and '_MAIN_PID["v"] = os.getpid()' in SRC
    assert 'os.environ["V17_COMBO_IN_PILOT"] = "1"' in SRC and "evaluator=_V17PilotEval()" in SRC
    assert "initializer=_worker_sig_default" in (ROOT / "tools" / "v17_combo_engine.py").read_text()
    monkeypatch.setenv("V17_COMBO_IN_PILOT", "1")
    with pytest.raises(RuntimeError):
        CE.search(None, "X_LONG", {}, budget_s=1, workers=2, evaluator=None)
