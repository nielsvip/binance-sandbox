"""NEVER-AGAIN 2026-10-13 (123456 incident): tests must never write to live
log files. A pytest run of the tradier backstop suite wrote a fixture order id
(123456, 'broker rejected: no position') into LIVE ~/logs, indistinguishable
from real broker events. Both managers now redirect every file handler to a
temp dir whenever pytest is present at import. These tests pin that routing —
they assert on handler paths, not content (account filters may drop records
without matching context, which is orthogonal)."""
import logging
import os
import tempfile
from types import SimpleNamespace


def _live_dirs():
    from pathlib import Path
    return {str(Path.home() / "logs")}


def _file_targets(logger_obj):
    out = []
    for h in getattr(logger_obj, "handlers", []):
        fn = getattr(h, "baseFilename", None)
        if fn:
            out.append(fn)
    return out


def _assert_no_live_targets(logger_obj, who):
    targets = _file_targets(logger_obj)
    assert targets, f"expected file handlers on {who} logger"
    # At least one handler must be ours under the temp dir (the redirect ran).
    tmp = os.path.realpath(tempfile.gettempdir())
    assert any(os.path.realpath(t).startswith(tmp) for t in targets), targets
    # And NONE may point at live log dirs (/dev/null + pytest capture
    # handlers are harmless and explicitly allowed).
    for t in targets:
        for live in _live_dirs():
            assert not os.path.realpath(t).startswith(live + os.sep), t


def test_tradier_test_run_never_touches_live_logs():
    import tradier_manage as tm
    _assert_no_live_targets(tm.logger, "tradier")


def test_ez_test_run_never_touches_live_logs():
    import ez_manage as em
    _assert_no_live_targets(em.logger, "ez")


def test_positions_test_run_never_touches_live_logs():
    import tradier_positions as tp
    _assert_no_live_targets(tp.logger, "tradier_positions")


def test_positions_redis_writes_carry_ttl():
    import asyncio
    import tradier_positions as tp

    seen = {}

    class FakeRedis:
        async def set(self, key, value, ex=None):
            seen[key] = ex
            return True

        async def publish(self, channel, message):
            return 1

    mgr = object.__new__(tp.TradierPositionManager)
    mgr.account_key = "trb"
    mgr.config = SimpleNamespace(POSITIONS_REDIS_TTL_SEC=300, REDIS_CHANNEL_POSITIONS="ch")
    mgr.redis_manager = FakeRedis()

    class P:
        def to_dict(self):
            return {"qty": 1}

    asyncio.run(mgr.broadcast_to_redis({"trb:A_LONG": P()}, should_log=False))
    assert seen == {"tradier:positions:trb": 300}, seen


def test_positions_redis_manager_set_passes_expiry():
    import asyncio
    import tradier_positions as tp

    got = {}

    class Raw:
        async def set(self, key, value, ex=None):
            got.update(key=key, ex=ex)
            return True

    m = tp.RedisPositionManager(SimpleNamespace())
    m.redis_client = Raw()
    asyncio.run(m.set("k", b"v", ex=123))
    assert got == {"key": "k", "ex": 123}, got
    asyncio.run(m.set("k", b"v"))
    assert got == {"key": "k", "ex": None}, got
