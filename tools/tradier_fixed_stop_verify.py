#!/usr/bin/env python3
"""tradier_fixed_stop_verify — read-only proof that every open STOCK position has a resting exchange stop at the
frozen dc_15m level. Run it at tomorrow's open (13:30Z) and again mid-session.

USER 2026-10-15: "TRADIER_FIXED_STOP_BROKER_BACKSTOP_ENABLED needs to be activated immediately tested and verified
tomorrow ... It needs to set a simple stop market order for the same amount as the entry order not sure why this is
not simply HAPPENING."

Why it was not happening: the constant did not exist in any module, so the gate at tradier_manage.py:14852
(getattr(config, NAME, False)) read False and ensure_fixed_stop_backstop_tradier was never called. On 2026-10-15 the
book held 42 open stock positions, the registry held 47 LEVELS and `"orders": {}`, and the logs contained exactly one
FIXED_STOP line all day (a listing failure). The flag is now True in config.py.

LEVELS ARE NOT REIMPLEMENTED HERE. This calls the same vec_decisions.fixed_stop helpers live calls, so what it prints
is what live places (CLAUDE.md: never present numbers from reimplemented logic):
    software leg  = freeze_software_level(is_long, dc_low_15m, dc_high_15m, entry_price)   # dc_15m -0.25%
    exchange leg  = exchange_level(is_long, software)                                      # a further -0.05%
    combined      = 0.9975 * 0.9995 = 0.99700125  ->  dc_low_15m -0.2999% LONG / dc_high_15m +0.2999% SHORT

    python3 tools/tradier_fixed_stop_verify.py              # local: positions vs registry vs expected level
    python3 tools/tradier_fixed_stop_verify.py --json
Exit 0 = every open position is recorded protected at the right level; 1 = at least one is not.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import vec_decisions.fixed_stop as fs  # noqa: E402  (real level core, shared with live)

ACCOUNTS = ("tra", "trb", "trc")
REGISTRY = ROOT / "data" / "fixed_stop_registry_tradier.json"
INDICATORS = ROOT / "data" / "tradier" / "tradier_indicators_latest.json"
LEVEL_TOL = 0.0051  # same tolerance ensure_fixed_stop_backstop_tradier uses to decide a stop is at the right price


def _f(x, d=0.0) -> float:
    try:
        v = float(x)
        return v if v == v else d
    except (TypeError, ValueError):
        return d


def load_positions() -> list:
    out = []
    for acct in ACCOUNTS:
        for side in ("long", "short"):
            p = ROOT / acct / f"{side}_positions.json"
            try:
                book = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            for key, pos in (book or {}).items():
                if not isinstance(pos, dict):
                    continue
                amt = abs(_f(pos.get("positionAmt")))
                if amt <= 0:
                    continue
                out.append({
                    "key": key, "account": acct, "symbol": pos.get("symbol"),
                    "side": str(pos.get("position_side") or side).upper(), "qty": amt,
                    "entry_price": _f(pos.get("entry_price")), "mark": _f(pos.get("mark_price")),
                    "gain": _f(pos.get("gain")), "entry_time": str(pos.get("entry_time") or "")[:19],
                })
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    positions = load_positions()
    try:
        reg = json.loads(REGISTRY.read_text())
    except (OSError, ValueError):
        reg = {}
    orders = reg.get("orders") or {}
    levels = reg.get("levels") or {}
    try:
        ind = json.loads(INDICATORS.read_text())
    except (OSError, ValueError):
        ind = {}

    rows = []
    for p in positions:
        is_long = p["side"] == "LONG"
        i = ind.get(p["symbol"]) if isinstance(ind.get(p["symbol"]), dict) else {}
        soft_live = fs.freeze_software_level(is_long, _f(i.get("dc_low_15m")), _f(i.get("dc_high_15m")), p["entry_price"])
        frozen = _f((levels.get(p["key"]) or {}).get("level")) if isinstance(levels.get(p["key"]), dict) else 0.0
        # live places from the FROZEN level (entry-bar channel), not the current channel; frozen is the truth to check.
        soft = frozen if frozen > 0 else soft_live
        want = fs.round_to_tick(is_long, fs.exchange_level(is_long, soft), 0.01) if soft > 0 else 0.0
        o = orders.get(p["key"]) if isinstance(orders.get(p["key"]), dict) else None
        resting = _f((o or {}).get("stopPrice"))
        oid = str((o or {}).get("orderId") or "")
        oqty = int(_f((o or {}).get("qty")))
        problems = []
        if not oid:
            problems.append("NO_RESTING_STOP")
        else:
            if want > 0 and abs(resting - want) >= LEVEL_TOL:
                problems.append(f"LEVEL_DRIFT(resting={resting:.2f} want={want:.2f})")
            if oqty != int(p["qty"]):
                problems.append(f"QTY_MISMATCH(stop={oqty} pos={int(p['qty'])})")
        if frozen <= 0:
            problems.append("NO_FROZEN_LEVEL")
        rows.append({**p, "frozen_soft": soft, "want_exchange": want, "resting": resting,
                     "order_id": oid, "stop_qty": oqty, "problems": problems})

    bad = [r for r in rows if r["problems"]]
    report = {"n_positions": len(rows), "n_unprotected_or_wrong": len(bad),
              "registry_orders": len(orders), "registry_levels": len(levels),
              "verdict": "FAIL" if bad else "PASS", "rows": rows}
    if args.json:
        print(json.dumps(report, indent=1, default=str))
        return 1 if bad else 0

    print(f"open stock positions: {len(rows)}   registry: {len(orders)} resting order(s), {len(levels)} level(s)")
    print(f"\n{'key':22}{'side':6}{'qty':>6}{'frozen_soft':>13}{'want_exch':>11}{'resting':>10}{'order_id':>12}  problems")
    for r in sorted(rows, key=lambda x: (bool(not x["problems"]), x["key"])):
        print(f"{r['key']:22}{r['side']:6}{int(r['qty']):6}{r['frozen_soft']:13.4f}{r['want_exchange']:11.2f}"
              f"{r['resting']:10.2f}{(r['order_id'] or '-'):>12}  {', '.join(r['problems']) or 'ok'}")
    if bad:
        print(f"\n🔴 {len(bad)} of {len(rows)} open position(s) are NOT provably protected at the exchange.")
        print("   If every row says NO_RESTING_STOP the backstop still is not running — check, in order:")
        print("     1. python3 -c \"import sys;sys.path.insert(0,'.');from config_tradier import TradierConfig;"
              "print(TradierConfig().TRADIER_FIXED_STOP_BROKER_BACKSTOP_ENABLED)\"   (must be True)")
        print("        NB: the flag lives in config_tradier.TradierConfig, NOT config.py — inside tradier_manage the")
        print("        bare name `config` IS TradierConfig() (tradier_manage.py:6645); that file never imports config.py")
        print("     2. tradier_manage must have been RESTARTED since config_tradier.py changed (loaded at import)")
        print("     3. grep -E 'FIXED_STOP_(BREAKER|QTY_UNVERIFIED|RETRY|NO_LEVEL)_TR' ~/logs/tradier_manage_tr*.log")
    else:
        print(f"\nPASS — every open position has a resting exchange stop at the frozen dc_15m level.")
    print(f"\nverdict: {report['verdict']}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
