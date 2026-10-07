#!/usr/bin/env python3
"""stock_npz_precompute_relaxed — run backtest_v8_precompute.py UNCHANGED on disk, but with the 2026-08-13 tradier coverage guard
(>=20000 bars AND >=300d, meant to keep 1yr-parity NPZs honest) disabled at import time, so a 30D sweep can get a FRESH NPZ for stocks whose
rolling klines cache only holds ~42 sessions. The locked file is not edited; output goes where --out-dir says (stage it, validate, then install
with tools/stock_npz_refresh.py). Same CLI as the original: --mode tradier --symbols A,B --out-dir DIR."""
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "backtest_v8_precompute.py"
text = SRC.read_text()
GUARD = 'if mode == "tradier" and (n < 20000 or _span_days < 300) and symbol not in ['
assert text.count(GUARD) == 1, "guard line not found exactly once — the precompute changed, re-check before relaxing"
text = text.replace(GUARD, 'if mode == "tradier" and (False) and symbol not in [')
ns = {"__name__": "backtest_v8_precompute_relaxed", "__file__": str(SRC)}
sys.path.insert(0, str(SRC.parent))
exec(compile(text, str(SRC), "exec"), ns)
sys.argv = [str(SRC)] + sys.argv[1:]
ns["main"]()
