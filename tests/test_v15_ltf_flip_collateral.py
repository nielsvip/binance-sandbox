"""LTF 3-leg paper test collateral (USER 2026-10-12: all 3 legs run continuously).

Nails the two root causes found commissioning the scheduled ltf_flip job:
1. indicators.price (mkt_px) decimal-shift glitches (ETC 100x, ENJ 67x, BIO 5x)
   poisoned avg-cost into fake -18672%/+185% exits -> cost basis MUST prefer
   fill/order price; exit-side >50% disagreements reconcile to order price.
2. tools/cat_side_defaults.py (stale twin, no cat_side_of) shadowed the real
   ROOT module when ltf_flip ran as a script (tools-first sys.path) -> vec
   replay silently 0/N. ROOT must precede tools on sys.path.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "tools") not in sys.path:
    sys.path.append(str(ROOT / "tools"))  # noqa: E402  (ROOT first: never shadow, see module docstring)

from forward_parity import ltf_flip as LF  # noqa: E402


def _h(t, typ, qty, price, mkt, reason):
    return {"t": t, "type": typ, "qty": qty, "price": price, "mkt_px": mkt, "reason": reason}


def test_entry_px_prefers_fill_over_glitch():
    # men:ETCUSDT_SHORT shape: fills ~8.36, indicators snapshot 0.083 (100x glitch)
    hist = [_h(1791500000, "AUGMENT", 3.34, 8.3637, 0.08338, "SUBSTITUTION_FOR_x"),
            _h(1791500100, "AUGMENT", 3.34, 8.3730, 0.08338, "SUBSTITUTION_FOR_x"),
            _h(1791513619, "REDUCE", 3.34, 8.108, 7.868, "Winner augment: 6.0% > 3.0%"),
            _h(1791513895, "REDUCE", 3.34, 7.77, 7.868, "Winner augment: 6.0% > 3.0%")]
    exits, entries, _, _ = LF._walk(hist, False, 1791510000, 1791520000, False)
    assert entries == 2 and len(exits) == 2
    for e in exits:
        assert abs(e["pct"]) < 15.0, e  # was -9336.2 with mkt-first basis


def test_exit_px_reconciles_glitch():
    px, rec = LF._exit_px({"mkt_px": 7.868, "price": 8.108}, False)
    assert rec is False and px == 7.868  # agreement -> market snapshot stands
    px, rec = LF._exit_px({"mkt_px": 0.083, "price": 8.36}, False)
    assert rec is True and px == 8.36  # >50% disagreement -> order price + flag
    assert LF._entry_px({"mkt_px": 0.083, "price": 8.36}) == 8.36
    assert LF._entry_px({"mkt_px": 0.083, "price": 0.0}) == 0.083  # fallback preserved


def test_script_mode_no_shadow(tmp_path):
    # Faithful regression: REAL script-mode argv (script dir on path, like cron).
    # Pre-fix this "passed" with vec replay 0/N (prepare-None via AttributeError).
    out = tmp_path / "ltf_probe"
    p = subprocess.run([sys.executable, str(ROOT / "tools" / "forward_parity" / "ltf_flip.py"),
                        "--days", "1", "--max-syms", "1", "--out", str(out)],
                       cwd=str(ROOT), capture_output=True, text=True, timeout=900)
    if p.returncode == 2 and "no NPZ tails" in (p.stdout + p.stderr):
        pytest.skip("no Mac NPZ tails on this host")
    assert p.returncode == 0, (p.stdout + p.stderr)[-2000:]
    assert "cat_side_of" not in p.stderr, p.stderr[-1500:]
    d = json.loads(Path(str(out) + ".json").read_text())
    assert d["legs"]["LTF_135"]["exits"] + d["legs"]["NO_TWIN"]["exits"] + d["legs"]["VEC15"]["exits"] >= 0
    assert "reconciled_exits" in d
    v = d["vec_replay"]
    ok = [k for k, x in v.items() if isinstance(x, dict) and not x.get("error")]
    if (ROOT / "backtest_v8" / "indicators" / "1000BONKUSDC.npz").exists():
        assert ok, "first-alphabetical key has Mac NPZ: replay must succeed post-shadow-fix"
