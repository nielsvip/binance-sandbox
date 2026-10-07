#!/usr/bin/env python3
"""crypto_npz_precompute_relaxed — run backtest_v8_precompute.py UNCHANGED on disk with the crypto coverage guard (n < 7000 15m bars) lowered to n < 2500 at import
time, so a short-history crypto symbol (e.g. new stock-perps GOOGLUSDT/MRVLUSDT/QNTUSDT with ~36 days of klines) gets an NPZ for 30D sweeps (365D chain = unverifiable).
Same CLI: --mode crypto --symbols A,B --out-dir DIR. Stage, validate, then install atomically."""
import sys
from pathlib import Path
SRC = Path(__file__).resolve().parents[1] / "backtest_v8_precompute.py"
text = SRC.read_text()
G = 'if mode == "crypto" and n < 7000:'
assert text.count(G) == 1, "crypto guard line not found exactly once"
text = text.replace(G, 'if mode == "crypto" and n < 2500:')
ns = {"__name__": "backtest_v8_precompute_relaxed", "__file__": str(SRC)}
sys.path.insert(0, str(SRC.parent))
exec(compile(text, str(SRC), "exec"), ns)
sys.argv = [str(SRC)] + sys.argv[1:]
ns["main"]()
