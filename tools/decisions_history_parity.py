#!/usr/bin/env python3
"""tools/decisions_history_parity.py — constant decisions<->history parity engine.

Every line in data/decisions/decisions_{acct}_{ymd}.jsonl is an UNFILTERED
switch intent (logged by record_decision_context_crypto AFTER pre-decision
gates, BEFORE execution filters). Every line in
data/history/{acct}/{SYM_SIDE}.jsonl is an ACTUAL live trade (AUGMENT/REDUCE).

This tool matches every intent to an actual (same position_key + action class
within 24h, reason-compatible) and emits per-intent MATCHED or MISSING.
MISSING intents are attributed to the blocking execution filter by scanning
the live logs for that key+time window.

Exit code: 0 iff zero MISSING intents (or --allow-missing). Non-zero on any
MISSING so cron/monitoring can alert.

Usage:
  python tools/decisions_history_parity.py --date 20261004 --days 2
  python tools/decisions_history_parity.py --date 20261005 --accounts fin,men
"""
from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys
from datetime import datetime, timedelta, timezone

ACCOUNTS = ("fin", "men", "ang", "inf", "flz", "trb", "trc")
CRYPTO_ACCOUNTS = ("fin", "men", "ang", "inf", "flz")
STOCK_ACCOUNTS = ("trb", "trc")

# Decision action -> history event types that satisfy it.
ACTION_TO_TYPES = {
    "OPEN": ("AUGMENT", "OPEN"),
    "AUGMENT": ("AUGMENT", "OPEN"),
    "REENTRY": ("AUGMENT", "OPEN"),
    "QUICK_OPEN": ("AUGMENT", "OPEN"),
    "CLOSE": ("REDUCE", "CLOSE"),
    "REDUCE": ("REDUCE", "CLOSE"),
    "STRONG_REDUCE": ("REDUCE", "CLOSE"),
    "QUICK_CLOSE": ("REDUCE", "CLOSE"),
    "FULL_CLOSE": ("REDUCE", "CLOSE"),
    "PROFIT_TAKE": ("REDUCE", "CLOSE"),
    "HEDGE_CLOSE": ("REDUCE", "CLOSE"),
}

MATCH_WINDOW_BEFORE_S = 300      # execution clock skew / optimistic record
MATCH_WINDOW_AFTER_S = 24 * 3600  # intent must actualize within 24h
TIGHT_MATCH_S = 30 * 60  # first pass: fills normally land within minutes.
# Two-pass matching prevents an early orphaned intent from consuming a later
# intent's fill (e.g. flz 10:53 OPEN orphaned by restart must not eat the
# 22:09 AUGMENT that belongs to the 22:09 OPEN).

# Attribution tokens, most-specific first. Each maps a log substring (regex)
# to a canonical filter name.
FILTER_PATTERNS = [
    (r"MAKER_ZERO_QTY", "MAKER_ZERO_QTY"),
    (r"MAKER_FAILED_SUPPRESS_WEBHOOK", "MAKER_FAILED_SUPPRESS_WEBHOOK"),
    (r"BLOCKED_MAKER_SUPPRESS_WEBHOOK", "BLOCKED_MAKER_SUPPRESS_WEBHOOK"),
    (r"\[NUKE\].*STALE ACTIVE order", "NUKE_STALE_MAKER_ORDER"),
    (r"Cleaning stuck .placing. state", "NUKE_STUCK_PLACING_WIPE"),
    (r"MAKER_CRITICAL_FAIL", "MAKER_CRITICAL_FAIL"),
    (r"MAKER_EXIT_FALLBACK", "MAKER_EXIT_FALLBACK"),
    (r"MAKER_BLOCK.*post-fill cooldown", "MAKER_BLOCK_POST_FILL_COOLDOWN"),
    (r"MAKER_BLOCK.*Lock busy", "MAKER_BLOCK_LOCK_BUSY"),
    (r"MARKET_API_ERROR code=(-?\d+)", "MARKET_API_ERROR"),
    (r"ReduceOnly Order is rejected", "MARKET_REDUCEONLY_REJECTED"),
    (r"NEWBORN_PROTECT", "NEWBORN_PROTECT"),
    (r"HARD_REDUCE_LOCK", "HARD_REDUCE_LOCK"),
    (r"AUGMENT_GUARD", "AUGMENT_GUARD"),
    (r"ORDER_SIZE_CAP", "ORDER_SIZE_CAP"),
    (r"GATES_A_VETO", "GATES_A_VETO"),
    (r"GOLDEN_RULE_CONSENSUS_BLOCK", "GOLDEN_RULE_CONSENSUS_BLOCK"),
    (r"PER_SYM_LIVE_GATE", "PER_SYM_LIVE_GATE"),
    (r"BROKER_SYNC_DEMAND.*REFUSING", "BROKER_SYNC_DEMAND_REFUSE"),
    (r"BLOCKED_BROKER_SYNC_PENDING_CONFIRMATION", "BLOCKED_BROKER_SYNC_PENDING"),
    (r"BLOCKED_BROKER_SYNC_STALE", "BLOCKED_BROKER_SYNC_STALE"),
    (r"BLOCKED_NON_TRADEABLE_POSITION_KEY", "BLOCKED_NON_TRADEABLE"),
    (r"NOT TRADEABLE", "BLOCKED_NON_TRADEABLE"),
    (r"BLOCKED_LAST_AUGMENT_SAVE", "BLOCKED_LAST_AUGMENT_SAVE"),
    (r"NO QUANTITY LEFT TO REDUCE", "NO_QUANTITY_LEFT_TO_REDUCE"),
    (r"BLOCK_POS_ALREADY_CLOSED", "BLOCK_POS_ALREADY_CLOSED"),
    (r"DATA_FRESHNESS.*blocking augment", "DATA_FRESHNESS_BLOCK"),
    (r"SHUTDOWN & CLEANUP", "LIVE_RESTART_ORPHAN"),
    (r"All background tasks cancelled", "LIVE_RESTART_ORPHAN"),
    (r"BLOCKED_[A-Z0-9_]+", "OTHER_BLOCKED"),
]

CRYPTO_LOG_RE = re.compile(r"^(\d{2}) (\d{2}):(\d{2}):(\d{2})")
TRADIER_LOG_RE = re.compile(r"^(\d{2})-(\d{2}) (\d{2}):(\d{2}):(\d{2})")


def parse_ts(s: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except Exception:
        return None


def load_decisions(root: str, acct: str, ymd: str) -> tuple[list[dict] | None, str]:
    path = os.path.join(root, "data", "decisions", f"decisions_{acct}_{ymd}.jsonl")
    if not os.path.exists(path):
        return None, path
    rows = []
    with open(path, errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows, path


def sym_side_of(position_key: str) -> str | None:
    if ":" not in position_key:
        return None
    rest = position_key.split(":", 1)[1]
    if "_" not in rest:
        return None
    return rest  # e.g. DASHUSDT_LONG


def load_history_for_keys(root: str, acct: str, keys: set[str]) -> dict[str, list[dict]]:
    """key -> sorted list of {ts, type, reason} (only files that exist)."""
    out: dict[str, list[dict]] = {}
    for key in keys:
        ss = sym_side_of(key)
        if not ss:
            continue
        path = os.path.join(root, "data", "history", acct, f"{ss}.jsonl")
        evs: list[dict] = []
        if os.path.exists(path):
            with open(path, errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        d = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts = parse_ts(d.get("ts", ""))
                    if ts is None:
                        continue
                    evs.append({"ts": ts, "type": str(d.get("type", "")).upper(),
                                "reason": str(d.get("reason", "") or "")})
        evs.sort(key=lambda e: e["ts"])
        out[key] = evs
    return out


def reason_compatible(intent_reason: str, hist_reason: str) -> int:
    """Return 2 for exact, 1 for prefix-compatible, 0 for incompatible.

    Live appends suffixes (e.g. _QWH, _UNVERIFIED_SHADOW_QWH) and maker
    prefixes (MAKER_PROFIT_EXIT_) — prefix-either-way covers those.
    """
    a = (intent_reason or "").strip()
    b = (hist_reason or "").strip()
    if not a or not b:
        return 0
    if a == b:
        return 2
    # strip known live wrappers before comparing
    for prefix in ("MAKER_PROFIT_EXIT_",):
        if b.startswith(prefix):
            b = b[len(prefix):]
    for suffix in ("_QWH", "_UNVERIFIED_SHADOW_QWH"):
        if b.endswith(suffix):
            b = b[: -len(suffix)]
    if a == b:
        return 2
    if a.startswith(b) or b.startswith(a):
        return 1
    return 0


def match_intents(intents: list[dict], history: dict[str, list[dict]]) -> list[dict]:
    """Greedy match: exact-reason+nearest first, then prefix matches.

    Each history event is consumed at most once.
    """
    # normalize intents
    norm = []
    for idx, d in enumerate(intents):
        ts = parse_ts(d.get("timestamp", ""))
        if ts is None:
            continue
        norm.append({"idx": idx, "ts": ts, "key": str(d.get("position_key", "")),
                     "action": str(d.get("action", "")).upper(),
                     "reason": str(d.get("reason", "") or "")})
    norm.sort(key=lambda r: r["ts"])
    used: dict[str, set[int]] = {k: set() for k in history}
    verdict: dict[int, dict] = {}

    def best_candidate(inten: dict, after_s: float):
        types = ACTION_TO_TYPES.get(inten["action"], ())
        evs = history.get(inten["key"], [])
        best = None  # (score, abs_dt_seconds, ev_idx)
        for ei, ev in enumerate(evs):
            if ei in used.get(inten["key"], set()):
                continue
            if types and ev["type"] not in types:
                continue
            dt = (ev["ts"] - inten["ts"]).total_seconds()
            if dt < -MATCH_WINDOW_BEFORE_S or dt > after_s:
                continue
            score = reason_compatible(inten["reason"], ev["reason"])
            if score == 0:
                continue
            cand = (score, abs(dt), ei)
            if best is None or (cand[0] > best[0] or (cand[0] == best[0] and cand[1] < best[1])):
                best = cand
        return best

    def claim(inten: dict, cand) -> None:
        used[inten["key"]].add(cand[2])
        ev = history[inten["key"]][cand[2]]
        verdict[inten["idx"]] = {
            **inten, "status": "MATCHED",
            "matched_ts": ev["ts"].isoformat(),
            "matched_type": ev["type"],
            "matched_reason": ev["reason"],
            "lag_s": round((ev["ts"] - inten["ts"]).total_seconds(), 1)}

    # Pass 1: tight window (normal fills). Pass 2: full 24h (slow fills).
    # Within a pass, claims are ordered by (reason-score desc, lag asc) so an
    # early orphaned intent cannot steal a later intent's nearer fill.
    for window in (TIGHT_MATCH_S, MATCH_WINDOW_AFTER_S):
        pairs = []
        for inten in norm:
            if inten["idx"] in verdict:
                continue
            cand = best_candidate(inten, window)
            if cand is not None:
                pairs.append((-cand[0], cand[1], inten["idx"], cand))
        pairs.sort()
        for _, _, idx, cand in pairs:
            if idx in verdict:
                continue
            inten = next(r for r in norm if r["idx"] == idx)
            # re-resolve: the event may have been consumed by an earlier claim
            cand2 = best_candidate(inten, window)
            if cand2 is not None:
                claim(inten, cand2)
    results = []
    for inten in norm:
        if inten["idx"] in verdict:
            results.append(verdict[inten["idx"]])
        else:
            results.append({**inten, "status": "MISSING"})
    return results


def log_files_for(root: str, acct: str) -> list[str]:
    logs = os.environ.get("DH_PARITY_LOG_DIR", "/Users/niels/logs")
    cands = []
    if acct in CRYPTO_ACCOUNTS:
        cands = [f"ez_manage_{acct}.log", f"ez_manage_{acct}.log.1"]
    else:
        cands = [f"tradier_manage_general_{acct}.log",
                 f"tradier_manage_{acct}.log",
                 f"tradier_manage_{acct}.log.1"]
    out = []
    for c in cands:
        p = os.path.join(logs, c)
        if os.path.exists(p):
            out.append(p)
    return out


def log_line_ts(line: str, intent_ts: datetime, is_crypto: bool) -> datetime | None:
    """Crypto logs carry only DD HH:MM:SS; resolve DD against intent month/year."""
    if is_crypto:
        m = CRYPTO_LOG_RE.match(line)
        if not m:
            return None
        dd, hh, mm, ss = map(int, m.groups())
        try:
            cand = intent_ts.replace(day=dd, hour=hh, minute=mm, second=ss,
                                     microsecond=0)
        except ValueError:
            return None
        # handle month boundary: pick candidate closest to intent
        best = cand
        for delta in (-1, 1):
            try:
                month = intent_ts.month + delta
                year = intent_ts.year
                if month < 1:
                    month, year = 12, year - 1
                elif month > 12:
                    month, year = 1, year + 1
                alt = cand.replace(year=year, month=month)
            except ValueError:
                continue
            if abs((alt - intent_ts).total_seconds()) < abs((best - intent_ts).total_seconds()):
                best = alt
        return best
    m = TRADIER_LOG_RE.match(line)
    if not m:
        return None
    mo, dd, hh, mm, ss = map(int, m.groups())
    try:
        return intent_ts.replace(month=mo, day=dd, hour=hh, minute=mm,
                                 second=ss, microsecond=0)
    except ValueError:
        return None


def attribute_missing(root: str, acct: str, missing: list[dict],
                      window_min: float = 10.0) -> None:
    """Fill in filter+evidence for each MISSING intent (mutates)."""
    if not missing:
        return
    files = log_files_for(root, acct)
    is_crypto = acct in CRYPTO_ACCOUNTS
    if not files:
        for m in missing:
            m["filter"] = "NO_LOG_FILE"
            m["evidence"] = ""
        return
    # index log lines by position key (one pass per file)
    by_key: dict[str, list[str]] = {}
    restarts: list[str] = []  # keyless shutdown/cancel lines (orphaned orders)
    for path in files:
        try:
            with open(path, errors="replace") as fh:
                for line in fh:
                    keys = set(re.findall(rf"{re.escape(acct)}:[A-Z0-9]+_(?:LONG|SHORT)", line))
                    if keys:
                        for key in keys:
                            by_key.setdefault(key, []).append(line.rstrip("\n")[:400])
                    elif "SHUTDOWN & CLEANUP" in line or "All background tasks cancelled" in line:
                        restarts.append(line.rstrip("\n")[:200])
        except OSError:
            continue
    compiled = [(re.compile(p), name) for p, name in FILTER_PATTERNS]
    for m in missing:
        key = m["key"]
        inten_ts = m["ts"]
        cands = by_key.get(key, [])
        window_s = window_min * 60
        # score: prefer lines closest in time that carry a filter token
        scored = []
        maker_qty_ctx = ""
        for line in cands:
            lts = log_line_ts(line, inten_ts, is_crypto)
            if lts is None:
                continue
            dt = abs((lts - inten_ts).total_seconds())
            if "MAKER_QTY" in line and dt <= window_s and not maker_qty_ctx:
                maker_qty_ctx = line.strip()[:200]
            if dt > window_s:
                continue
            tok_idx = None
            tok_name = None
            for i, (rx, name) in enumerate(compiled):
                if rx.search(line):
                    tok_idx, tok_name = i, name
                    break
            if tok_name is None:
                continue
            scored.append((tok_idx, dt, tok_name, line.strip()[:300]))
        if not scored:
            # keyless restart in window? (maker order orphaned by live restart)
            hit = ""
            for line in restarts:
                lts = log_line_ts(line, inten_ts, is_crypto)
                if lts is None:
                    continue
                dt = (inten_ts - lts).total_seconds()
                if -60 <= dt <= 600:
                    hit = line.strip()[:200]
                    break
            if hit:
                m["filter"] = "LIVE_RESTART_ORPHAN"
                m["evidence"] = hit
            else:
                m["filter"] = "UNATTRIBUTED"
                m["evidence"] = ""
            continue
        scored.sort()
        m["filter"] = scored[0][2]
        m["evidence"] = scored[0][3]
        # prefer a more specific token if a zero-qty line exists slightly further out
        for tok_idx, dt, tok_name, line in scored:
            if tok_name in ("MAKER_ZERO_QTY", "NUKE_STALE_MAKER_ORDER",
                            "MARKET_REDUCEONLY_REJECTED", "MARKET_API_ERROR") and dt <= window_s * 3:
                m["filter"] = tok_name
                m["evidence"] = line
                break
        if maker_qty_ctx and "MAKER_QTY" not in m["evidence"]:
            m["maker_qty_ctx"] = maker_qty_ctx


def main() -> int:
    ap = argparse.ArgumentParser(description="decisions<->history parity check")
    ap.add_argument("--date", default=datetime.now(timezone.utc).strftime("%Y%m%d"),
                    help="first YYYYMMDD (UTC)")
    ap.add_argument("--days", type=int, default=1, help="number of days from --date")
    ap.add_argument("--accounts", default=",".join(ACCOUNTS))
    ap.add_argument("--root", default=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    ap.add_argument("--out", default="", help="report JSON path (default: data/decisions_history_parity/DH_{first}_{last}.json)")
    ap.add_argument("--no-logs", action="store_true", help="skip log attribution")
    ap.add_argument("--allow-missing", action="store_true", help="exit 0 even with MISSING")
    ap.add_argument("--window-min", type=float, default=10.0)
    args = ap.parse_args()

    root = args.root
    base = datetime.strptime(args.date, "%Y%m%d").replace(tzinfo=timezone.utc)
    ymds = [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(args.days)]
    accts = [a.strip() for a in args.accounts.split(",") if a.strip()]

    report: dict = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "window": {"first": ymds[0], "last": ymds[-1], "days": ymds},
        "accounts": {},
        "totals": {"intents": 0, "matched": 0, "missing": 0},
    }
    for acct in accts:
        intents: list[dict] = []
        files_seen = []
        for ymd in ymds:
            rows, path = load_decisions(root, acct, ymd)
            if rows is None:
                continue
            files_seen.append(path)
            intents.extend(rows)
        acct_rep: dict = {
            "decision_files": files_seen,
            "no_decision_file": len(files_seen) == 0,
            "intents": len(intents),
            "matched": 0,
            "missing": 0,
            "missing_intents": [],
            "matched_intents": [],
        }
        if intents:
            keys = {str(d.get("position_key", "")) for d in intents if d.get("position_key")}
            history = load_history_for_keys(root, acct, keys)
            results = match_intents(intents, history)
            missing = [r for r in results if r["status"] == "MISSING"]
            if not args.no_logs:
                attribute_missing(root, acct, missing, args.window_min)
            for r in results:
                r["ts"] = r["ts"].isoformat()
                r.pop("idx", None)
            acct_rep["matched"] = sum(1 for r in results if r["status"] == "MATCHED")
            acct_rep["missing"] = len(missing)
            acct_rep["missing_intents"] = missing
            # keep matched compact
            acct_rep["matched_intents"] = [
                {"ts": r["ts"], "key": r["key"], "action": r["action"],
                 "reason": r["reason"][:80], "lag_s": r.get("lag_s")} for r in results if r["status"] == "MATCHED"]
            filt_counts: dict[str, int] = {}
            for m in missing:
                filt_counts[m.get("filter", "?")] = filt_counts.get(m.get("filter", "?"), 0) + 1
            acct_rep["missing_by_filter"] = filt_counts
        report["accounts"][acct] = acct_rep
        report["totals"]["intents"] += acct_rep["intents"]
        report["totals"]["matched"] += acct_rep["matched"]
        report["totals"]["missing"] += acct_rep["missing"]

    out = args.out or os.path.join(root, "data", "decisions_history_parity",
                                   f"DH_{ymds[0]}_{ymds[-1]}.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as fh:
        json.dump(report, fh, indent=1)
    t = report["totals"]
    print(f"intents={t['intents']} matched={t['matched']} missing={t['missing']} report={out}")
    for acct in accts:
        a = report["accounts"][acct]
        extra = ""
        if a.get("no_decision_file"):
            extra = " NO_DECISION_FILE"
        elif a.get("missing_by_filter"):
            extra = " " + json.dumps(a["missing_by_filter"])
        print(f"  {acct}: intents={a['intents']} matched={a['matched']} missing={a['missing']}{extra}")
    if t["missing"] > 0 and not args.allow_missing:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
