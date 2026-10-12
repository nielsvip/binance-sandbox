"""Price-guard collateral (USER 2026-10-12: fake prices can never happen anymore).

Nails the kline-anchored guard in tools/forward_parity/price_guard.py, wired
inside load_history so EVERY offline PnL consumer (live_trade_returns walkers,
ltf_flip attribution) is covered: glitch prices quarantine to None/0.0 with a
px_flag, values without kline truth pass through as UNCHECKED (never destroyed).
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.forward_parity import price_guard as pg  # noqa: E402


def _etc_bar():
    p = ROOT / "klines_cache" / "ETCUSDT_15m.json"
    if not p.exists():
        pytest.skip("no ETC klines on this host")
    import datetime as dt
    rows = json.loads(p.read_text())
    rows = rows if isinstance(rows, list) else rows.get("klines", [])
    r = rows[len(rows) // 2]
    t = dt.datetime.fromisoformat(str(r["timestamp"]).replace("Z", "+00:00")).timestamp()
    return t, float(r["close"]), float(r["low"]), float(r["high"])


def test_guard_quarantines_decimal_glitch():
    t, cl, lo, hi = _etc_bar()
    assert pg.check("ETCUSDT", t, cl, False) == "OK"
    assert pg.check("ETCUSDT", t, cl / 100.0, False) == "FAKE"  # the 0.083-shape glitch
    assert pg.check("ETCUSDT", t, hi, False) == "OK"  # wick fill inside bar range passes


def test_guard_unchecked_without_truth_preserves_value():
    assert pg.check("NOPEUSDT", 1791500000.0, 123.45, False) == "UNCHECKED"
    rows = [{"t": 1791500000.0, "price": 123.45, "mkt_px": 123.45}]
    assert pg.guard_rows("NOPEUSDT", rows, False) == 0
    assert rows[0]["price"] == 123.45 and rows[0]["mkt_px"] == 123.45


def test_sanitize_snapshot_anchors_to_fill():
    out, rep = pg.sanitize_snapshot(8.3637, {"price": 0.08338062, "k_3m": 50})
    assert rep is True and out["price"] == 8.3637
    assert out["price_raw_glitch"] == 0.08338062 and out["px_guard"] == "SNAPSHOT_REPLACED_WITH_FILL"
    out, rep = pg.sanitize_snapshot(8.3637, {"price": 8.36})
    assert rep is False and out["price"] == 8.36
    assert pg.sanitize_snapshot(8.36, {})[1] is False
    assert pg.sanitize_snapshot(0, {"price": 5})[1] is False
    assert pg.sanitize_snapshot(8.36, None) == (None, False)


def test_load_history_funnel_quarantines_etc():
    from tools.forward_parity.live_vs_vec import load_history
    if not (ROOT / "data" / "history" / "men" / "ETCUSDT_SHORT.jsonl").exists():
        pytest.skip("no men:ETCUSDT_SHORT history on this host")
    rows = load_history("men", "ETCUSDT_SHORT")
    augs = [h for h in rows if h["type"] == "AUGMENT"]
    assert augs and all("px_flag" in h for h in augs)
    if any("UNCHECKED" in h["px_flag"] for h in augs):
        pytest.skip("ETC rows outside kline coverage on this host")
    for h in augs:
        assert h["mkt_px"] is None, h  # 0.083 glitch quarantined
        assert h["price"] > 8.0, h  # 8.36 fill stands
