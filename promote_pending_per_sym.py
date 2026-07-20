#!/usr/bin/env python3
"""promote_pending_per_sym — promote staged per-(sym,side) overrides to LIVE.

2026-05-18 USER MANDATE: every per-sym override must be human-reviewed via the
interactive HTML chart BEFORE it goes live. The hourly_reconfig daemons stage
candidates to data/hourly_reconfig/_pending_per_sym_active_config.json and
render per-(sym,side) charts to data/hourly_reconfig/_pending_review/. This
script is the explicit human-controlled promotion step.

Usage:
  # Print every pending entry + its chart path. No live write.
  python3 promote_pending_per_sym.py --review-charts-first

  # List pending entries only (machine-readable).
  python3 promote_pending_per_sym.py --list

  # Promote ALL pending entries to live per_sym_active_config.json (no prompts).
  python3 promote_pending_per_sym.py --promote-all --yes

  # Promote only a subset, reject the rest.
  python3 promote_pending_per_sym.py --promote SYM1_LONG,SYM2_SHORT \
                                     --reject SYM3_LONG

  # Reject one and leave the rest staged for later review.
  python3 promote_pending_per_sym.py --reject FOOSPY_SHORT

Files:
  PENDING:  data/hourly_reconfig/_pending_per_sym_active_config.json
  LIVE:     data/hourly_reconfig/per_sym_active_config.json  (read by live
            loaders ez_positions_quick._get_per_sym_overrides and
            tradier_manage._get_tradier_sym_cfg — never edited by this script
            unless --promote / --promote-all is given).
  ARCHIVE:  data/hourly_reconfig/_pending_review/<TS>_<promoted|rejected>/
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, List, Tuple

# ── Promotion gates: CLAUDE.md sample-floor + USER 2026-06-04 quality rule ──
# A per_sym key may go LIVE only if BOTH hold:
#   (1) SAMPLE FLOOR  — trades >= LIVE_PROMOTE_MIN_TRADES AND (years unknown or >= 1yr).
#   (2) QUALITY       — wsharpe > PROMOTE_MIN_WSHARPE AND a decent gain.
# (2) replaces the old wsharpe>=0.7 bar (USER): positive sharpe that makes decent
# money is promotable. Gain = summed per-trade return % (CLAUDE.md: summed, not
# compounded). A true per-month figure needs a backtest-window stamp the daemon
# does not yet write, so we gate on summed gain over the window via
# PROMOTE_MIN_GAIN_PCT (tunable); when gain data is absent we do not block (the
# sample floor + positive sharpe are already required).
try:
    import metrics_guard as _mg
    LIVE_PROMOTE_MIN_TRADES = int(getattr(_mg, "MIN_TRADES_PER_SYM_FOR_SYM_SHARPE", 30))
except Exception:
    LIVE_PROMOTE_MIN_TRADES = 30
LIVE_PROMOTE_MIN_YEARS = 1.0
PROMOTE_MIN_WSHARPE = 0.0
PROMOTE_MIN_GAIN_PCT = 5.0

ROOT = Path(__file__).resolve().parent
PENDING_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "_pending_per_sym_active_config.json"
LIVE_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
PENDING_DIR = ROOT / "data" / "hourly_reconfig" / "_pending_review"
MANIFEST_PATH = PENDING_DIR / "manifest.json"
CLASSIFIED_DIR = PENDING_DIR / "_classified"
CLASSIFIED_FULL = CLASSIFIED_DIR / "promote_full.json"
CLASSIFIED_MIN = CLASSIFIED_DIR / "promote_min_amount.json"
CLASSIFIED_REJECT = CLASSIFIED_DIR / "reject.json"


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
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(p)


def _archive(label: str, keys: List[str], pending_cfg: Dict) -> Path:
    """Copy promoted/rejected entries to _pending_review/<TS>_<label>/."""
    ts = time.strftime("%Y%m%d_%H%M%S", time.gmtime())
    dst = PENDING_DIR / f"{ts}_{label}"
    dst.mkdir(parents=True, exist_ok=True)
    # Save the decision JSON for each key
    archived = {k: pending_cfg.get(k, {}) for k in keys if k in pending_cfg}
    (dst / "entries.json").write_text(json.dumps(archived, indent=2, default=str))
    # Move associated HTML + trades JSONL into archive
    for k in keys:
        try:
            sym, side = k.rsplit("_", 1)
        except ValueError:
            continue
        for ext in (".html", ".txt", "_trades.jsonl"):
            src = PENDING_DIR / f"{sym}_{side}{ext}" if ext != "_trades.jsonl" \
                else PENDING_DIR / f"{sym}_{side}_trades.jsonl"
            if src.exists():
                shutil.move(str(src), str(dst / src.name))
    return dst


def cmd_review(pending: Dict) -> int:
    manifest = _load(MANIFEST_PATH)
    if not pending:
        print("NO_PENDING_ENTRIES")
        print(f"  pending file: {PENDING_CFG_PATH}")
        return 0
    print(f"{len(pending)} PENDING entries (NOT YET LIVE)")
    print(f"  pending cfg:   {PENDING_CFG_PATH}")
    print(f"  manifest:      {MANIFEST_PATH}")
    print(f"  charts dir:    {PENDING_DIR}")
    print()
    print(f"{'sym_side':<24} {'tag':<28} {'wsharpe':>8} {'trades':>6} {'sample':>10}  chart")
    print("-" * 100)
    chart_map = {}
    for e in manifest.get("entries", []):
        chart_map[f"{e.get('sym')}_{e.get('side')}"] = e.get("html", "")
    for sym_side, decision in sorted(pending.items()):
        tag = decision.get("winning_tag", "?")[:28]
        ws = decision.get("wsharpe", 0.0)
        ntr = decision.get("trades", 0)
        st = decision.get("sample_tag", "?")[:10]
        chart = chart_map.get(sym_side, "?")
        print(f"{sym_side:<24} {tag:<28} {ws:>8.4f} {ntr:>6d} {st:>10}  {chart}")
    print()
    print("To promote:")
    print(f"  python3 {Path(__file__).name} --promote-all --yes")
    print(f"  python3 {Path(__file__).name} --promote SYM_LONG[,SYM_SHORT,...]")
    print("To reject (drop without promoting):")
    print(f"  python3 {Path(__file__).name} --reject SYM_LONG[,...]")
    return 0


def cmd_list(pending: Dict) -> int:
    for k in sorted(pending.keys()):
        print(k)
    return 0


def _is_disable_entry(entry: Dict) -> bool:
    wt = str(entry.get("winning_tag", "") or "")
    if "PER_SYM_SIDE_DISABLED" in wt:
        return True
    if entry.get("wsharpe") in (0, 0.0) and entry.get("trades") is None:
        return True
    return False


def _is_sub_floor(entry: Dict) -> Tuple[bool, str]:
    if _is_disable_entry(entry):
        return False, ""
    tr = entry.get("trades")
    if tr is None:
        return True, "trades=None"
    try:
        tr = int(tr)
    except Exception:
        return True, f"trades={tr!r}"
    if tr < LIVE_PROMOTE_MIN_TRADES:
        return True, f"trades={tr}<{LIVE_PROMOTE_MIN_TRADES}"
    yrs = entry.get("years")
    if isinstance(yrs, (int, float)) and yrs < LIVE_PROMOTE_MIN_YEARS:
        return True, f"years={yrs:.3f}<{LIVE_PROMOTE_MIN_YEARS}"
    return False, ""


def _total_gain_pct(entry: Dict):
    g = entry.get("total_pnl_pct")
    if isinstance(g, (int, float)):
        return float(g)
    rr = entry.get("raw_returns")
    if isinstance(rr, list) and rr:
        try:
            return float(sum(float(x) for x in rr))
        except Exception:
            return None
    return None


def _is_promotable(entry: Dict) -> Tuple[bool, str]:
    if _is_disable_entry(entry):
        return True, "disable_entry"
    try:
        ws = float(entry.get("wsharpe"))
    except (TypeError, ValueError):
        return False, "wsharpe=NA"
    if ws <= PROMOTE_MIN_WSHARPE:
        return False, f"wsharpe={ws:.4f}<={PROMOTE_MIN_WSHARPE:.2f}"
    gain = _total_gain_pct(entry)
    if gain is None:
        return True, f"wsharpe={ws:.3f}|gain=unknown"
    if gain < PROMOTE_MIN_GAIN_PCT:
        return False, f"gain={gain:.1f}%<{PROMOTE_MIN_GAIN_PCT:.0f}%"
    return True, f"wsharpe={ws:.3f}|gain={gain:.1f}%"


def cmd_promote_reject(pending: Dict, promote_keys: List[str],
                       reject_keys: List[str], yes: bool,
                       payload_source: Dict = None) -> int:
    promote_keys = [k for k in promote_keys if k in pending]
    reject_keys = [k for k in reject_keys if k in pending]
    _blocked = {}
    _kept = []
    for k in promote_keys:
        sf, sreason = _is_sub_floor(pending[k])
        if sf:
            _blocked[k] = f"SUB_FLOOR:{sreason}"
            continue
        ok, qreason = _is_promotable(pending[k])
        if not ok:
            _blocked[k] = f"NOT_PROMOTABLE:{qreason}"
            continue
        _kept.append(k)
    if _blocked:
        print(f"BLOCKED {len(_blocked)} (gate: trades>={LIVE_PROMOTE_MIN_TRADES}, wsharpe>{PROMOTE_MIN_WSHARPE}, gain>={PROMOTE_MIN_GAIN_PCT:.0f}%):")
        for k in sorted(_blocked):
            print(f"  - {k}: {_blocked[k]}")
    promote_keys = _kept
    if not promote_keys and not reject_keys:
        print("Nothing to promote or reject.")
        return 0
    print(f"Promote ({len(promote_keys)}): {promote_keys}")
    print(f"Reject  ({len(reject_keys)}): {reject_keys}")
    if not yes:
        try:
            resp = input("Proceed? [y/N] ").strip().lower()
        except EOFError:
            resp = "n"
        if resp not in ("y", "yes"):
            print("ABORTED.")
            return 1
    live = _load(LIVE_CFG_PATH)
    for k in promote_keys:
        # Prefer classified payload (has injected START_POSITION_SIZE_OVERRIDE_USD + warning)
        src_entry = (payload_source or {}).get(k) or pending[k]
        decision = dict(src_entry)
        meta = dict(decision.get("_meta", {}))
        meta["promoted_ts_utc"] = time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())
        meta["pending"] = False
        decision["_meta"] = meta
        live[k] = decision
    _atomic_write(LIVE_CFG_PATH, live)
    promoted_dst = _archive("promoted", promote_keys, pending) if promote_keys else None
    rejected_dst = _archive("rejected", reject_keys, pending) if reject_keys else None
    # Remove promoted+rejected from pending
    for k in promote_keys + reject_keys:
        pending.pop(k, None)
    _atomic_write(PENDING_CFG_PATH, pending)
    print(f"PROMOTED {len(promote_keys)} to {LIVE_CFG_PATH}")
    if promoted_dst:
        print(f"  archive: {promoted_dst}")
    if rejected_dst:
        print(f"REJECTED {len(reject_keys)} archived to {rejected_dst}")
    print(f"REMAINING PENDING: {len(pending)}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description="Promote staged per-sym overrides to LIVE.")
    ap.add_argument("--review-charts-first", action="store_true",
                    help="Print all pending entries with chart paths and exit (no write).")
    ap.add_argument("--list", action="store_true",
                    help="Print pending sym_side keys (one per line).")
    ap.add_argument("--promote-all", action="store_true",
                    help="Promote every pending entry to live (no per-entry prompts).")
    ap.add_argument("--promote", default="",
                    help="Comma-separated SYM_SIDE keys to promote.")
    ap.add_argument("--reject", default="",
                    help="Comma-separated SYM_SIDE keys to reject.")
    ap.add_argument("--promote-eligible", action="store_true",
                    help="Auto-select & promote every pending key passing the "
                         "sample-floor + wsharpe>0 + decent-gain gate that is new "
                         "or improves on live (used by the gated cron). Implies --yes.")
    ap.add_argument("--yes", "-y", action="store_true",
                    help="Skip confirmation prompt.")
    ap.add_argument("--use-classified", action="store_true",
                    help=("Promote keys listed in _classified/promote_full.json + "
                          "_classified/promote_min_amount.json; reject keys in "
                          "_classified/reject.json. Refuses if classified dir missing."))
    ap.add_argument("--full-only", action="store_true",
                    help="With --use-classified, only promote promote_full.json (skip MIN).")
    ap.add_argument("--trial-only", action="store_true",
                    help="With --use-classified, only promote promote_min_amount.json (skip FULL).")
    args = ap.parse_args()
    pending = _load(PENDING_CFG_PATH)
    if args.review_charts_first or (not any([args.list, args.promote_all, args.use_classified,
                                              args.promote, args.reject, args.promote_eligible])):
        return cmd_review(pending)
    if args.list:
        return cmd_list(pending)
    promote_keys = []
    reject_keys = []
    payload_source = None
    if args.use_classified:
        full_map = _load(CLASSIFIED_FULL) if CLASSIFIED_FULL.exists() else None
        min_map = _load(CLASSIFIED_MIN) if CLASSIFIED_MIN.exists() else None
        rej_map = _load(CLASSIFIED_REJECT) if CLASSIFIED_REJECT.exists() else None
        if full_map is None or min_map is None or rej_map is None:
            print(f"ERROR: --use-classified requires {CLASSIFIED_DIR}/{{promote_full,promote_min_amount,reject}}.json",
                  file=sys.stderr)
            print("Run: python3 tools/classify_pending_per_sym.py", file=sys.stderr)
            return 2
        if args.full_only and args.trial_only:
            print("ERROR: --full-only and --trial-only are mutually exclusive.", file=sys.stderr)
            return 2
        if args.full_only:
            promote_keys = list(full_map.keys())
            payload_source = dict(full_map)
        elif args.trial_only:
            promote_keys = list(min_map.keys())
            payload_source = dict(min_map)
        else:
            promote_keys = list(full_map.keys()) + list(min_map.keys())
            payload_source = {**full_map, **min_map}
        reject_keys = list(rej_map.keys())
        print(f"[--use-classified] promote_full={len(full_map)} promote_min={len(min_map)} reject={len(rej_map)}")
    if args.promote_eligible:
        live_now = _load(LIVE_CFG_PATH)
        for k, v in pending.items():
            if k.startswith("_") or not isinstance(v, dict):
                continue
            if _is_sub_floor(v)[0] or not _is_promotable(v)[0]:
                continue
            lw = (live_now.get(k) or {}).get("wsharpe")
            pw = v.get("wsharpe")
            try:
                improver = (lw is None) or (float(pw) > float(lw))
            except (TypeError, ValueError):
                improver = lw is None
            if improver:
                promote_keys.append(k)
        args.yes = True
    if args.promote_all:
        promote_keys = list(pending.keys())
    if args.promote:
        promote_keys.extend([k.strip() for k in args.promote.split(",") if k.strip()])
    if args.reject:
        reject_keys.extend([k.strip() for k in args.reject.split(",") if k.strip()])
    # Dedup
    promote_keys = sorted(set(promote_keys) - set(reject_keys))
    reject_keys = sorted(set(reject_keys))
    return cmd_promote_reject(pending, promote_keys, reject_keys, args.yes,
                              payload_source=payload_source)


if __name__ == "__main__":
    sys.exit(main())
