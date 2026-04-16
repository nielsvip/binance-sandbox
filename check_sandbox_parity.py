#!/usr/bin/env python3
"""check_sandbox_parity.py — verify MacBook ↔ S1 ↔ S2 parity for critical files.

Run at every session start. Run before launching any sweep.

Exit code 0 = all parity. Exit code 1 = DRIFT detected — STOP.

Per CLAUDE.md "SANDBOX PARITY" section: if any file drifts, sweep results are
invalid and live trading decisions are at risk. This script is the canonical
detector.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent

# The 6 MUST-MATCH trading scripts + their direct dependencies
CRITICAL_FILES = [
    # Live orchestrators (the 6 core)
    "ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
    "tradier_manage.py", "config.py", "config_tradier.py",
    # Live data pipeline (imported by the 6)
    "ez_positions.py", "ez_indicators.py", "ez_market_data.py",
    "ez_rankings.py", "ez_klines.py", "ez_prices.py",
    "utils.py", "symbols.json",
    # Stock pipeline
    "tradier_api.py", "tradier_positions.py", "tradier_indicators.py",
    "tradier_rankings.py", "tradier_prices.py",
    # WaveTrend + Delta engine (core signal system)
    "wt_dc_delta.py", "wt_dc_exit_scorer.py", "wt_dc_entry_scorer.py",
    # Backtest engine (sweep results depend on this)
    "v8_quick_engine.py", "v8_quick_sweep.py", "breakout_multi_lung.py",
    "backtest_v8_engine.py", "backtest_v8_precompute.py", "backtest_v8_harness.py",
]


def md5_local(p: Path) -> str:
    if not p.exists():
        return "MISSING"
    h = hashlib.md5()
    with p.open("rb") as f:
        for chunk in iter(lambda: f.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def md5_remote(host: str, path: str) -> str:
    try:
        r = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", host, f"md5sum {path} 2>/dev/null | awk '{{print $1}}' || echo MISSING"],
            capture_output=True, text=True, timeout=15,
        )
        out = (r.stdout or "").strip()
        return out if out else "MISSING"
    except Exception as e:
        return f"ERR:{e.__class__.__name__}"


def main() -> int:
    drifts = []
    print(f"{'FILE':40s} {'MACBOOK':10s} {'S1':10s} {'S2':10s} STATUS")
    print("-" * 90)
    for fname in CRITICAL_FILES:
        mb = md5_local(BASE / fname)
        s1 = md5_remote("s1-int", f"/home/niels/binance-sandbox/{fname}")
        s2 = md5_remote("s2-int", f"/home/niels/binance-sandbox/{fname}")
        short_mb = mb[:10] if len(mb) >= 10 else mb
        short_s1 = s1[:10] if len(s1) >= 10 else s1
        short_s2 = s2[:10] if len(s2) >= 10 else s2
        if mb == "MISSING":
            status = "SKIP_MB_MISSING"
        elif mb == s1 == s2:
            status = "OK"
        else:
            status = "DRIFT"
            drifts.append((fname, mb, s1, s2))
        print(f"{fname:40s} {short_mb:10s} {short_s1:10s} {short_s2:10s} {status}")

    print("-" * 90)
    if drifts:
        print(f"\n🔴 DRIFT DETECTED: {len(drifts)} file(s) differ between MacBook and sandbox(es)")
        print("   Fix with: rsync -az --existing --update <file> s1-int:/home/niels/binance-sandbox/")
        print("   Then:     rsync -az --existing --update <file> s2-int:/home/niels/binance-sandbox/")
        print("   See CLAUDE.md 'SANDBOX PARITY' section — do NOT launch any sweep until fixed.")
        for fname, mb, s1, s2 in drifts:
            print(f"   - {fname}: MB={mb[:10]}  S1={s1[:10]}  S2={s2[:10]}")
        return 1
    print("\n✅ All parity OK — sandboxes match MacBook.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
