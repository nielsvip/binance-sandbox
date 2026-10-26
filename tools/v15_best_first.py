#!/usr/bin/env python3
"""Frozen previous-round gain order and current side-exact trading universe.

The rank is frozen once per round. Tradeability is deliberately refreshed on
every publication; a stale rank never grants permission to run a side.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ("ang", "flz", "men", "fin", "inf")


def _read(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return None


def tradeable(root=ROOT):
    """Only current account side books, intersected with crypto tradeable keys.

    A held position or a mandatory config entry cannot create a new entry or a
    backtest. An unreadable source fails closed for that venue/account.
    """
    root = Path(root)
    keys = _read(root / "tradeable_keys.json")
    keys = keys.get("keys") if isinstance(keys, dict) else keys
    keyed = {}
    for item in keys if isinstance(keys, list) else ():
        account, sep, ss = str(item).upper().partition(":")
        if sep and ss.endswith(("_LONG", "_SHORT")):
            keyed.setdefault(account.lower(), set()).add(ss)
    out = set()
    for account in ACCOUNTS:
        for side in ("LONG", "SHORT"):
            source = "men" if account == "fin" and _fin_mirrors_men() else account
            rows = _read(root / f"symbols_{source}_{side.lower()}.json")
            if isinstance(rows, dict):
                rows = rows.get("symbols")
            if isinstance(rows, list):
                out.update({f"{str(s).upper()}_{side}" for s in rows} & keyed.get(account, set()))
    for side in ("LONG", "SHORT"):
        rows = _read(root / f"symbols_trb_{side.lower()}.json")
        if isinstance(rows, dict):
            rows = rows.get("symbols")
        if isinstance(rows, list):
            out.update(f"{str(s).upper()}_{side}" for s in rows)
    excluded = _read(root / "data" / "universe_exclude.json")
    if isinstance(excluded, dict):
        out.difference_update(str(s).upper() for s in excluded.get("exclude_sym_sides", []))
    try:
        from excluded_tokenized_stocks import load_denied, symbol_of
        deny = load_denied(root)
        out = {ss for ss in out if symbol_of(ss) not in deny}
    except Exception:
        # The stock deny source is required for stock admission.
        out = {ss for ss in out if ss.rsplit("_", 1)[0].endswith(("USDT", "USDC"))}
    return out


def _fin_mirrors_men():
    try:
        from account_comparison_books import source_account
        return source_account("fin") == "men"
    except Exception:
        return False


def _gain(row):
    if not isinstance(row, dict):
        return None
    for key in ("final_gain", "cumulative_gain", "potential"):
        try:
            v = row.get(key)
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            pass
    return None


def round_id(root=ROOT):
    try:
        p = (Path.home() / "v15_current_progress_dir.txt").read_text().strip()
        return Path(p).parent.name
    except OSError:
        return "unknown"


def publish(root=ROOT, round_name=None):
    root = Path(root)
    round_name = round_name or round_id(root)
    dest = root / "data" / "v15_best_first.json"
    frozen = root / "data" / "best_first_rounds" / f"{round_name}.json"
    frozen.parent.mkdir(parents=True, exist_ok=True)
    previous = _read(frozen)
    if not isinstance(previous, dict) or previous.get("round") != round_name:
        ledger = _read(root / "data" / "v15_symside_ledger.json") or {}
        gains = {str(ss).upper(): g for ss, row in (ledger.get("sym_sides") or {}).items()
                 if (g := _gain(row)) is not None}
        previous = {"round": round_name, "frozen_at": time.time(), "gains": gains}
        tmp = frozen.with_suffix(".tmp")
        tmp.write_text(json.dumps(previous, sort_keys=True))
        os.replace(tmp, frozen)
    result = dict(previous, generated_at=time.time(), allowed=sorted(tradeable(root)))
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(json.dumps(result, sort_keys=True))
    os.replace(tmp, dest)
    return result


def rank_key(ss, snapshot):
    g = (snapshot.get("gains") or {}).get(ss)
    return (0, -float(g), ss) if g is not None else (1, 0.0, ss)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--publish", action="store_true")
    p.add_argument("--ordered")
    p.add_argument("--allowed")
    p.add_argument("--snapshot", default=str(ROOT / "data" / "v15_best_first.json"))
    a = p.parse_args()
    if a.publish:
        d = publish()
        print(f"{d['round']} allowed={len(d['allowed'])} ranked={len(d['gains'])}")
        return
    d = _read(a.snapshot)
    if not isinstance(d, dict) or time.time() - float(d.get("generated_at") or 0) > 600:
        sys.exit(2)
    allowed = set(d.get("allowed") or [])
    if a.allowed:
        sys.exit(0 if a.allowed.upper() in allowed else 1)
    if a.ordered:
        sides = [s.strip().upper() for s in Path(a.ordered).read_text().splitlines()]
        print("\n".join(sorted({s for s in sides if s in allowed}, key=lambda s: rank_key(s, d))))


if __name__ == "__main__":
    main()
