#!/usr/bin/env python3
"""Build the controlling 1-month lifecycle campaign order.

This scheduler is deliberately separate from the optimizer. It decides which
current live symbol/side gets compute next; lifecycle_pilot.py still owns the
within-side hierarchy, gates, checkpointing, verification, and promotion.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from tools.opt import lifecycle_pilot as pilot

DEFAULT_OUTPUT = ROOT / "data" / "reports" / "lifecycle_pilot" / "campaign_order_1mo.json"


def _symbols(name: str) -> list[str]:
    """Read a symbol list; recover quoted tokens from a concurrently torn file."""
    # 2026-09-06: CRYPTO TESTS ONLY TRADEABLE KEYS — filter to tradeable (symbols_flz etc already filtered, defense in depth)
    path = ROOT / name
    try:
        raw = json.loads(path.read_text())
        values = list(raw) if isinstance(raw, (list, dict)) else []
    except (OSError, ValueError):
        try:
            values = re.findall(r'"([A-Za-z0-9._-]+)"', path.read_text())
        except OSError:
            values = []
    vals = list(dict.fromkeys(str(value).upper() for value in values if value))
    # Filter crypto long/short and bare lists to tradeable
    try:
        _tk_p = ROOT / "tradeable_keys.json"
        _ps_p = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
        _tk_set: set[str] = set()
        _ps_tradeable: set[str] = set()
        if _tk_p.exists():
            _tk_raw = json.loads(_tk_p.read_text())
            if isinstance(_tk_raw, list):
                _tk_set = set(_tk_raw)
        if _ps_p.exists():
            _ps_raw = json.loads(_ps_p.read_text())
            _ps_tradeable = {k for k, v in _ps_raw.items() if not k.startswith("_") and isinstance(v, dict) and int(v.get("trades", 0) or 0) > 0 and v.get(k.rsplit("_", 1)[-1] + "_ENABLED", True) is not False}
        def _is_tradeable(_sk: str) -> bool:
            return _sk in _ps_tradeable or any(_k.endswith(f":{_sk}") for _k in _tk_set)
        _no_tradeable_filter = not _tk_set and not _ps_tradeable
        if _no_tradeable_filter:
            pass
        elif name.endswith("_long.json"):
            vals = [s for s in vals if _is_tradeable(f"{s}_LONG")]
        elif name.endswith("_short.json"):
            vals = [s for s in vals if _is_tradeable(f"{s}_SHORT")]
        elif name in ("symbols_flz.json", "symbols_men.json", "symbols_fin.json"):
            vals = [s for s in vals if _is_tradeable(f"{s}_LONG") or _is_tradeable(f"{s}_SHORT")]
    except Exception:
        pass
    return vals


def _performance() -> tuple[Dict[str, Dict[str, Any]], str]:
    path = ROOT / "data" / "reports" / "performance_report.json"
    try:
        report = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}, ""
    return report.get("stats") or {}, str(report.get("generated_at") or "")


def _recent_trb_sides() -> Dict[str, float]:
    """Exact-side evidence from TRB state timestamps within the last 30 days."""
    path = ROOT / "trb" / "state.json"
    try:
        state = json.loads(path.read_text())
    except (OSError, ValueError):
        return {}
    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    scores: Dict[str, float] = {}
    for mapping in state.values():
        if not isinstance(mapping, dict):
            continue
        for key, stamp in mapping.items():
            match = re.search(r"TRB:([A-Z0-9.]+)_(LONG|SHORT)$", str(key).upper())
            if not match:
                continue
            try:
                at = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
            except ValueError:
                continue
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            if at >= cutoff:
                scores[f"{match.group(1)}_{match.group(2)}"] = at.timestamp()
    return scores


def campaign_order() -> Dict[str, Any]:
    recipes = pilot.load_live_recipes()
    eligible = set(recipes)
    stats, performance_generated_at = _performance()
    recent_sides = _recent_trb_sides()
    queue: list[Dict[str, Any]] = []
    seen: set[str] = set()

    def activity(symbol: str, account: str) -> int:
        row = stats.get(symbol, {})
        return int(row.get("trade_count") or 0) if account in (row.get("accounts") or []) else 0

    def add(phase: str, account: str, symbol: str, side: str, reason: str) -> None:
        symside = f"{symbol}_{side}"
        if symside not in eligible or symside in seen:
            return
        seen.add(symside)
        queue.append({"priority": len(queue) + 1, "phase": phase, "account": account,
                      "symside": symside, "symbol": symbol, "side": side,
                      "last_30d_trades": activity(symbol, account),
                      "exact_recent_side": symside in recent_sides, "reason": reason,
                      "window": "30_calendar_days" if pilot.is_crypto_symside(symside)
                                else "30_trading_sessions", "execution_tf": "15m"})

    # 1. FLZ crypto, preserving the explicit operator list and LONG/SHORT pair.
    for symbol in _symbols("symbols_flz.json"):
        for side in ("LONG", "SHORT"):
            add("01_FLZ_CRYPTO", "flz", symbol, side, "explicit FLZ order")

    # 2. Recently traded TRB sides first. Exact-side timestamps outrank the
    # account's last-30-day symbol trade count; file order only breaks ties.
    trb_long, trb_short = _symbols("symbols_trb_long.json"), _symbols("symbols_trb_short.json")
    trb_candidates = [(symbol, "LONG", index) for index, symbol in enumerate(trb_long)]
    trb_candidates += [(symbol, "SHORT", index) for index, symbol in enumerate(trb_short)]
    trb_candidates.sort(key=lambda item: (
        -(recent_sides.get(f"{item[0]}_{item[1]}", 0.0)),
        -activity(item[0], "trb"), item[2], item[0], item[1]))
    for symbol, side, _index in trb_candidates:
        if f"{symbol}_{side}" in recent_sides or activity(symbol, "trb") > 0:
            add("02_TRB_ACTIVE_30D", "trb", symbol, side,
                "exact recent side or TRB last-30-day trade evidence")

    # 3. MEN, FIN, then ANG crypto. Account activity orders symbols when known.
    account_lists = (
        ("men", _symbols("symbols_men_long.json"), _symbols("symbols_men_short.json")),
        ("fin", _symbols("symbols_fin.json"), _symbols("symbols_fin.json")),
        ("ang", _symbols("symbols_ang_long.json"), _symbols("symbols_ang_short.json")),
    )
    for account, longs, shorts in account_lists:
        candidates = [(symbol, "LONG", index) for index, symbol in enumerate(longs)]
        candidates += [(symbol, "SHORT", index) for index, symbol in enumerate(shorts)]
        candidates.sort(key=lambda item: (-activity(item[0], account), item[2], item[1]))
        for symbol, side, _index in candidates:
            add(f"03_{account.upper()}_CRYPTO", account, symbol, side,
                f"{account.upper()} crypto; recent activity before file order")

    # 4. Remaining TRB stock universe, then mandatory configured side lists.
    mandatory = {(symbol, "LONG") for symbol in trb_long} | {(symbol, "SHORT") for symbol in trb_short}
    for symbol in _symbols("symbols_tradier.json"):
        for side in ("LONG", "SHORT"):
            if (symbol, side) not in mandatory:
                add("04_TRB_REMAINING", "trb", symbol, side, "remaining TRB stock universe")
    for symbol in trb_long:
        add("05_TRB_MANDATORY", "trb", symbol, "LONG", "mandatory symbols_trb_long side")
    for symbol in trb_short:
        add("05_TRB_MANDATORY", "trb", symbol, "SHORT", "mandatory symbols_trb_short side")

    return {"schema": "lifecycle-campaign-order-v1", "created_at": pilot.utcnow(),
            "performance_generated_at": performance_generated_at,
            "policy": ["FLZ crypto LONG/SHORT", "TRB active sides from last 30 days",
                       "MEN then FIN then ANG crypto", "remaining TRB stocks",
                       "mandatory TRB long/short lists"],
            "window_contract": {"crypto": "30_calendar_days", "stocks": "30_trading_sessions",
                                "execution_tf": "causal_completed_15m", "synthetic_3m_5m": False},
            "count": len(queue), "queue": queue}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--print", action="store_true", dest="print_queue")
    args = parser.parse_args()
    result = campaign_order()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    if args.print_queue:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print(f"{args.output} ({result['count']} symbol/sides)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
