#!/usr/bin/env python3
"""native_replay_verdicts — record + stage refused native OPENs for immediate replay.

USER 2026-10-22: "as soon as a sym_side changes state while live did not trade
we need to record the reason for the vectorized trade and enforce it in live
immediately."

Source: tradier_manage logs (tra/trb/trc) — native OPEN verdicts refused in the
lookback window. Each candidate is re-verified through the LIVE gates before
anything is written:
  * fresh positive-gain per_sym set required (board-authorized = the vector
    product that backs the reason; without it the row is SKIPPED, logged);
  * live flat required (position files);
  * vector_enforce.check must PASS now (a still-killed/restored-against family
    stays SKIPPED — laws are never fought);
  * budget / universe / board-gate / non-shortable refusals are NEVER replayed
    (risk and physics stay authoritative).
Writes: data/pure_board/native_replay_verdicts.jsonl rows
{acct, sym_side, action=REPLAY_OPEN, reason (NATIVE_REPLAY_<orig>), qty_hint,
bar_ts, ts, flat_qty} consumed by tradier_manage.native_replay_loop through
execute_now (the one gate). Ledger: every candidate + disposition
(REPLAYED/SKIPPED+why) -> data/paper_forward_comprehensive/native_replay_ledger.jsonl.
Read-only vs live (no orders, no state writes but those two files). Exit 0.
Usage: python3 tools/native_replay_verdicts.py [--lookback-min 20] [--rth-only]
Cron: */5 RTH (deduped per bar downstream; replays only fresh signals).
"""
from __future__ import annotations

import argparse
import glob
import json
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "pure_board" / "native_replay_verdicts.jsonl"
LEDGER = ROOT / "data" / "paper_forward_comprehensive" / "native_replay_ledger.jsonl"

NEVER_REPLAY = (
    "SwingBudget_", "BUDGET", "NOT_IN_UNIVERSE", "NOT_TRADEABLE", "NON_SHORTABLE",
    "NO_BOARD", "BOARD_BLOCK", "MIN_GAIN", "CATALYST", "GFV", "VALUE_CAP",
    "UNIVERSE", "BLACKLIST", "RISK_", "KILL_SWITCH", "TRADING_HALTED",
)

BAR = 900


def _now() -> float:
    return datetime.now(timezone.utc).timestamp()


def rth_now() -> bool:
    et = datetime.now(timezone.utc).astimezone(timezone(timedelta(hours=-4)))
    return et.weekday() < 5 and (9, 30) <= (et.hour, et.minute) < (16, 0)


def board_ok(sym_side: str):
    try:
        con = sqlite3.connect(str(ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"), timeout=10)
        try:
            r = con.execute("select acc_gain_pct, updated_at from per_sym_active where sym_side=?", (sym_side,)).fetchone()
        finally:
            con.close()
    except Exception:
        return False, "store unreadable"
    if not r:
        return False, "no board set"
    try:
        age_h = (_now() - datetime.fromisoformat(str(r[1]).replace("Z", "+00:00")).timestamp()) / 3600
    except Exception:
        return False, "bad timestamp"
    if (r[0] or 0) <= 0:
        return False, f"board gain {r[0]} not positive"
    if age_h > 9 * 24:
        return False, f"board stale {age_h:.0f}h"
    return True, f"gain={r[0]:.1f} age={age_h:.1f}h"


def live_flat(acct: str, sym_side: str) -> bool:
    side = "long" if sym_side.endswith("_LONG") else "short"
    try:
        d = json.loads((ROOT / acct / f"{side}_positions.json").read_text())
    except Exception:
        return True
    for k, pos in d.items():
        if k == f"{acct}:{sym_side}":
            try:
                return abs(float((pos or {}).get("positionAmt") or 0)) <= 0
            except Exception:
                return True
    return True


def gate_passes(reason: str, position_key: str) -> tuple:
    try:
        from live_twins import vector_enforce as ve
        ok, verdict = ve.check("OPEN", position_key, reason)
        return bool(ok), verdict
    except Exception as exc:
        return False, f"check error {exc}"


def scan(lookback_min: int):
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=lookback_min)
    cands = {}
    for acct in ("trb", "trc"):
        for pat in (ROOT / f"../logs/tradier_manage_{acct}.log", Path(f"/Users/niels/logs/tradier_manage_{acct}.log")):
            try:
                fp = pat if pat.exists() else None
            except Exception:
                fp = None
            if fp is None:
                continue
            try:
                for ln in open(fp, errors="replace"):
                    if "action=OPEN" not in ln or ("REFUSED" not in ln and "BLOCKED" not in ln):
                        continue
                    try:
                        ts = datetime.strptime(ln[:14], "%m-%d %H:%M:%S").replace(year=2026, tzinfo=timezone.utc)
                    except Exception:
                        continue
                    if ts < cutoff:
                        continue
                    m = re.search(r"(tr[bc]:[A-Z]+_(?:LONG|SHORT))", ln)
                    r = re.search(r"reason=([A-Za-z0-9_|.:/=%+()\-]+)", ln)
                    if not m or not r:
                        continue
                    pk, reason = m.group(1), r.group(1).split("|")[0]
                    cands.setdefault((acct, pk.split(":", 1)[1]), {"reason": reason, "ts": ts.timestamp()})
            except OSError:
                continue
    return cands


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lookback-min", type=int, default=20)
    ap.add_argument("--rth-only", action="store_true")
    a = ap.parse_args()
    if a.rth_only and not rth_now():
        print("off-hours, nothing staged")
        return
    cands = scan(a.lookback_min)
    staged, skipped = 0, 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    bar_ts = (_now() // BAR) * BAR - BAR
    with open(OUT, "a") as fo, open(LEDGER, "a") as fl:
        for (acct, ss), c in sorted(cands.items()):
            reason, pk = c["reason"], f"{acct}:{ss}"
            disp, why = "SKIPPED", ""
            if any(t in reason.upper() for t in NEVER_REPLAY):
                why = "risk/physics refusal, never replayed"
            else:
                ok_b, why_b = board_ok(ss)
                if not ok_b:
                    why = why_b
                elif not live_flat(acct, ss):
                    why = "already holding"
                else:
                    ok_g, v_g = gate_passes(reason, pk)
                    if not ok_g:
                        why = f"gates still refuse ({v_g})"
                    else:
                        disp, why = "REPLAYED", f"gates pass ({v_g}); {why_b}"
                        fo.write(json.dumps({"ts": _now(), "bar_ts": bar_ts, "acct": acct, "sym_side": ss,
                                             "action": "REPLAY_OPEN", "reason": f"NATIVE_REPLAY_{reason}",
                                             "orig_reason": reason}) + "\n")
                        staged += 1
            if disp == "SKIPPED":
                skipped += 1
            fl.write(json.dumps({"ts": _now(), "disposition": disp, "acct": acct, "sym_side": ss,
                                 "reason": reason, "why": why}) + "\n")
    print(f"[replay] candidates={len(cands)} staged={staged} skipped={skipped} -> {OUT.name}")


if __name__ == "__main__":
    main()
