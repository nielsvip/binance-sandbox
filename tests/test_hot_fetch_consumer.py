"""2026-10-24 stale scan ages (RLC 1588s, ADA 1368s) with second-fresh Redis:
consumer fetch must survive a loaded box. get_hot_state pins: Redis wait
2.5s (1.0s starved), bridge IPC off-loop with loud warning, and a throttled
HOT_MISS line whenever the answer is served cold — silent rot is banned."""
import re
from pathlib import Path

SRC = (Path(__file__).resolve().parent.parent / "ez_positions_quick.py").read_text()


def test_redis_wait_survives_loaded_box():
    m = re.search(r"client\.get\(f\"hot_metrics:\{clean_sym\}\"\), timeout=([\d.]+)", SRC)
    assert m and float(m.group(1)) >= 2.5


def test_bridge_off_loop_with_loud_errors():
    assert "asyncio.to_thread(self.shared_proxy.get_symbol, clean_sym)" in SRC
    assert "[HotState] get_symbol" in SRC


def test_cold_fallback_is_loud_not_silent():
    assert "[HOT_MISS]" in SRC
    assert "_hot_miss_log" in SRC
