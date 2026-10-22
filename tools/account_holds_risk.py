#!/usr/bin/env python3
"""account_holds_risk — does this account hold ANY open position right now?

USER 2026-10-22, verbatim: "can you ABSOLUTELY GUARANTEE no account can stop trading EVER when there is any open
position?" — the honest answer was no, because NONE of the four layers that can restore trading
(`tools/never_untraded_guard.py`, `tools/mac_pos_writer_keepalive.sh`, `run_with_watchdog.sh`,
`crypto_watchdog_cron.sh`) read position state at all. "There is an open position" could not change any decision
because nothing asked. This is the thing that asks, and it is deliberately the ONLY implementation so the three
call sites cannot drift apart.

TRUTH SOURCE is the same one `tools/v15_open_positions_push.py` publishes from and the same one
`tools/tradier_position_stop_audit.py` audits against: `{ROOT}/{acct}/{long,short}_positions.json`, dict keyed
"acct:SYM_SIDE", open = |positionAmt| > 0. Written only by the sanctioned writers
(ez_positions_service / ez_positions / tradier_positions / ez_manage / ez_positions_quick / tradier_manage, per
`ez_positions_service.py:1732`). Nothing here writes; it is read-only.

FAIL-SAFE DIRECTION, AND WHY IT IS NOT SYMMETRIC. Unreadable, missing-but-expected, or malformed files report
HOLDS RISK, never FLAT. The two errors do not cost the same: a false "holds risk" costs one extra engine restart,
a false "flat" costs exactly the guarantee this exists to provide — an account sitting on real exposure with a
dead engine because a json file happened to be mid-write. Only a file that parses and genuinely contains no
nonzero positionAmt returns FLAT.

Exit codes (so bash can branch without parsing):
    0  holds risk (or unknown -> treated as holds risk; `reason` says which)
    1  provably flat
    2  bad usage

Usage:
    tools/account_holds_risk.py --account men            # exit 0/1, one line to stdout
    tools/account_holds_risk.py --account men --quiet    # exit code only
    tools/account_holds_risk.py --json                   # census of every account
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO = ("flz", "men", "ang", "inf", "fin")
STOCKS = ("tra", "trb", "trc")
ACCOUNTS = CRYPTO + STOCKS


def account_risk(acct: str, root: Path | None = None) -> dict:
    """{holds: bool, unknown: bool, n: int, keys: [...], reason: str} for one account. Never raises."""
    base = Path(root) if root else ROOT
    acct = str(acct or "").strip().lower()
    if acct not in ACCOUNTS:
        return {"holds": True, "unknown": True, "n": 0, "keys": [], "reason": f"unknown account {acct!r} — treated as holding"}
    keys: list[str] = []
    seen_any_file = False
    for side in ("long", "short"):
        p = base / acct / f"{side}_positions.json"
        if not p.exists():
            continue
        seen_any_file = True
        try:
            d = json.loads(p.read_text())
        except Exception as exc:
            return {"holds": True, "unknown": True, "n": 0, "keys": [],
                    "reason": f"{p.name} unreadable ({type(exc).__name__}) — treated as holding"}
        if not isinstance(d, dict):
            return {"holds": True, "unknown": True, "n": 0, "keys": [],
                    "reason": f"{p.name} is not a dict — treated as holding"}
        for k, v in d.items():
            if not isinstance(v, dict):
                continue
            try:
                amt = abs(float(v.get("positionAmt") or 0.0))
            except Exception:
                return {"holds": True, "unknown": True, "n": 0, "keys": [],
                        "reason": f"{p.name}:{k} positionAmt unparseable — treated as holding"}
            if amt > 0:
                keys.append(str(k).split(":", 1)[-1].upper())
    if not seen_any_file:
        return {"holds": True, "unknown": True, "n": 0, "keys": [],
                "reason": f"no positions file for {acct} — treated as holding"}
    keys = sorted(set(keys))
    return {"holds": bool(keys), "unknown": False, "n": len(keys), "keys": keys,
            "reason": (f"{len(keys)} open position(s)" if keys else "provably flat")}


def census(root: Path | None = None) -> dict:
    return {a: account_risk(a, root) for a in ACCOUNTS}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--root", default=None, help="test fixture root")
    a = ap.parse_args()
    root = Path(a.root) if a.root else None
    if a.json and not a.account:
        print(json.dumps(census(root), indent=1))
        return 0
    if not a.account:
        print("need --account <acct> or --json", file=sys.stderr)
        return 2
    r = account_risk(a.account, root)
    if a.json:
        print(json.dumps(r))
    elif not a.quiet:
        print(f"{a.account}: holds_risk={r['holds']} unknown={r['unknown']} n={r['n']} — {r['reason']}")
    return 0 if r["holds"] else 1


if __name__ == "__main__":
    sys.exit(main())
