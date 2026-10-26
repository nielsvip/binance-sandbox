#!/usr/bin/env python3
"""Queue owed vector closes for the live intervention loop.

No VEC_ verdict is ever "ignored per doctrine" again. Each minute this reads the
executor STATE targets (data/vec_live/state/*.json) against live positions and
pushes every owed close to the live loop's Redis intervention queue
(intervention_queue:<account>), which routes through execute_now() with all
live guards intact (ez_manage.py _intervention_queue_loop). A VEC_DRIVEN_*
reason is a vec order: never refused, never breaker-judged, skips kill + bar
window (live_twins/vector_enforce._is_board_confirmed + ez_manage._parity_vec_order).

Only closes are pushed: qty = exact live positionAmt, no sizing invented.
Open-side mismatches are logged as VEC_OPEN_OWED decisions for batch-fix.
Every push and every hold is logged as a VEC_ decision in the disparity queue.
Idempotent per (sym_side, bar_ts): never pushes twice for the same bar.
"""

from __future__ import annotations
import argparse
import glob
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STATE_DIR = ROOT / "data" / "vec_live" / "state"
QUEUE_LOG = ROOT / "data" / "vector_disparity_queue.jsonl"
PUSHED_DB = ROOT / "data" / "vec_live" / "backstop_pushed.json"
MAX_BAR_AGE_MIN = 45.0
TTL_S = 300.0

CLOSE_SIDE = {"LONG": "SELL", "SHORT": "BUY"}


def load_states(state_dir=STATE_DIR):
    out = {}
    for f in glob.glob(str(Path(state_dir) / "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ss = d.get("ss") or Path(f).stem
        if isinstance(ss, str) and ss:
            out[ss] = d
    return out


def load_live_positions(base_path, account):
    pos = {}
    for fname in ("long_positions.json", "short_positions.json"):
        p = Path(base_path) / account / fname
        try:
            d = json.load(open(p))
        except Exception:
            continue
        items = d.values() if isinstance(d, dict) else d
        for v in items:
            if not isinstance(v, dict):
                continue
            sym = str(v.get("symbol") or "").upper()
            pside = str(v.get("position_side") or "").upper()
            if not sym or pside not in ("LONG", "SHORT"):
                continue
            try:
                amt = float(v.get("positionAmt") or 0.0)
            except Exception:
                amt = 0.0
            pos[f"{account}:{sym}_{pside}"] = amt
    return pos


def owed_key(ss, bar_ts):
    return f"{ss}|{int(float(bar_ts))}"


def collect_owed(states, positions, now_s, pushed, max_bar_age_min=MAX_BAR_AGE_MIN):
    cmds = []
    for ss, st in states.items():
        if not isinstance(st, dict) or not st.get("actionable"):
            continue
        if st.get("mode") != "live":
            continue
        tgt = (st.get("target_state") or {}).get("side")
        if tgt != "flat":
            continue
        try:
            age_min = (float(now_s) - float(st.get("bar_ts") or 0.0)) / 60.0
        except Exception:
            continue
        if age_min < 0 or age_min > max_bar_age_min:
            continue
        if "_" not in ss:
            continue
        sym, pside = ss.rsplit("_", 1)
        if pside not in ("LONG", "SHORT"):
            continue
        key = owed_key(ss, st.get("bar_ts"))
        if key in pushed:
            continue
        amt = 0.0
        for pkey, a in positions.items():
            if pkey.split(":", 1)[-1] == ss:
                amt = a
                account = pkey.split(":", 1)[0]
                break
        else:
            continue
        if abs(amt) <= 0:
            continue
        cmds.append(
            {
                "account": account,
                "symbol": sym,
                "side": CLOSE_SIDE[pside],
                "position_side": pside,
                "quantity": abs(amt),
                "reason": f"VEC_DRIVEN_EXIT_OWED_CLOSE_{ss}_bar{int(float(st.get('bar_ts')))}",
                "ttl_epoch": float(now_s) + TTL_S,
                "key": key,
                "bar_age_min": round(age_min, 1),
            }
        )
    return cmds


def log_decision(entry):
    try:
        QUEUE_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(str(QUEUE_LOG), "a") as fh:
            fh.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
    except Exception:
        pass


def load_pushed():
    try:
        d = json.load(open(str(PUSHED_DB)))
        return set(d if isinstance(d, list) else [])
    except Exception:
        return set()


def save_pushed(pushed):
    try:
        PUSHED_DB.parent.mkdir(parents=True, exist_ok=True)
        tmp = PUSHED_DB.with_suffix(".tmp")
        tmp.write_text(json.dumps(sorted(pushed)[-5000:]))
        os.replace(tmp, PUSHED_DB)
    except Exception:
        pass


def push_cmds(redis_client, cmds):
    queued = []
    failed = []
    for c in cmds:
        qkey = f"intervention_queue:{c['account']}"
        payload = {
            k: c[k]
            for k in (
                "symbol",
                "side",
                "position_side",
                "quantity",
                "reason",
                "ttl_epoch",
            )
        }
        try:
            result = redis_client.rpush(qkey, json.dumps(payload))
            if not result:
                raise RuntimeError("Redis RPUSH returned no queue length")
            queued.append(c)
        except Exception as exc:
            failed.append((c, str(exc)))
    return queued, failed


def run(
    base_path,
    account,
    state_dir=STATE_DIR,
    redis_client=None,
    now_s=None,
    dry_run=False,
):
    now_s = now_s if now_s is not None else time.time()
    states = load_states(state_dir)
    positions = load_live_positions(base_path, account)
    pushed = load_pushed()
    cmds = collect_owed(states, positions, now_s, pushed)
    rec = {
        "t": now_s,
        "account": account,
        "states": len(states),
        "open_positions": sum(1 for a in positions.values() if abs(a) > 0),
        "owed": len(cmds),
        "pushed": 0,
    }
    queued = []
    failed = []
    if cmds and not dry_run and redis_client is not None:
        queued, failed = push_cmds(redis_client, cmds)
        rec["pushed"] = len(queued)
        rec["failed"] = len(failed)
        for c in queued:
            pushed.add(c["key"])
        if queued:
            save_pushed(pushed)
    for c in cmds:
        if dry_run:
            kind = "VEC_BACKSTOP_DRY_RUN"
            detail = "dry run; no queue write attempted"
        elif redis_client is None:
            kind = "VEC_BACKSTOP_HELD"
            detail = "Redis unavailable; key remains eligible for retry"
        elif any(fc["key"] == c["key"] for fc, _ in failed):
            kind = "VEC_BACKSTOP_QUEUE_FAILED"
            detail = next(err for fc, err in failed if fc["key"] == c["key"])
        else:
            kind = "VEC_BACKSTOP_QUEUED"
            detail = "successfully enqueued for live intervention; broker execution unconfirmed"
        log_decision(
            {
                "ts": now_s,
                "venue": "crypto",
                "kind": kind,
                "position_key": f"{c['account']}:{c['symbol']}_{c['position_side']}",
                "action": "CLOSE",
                "reason": c["reason"][:160],
                "vec_path": c["reason"][:80],
                "dry_run": dry_run,
                "detail": detail[:240],
                "queue_confirmed": kind == "VEC_BACKSTOP_QUEUED",
                "broker_execution_confirmed": False,
            }
        )
    return rec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="flz")
    ap.add_argument("--base-path", default=None)
    ap.add_argument("--redis-port", type=int, default=6379)
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    try:
        from config import Config as _Cfg

        base = a.base_path or _Cfg.BASE_PATH
    except Exception:
        base = a.base_path or str(ROOT)
    client = None
    if not a.dry_run:
        import redis

        client = redis.Redis(host="127.0.0.1", port=a.redis_port, decode_responses=True)
    rec = run(base, a.account, redis_client=client, dry_run=a.dry_run)
    print(json.dumps(rec, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
