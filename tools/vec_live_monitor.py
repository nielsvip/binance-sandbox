#!/usr/bin/env python3
"""X3 VEC-DRIVEN LIVE monitor (USER 2026-10-06: "emergency fix ... very dangerous ... closely monitored").

Run on the Mac every 60 s. Per account listed in data/vec_live/vec_driven.json:
  received   intents seen by the consumer (data/vec_live/live_exec_{acct}.jsonl rows, last hour; no-op SKIPPED_ALREADY_* excluded)
  queued     rows whose result starts with QUEUED
  filled     data/history/{acct}/{SS}.jsonl fills with a VEC_DRIVEN_* reason (last hour)
  refused    by consumer result (REFUSED_* / SKIPPED_* / FAILED_*) and by execute_now gate tag grepped from ~/logs/ez_manage_{acct}*.log
  open       vec-driven positions from {acct}/{long,short}_positions.json with unrealised gain %
  last_intent_age_s, mode state (ARMED / EXPIRED / KILL_FILE / disabled)
Writes data/vec_live/monitor.jsonl, prints one status line per account, logs ERROR (also to logs/vec_live_monitor.log) when
  filled < received in each of the last 2 complete 15m bars that had intents, or any open vec-driven position gain < -2 %.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from live_twins import vec_driven as VD  # noqa: E402

BAR = VD.BAR_SECONDS
TAG_RE = re.compile(r"\[([A-Z0-9_]+)\]")


def _jsonl(path: Path, since: float, ts_key: str = "ts"):
    out = []
    if not path.exists():
        return out
    with open(path, errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            t = VD.parse_bar_ts(rec.get(ts_key) or rec.get("timestamp"))
            if t >= since:
                rec["_t"] = t
                out.append(rec)
    return out


def _tail_lines(path: Path, max_bytes: int = 3_000_000):
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - max_bytes))
            return f.read().decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


def gate_refusals(log_dir: Path, account: str) -> Counter:
    c: Counter = Counter()
    for p in log_dir.glob(f"ez_manage_{account}*.log"):
        for line in _tail_lines(p):
            if "VEC_DRIVEN_" not in line or "BLOCK" not in line.upper() or "[VEC_DRIVEN_EXEC]" in line:
                continue
            m = TAG_RE.findall(line)
            tag = next((t for t in m if t not in ("VEC_DRIVEN_LIVE",)), "UNKNOWN")
            c[tag] += 1
    return c


def open_positions(base: Path, account: str, sym_sides):
    out = {}
    for side in ("long", "short"):
        p = base / account / f"{side}_positions.json"
        try:
            data = json.loads(p.read_text())
        except Exception:
            continue
        for ss in sym_sides:
            rec = data.get(f"{account}:{ss}")
            if not isinstance(rec, dict):
                continue
            amt = abs(float(rec.get("positionAmt") or 0.0))
            if amt > 0:
                out[ss] = {"amt": amt, "gain_pct": float(rec.get("gain") or 0.0), "entry": rec.get("entry_price"), "mark": rec.get("mark_price")}
    return out


def run_once(base: Path, log_dir: Path, now: float, log: logging.Logger) -> list:
    vl = VD.vec_live_dir(base)
    reg = VD.VecDrivenRegistry(vl / "vec_driven.json")
    entries = reg.entries()
    live_ok, why = reg.live_allowed(now)
    state = "ARMED" if live_ok else why
    accounts = sorted({a for e in entries.values() for a in ([e.get("account")] if isinstance(e.get("account"), str) else (e.get("account") or e.get("accounts") or []))})
    intents = VD.load_intents(vl / "intents")
    last_intent = max((max(i["_bar_epoch"], VD.parse_bar_ts(i.get("emitted_at"))) for i in intents), default=0.0)
    rows = []
    for acct in accounts:
        modes = reg.sym_sides_for(acct, True)
        sss = sorted(modes)
        ex = [r for r in _jsonl(vl / f"live_exec_{acct}.jsonl", now - 3600) if not r.get("reconcile")]
        rec_rows = [r for r in _jsonl(vl / f"live_exec_{acct}.jsonl", now - 3600) if r.get("reconcile")]
        received = [r for r in ex if not str(r.get("result", "")).startswith(("SKIPPED_ALREADY", "SKIPPED_FLAT"))]
        queued = [r for r in received if str(r.get("result", "")).startswith("QUEUED")]
        refused = Counter(str(r.get("result", "")).split("_0")[0][:40] for r in received if not str(r.get("result", "")).startswith("QUEUED"))
        fills = []
        for ss in sss:
            for h in _jsonl(base / "data" / "history" / acct / f"{ss}.jsonl", now - 3600):
                if str(h.get("reason", "")).upper().startswith("VEC_DRIVEN_"):
                    fills.append(h)
        gates = gate_refusals(log_dir, acct)
        opens = open_positions(base, acct, sss)
        alerts = []
        bars = sorted({int(r["_t"] // BAR) for r in received if r["_t"] < (now // BAR) * BAR})[-2:]
        if len(bars) == 2 and bars[1] - bars[0] == 1:
            short = 0
            for b in bars:
                n_rcv = sum(1 for r in received if int(r["_t"] // BAR) == b)
                n_fill = sum(1 for h in fills if b * BAR <= h["_t"] < (b + 1) * BAR + 300)
                if n_fill < n_rcv:
                    short += 1
            if short == 2:
                alerts.append("EXECUTED_LT_RECEIVED_2_BARS")
        for ss, p in opens.items():
            if p["gain_pct"] < -2.0:
                alerts.append(f"LOSS_GT_2PCT_{ss}_{p['gain_pct']:.2f}")
        row = {"ts": now, "account": acct, "state": state, "expires_utc": reg.expires_at(), "live_sym_sides": [s for s, m in modes.items() if m == "live"], "shadow_sym_sides": [s for s, m in modes.items() if m == "shadow"], "received_1h": len(received), "queued_1h": len(queued), "filled_1h": len(fills), "reconcile_1h": len(rec_rows), "refused_consumer": dict(refused), "refused_gate_log": dict(gates), "open": opens, "unrealised_avg_gain_pct": (sum(p["gain_pct"] for p in opens.values()) / len(opens)) if opens else 0.0, "last_intent_age_s": (now - last_intent) if last_intent else None, "alerts": alerts}
        rows.append(row)
        VD.append_jsonl(vl / "monitor.jsonl", row)
        print(f"[VEC_LIVE] {time.strftime('%H:%M:%S')} {acct} {state} rcv={len(received)} q={len(queued)} fill={len(fills)} rec={len(rec_rows)} refused={sum(refused.values())}+gate{sum(gates.values())} open={len(opens)} uPnL={row['unrealised_avg_gain_pct']:+.2f}% last_intent={int(row['last_intent_age_s']) if row['last_intent_age_s'] is not None else 'n/a'}s alerts={','.join(alerts) or '-'}")
        for a in alerts:
            log.error(f"[VEC_LIVE_ALERT] {acct} {a}")
    if not accounts:
        print(f"[VEC_LIVE] {time.strftime('%H:%M:%S')} no accounts in {vl / 'vec_driven.json'} state={state}")
    return rows


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default=os.environ.get("BASE_PATH", "/Users/niels/Documents/binance"))
    ap.add_argument("--log-dir", default=str(Path.home() / "logs"))
    ap.add_argument("--loop", type=float, default=0.0, help="repeat every N seconds")
    a = ap.parse_args()
    base = Path(a.base)
    (base / "logs").mkdir(exist_ok=True)
    log = logging.getLogger("vec_live_monitor")
    h = logging.FileHandler(base / "logs" / "vec_live_monitor.log")
    h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h)
    log.addHandler(logging.StreamHandler(sys.stderr))
    while True:
        try:
            run_once(base, Path(a.log_dir), time.time(), log)
        except Exception as e:
            log.error(f"[VEC_LIVE_MONITOR] cycle error: {e}")
        if a.loop <= 0:
            return 0
        time.sleep(a.loop)


if __name__ == "__main__":
    sys.exit(main())
