#!/usr/bin/env python3
"""persym_floor_gate — enforce the per-sym LIVE sample-floor (CLAUDE.md).

2026-06-04 IMPOSTER-BLOCK / NO-LIES enforcement. A per-sym override key
(SYMBOL_SIDE) may only be LIVE-ACTIVE (drive entries/sizing) when it satisfies
the per-sym sample floor:

    trades >= 30  AND  (years is None or years >= 1.0)

A per-sym key is inherently single-symbol, so the n_syms>=48/100 pool floor does
NOT apply here; the >=30-trade + >1yr floor is the applicable bar (see CLAUDE.md
"SHARPE DEFINITION" rule 5/6 + "IMPOSTER BLOCK").

This module is the chokepoint the promote cron runs because the canonical promote
script (promote_pending_per_sym.py) is LOCKED and cannot be edited. It does two
jobs, both idempotent and conservative:

  prune_pending(): drop sub-floor entries from
      data/hourly_reconfig/_pending_per_sym_active_config.json
    so the locked promote can NEVER promote a sub-floor key to live.

  clean_live(): HANG-IT-FIRST then clean
      data/hourly_reconfig/per_sym_active_config.json
    - protective DISABLE entries (winning_tag contains PER_SYM_SIDE_DISABLED,
      or wsharpe==0 with trades None) are LEFT UNTOUCHED — removing them would
      re-enable trading (unsafe direction).
    - sub-floor ACTIVE keys tied to a CURRENTLY-OPEN position are TAG-ONLY:
      sample_tag set to "[DIAGNOSTIC sub-floor ...]" + diagnostic_only=True +
      its override side-_ENABLED forced False so the live loader treats it as a
      neutral DISABLE (no new entries / sizing) without forcing any exit.
    - sub-floor ACTIVE keys NOT tied to an open position are TAGGED then REMOVED
      (reverts that symbol to SAFE global defaults; never forces a position
      action — exits are R1/R2/hedge only).

Removing an override key just reverts the symbol to config.py global defaults,
which always exist; it cannot trigger a close.

CLI:
  python3 persym_floor_gate.py --report           # dry-run, print plan only
  python3 persym_floor_gate.py --prune-pending     # clean staging only
  python3 persym_floor_gate.py --clean-live        # tag+clean live only
  python3 persym_floor_gate.py --all               # prune-pending + clean-live
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path
from typing import Dict, List, Set, Tuple

ROOT = Path(__file__).resolve().parent
LIVE_CFG = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
PENDING_CFG = ROOT / "data" / "hourly_reconfig" / "_pending_per_sym_active_config.json"
HISTORY_DIR = ROOT / "data" / "history"
BACKUP_DIR = ROOT / "backups"
ACCOUNTS = ("trb", "trc", "tra", "ang", "inf", "flz", "men", "fin")

try:
    import metrics_guard as _mg
    MIN_TRADES = int(_mg.MIN_TRADES_PER_SYM_FOR_SYM_SHARPE)
except Exception:
    MIN_TRADES = 30
MIN_YEARS = 1.0


def _load(p: Path) -> Dict:
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception as exc:
        print(f"WARN: failed to read {p}: {exc}", file=sys.stderr)
        return {}


def _atomic_write(p: Path, data: Dict) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(p)


def _backup(p: Path, label: str) -> Path:
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    dst = BACKUP_DIR / f"before_persym_floor_gate_{label}_{ts}{p.suffix}"
    dst.write_bytes(p.read_bytes())
    return dst


def is_meta_key(key: str) -> bool:
    return key.startswith("_meta") or key.startswith("_") and not key.endswith(("_LONG", "_SHORT"))


def is_disable_entry(entry: Dict) -> bool:
    wt = str(entry.get("winning_tag", "") or "")
    if "PER_SYM_SIDE_DISABLED" in wt:
        return True
    if entry.get("wsharpe") in (0, 0.0) and entry.get("trades") is None:
        return True
    return False


def is_sub_floor(entry: Dict) -> Tuple[bool, str]:
    """Return (sub_floor, reason). DISABLE entries are never 'sub-floor' here
    because they BLOCK trading (safe) and must be left alone."""
    tr = entry.get("trades")
    if tr is None:
        return True, "trades=None"
    try:
        tr = int(tr)
    except Exception:
        return True, f"trades={tr!r}"
    if tr < MIN_TRADES:
        return True, f"trades={tr}<{MIN_TRADES}"
    yrs = entry.get("years")
    if isinstance(yrs, (int, float)) and yrs < MIN_YEARS:
        return True, f"years={yrs:.3f}<{MIN_YEARS}"
    return False, ""


def open_position_keys() -> Tuple[Set[str], Set[str]]:
    """Return (open_side_keys, open_symbols).
    open_side_keys: SYMBOL_SIDE keys with a net-open position in any real account.
    open_symbols:   bare SYMBOLs that are open on either side (so a bare-symbol
                    override key tied to a live position is treated tag-only too).
    Computed from the /history/ trade ledger (CLOSE/HEDGE_CLOSE zero net qty)."""
    open_side: Set[str] = set()
    open_syms: Set[str] = set()
    for acct in ACCOUNTS:
        d = HISTORY_DIR / acct
        if not d.is_dir():
            continue
        for fp in glob.glob(os.path.join(str(d), "*.jsonl")):
            base = os.path.basename(fp)[:-6]
            net = 0.0
            try:
                for line in open(fp):
                    line = line.strip()
                    if not line:
                        continue
                    ev = json.loads(line)
                    t = str(ev.get("type", "")).upper()
                    try:
                        q = float(ev.get("qty", 0) or 0)
                    except Exception:
                        q = 0.0
                    if t in ("OPEN", "AUGMENT", "REENTRY", "REENTER", "HEDGE_OPEN"):
                        net += q
                    elif t == "REDUCE":
                        net -= q
                    elif t in ("CLOSE", "HEDGE_CLOSE"):
                        net = 0.0
            except Exception:
                continue
            if net > 1e-6:
                open_side.add(base)
                if base.endswith(("_LONG", "_SHORT")):
                    open_syms.add(base.rsplit("_", 1)[0])
                else:
                    open_syms.add(base)
    return open_side, open_syms


def _key_is_open(key: str, open_side: Set[str], open_syms: Set[str]) -> bool:
    if key.endswith(("_LONG", "_SHORT")):
        return key in open_side
    return key in open_syms


def _diag_tag(reason: str) -> str:
    return f"[DIAGNOSTIC sub-floor · {reason} · n_syms=1]"


def _force_side_disabled(entry: Dict, key: str) -> None:
    side = entry.get("side") or (key.rsplit("_", 1)[1] if key.endswith(("_LONG", "_SHORT")) else "")
    ov = dict(entry.get("overrides", {}) or {})
    if side == "LONG":
        ov["LONG_ENABLED"] = False
        ov["PER_SYM_LONG_DISABLED"] = True
    elif side == "SHORT":
        ov["SHORT_ENABLED"] = False
        ov["PER_SYM_SHORT_DISABLED"] = True
    else:
        ov["LONG_ENABLED"] = False
        ov["SHORT_ENABLED"] = False
        ov["PER_SYM_LONG_DISABLED"] = True
        ov["PER_SYM_SHORT_DISABLED"] = True
    entry["overrides"] = ov


def plan(live: Dict, pending: Dict) -> Dict:
    open_side, open_syms = open_position_keys()
    pend_drop, live_tag_only, live_remove, live_disable_skip = [], [], [], []
    for k, v in pending.items():
        if not isinstance(v, dict) or is_meta_key(k):
            continue
        if is_disable_entry(v):
            continue
        sf, reason = is_sub_floor(v)
        if sf:
            pend_drop.append((k, reason))
    for k, v in live.items():
        if not isinstance(v, dict) or is_meta_key(k):
            continue
        if is_disable_entry(v):
            live_disable_skip.append(k)
            continue
        sf, reason = is_sub_floor(v)
        if not sf:
            continue
        if _key_is_open(k, open_side, open_syms):
            live_tag_only.append((k, reason))
        else:
            live_remove.append((k, reason))
    return {
        "open_side_count": len(open_side),
        "pend_drop": pend_drop,
        "live_tag_only": live_tag_only,
        "live_remove": live_remove,
        "live_disable_skip": live_disable_skip,
    }


def prune_pending() -> int:
    pending = _load(PENDING_CFG)
    if not pending:
        print(f"[floor_gate] pending empty: {PENDING_CFG}")
        return 0
    p = plan({}, pending)
    drops = p["pend_drop"]
    if not drops:
        print(f"[floor_gate] pending: 0 sub-floor entries (of {len(pending)}) — nothing to prune")
        return 0
    _backup(PENDING_CFG, "pending")
    for k, _r in drops:
        pending.pop(k, None)
    _atomic_write(PENDING_CFG, pending)
    print(f"[floor_gate] PRUNED {len(drops)} sub-floor entries from pending; {len(pending)} remain")
    for k, r in drops:
        print(f"    drop  {k}  ({r})")
    return len(drops)


def clean_live() -> int:
    live = _load(LIVE_CFG)
    if not live:
        print(f"[floor_gate] live empty: {LIVE_CFG}")
        return 0
    p = plan(live, {})
    tag_only, remove = p["live_tag_only"], p["live_remove"]
    if not tag_only and not remove:
        print(f"[floor_gate] live: 0 sub-floor ACTIVE keys — already compliant")
        return 0
    _backup(LIVE_CFG, "live")
    # HANG IT FIRST: tag everything (open => tag+neutralize, closed => tag) ...
    for k, reason in tag_only:
        e = live[k]
        e["sample_tag"] = _diag_tag(reason)
        e["diagnostic_only"] = True
        e["floor_gate_action"] = "TAG_ONLY_OPEN_POSITION"
        e["floor_gate_ts_utc"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        _force_side_disabled(e, k)
    for k, reason in remove:
        e = live[k]
        e["sample_tag"] = _diag_tag(reason)
        e["diagnostic_only"] = True
        e["floor_gate_action"] = "TAGGED_FOR_REMOVAL"
    # ... THEN remove the closed ones (revert to safe global defaults).
    for k, _reason in remove:
        live.pop(k, None)
    _atomic_write(LIVE_CFG, live)
    print(f"[floor_gate] LIVE cleaned: tag-only(open)={len(tag_only)} removed(closed)={len(remove)} "
          f"disable-skipped={len(p['live_disable_skip'])}; {len(live)} keys remain")
    for k, r in tag_only:
        print(f"    tag-only  {k}  ({r})")
    for k, r in remove:
        print(f"    removed   {k}  ({r})")
    return len(tag_only) + len(remove)


def report() -> int:
    live, pending = _load(LIVE_CFG), _load(PENDING_CFG)
    p = plan(live, pending)
    print(f"[floor_gate REPORT] MIN_TRADES={MIN_TRADES} MIN_YEARS={MIN_YEARS}")
    print(f"  open side-keys: {p['open_side_count']}")
    print(f"  PENDING sub-floor to drop:        {len(p['pend_drop'])}")
    print(f"  LIVE sub-floor active TAG-ONLY:    {len(p['live_tag_only'])} (open positions)")
    print(f"  LIVE sub-floor active TAG+REMOVE:  {len(p['live_remove'])} (closed)")
    print(f"  LIVE protective DISABLE (left):    {len(p['live_disable_skip'])}")
    for k, r in sorted(p["pend_drop"]):
        print(f"    PEND drop     {k}  ({r})")
    for k, r in sorted(p["live_tag_only"]):
        print(f"    LIVE tag-only {k}  ({r})")
    for k, r in sorted(p["live_remove"]):
        print(f"    LIVE remove   {k}  ({r})")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Enforce per-sym LIVE sample floor (>=30 trades, >1yr).")
    ap.add_argument("--report", action="store_true", help="Dry-run: print plan, no writes.")
    ap.add_argument("--prune-pending", action="store_true", help="Drop sub-floor entries from staging.")
    ap.add_argument("--clean-live", action="store_true", help="Tag+clean sub-floor active keys in live.")
    ap.add_argument("--all", action="store_true", help="prune-pending then clean-live.")
    args = ap.parse_args()
    if args.report or not any([args.prune_pending, args.clean_live, args.all]):
        return report()
    if args.all or args.prune_pending:
        prune_pending()
    if args.all or args.clean_live:
        clean_live()
    return 0


if __name__ == "__main__":
    sys.exit(main())
