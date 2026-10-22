#!/usr/bin/env python3
"""vec_native_parity_watch — 48h discrepancy catch for removing the vec backstop.

USER 2026-10-22: most ez_ opens/closes are VEC_DRIVEN (correct: the vector
defines open vs closed), but native live must reach the SAME decision by its
own functions so the vec fishnet can come out before LTF trading.

Read-only audit, safe to run every 15m from cron. Per run it appends ONE row
to data/vec_native_parity_watch.jsonl and prints ALERT lines for:
  1. a NEW breach-gate string never seen before (a new native refusal mode),
  2. filled==0 while received>50 on an ARMED account (dictates dying),
  3. breach-rate spike vs the trailing 24h baseline (>3x mean).

Locked files are never touched; this script only reads ledgers the system
already writes. Scheduler-track file: Mac-only dev, never fleet-pushed.
"""
from __future__ import annotations

import collections
import datetime as dt
import glob
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
OUT = ROOT / "data" / "vec_native_parity_watch.jsonl"
SEEN = ROOT / "data" / "vec_native_parity_watch_seen.json"


def _parse_ts(v) -> float:
    if v is None:
        return 0.0
    if isinstance(v, (int, float)):
        f = float(v)
        return f / 1000.0 if f > 1e11 else f
    try:
        return dt.datetime.fromisoformat(str(v).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def _jsonl_since(path: Path, since: float):
    out = []
    try:
        with open(path, errors="replace") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if _parse_ts(rec.get("ts") or rec.get("timestamp")) >= since:
                    out.append(rec)
    except OSError:
        pass
    return out


def main() -> int:
    now = time.time()
    day = dt.datetime.fromtimestamp(now, dt.timezone.utc).strftime("%Y-%m-%d")
    alerts: list[str] = []

    intents = _jsonl_since(ROOT / "data" / "pure_board" / f"intents_{day}.jsonl", now - 900)
    acts = collections.Counter(str(r.get("action") or "?") for r in intents)

    breaches = _jsonl_since(ROOT / "data" / "vector_parity_breaches.jsonl", now - 900)
    gates = collections.Counter(str(r.get("gate", "?"))[:60] for r in breaches)

    try:
        seen = set(json.loads(SEEN.read_text()))
    except Exception:
        seen = set()
    fresh = sorted(set(gates) - seen)
    for gate in fresh:
        alerts.append(f"ALERT NEW_GATE {gate} x{gates[gate]} in last 15m")
    if fresh:
        SEEN.write_text(json.dumps(sorted(seen | set(gates))))

    exec_rows: dict[str, collections.Counter] = {}
    for acct in ACCOUNTS:
        recs = _jsonl_since(ROOT / "data" / "vec_live" / f"live_exec_{acct}.jsonl", now - 3600)
        exec_rows[acct] = collections.Counter(str(r.get("result", "?"))[:48] for r in recs if not r.get("reconcile"))

    mon_last: dict = {}
    for line in (ROOT / "data" / "vec_live" / "monitor.jsonl").read_text().splitlines()[-500:]:
        try:
            rec = json.loads(line)
        except Exception:
            continue
        mon_last[rec.get("account")] = rec
    for acct in ACCOUNTS:
        row = mon_last.get(acct) or {}
        actionable = sum(v for k, v in exec_rows[acct].items()
                         if not k.startswith(("SKIPPED_ALREADY", "SKIPPED_FLAT", "SAME_DICTATE")))
        if row.get("state") == "ARMED" and actionable > 0 and int(row.get("filled_1h") or 0) == 0:
            alerts.append(f"ALERT NO_FILLS {acct} actionable={actionable} filled=0 last hour")

    base = _jsonl_since(ROOT / "data" / "vector_parity_breaches.jsonl", now - 86400)
    base_rate = len(base) / 96.0
    if base_rate > 1.0 and len(breaches) > 3 * base_rate:
        alerts.append(f"ALERT BREACH_SPIKE {len(breaches)}/15m vs 24h-mean {base_rate:.1f}/15m")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "a") as fh:
        fh.write(json.dumps({
            "ts": now,
            "utc": dt.datetime.fromtimestamp(now, dt.timezone.utc).isoformat(),
            "intents_15m": dict(acts),
            "breach_gates_15m": dict(gates),
            "exec_1h": {a: dict(exec_rows[a].most_common(8)) for a in ACCOUNTS},
            "alerts": alerts,
        }) + "\n")
    print(f"[parity-watch] intents_15m={dict(acts)} breaches_15m={len(breaches)} gates={dict(gates.most_common(5))}")
    for alert in alerts:
        print(alert, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
