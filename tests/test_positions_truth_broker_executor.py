"""2026-10-14: broker reads never queue behind twin evals (dedicated executor) and offloop waiters park no threads."""
import asyncio
import threading
import time
import positions_truth as PT


def test_broker_read_not_starved_by_offloop_convoy():
    busy = {"max": 0, "now": 0}
    lk = threading.Lock()

    def slow_eval():
        with lk:
            busy["now"] += 1
            busy["max"] = max(busy["max"], busy["now"])
        time.sleep(0.3)
        with lk:
            busy["now"] -= 1
        return 1

    async def main():
        evals = [asyncio.create_task(PT.offloop_serialized(slow_eval)) for _ in range(40)]
        await asyncio.sleep(0.05)
        t0 = time.time()
        r = await PT._broker_io(lambda: (time.sleep(0.1), "positions")[1])
        dt = time.time() - t0
        for t in evals[:3]:
            await t
        for t in evals[3:]:
            t.cancel()
        return r, dt

    r, dt = asyncio.run(main())
    assert r == "positions"
    assert dt < 1.0, f"broker read waited {dt:.2f}s behind the twin-eval convoy"
    assert busy["max"] == 1
