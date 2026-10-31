import asyncio

from backtest_v12_engine import (
    _bt_eta_marker_mark_applied,
    _bt_eta_marker_reset,
    _bt_eta_marker_set,
)


def test_interleaved_eta_markers_are_call_local():
    async def run_pair():
        entered = 0
        both_entered = asyncio.Event()

        async def eta_call(position_key):
            nonlocal entered
            marker = {"position_key": position_key, "execute_now_applied": False}
            token = _bt_eta_marker_set(marker)
            try:
                entered += 1
                if entered == 2:
                    both_entered.set()
                await both_entered.wait()
                await asyncio.sleep(0)
                _bt_eta_marker_mark_applied(position_key)
                return marker
            finally:
                _bt_eta_marker_reset(token)

        return await asyncio.gather(eta_call("trb:AAPL_LONG"), eta_call("trb:MSFT_SHORT"))

    first, second = asyncio.run(run_pair())
    assert first["execute_now_applied"] is True
    assert second["execute_now_applied"] is True
