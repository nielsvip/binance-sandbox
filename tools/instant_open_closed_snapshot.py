"""Instant open/closed verdict per sym_side: S1 vector target vs Mac delivered state.

Joins (all Mac-local, pulled from S1 3x/min):
  data/vec_live/intents/*.json      latest vector intent per key (target_state, bar_ts)
  data/vec_live/live_exec_*.jsonl   delivered SUCCESS actions per account
  data/vector_disparity_queue.jsonl backstop holds (disagree/block/refuse)
Verdicts: MATCH / IN_FLIGHT / HELD_BY_BACKSTOP / STALE_BAR / DIVERGED.
Live state is exchange truth via <acct>/{long,short}_positions.json (6s REST
writer). Keys live-open with no intent file are reported NO_INTENT. Exit 1
on DIVERGED.
"""
import glob
import json
import os
import sys
import time

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INTENTS = os.path.join(HERE, "data", "vec_live", "intents")
EXEC = os.path.join(HERE, "data", "vec_live")
QUEUE = os.path.join(HERE, "data", "vector_disparity_queue.jsonl")
STALE_BAR_S = 20 * 60
PENDING_SLA_S = 10 * 60
HELD_KINDS = {"VEC_STATE_DISAGREE", "VEC_DICTATE_ONLY_BLOCK", "VEC_LAST_REFUSE"}


def load_intents():
    latest = {}
    for f in glob.glob(os.path.join(INTENTS, "*.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        key = (d.get("account"), d.get("ss"))
        if not key[0] or not key[1]:
            continue
        ts = d.get("bar_ts", 0)
        if ts >= latest.get(key, {}).get("bar_ts", -1):
            latest[key] = d
    return latest


ACCOUNTS = ("ang", "fin", "flz", "inf", "men")


def load_live_positions():
    """Exchange truth via per-account positions service files (6s REST)."""
    live = {}
    for acct in ACCOUNTS:
        for side in ("long", "short"):
            p = os.path.join(HERE, acct, "%s_positions.json" % side)
            try:
                d = json.load(open(p))
            except Exception:
                continue
            for key, pos in d.items():
                try:
                    qty = float(pos.get("positionAmt", 0) or 0)
                except Exception:
                    qty = 0
                if abs(qty) > 0:
                    live[key] = {"qty": qty,
                                 "gain": pos.get("gain"),
                                 "entry": pos.get("entry_price")}
    return live


def load_delivered():
    delivered = {}
    for f in glob.glob(os.path.join(EXEC, "live_exec_*.jsonl")):
        acct = os.path.basename(f)[len("live_exec_"):-len(".jsonl")]
        try:
            lines = open(f).read().strip().split("\n")
        except Exception:
            continue
        for line in lines[-4000:]:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("result") != "SUCCESS" and d.get("status") != "SUCCESS":
                continue
            ss = d.get("ss") or d.get("sym_side")
            act = (d.get("action") or "").upper()
            if not ss or act not in ("OPEN", "CLOSE"):
                continue
            key = (acct, ss)
            ts = d.get("ts", 0)
            if ts >= delivered.get(key, {}).get("ts", -1):
                delivered[key] = {"action": act, "ts": ts}
    return delivered


def load_holds(now):
    holds = {}
    try:
        lines = open(QUEUE).read().strip().split("\n")
    except Exception:
        return holds
    for line in lines:
        try:
            d = json.loads(line)
        except Exception:
            continue
        if d.get("kind") not in HELD_KINDS or now - d.get("ts", 0) > 3600:
            continue
        pk = d.get("position_key", "")
        if ":" not in pk:
            continue
        acct, ss = pk.split(":", 1)
        key = (acct, ss)
        if d.get("ts", 0) >= holds.get(key, {}).get("ts", -1):
            holds[key] = {"kind": d.get("kind"), "ts": d.get("ts"),
                          "reason": str(d.get("reason"))[:70]}
    return holds


def main():
    now = time.time()
    intents = load_intents()
    delivered = load_delivered()
    live = load_live_positions()
    holds = load_holds(now)
    rows = []
    counts = {"MATCH": 0, "IN_FLIGHT": 0, "HELD_BY_BACKSTOP": 0,
              "STALE_BAR": 0, "DIVERGED": 0}
    both_open = []
    per_sym = {}
    for live_key in sorted(live):
        acct, ss = live_key.split(":", 1)
        if (acct, ss) not in intents:
            intents[(acct, ss)] = {"account": acct, "ss": ss,
                                   "target_state": "NO_INTENT",
                                   "bar_ts": now, "reason": "no intent file"}
    for (acct, ss), inten in sorted(intents.items()):
        tgt = (inten.get("target_state") or "").upper()
        bar_age = now - inten.get("bar_ts", now)
        liver = delivered.get((acct, ss))
        in_live = (acct + ":" + ss) in live
        implied = ("OPEN" if in_live else "FLAT")
        want = "OPEN" if tgt in ("LONG", "SHORT") else "FLAT"
        hold = holds.get((acct, ss))
        stale = "*" if bar_age > STALE_BAR_S else ""
        if (inten.get("target_state") or "").upper() == "NO_INTENT":
            verdict = "NO_INTENT"
            counts.setdefault("NO_INTENT", 0)
            counts["NO_INTENT"] += 1
            if implied == "OPEN":
                counts["DIVERGED"] += 1
                verdict = "NO_INTENT_OPEN"
            rows.append((acct, ss, "?", implied, int(bar_age / 60), verdict))
            continue
        if want == ("OPEN" if implied == "OPEN" else "FLAT"):
            verdict = "MATCH" + stale
            counts["MATCH" if not stale else "STALE_BAR"] += 1
        elif hold:
            verdict = "HELD_BY_BACKSTOP" + stale
            counts["HELD_BY_BACKSTOP" if not stale else "STALE_BAR"] += 1
        elif now - inten.get("bar_ts", now) < PENDING_SLA_S:
            verdict = "IN_FLIGHT" + stale
            counts["IN_FLIGHT" if not stale else "STALE_BAR"] += 1
        else:
            verdict = "DIVERGED" + stale
            counts["DIVERGED"] += 1
        rows.append((acct, ss, want, implied, int(bar_age / 60), verdict))
        base = ss[:-5] if ss.endswith("_LONG") else ss[:-6] if ss.endswith("_SHORT") else ss
        per_sym.setdefault(base, {})[ss] = want
    for sym, sides in per_sym.items():
        if sides.get(sym + "_LONG") == "OPEN" and sides.get(sym + "_SHORT") == "OPEN":
            both_open.append(sym)
    print("acct sym_side want live bar_age_m verdict")
    for acct, ss, want, implied, age_m, verdict in rows:
        if verdict not in ("MATCH", "MATCH*"):
            print("%s %s %s %s %dm %s" % (acct, ss, want, implied, age_m, verdict))
    print("counts=%s n_keys=%d both_sides_open=%d %s"
          % (json.dumps(counts), len(rows), len(both_open), both_open[:15]))
    rc = 1 if (counts["DIVERGED"] or counts["STALE_BAR"]) else 0
    return rc


if __name__ == "__main__":
    sys.exit(main())
