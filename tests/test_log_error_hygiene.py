"""Log-error hygiene (2026-10-22): background tasks must never die as
``Task exception was never retrieved`` in the live stderr logs.

Covers the 2026-10-21 cron-spam bundle:
- fire-and-forget ``futures_cancel_order`` dying with -2011 (order already gone)
  -> now routed via ``ez_manage._cancel_order_background`` (-2011 at debug).
- loop-level backstop ``ez_manage._ez_loop_exception_handler`` demotes the three
  known-benign unretrieved classes (redis tunnel TimeoutError/ConnectionError,
  -2011 order-gone, tradier shared-listing TimeoutError) to counted warnings and
  keeps every other unretrieved error loud.
- ``ez_share_ind`` basicConfig no longer hijacks the root logger on import
  (the [SHARED_MEM] tag + doubled stderr lines in every importing process).
- ``env_gpg`` stays silent when the gpg binary is absent.
- the loop-lag watchdog no longer leaves a faulthandler dump armed across
  task cancellation.
"""
import ast
import asyncio
import gc
import logging
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ez_manage as EZ


def _strict_loop():
    seen = []

    async def _run(coro_factory):
        loop = asyncio.get_running_loop()
        loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
        task = asyncio.ensure_future(coro_factory())
        await asyncio.sleep(0.5)
        assert task.done(), "background task did not finish"
        return task

    return seen, _run


def test_bare_fire_and_forget_shape_produces_unretrieved():
    """Documents the original bug shape: a dropped task whose coroutine raises
    surfaces as 'Task exception was never retrieved' (asyncio, not our code)."""

    async def _boom():
        raise RuntimeError("-2011 Unknown order sent.")

    async def _main():
        loop = asyncio.get_running_loop()
        seen = []
        loop.set_exception_handler(lambda _l, ctx: seen.append(ctx))
        asyncio.ensure_future(_boom())
        await asyncio.sleep(0.2)
        gc.collect()
        await asyncio.sleep(0.1)
        return seen

    seen = asyncio.run(_main())
    assert any("never retrieved" in str(c.get("message", "")) for c in seen)


def test_cancel_order_background_absorbs_2011():
    """-2011 (already-gone order) completes quietly; other errors warn; nothing leaks."""

    class _GoneClient:
        def futures_cancel_order(self, **kwargs):
            raise RuntimeError("{'code':-2011,'msg':'Unknown order sent.'}")

    class _BrokenClient:
        def futures_cancel_order(self, **kwargs):
            raise RuntimeError("boom")

    seen, _run = _strict_loop()

    async def _scenario():
        t1 = await _run(lambda: EZ._cancel_order_background(_GoneClient(), "BTCUSDC", 1, "test"))
        assert t1.result() is None
        t2 = await _run(lambda: EZ._cancel_order_background(_BrokenClient(), "BTCUSDC", 2, "test"))
        assert t2.result() is None

    async def _outer():
        await _scenario()
        gc.collect()
        await asyncio.sleep(0.1)

    asyncio.run(_outer())
    assert seen == []


def test_no_bare_fire_and_forget_cancel_in_ez_manage():
    """No create_task(asyncio.to_thread(...)) remains in ez_manage.py — every
    background cancel routes via _cancel_order_background (regression pin)."""
    root = Path(__file__).resolve().parent.parent
    tree = ast.parse((root / "ez_manage.py").read_text(), filename="ez_manage.py")
    bare = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name != "create_task" or not node.args:
            continue
        inner = node.args[0]
        if isinstance(inner, ast.Call):
            ifunc = inner.func
            iname = ifunc.attr if isinstance(ifunc, ast.Attribute) else getattr(ifunc, "id", "")
            if iname == "to_thread":
                bare.append(node.lineno)
    assert bare == [], f"bare fire-and-forget to_thread at lines {bare}"


class _FakeLoop:
    def __init__(self):
        self.defaults = []

    def default_exception_handler(self, context):
        self.defaults.append(context)


def _ctx(coro_qualname, exc):
    return {
        "message": "Task exception was never retrieved",
        "future": SimpleNamespace(get_coro=lambda: SimpleNamespace(__qualname__=coro_qualname)),
        "exception": exc,
    }


class BinanceAPIException(Exception):
    pass


def test_loop_handler_demotes_known_benign():
    EZ._LOOP_EXC_STATS.clear()
    loop = _FakeLoop()
    EZ._ez_loop_exception_handler(loop, _ctx("Redis.execute_command", TimeoutError("Timeout reading from 127.0.0.1:6380")))
    EZ._ez_loop_exception_handler(loop, _ctx("Redis.execute_command", ConnectionError("refused")))
    EZ._ez_loop_exception_handler(loop, _ctx("to_thread", BinanceAPIException("{'code':-2011}")))
    EZ._ez_loop_exception_handler(loop, _ctx("TradierDedupeGuard._fetch_orders", TimeoutError()))
    assert loop.defaults == []
    assert sum(EZ._LOOP_EXC_STATS.values()) == 4


def test_loop_handler_keeps_unknown_loud():
    loop = _FakeLoop()
    EZ._ez_loop_exception_handler(loop, _ctx("some_worker", ValueError("real bug")))
    assert len(loop.defaults) == 1


def test_share_ind_import_adds_no_root_handlers():
    code = "import logging, ez_share_ind; print(len(logging.getLogger().handlers))"
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent), timeout=60)
    assert out.returncode == 0, out.stderr[-2000:]
    assert out.stdout.strip() == "0", f"root handlers after import: {out.stdout.strip()} {out.stderr[-500:]}"


def test_env_gpg_silent_without_binary(monkeypatch, capsys):
    import shutil

    import env_gpg

    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.delenv("EZ_SKIP_ENV_GPG", raising=False)
    assert env_gpg.load_env_gpg() == 0
    assert capsys.readouterr().err == ""


_ENV_LOADER_DRIVER = """\
import json
import logging

recs = []


class H(logging.Handler):
    def emit(self, r):
        recs.append((r.levelname, r.getMessage()))


logging.getLogger().addHandler(H())
logging.getLogger().setLevel(logging.DEBUG)
import utils

ok1 = utils.load_environment_from_gpg(None)
ok2 = utils.load_environment_from_gpg(None)
loud = [m for lv, m in recs if lv in ("INFO", "WARNING", "ERROR")]
print(json.dumps({"ok": [ok1, ok2], "loud": loud}))
"""


def _run_env_loader(path_value, tmp_path):
    """Subprocess driver: run utils.load_environment_from_gpg twice, report the
    INFO+ log lines. Isolated process because the loader mutates os.environ and
    the ENV_LOADED global."""
    import os

    driver = tmp_path / "env_loader_driver.py"
    driver.write_text(_ENV_LOADER_DRIVER)
    env = dict(os.environ)
    env["PATH"] = path_value
    repo_root = str(Path(__file__).resolve().parent.parent)
    env["PYTHONPATH"] = repo_root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
    out = subprocess.run([sys.executable, str(driver)], capture_output=True, text=True, cwd=str(Path(__file__).resolve().parent.parent), timeout=120, env=env)
    assert out.returncode == 0, out.stderr[-2000:]
    import json as _json

    return _json.loads(out.stdout.strip())


def test_env_loader_quiet_without_gpg(tmp_path):
    """Cron PATH (no gpg): one line naming the plain-.env win, no GPG chatter."""
    res = _run_env_loader("/usr/bin:/bin", tmp_path)
    assert res["ok"] == [True, True]
    assert len(res["loud"]) == 1, res["loud"]
    assert "plain .env" in res["loud"][0]
    assert not any("GPG decryption failed" in m or "Searching for" in m or "Attempting to load" in m for m in res["loud"])


def test_env_loader_quiet_with_gpg(tmp_path):
    """Login PATH (gpg present): one line naming the .env.gpg win."""
    res = _run_env_loader("/opt/homebrew/bin:/usr/bin:/bin", tmp_path)
    assert res["ok"] == [True, True]
    assert len(res["loud"]) == 1, res["loud"]
    assert ".env.gpg" in res["loud"][0]


def test_watchdog_cancels_pending_dump(monkeypatch):
    """Cancelling the watchdog must disarm its faulthandler dump (no post-shutdown dump)."""
    import faulthandler
    import threading

    armed = []
    fired = []

    def _fake_schedule(timeout, repeat=False):
        timer = threading.Timer(0.2, lambda: fired.append(1))
        armed.append(timer)
        timer.start()

    def _fake_cancel():
        for timer in armed:
            timer.cancel()

    monkeypatch.setattr(faulthandler, "dump_traceback_later", _fake_schedule)
    monkeypatch.setattr(faulthandler, "cancel_dump_traceback_later", _fake_cancel)

    async def _main():
        task = asyncio.ensure_future(EZ._ez_loop_lag_watchdog())
        await asyncio.sleep(0.1)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await asyncio.sleep(0.5)

    asyncio.run(_main())
    assert armed, "watchdog never armed a dump"
    assert fired == [], "pending dump fired after watchdog cancellation"
