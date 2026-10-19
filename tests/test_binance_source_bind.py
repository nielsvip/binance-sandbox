"""Source-bind Binance traffic to a clean egress IP (USER 2026-10-19: never stop trading).

S1 has 3 egress IPs; only 157.180.125.52 was Binance-banned (-1003) while 5.75.211.216 and 49.13.39.233 answer 200.
The default route sources everything from the banned IP and route changes need CAP_NET_ADMIN (denied), so engines
bind Binance-bound sockets at the only chokepoint every order path crosses (wrap_binance_client), gated on the
BINANCE_LOCAL_IP env the S1 live unit already sets. Mac sets nothing: zero behavior change there.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import order_dedupe_guard as G  # noqa: E402


def _reset(orig):
    import socket

    socket.create_connection = orig
    G._SOURCE_BIND_DONE["on"] = False


def test_invalid_ip_refuses_without_touching_socket(monkeypatch):
    import socket

    seen = []
    monkeypatch.setattr(socket, "create_connection", lambda *a, **k: seen.append((a, k)))
    assert G._install_binance_source_bind("not-an-ip") is False
    assert G._install_binance_source_bind("") is False
    assert G._SOURCE_BIND_DONE["on"] is False


def test_binance_gets_source_others_untouched(monkeypatch):
    import socket

    calls = []

    def fake_orig(address, *args, **kwargs):
        calls.append((address, kwargs))
        return object()

    _orig = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", fake_orig)
    assert G._install_binance_source_bind("5.75.211.216") is True
    socket.create_connection(("fapi.binance.com", 443))
    socket.create_connection(("127.0.0.1", 6379))
    socket.create_connection(("data-stream.binance.com", 9443))
    assert calls[0][1].get("source_address") == ("5.75.211.216", 0)
    assert "source_address" not in calls[1][1]
    assert calls[2][1].get("source_address") == ("5.75.211.216", 0)
    _reset(_orig)


def test_explicit_source_address_wins(monkeypatch):
    import socket

    calls = []

    def fake_orig(address, *args, **kwargs):
        calls.append((address, kwargs))
        return object()

    _orig = socket.create_connection
    monkeypatch.setattr(socket, "create_connection", fake_orig)
    assert G._install_binance_source_bind("5.75.211.216") is True
    socket.create_connection(("fapi.binance.com", 443), source_address=("1.2.3.4", 0))
    assert calls[0][1].get("source_address") == ("1.2.3.4", 0)
    _reset(_orig)
