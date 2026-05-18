"""_pending_review_chart — render per-(sym,side) interactive HTML for pending overrides.

2026-05-18: USER MANDATE — "MUST see all trades from the 7D functions on a chart on
screen BEFORE they are applied to live for future trades."

Per cycle, the hourly_reconfig daemons write candidate per-sym overrides to
data/hourly_reconfig/_pending_per_sym_active_config.json (NOT the live file).
This module renders one interactive HTML per (sym, side) pending entry so the
user can review BEFORE running promote_pending_per_sym.py.

Uses charts/_interactive_lib.build_symbol_html, the same renderer already used
for crypto_week_review_20260518 and other audit dashboards.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Tuple

_ROOT = Path(__file__).resolve().parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))
if str(_ROOT / "charts") not in sys.path:
    sys.path.insert(0, str(_ROOT / "charts"))

PENDING_CFG_PATH = _ROOT / "data" / "hourly_reconfig" / "_pending_per_sym_active_config.json"
PENDING_DIR = _ROOT / "data" / "hourly_reconfig" / "_pending_review"
PENDING_DIR.mkdir(parents=True, exist_ok=True)


def jsonl_records_to_events(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Convert per-trade JSONL records (entry_ts, exit_ts, pnl_pct, side, ...)
    into the event-stream shape consumed by charts/_interactive_lib (one OPEN
    + one CLOSE per trade).
    """
    events: List[Dict[str, Any]] = []
    for rec in records:
        side = (rec.get("side") or "").upper()
        if side not in ("LONG", "SHORT"):
            continue
        entry_ts = int(rec.get("entry_ts", 0) or 0)
        exit_ts = int(rec.get("exit_ts", 0) or 0)
        entry_price = float(rec.get("entry_price", 0.0) or 0.0)
        exit_price = float(rec.get("exit_price", 0.0) or 0.0)
        pnl_pct = float(rec.get("pnl_pct", 0.0) or 0.0)
        reason = rec.get("exit_reason", "VEC_CLOSE") or "VEC_CLOSE"
        if entry_ts > 0 and entry_price > 0:
            events.append({
                "ts": entry_ts,
                "type": "OPEN",
                "side": side,
                "price": entry_price,
                "pnl_pct": 0.0,
                "reason": "VEC_OPEN",
            })
        if exit_ts > 0 and exit_price > 0:
            events.append({
                "ts": exit_ts,
                "type": "CLOSE",
                "side": side,
                "price": exit_price,
                "pnl_pct": pnl_pct,
                "reason": str(reason),
            })
    events.sort(key=lambda e: e["ts"])
    return events


def stash_trade_jsonl(sym: str, side: str, records: List[Dict[str, Any]]) -> Path:
    """Write the raw trade records to _pending_review/<SYM>_<SIDE>_trades.jsonl."""
    out = PENDING_DIR / f"{sym}_{side}_trades.jsonl"
    with out.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return out


def render_pending_chart(sym: str, side: str, mode: str,
                         records: List[Dict[str, Any]],
                         summary_row: Dict[str, Any]) -> Path:
    """Render one HTML for (sym, side). Returns path."""
    out = PENDING_DIR / f"{sym}_{side}.html"
    try:
        from charts._interactive_lib import build_symbol_html
    except Exception as exc:
        # If charts lib unavailable, write a plain-text fallback so the user can
        # at least see the trade list and decide before promoting.
        with out.with_suffix(".txt").open("w") as f:
            f.write(f"# Pending review: {sym} {side}  (mode={mode})\n")
            f.write(f"# {len(records)} trades. Summary: {json.dumps(summary_row, default=str)}\n")
            f.write(f"# charts lib import failed: {exc}\n\n")
            for rec in records:
                f.write(json.dumps(rec, default=str) + "\n")
        return out.with_suffix(".txt")
    events = jsonl_records_to_events(records)
    banner = (f"PENDING — NOT YET LIVE  ·  {sym} {side}  ·  "
              f"pool_sharpe={summary_row.get('wsharpe', 0):.4f}  ·  "
              f"trades={summary_row.get('trades', 0)}  ·  "
              f"tag={summary_row.get('winning_tag', '')}")
    try:
        build_symbol_html(
            symbol=sym, mode=mode, events=events,
            summary_row=summary_row, banner_text=banner,
            out_path=out, offline=True,
        )
    except Exception as exc:
        with out.with_suffix(".txt").open("w") as f:
            f.write(f"# Pending review: {sym} {side}  (mode={mode})\n")
            f.write(f"# charts render failed: {exc}\n")
            f.write(f"# {len(records)} trades. Summary: {json.dumps(summary_row, default=str)}\n\n")
            for rec in records:
                f.write(json.dumps(rec, default=str) + "\n")
        return out.with_suffix(".txt")
    return out


def write_manifest(entries: List[Tuple[str, str, str, Path, Dict[str, Any]]]) -> Path:
    """Write _pending_review/manifest.json with the list of pending entries.

    entries: list of (sym, side, mode, html_path, summary_row)
    """
    manifest = {
        "generated_at": int(time.time()),
        "n_pending": len(entries),
        "entries": [
            {
                "sym": sym,
                "side": side,
                "mode": mode,
                "html": str(html_path),
                "trades_jsonl": str(PENDING_DIR / f"{sym}_{side}_trades.jsonl"),
                "summary": {
                    k: v for k, v in summary.items()
                    if k in ("winning_tag", "wsharpe", "trades",
                             "sample_tag", "total_pnl_pct", "cycle_id")
                },
            }
            for sym, side, mode, html_path, summary in entries
        ],
    }
    out = PENDING_DIR / "manifest.json"
    out.write_text(json.dumps(manifest, indent=2, default=str))
    return out


def upsert_pending_cfg(new_entries: Dict[str, Dict[str, Any]],
                      source_daemon: str, source_account: str,
                      source_cycle: str) -> Tuple[int, int, int]:
    """First-writer-wins upsert into _pending_per_sym_active_config.json.

    Returns (n_added, n_skipped_present, total).
    """
    from datetime import datetime, timezone
    try:
        if PENDING_CFG_PATH.exists():
            existing = json.loads(PENDING_CFG_PATH.read_text())
        else:
            existing = {}
    except Exception:
        existing = {}
    n_added = 0
    n_skipped = 0
    for sym_side, decision in new_entries.items():
        if sym_side in existing:
            n_skipped += 1
            continue
        tagged = dict(decision)
        meta = dict(tagged.get("_meta", {}))
        meta.update({
            "source_account": source_account,
            "source_cycle": source_cycle,
            "source_ts_utc": datetime.now(timezone.utc).isoformat(),
            "source_daemon": source_daemon,
            "pending": True,
        })
        tagged["_meta"] = meta
        existing[sym_side] = tagged
        n_added += 1
    tmp = PENDING_CFG_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(existing, indent=2, default=str))
    tmp.replace(PENDING_CFG_PATH)
    return n_added, n_skipped, len(existing)
