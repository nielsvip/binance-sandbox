"""Close-age staleness gate (USER 2026-10-25): bars are labeled by OPEN and are
current until bar_ts + 900. Ageing from the label marked every state STALE
~5 min after its NPZ tail landed (actionable=False), so the Mac consumer
skipped it — closes enforced, opens refused, live bled vs boards.
"""
from tools.vec_live_executor import BAR_SEC, STALE_SEC, is_bar_stale

T = 1792897200.0  # 2026-10-25T03:00:00Z


def test_fresh_bar_not_stale():
    # The ZENUSDT_SHORT case: open-age 20.4 min but closed only 5.4 min ago.
    assert is_bar_stale(T, T + 20.4 * 60) is False


def test_just_closed_bar_not_stale():
    assert is_bar_stale(T, T + BAR_SEC) is False


def test_old_closed_bar_stale():
    assert is_bar_stale(T, T + BAR_SEC + STALE_SEC + 1) is True


def test_boundary_uses_close_not_open():
    # Open-age far past STALE_SEC but close-age inside it -> fresh.
    assert is_bar_stale(T, T + STALE_SEC + BAR_SEC - 1) is False
    assert is_bar_stale(T, T + STALE_SEC + BAR_SEC + 1) is True


def test_bad_input_fails_closed():
    assert is_bar_stale(None, T) is True
    assert is_bar_stale("x", T) is True
