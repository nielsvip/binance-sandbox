"""IP-ban guard — the -1003 tracker is fed ONLY by genuine Binance -1003 responses,
and the WS-manager start loop must sleep out a ban instead of firing REST
client-init into it (USER 2026-10-23 ban root-cause: 2s client-init retries
through an active ban on all 5 account loops). No network, no live orders."""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import ez_positions_service as eps  # noqa: E402


def _reset_ban():
    eps._GLOBAL_BAN_UNTIL_MS = 0


def test_record_parses_genuine_1003_only():
    prev = eps._GLOBAL_BAN_UNTIL_MS
    try:
        _reset_ban()
        future_ms = int((time.time() + 500) * 1000)
        exc = Exception(f"APIError(code=-1003): Way too many requests; banned until {future_ms}.")
        assert eps._record_ip_ban_from_exc(exc) == future_ms
        assert 490.0 < eps._ban_remaining_seconds() <= 500.0
    finally:
        eps._GLOBAL_BAN_UNTIL_MS = prev


def test_record_ignores_non_ban_errors():
    prev = eps._GLOBAL_BAN_UNTIL_MS
    try:
        _reset_ban()
        assert eps._record_ip_ban_from_exc(Exception("APIError(code=-2015): Invalid API-key")) == 0
        assert eps._record_ip_ban_from_exc(Exception("Timeout")) == 0
        assert eps._record_ip_ban_from_exc(Exception("banned until soon")) == 0
        assert eps._ban_remaining_seconds() == 0.0
    finally:
        eps._GLOBAL_BAN_UNTIL_MS = prev


def test_start_defers_client_init_while_banned():
    """Pin: start() checks the ban tracker BEFORE _init_binance_client_blocking."""
    src = Path(eps.__file__).read_text()
    i_client = src.index("if not self.client:")
    i_init = src.index("self.client = await asyncio.to_thread(self._init_binance_client_blocking)", i_client)
    seg = src[i_client:i_init]
    assert "_ban_remaining_seconds()" in seg and "deferring client init" in seg, seg
