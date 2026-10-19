"""A held position is evaluated before a flat key, and results still come back in the caller's order.

USER 2026-10-18 "THEY ENTER BUT THEY DO NOT EXIT OR NOT IN TIME". Measured that morning: the men twin evaluated 45-73 keys
per 15-minute bucket against 106+ tradeable keys, exit decisions landed a median of 8.0 min after their bar closed, and
men:ENJUSDT_LONG (986 units, -26% ROE, near liquidation) had no decision since 10-14 because it queued behind flat keys.
"""

import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import vec_dispatch as VD  # noqa: E402


def _set_holders(monkey_set):
    VD._HOLDERS["at"] = 1e18  # freeze the cache so the test never reads the live file
    VD._HOLDERS["set"] = frozenset(x.upper() for x in monkey_set)


def test_held_keys_run_first_and_results_keep_input_order():
    _set_holders({"ENJUSDT_LONG", "BTCUSDC_LONG"})
    started = []

    def mk(name):
        async def fn():
            started.append(name)
            await asyncio.sleep(0.01)
            return f"res:{name}"

        return fn

    calls = [("men:FLATA_SHORT", mk("men:FLATA_SHORT")), ("men:ENJUSDT_LONG", mk("men:ENJUSDT_LONG")),
             ("men:FLATB_LONG", mk("men:FLATB_LONG")), ("ang:BTCUSDC_LONG", mk("ang:BTCUSDC_LONG"))]
    out = asyncio.run(VD.run_all(calls, limit=1, timeout_s=5.0))
    # the two held keys were evaluated before either flat key
    assert started[:2] == ["men:ENJUSDT_LONG", "ang:BTCUSDC_LONG"], started
    # and every result is still on its own caller index
    assert out == [(True, "res:men:FLATA_SHORT"), (True, "res:men:ENJUSDT_LONG"),
                   (True, "res:men:FLATB_LONG"), (True, "res:ang:BTCUSDC_LONG")]


def test_no_holders_keeps_original_order():
    _set_holders(set())
    order = []

    def mk(name):
        async def fn():
            order.append(name)
            return name

        return fn

    calls = [(f"men:K{i}_LONG", mk(f"men:K{i}_LONG")) for i in range(4)]
    out = asyncio.run(VD.run_all(calls, limit=1, timeout_s=5.0))
    assert order == [f"men:K{i}_LONG" for i in range(4)]
    assert [r[1] for r in out] == [f"men:K{i}_LONG" for i in range(4)]


def test_no_timeout_waits_for_slow_keys():
    """USER 2026-10-19: entries NEVER time out — maker 120-150s then market fallback runs inside the worker."""
    _set_holders(set())

    async def slow():
        await asyncio.sleep(0.3)
        return "late-fill"

    out = asyncio.run(VD.run_all([("men:SLOW_LONG", slow)], limit=1, timeout_s=None))
    assert out == [(True, "late-fill")]


def test_numeric_timeout_still_timeboxes():
    _set_holders(set())

    async def hang():
        await asyncio.sleep(5.0)

    out = asyncio.run(VD.run_all([("men:HANG_SHORT", hang)], limit=1, timeout_s=0.1))
    assert out[0][0] is False and "BLOCKED_DISPATCH_TIMEOUT" in str(out[0][1])


def test_kill_switch_restores_plain_order(monkeypatch):
    _set_holders({"ENJUSDT_LONG"})
    monkeypatch.setenv("VEC_DISPATCH_HOLDERS_FIRST", "0")
    order = []

    def mk(name):
        async def fn():
            order.append(name)
            return name

        return fn

    calls = [("men:FLATA_SHORT", mk("men:FLATA_SHORT")), ("men:ENJUSDT_LONG", mk("men:ENJUSDT_LONG"))]
    asyncio.run(VD.run_all(calls, limit=1, timeout_s=5.0))
    assert order == ["men:FLATA_SHORT", "men:ENJUSDT_LONG"]


def test_timeout_of_a_flat_key_does_not_drop_a_held_key_result():
    _set_holders({"ENJUSDT_LONG"})

    async def hang():
        await asyncio.sleep(5.0)

    async def quick():
        return "exit-decided"

    calls = [("men:HANGER_SHORT", hang), ("men:ENJUSDT_LONG", quick)]
    out = asyncio.run(VD.run_all(calls, limit=2, timeout_s=0.2))
    assert out[1] == (True, "exit-decided")
    assert out[0][0] is False and "BLOCKED_DISPATCH_TIMEOUT" in str(out[0][1])


def test_entry_label_evaluates_flat_keys_before_held_keys():
    """ENTRY sweep: a held key unconditionally skips (SKIP_LIVE_HOLDS) after a full serial eval, so flat keys go first.

    USER 2026-10-19 unlock-and-fix: ang/men/fin burned tens of thousands of BLOCKED_DISPATCH_TIMEOUT on ENTRY while held
    keys spent the serial offloop budget ahead of real entries. Exits keep held-first (test above); kill switch still wins.
    """
    _set_holders({"ENJUSDT_LONG"})
    started = []

    def mk(name):
        async def fn():
            started.append(name)
            await asyncio.sleep(0.01)
            return f"res:{name}"

        return fn

    calls = [("men:ENJUSDT_LONG", mk("men:ENJUSDT_LONG")), ("men:FLATA_SHORT", mk("men:FLATA_SHORT")),
             ("men:FLATB_LONG", mk("men:FLATB_LONG"))]
    out = asyncio.run(VD.run_all(calls, limit=1, timeout_s=5.0, label="VEC_EXACT_ENTRY"))
    assert started[:2] == ["men:FLATA_SHORT", "men:FLATB_LONG"], started
    assert [r[1] for r in out] == ["res:men:ENJUSDT_LONG", "res:men:FLATA_SHORT", "res:men:FLATB_LONG"]


def test_exit_label_keeps_held_first():
    _set_holders({"ENJUSDT_LONG"})
    started = []

    def mk(name):
        async def fn():
            started.append(name)
            await asyncio.sleep(0.01)
            return f"res:{name}"

        return fn

    calls = [("men:FLATA_SHORT", mk("men:FLATA_SHORT")), ("men:ENJUSDT_LONG", mk("men:ENJUSDT_LONG"))]
    asyncio.run(VD.run_all(calls, limit=1, timeout_s=5.0, label="VEC_EXACT_EXIT"))
    assert started[0] == "men:ENJUSDT_LONG", started


def test_holders_file_shape_is_what_the_writer_produces():
    """The registry this reads is data/open_position_sym_sides.json: {"at":…, "sym_sides":[…]}."""
    p = ROOT / "data" / "open_position_sym_sides.json"
    if not p.exists():
        return
    d = json.loads(p.read_text())
    assert isinstance(d.get("sym_sides"), list) and d["sym_sides"], "position registry empty or malformed"
