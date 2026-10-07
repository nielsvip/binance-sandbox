"""Regression: ez_market_data must synthesize klines from mark prices when WS dead (buf_age>3s).

Previously broadcast_loop did `continue` on buf_age>3.0, leaving it buffered and never
updating indicators. With alt-season volatility, WS gaps of 36s are common and must
synthesize kline close from mark price to keep ez_indicators fresh.
"""
import time


def test_broadcast_loop_synthesizes_on_stale():
    # Verify source contains synthesis path, not early continue
    p = __import__("pathlib").Path("ez_market_data.py")
    src = p.read_text()
    # Old code had "Leave it buffered so a fresh source can replace it." + continue
    # New code should have "synthesizing kline from mark price" and _is_synthetic
    assert "synthesizing kline from mark price" in src, "synthesis warning missing"
    assert "_is_synthetic = True" in src, "_is_synthetic flag missing"
    # Ensure the stale branch does NOT have bare continue (it should proceed to market.update)
    # Find the buf_age >3.0 block and ensure next non-comment after _is_synthetic is market.update
    lines = src.splitlines()
    idx = None
    for i, l in enumerate(lines):
        if "if _buf_age > 3.0:" in l:
            idx = i
            break
    assert idx is not None, "buf_age check missing"
    block = "\n".join(lines[idx:idx+50])
    # The block should NOT end with a bare continue at the same indent as the if
    # It should set _is_synthetic and fall through to market.update
    assert "continue" not in block.split("_is_synthetic = True")[0].split("if _buf_age")[1] or "_is_synthetic" in block, "stale branch still has early continue"
    # Verify market.update is after the stale handling
    assert "self.market.update(sym, buf['p'], buf['t'])" in block or "self.market.update" in "\n".join(lines[idx:idx+80])


def test_stale_still_warns():
    p = __import__("pathlib").Path("ez_market_data.py")
    src = p.read_text()
    assert "buf_age" in src and "WS likely dead" in src
