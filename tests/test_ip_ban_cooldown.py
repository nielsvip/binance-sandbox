"""Ban cooldown honors the exchange's real expiry (USER 2026-10-19: never-stop-trading).

Rotation marked every -1003 ban 900s while the exchange ban ran 7h: the banned IP re-entered rotation after 15 min
and re-banned on sight, escalating all day. ban_cooldown_seconds returns max(900, ban_until - now).
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from order_dedupe_guard import ban_cooldown_seconds  # noqa: E402


def test_no_expiry_falls_back_to_minimum():
    assert ban_cooldown_seconds(ValueError("boom")) == 900.0
    assert ban_cooldown_seconds(None) == 900.0
    assert ban_cooldown_seconds("APIError(code=-1003)") == 900.0


def test_future_expiry_returned():
    ms = int((time.time() + 3600) * 1000)
    v = ban_cooldown_seconds(f"APIError(code=-1003): Way too many requests; IP(1.2.3.4) banned until {ms}.")
    assert 3590.0 < v <= 3600.0, v


def test_past_expiry_falls_back_to_minimum():
    ms = int((time.time() - 60) * 1000)
    assert ban_cooldown_seconds(f"banned until {ms}") == 900.0


def test_custom_minimum_respected():
    assert ban_cooldown_seconds("nothing here", minimum=60.0) == 60.0
