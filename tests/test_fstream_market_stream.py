"""Regression: Binance fstream WS must use /market/stream (not /stream).

Since 2026-05-10 Binance silently drops all data on wss://fstream.binance.com/stream
without /market/ prefix. ez_mark_prices and config.FSTREAM_WS_URL_BASE must route
through /market/stream, otherwise mark-price feed is dead (health = NO message ever).
"""

import pathlib


def test_config_fstream_ws_url_uses_market_prefix():
    from config import Config

    c = Config()
    assert c.FSTREAM_WS_URL_BASE == "wss://fstream.binance.com/market/stream", (
        f"FSTREAM_WS_URL_BASE must be wss://fstream.binance.com/market/stream, got {c.FSTREAM_WS_URL_BASE!r}. "
        "Without /market/ Binance sends 0 msgs (silent throttle)."
    )
    # Guard against accidental revert to bare /stream
    assert "/market/stream" in c.FSTREAM_WS_URL_BASE
    assert c.FSTREAM_WS_URL_BASE.count("/market/") == 1


def test_ez_mark_prices_ws_url_uses_market_prefix():
    p = pathlib.Path("ez_mark_prices.py")
    text = p.read_text(encoding="utf-8")
    # The handler builds url = f"wss://fstream.binance.com/market/stream?streams=..."
    assert "wss://fstream.binance.com/market/stream?streams=" in text, (
        "ez_mark_prices.py must use wss://fstream.binance.com/market/stream?streams= — "
        "bare /stream delivers 0 messages since Binance 2026-05 routing change"
    )
    # Ensure no remaining bare /stream URL (without /market) for fstream
    # Allow the corrected /market/stream only; fail if bare form still exists
    bare = "wss://fstream.binance.com/stream?streams="
    assert bare not in text, f"Stale bare WS URL still present: {bare!r}"


def test_ez_market_data_reference_url_unchanged():
    # Ensure ez_market_data's known-good URL remains correct (canary for drift)
    p = pathlib.Path("ez_market_data.py")
    if not p.exists():
        return
    text = p.read_text(encoding="utf-8")
    assert "wss://fstream.binance.com/market/stream?streams=!markPrice@arr@1s" in text
