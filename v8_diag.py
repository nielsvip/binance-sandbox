#!/usr/bin/env python3
"""Diagnostic: run V8 tradier on 1 symbol/50 bars and show every process_position result."""
import os, sys, asyncio, logging, json
from pathlib import Path
from datetime import datetime, timezone

logging.basicConfig(level=logging.WARNING)
os.chdir("/Users/niels/Documents/binance")

# Force AAPL only, 2026-03-20 start
sys.argv = ["backtest_v8_engine.py","--mode","tradier","--account","trb","--start","2026-03-20","--symbols","AAPL"]

# ── patch engine before running ───────────────────────────────────
import importlib.util
spec = importlib.util.spec_from_file_location("v8e", "/Users/niels/Documents/binance/backtest_v8_engine.py")
# We can't use spec.loader.exec_module because __file__ won't be set properly
# Instead, run it as subprocess with patched code injected
import subprocess, tempfile, re

src = Path("/Users/niels/Documents/binance/backtest_v8_engine.py").read_text()

# 1) Limit to 50 bars
src = src.replace(
    "for step, ts in enumerate(all_ts):\n",
    "for step, ts in enumerate(all_ts[:50]):\n"
)

# 2) Log each process_position result
src = src.replace(
    "await asyncio.gather(*[tm_mod.process_position(account_key, pk, manager.order_queue, manager, event_type=\"backtest\", force=True) for pk in all_keys], return_exceptions=True)",
    """_tasks = [tm_mod.process_position(account_key, _pk, manager.order_queue, manager, event_type="backtest", force=True) for _pk in all_keys]
        _results = await asyncio.gather(*_tasks, return_exceptions=True)
        for _pk, _r in zip(all_keys, _results):
            if isinstance(_r, Exception):
                import traceback as _tb
                v8_logger.warning(f"[DIAG_ERR] {_pk}: {type(_r).__name__}: {_r}")
                if step < 3:
                    v8_logger.warning(_tb.format_exc())
            elif step < 3 or (step % 10 == 0 and _r not in (None, "THROTTLED", "STALE_INDICATORS_HELD")):
                v8_logger.warning(f"[DIAG_OK] step={step} {_pk}: {_r}")"""
)

# Write patched script to temp file
with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, dir='/tmp') as tf:
    tf.write(src)
    tmp_path = tf.name

print(f"Running diagnostic with patched script: {tmp_path}", flush=True)
result = subprocess.run(
    ["/opt/anaconda3/envs/binance_env/bin/python3", tmp_path,
     "--mode", "tradier", "--account", "trb", "--start", "2026-03-20", "--symbols", "AAPL"],
    capture_output=True, text=True, cwd="/Users/niels/Documents/binance"
)
# Filter output
for line in (result.stdout + result.stderr).splitlines():
    if any(x in line for x in ["DIAG", "TRADE", "ERROR", "error", "Exception", "Traceback", "V8_RESULT", "RESULT", "WARN", "Warning"]):
        if "Environment" not in line and "GPG" not in line and "variables already" not in line:
            print(line)
