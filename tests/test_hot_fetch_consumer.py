"""2026-10-24 stale scan ages (RLC 1588s, ADA 1368s) with second-fresh Redis:
consumer fetch must survive a loaded box. get_hot_state pins: Redis wait
2.5s (1.0s starved), bridge IPC off-loop with loud warning, and a throttled
HOT_MISS line whenever the answer is served cold — silent rot is banned.
Publish pins: hot key TTL bridges the ~201/342 compute rotation (90s TTL
deleted keys between computes and forced cold fallbacks)."""
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


def test_hot_ttl_bridges_compute_rotation():
    md = (Path(__file__).resolve().parent.parent / "ez_market_data.py").read_text()
    live = [ln for ln in md.splitlines() if "pipe.set(f\"hot_metrics" in ln and not ln.lstrip().startswith("#")]
    m = re.findall(r"ex=(\d+)\)", "\n".join(live))
    assert m and all(int(x) >= 300 for x in m)
