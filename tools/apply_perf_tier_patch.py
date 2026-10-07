#!/usr/bin/env python3
"""apply_perf_tier_patch — PERF_TIER_SIZING live wiring (USER 2026-10-06), surgical anchor patch for 4 LOCKED files.

Re-reads every file at apply time (other agents edit ez_manage/tradier_manage concurrently), refuses unless each anchor is
found exactly once, backs up, writes, py_compiles (restoring that file from the backup only if compile fails), and appends a
ledger row to data/parity/locked_files_change_ledger_20261006.md.
  python tools/apply_perf_tier_patch.py --root <dir> --check     # dry: anchors only
  python tools/apply_perf_tier_patch.py --root <dir> --apply     # write (repo root = live files; needs the user's unlock)
Idempotent: a file already carrying its marker is skipped.
"""
import argparse
import datetime as dt
import py_compile
import shutil
import sys
from pathlib import Path

MARK = "PERF_TIER_SIZING"
NEG_OLD = '''                    if (_g is not None and float(_g) <= 0) or "_NEG_BLOCK" in str(_v.get("winning_tag", "")):'''
NEG_NEW = '''                    if __import__("perf_tier_sizing").negbook_blocks(_v, bool(getattr(config, "PERF_TIER_SIZING_ENABLED", False))):  # PERF_TIER_SIZING 2026-10-06: tier ON -> only _NEG_BLOCK tag blocks; acc_gain<=0 = minimal size'''
PSYM_OLD = '''def _psym_sps(symbol: str, side: str):
    """Effective START_POSITION_SIZE for (symbol, side).'''
PSYM_NEW = '''def _psym_sps(symbol: str, side: str):
    """PERF_TIER_SIZING 2026-10-06 (USER): _psym_sps_raw x per-sym performance tier (data/persym_size_tiers.json; <1 allowed),
    floored at PERF_TIER_MIN_ORDER_USD (exchange minimum) and capped at MAX_ORDER_VALUE. Switch off / file missing = raw."""
    _raw = _psym_sps_raw(symbol, side)
    if not bool(getattr(config, "PERF_TIER_SIZING_ENABLED", False)):
        return _raw
    try:
        import perf_tier_sizing as _pts
        return _pts.apply_usd(_raw, symbol, side, float(getattr(config, "PERF_TIER_MIN_ORDER_USD", 6.0)), float(getattr(config, "MAX_ORDER_VALUE", 300.0)))
    except Exception:
        return _raw


def _psym_sps_raw(symbol: str, side: str):
    """Effective START_POSITION_SIZE for (symbol, side).'''
TQ_OLD = '''    # USER 2026-06-03 NO-EXCEPTIONS: WT_3M_FORCE_OPEN (with-trend, above-200MA, WT-favor build)'''
TQ_NEW = '''    if (not _absolute_target_order) and bool(getattr(config, "PERF_TIER_SIZING_ENABLED", False)) and action in ('OPEN', 'AUGMENT', 'REENTRY', 'QUICK_OPEN', 'QUICK_AUGMENT'):
        try:  # PERF_TIER_SIZING 2026-10-06 (USER): per-sym performance tier (<1 allowed, floor 1 share); MAX_ORDER_VALUE cap below still applies
            import perf_tier_sizing as _pts
            _pt_q0 = quantity
            quantity = _pts.apply_qty(quantity, symbol, position_side)
            if quantity != _pt_q0:
                logger.info(f"[PERF_TIER_SIZING] {position_key}: qty {_pt_q0}->{quantity} (x{_pts.get_mult(symbol, position_side):.2f})")
        except Exception as _pte:
            logger.warning(f"[PERF_TIER_SIZING] {position_key}: fail-open {_pte}")
    # USER 2026-06-03 NO-EXCEPTIONS: WT_3M_FORCE_OPEN (with-trend, above-200MA, WT-favor build)'''
CFG_OLD = '''    SYMBOL_PERF_DECAY_HOURS: float = 12.0
'''
CFG_NEW = '''    SYMBOL_PERF_DECAY_HOURS: float = 12.0
    PERF_TIER_SIZING_ENABLED: bool = True  # USER 2026-10-06: per-sym START_POSITION_SIZE x performance tier (data/persym_size_tiers.json: neg 30D+365D -> minimal, ranked gainers -> larger); acc_gain<=0 no longer NEG-blocks (only _NEG_BLOCK tag). False = legacy sizing + legacy NEG-block
    PERF_TIER_MIN_ORDER_USD: float = 6.0  # PERF_TIER_SIZING floor: never size an order below the Binance futures minimum notional (5 USDT) + margin
'''
CFGT_OLD = '''    CONVICTION_SIZING_MAX: float = 8.0
'''
CFGT_NEW = '''    CONVICTION_SIZING_MAX: float = 8.0
    PERF_TIER_SIZING_ENABLED: bool = True  # USER 2026-10-06: per-sym qty x performance tier (data/persym_size_tiers.json; floor 1 share, MAX_ORDER_VALUE cap); acc_gain<=0 no longer NEG-blocks (only _NEG_BLOCK tag). False = legacy
'''
EDITS = {
    "ez_manage.py": [(NEG_OLD, NEG_NEW), (PSYM_OLD, PSYM_NEW)],
    "tradier_manage.py": [(NEG_OLD, NEG_NEW), (TQ_OLD, TQ_NEW)],
    "config.py": [(CFG_OLD, CFG_NEW)],
    "config_tradier.py": [(CFGT_OLD, CFGT_NEW)],
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    root = Path(a.root).resolve()
    ts = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d%H%M")
    plan, bad = {}, []
    for name, edits in EDITS.items():
        p = root / name
        s = p.read_text()
        if MARK in s and all(new in s for _, new in edits):
            plan[name] = None
            print(f"[skip] {name}: already patched")
            continue
        for old, _ in edits:
            n = s.count(old)
            if n != 1:
                bad.append(f"{name}: anchor found {n}x: {old.splitlines()[0][:90]}")
        plan[name] = s
    if bad:
        print("REFUSED (anchors not unique / missing — re-read and re-anchor):\n  " + "\n  ".join(bad))
        return 1
    if a.check:
        print(f"[check] all anchors unique in {root}")
        return 0
    led = root / "data" / "parity" / "locked_files_change_ledger_20261006.md"
    led.parent.mkdir(parents=True, exist_ok=True)
    for name, s in plan.items():
        if s is None:
            continue
        p = root / name
        bk = root / "backups" / f"before_perf_tier_sizing_{ts}_{name}"
        bk.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, bk)
        for old, new in EDITS[name]:
            s = s.replace(old, new, 1)
        p.write_text(s)
        try:
            py_compile.compile(str(p), doraise=True)
        except Exception as e:
            shutil.copy2(bk, p)
            print(f"[FAIL] {name} compile error, restored from {bk.name}: {e}")
            return 1
        print(f"[applied] {name} (backup {bk.name})")
        with open(led, "a") as fh:
            fh.write(f"| {dt.datetime.now(dt.timezone.utc).isoformat()} | {name} | PERF_TIER_SIZING (USER 2026-10-06 'unlock all you need to get this right'): {len(EDITS[name])} anchor edit(s) via tools/apply_perf_tier_patch.py; backup backups/{bk.name} |\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
