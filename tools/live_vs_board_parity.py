#!/usr/bin/env python3
"""live_vs_board_parity — each crypto sym_side's board vs its ACTUAL Binance trades since that board went live (USER 2026-10-14).

Inputs (all read-only, produced beforehand):
  --fills   tools/binance_fills_fetch.py output (GET /fapi/v1/userTrades + /income + /allOrders, Mac only)
  --board   board ledgers: the live per-sym set evaluated by the vector engine (tools.opt.evaluate_v12.evaluate_prepared,
            include_ledger) on the live twin's frozen 15m store (/tmp/vec_exact_frozen/<SYM>.pkl copy) = the same bars live decided on
  --s2      optional cross-check: the same sets via v12_pilot.evaluate_sanitized on S2's NPZs (end ~2026-10-14 06:30Z)
  live logs ~/logs/ez_manage_<acct>.log* (decisions, dispatch results), per_sym_history (when each set went live).
Never uses data/per_trade_returns as truth (only to show its discrepancy). No Sharpe anywhere.
"""
import argparse
import bisect
import collections
import csv
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOL_S = 1800.0
BAR_S = 900.0
ACCOUNTS = ("ang", "fin", "flz", "inf", "men")


def md5(ov):
    return hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()


def iso(ts):
    return dt.datetime.fromtimestamp(float(ts), dt.timezone.utc).strftime("%m-%d %H:%M") if ts else ""


def bnb_price_fn():
    rows = json.load(open(ROOT / "klines_cache" / "BNBUSDC_15m.json"))
    ts = [dt.datetime.fromisoformat(r["timestamp"].replace("Z", "+00:00")).timestamp() for r in rows]
    px = [float(r["close"]) for r in rows]

    def f(t):
        i = max(0, min(len(ts) - 1, bisect.bisect_right(ts, t) - 1))
        return px[i]

    return f


def live_trips(fills_doc, bnb):
    """Binance fills -> round trips per (acct, symbol, positionSide). Hedge mode: LONG opens on BUY, SHORT opens on SELL.
    Starting position = current positionAmt (GET /fapi/v2/positionRisk at fetch end) minus the net of the window fills, so a
    position opened before the window is a carried-in trip (pre_window=True, entry unknown) instead of a fake new entry."""
    trips = []
    for acct, a in fills_doc["accounts"].items():
        orders = {}
        for symbol, olist in (a.get("orders") or {}).items():
            for o in olist:
                orders[(symbol, int(o["orderId"]))] = o
        now_pos = {}
        for r in a.get("positions") or []:
            now_pos[(r["symbol"], r["positionSide"])] = abs(float(r["positionAmt"]))
        for symbol, fl in (a.get("trades") or {}).items():
            by_side = collections.defaultdict(list)
            for f in fl:
                by_side[f["positionSide"]].append(f)
            for pside, fs in by_side.items():
                fs.sort(key=lambda f: (int(f["time"]), int(f["id"])))
                net = sum(float(f["qty"]) * (1 if (f["side"] == "BUY") == (pside == "LONG") else -1) for f in fs)
                pos0 = max(0.0, now_pos.get((symbol, pside), 0.0) - net) if a.get("positions") is not None else 0.0
                pos = pos0
                cur = None

                def new_trip(ts, pre):
                    return {"acct": acct, "symbol": symbol, "side": pside, "entry_ts": ts, "pre_window": pre, "open_qty": 0.0, "open_notional": 0.0, "close_qty": 0.0, "close_notional": 0.0, "realized": 0.0, "fees": 0.0, "max_qty": pos, "fills": [], "exit_ts": None, "n_adds": 0, "n_reduces": 0, "exit_orders": [], "entry_orders": []}

                if pos0 > 1e-12:
                    cur = new_trip(int(fs[0]["time"]) / 1000.0, True)
                for f in fs:
                    q = float(f["qty"])
                    opening = (f["side"] == "BUY") == (pside == "LONG")
                    fee = float(f["commission"]) * (bnb(int(f["time"]) / 1000.0) if f["commissionAsset"] == "BNB" else 1.0)
                    o = orders.get((symbol, int(f["orderId"]))) or {}
                    oid = int(f["orderId"])
                    if cur is None:
                        cur = new_trip(int(f["time"]) / 1000.0, not opening)
                    cur["fills"].append(int(f["id"]))
                    cur["fees"] += fee
                    cur["realized"] += float(f["realizedPnl"])
                    od = {"orderId": oid, "type": o.get("origType") or o.get("type"), "cid": o.get("clientOrderId"), "ts": int(f["time"]) / 1000.0}
                    if opening:
                        if oid not in [x["orderId"] for x in cur["entry_orders"]]:
                            if cur["open_qty"] > 0 or cur["pre_window"]:
                                cur["n_adds"] += 1
                            cur["entry_orders"].append(od)
                        cur["open_qty"] += q
                        cur["open_notional"] += q * float(f["price"])
                        pos += q
                    else:
                        if oid not in [x["orderId"] for x in cur["exit_orders"]]:
                            cur["exit_orders"].append(od)
                            cur["n_reduces"] += 1
                        cur["close_qty"] += q
                        cur["close_notional"] += q * float(f["price"])
                        pos -= q
                    cur["max_qty"] = max(cur["max_qty"], pos)
                    if pos <= 1e-9 * max(1.0, cur["max_qty"]):
                        cur["exit_ts"] = int(f["time"]) / 1000.0
                        trips.append(cur)
                        cur, pos = None, 0.0
                if cur is not None:
                    trips.append(cur)
    for t in trips:
        t["entry_px"] = t["open_notional"] / t["open_qty"] if t["open_qty"] else None
        t["exit_px"] = t["close_notional"] / t["close_qty"] if t["close_qty"] else None
        t["net_pnl"] = t["realized"] - t["fees"]
        t["notional"] = t["open_notional"] if t["open_notional"] else t["close_notional"]
        t["net_pct"] = (t["net_pnl"] / t["notional"] * 100.0) if t["notional"] else None
        t["is_open"] = t["exit_ts"] is None
        t["n_reduces"] = max(0, t["n_reduces"] - (0 if t["is_open"] else 1))
    return trips


def board_trips(ledger):
    trips, cur = [], None
    for e in sorted(ledger, key=lambda e: (float(e.get("ts") or 0), 0 if e.get("type") == "OPEN" else 1)):
        typ = str(e.get("type") or "").upper()
        ts = float(e.get("ts") or 0)
        reason = str(e.get("exit_reason") or e.get("reason") or "")
        if typ == "OPEN":
            if cur is not None:
                trips.append(cur)
            cur = {"bar_ts": ts, "entry_ts": ts + BAR_S, "entry_px": float(e.get("price") or 0), "entry_reason": str(e.get("reason") or ""), "pnl_dollars": 0.0, "deployed": float(e.get("pos_deployed") or 0), "n_adds": 0, "n_reduces": 0, "exit_ts": None, "exit_reason": None, "exit_px": None}
            continue
        if cur is None:
            continue
        if typ == "AUGMENT":
            cur["n_adds"] += 1
            continue
        if "pnl_dollars" in e:
            cur["pnl_dollars"] += float(e.get("pnl_dollars") or 0)
            cur["deployed"] = max(cur["deployed"], float(e.get("deployed") or 0))
        if typ == "CLOSE" or (typ == "REDUCE" and "bar_exit" in e):
            if reason.startswith("FINAL_MTM"):
                cur["mtm_pnl"] = cur["pnl_dollars"]
                trips.append(cur)
                cur = None
                continue
            cur["exit_ts"] = ts + BAR_S
            cur["exit_reason"] = reason
            cur["exit_px"] = float(e.get("price") or 0)
            trips.append(cur)
            cur = None
        elif typ == "REDUCE":
            cur["n_reduces"] += 1
    if cur is not None:
        trips.append(cur)
    for t in trips:
        t["pct"] = (t["pnl_dollars"] / t["deployed"] * 100.0) if t["deployed"] else None
    return trips


LINE = re.compile(r"^(\d\d) (\d\d):(\d\d):(\d\d) - (\w+) - \[(\w+)\] (.*)$")


def parse_logs(month_prefix="2026-10"):
    """Per account: vec decisions ([VEC_EXACT] KEY bar=.. acts=[..]) and order outcomes, keyed by sym_side."""
    ev = collections.defaultdict(list)
    files = []
    for a in ACCOUNTS:
        files += sorted(glob.glob(os.path.expanduser(f"~/logs/ez_manage_{a}.log*")), reverse=True)
    for fp in files:
        with open(fp, errors="ignore") as fh:
            for line in fh:
                m = LINE.match(line)
                if not m:
                    continue
                d, hh, mm, ss, lvl, acct, msg = m.groups()
                ts = dt.datetime(2026, 10, int(d), int(hh), int(mm), int(ss), tzinfo=dt.timezone.utc).timestamp()
                if "[VEC_EXACT] " in msg and " bar=" in msg and "acts=" in msg:
                    mm2 = re.search(r"\[VEC_EXACT\] (\S+) bar=(\d+) k=\d+ acts=(\[.*\])", msg)
                    if mm2:
                        ev[(acct, mm2.group(1))].append((ts, "DECIDE", int(mm2.group(2)), mm2.group(3)[:300]))
                    continue
                km = re.search(rf"{acct}:([A-Z0-9]+_(?:LONG|SHORT))", msg)
                if not km:
                    continue
                key = km.group(1)
                tags = ("WH_TRACE_2_done", "VEC_EXACT_ENTRY_REFUSED", "VEC_EXACT_SIZE_FLOOR", "OPEN_ON_LEFTOVER", "BLOCKED_", "CANCELLED_", "SKIPPED_", "VEC_EXACT_STALE_FLATTEN", "VEC_EXACT_EXIT_OWED", "FIXED_STOP_PLACED", "FIXED_STOP_GONE", "FIXED_STOP_WRONG_LEVEL", "FIXED_STOP_FILLED", "VEC_EXACT_EXEC", "VEC_EXACT_EXPIRED", "VEC_EXACT_LEGACY_ADOPTED", "✅", "[VEC_EXACT] ")
                if any(t in msg for t in tags):
                    ev[(acct, key)].append((ts, "LOG", lvl, msg[:400]))
    for k in ev:
        ev[k].sort(key=lambda x: x[0])
    return ev


def history_since(con, ss, cur_md5, horizon):
    rows = con.execute("select epoch,updated_at,overrides_json from per_sym_history where sym_side=? and epoch>=? order by epoch", (ss, horizon - 7 * 86400)).fetchall()
    seq = []
    for ep, u, oj in rows:
        try:
            m = md5(json.loads(oj)) if oj else None
        except Exception:
            m = None
        if not seq or seq[-1][1] != m:
            seq.append((float(ep), m, u))
    first = next((ep for ep, m, u in seq if m == cur_md5 and ep >= horizon), None)
    if first is None:
        prev_in = [ep for ep, m, u in seq if m == cur_md5]
        first = max(horizon, min(prev_in)) if prev_in else None
        if prev_in and any(m == cur_md5 for ep, m, u in seq if ep < horizon):
            first = horizon
    gaps = []
    if first is not None:
        live_md5 = None
        gap_start = None
        for ep, m, u in seq:
            if ep < first:
                continue
            if m != cur_md5 and gap_start is None:
                gap_start = (ep, m)
            elif m == cur_md5 and gap_start is not None:
                gaps.append((gap_start[0], ep, gap_start[1]))
                gap_start = None
    return first, gaps, seq


def window_events(evs, t0, t1):
    return [e for e in evs if t0 <= e[0] <= t1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fills", required=True)
    ap.add_argument("--board", required=True)
    ap.add_argument("--s2")
    ap.add_argument("--out-json", required=True)
    ap.add_argument("--out-csv", required=True)
    ap.add_argument("--now", type=float, default=None)
    a = ap.parse_args()
    fills = json.load(open(a.fills))
    now = a.now or fills["end_ms"] / 1000.0
    horizon = now - 7 * 86400
    bnb = bnb_price_fn()
    con = sqlite3.connect(f"file:{ROOT}/data/hourly_reconfig/per_sym_store.db?mode=ro", uri=True)
    act = {}  # the sets live actually reads: per_sym_store.get_overrides -> per_sym_active (the JSON file is a partial projection)
    for ss, u, oj, wt, g in con.execute("select sym_side,updated_at,overrides_json,winning_tag,acc_gain_pct from per_sym_active where cat_side like 'CRYPTO%'"):
        act[ss] = {"updated_at": u, "overrides": json.loads(oj or "{}"), "winning_tag": wt, "acc_gain_pct": g}
    pub = (json.load(open(ROOT / "data/published_selection.json")).get("sym_sides") or {})
    golive = {}
    for gp in sorted(glob.glob(str(ROOT / "data/daily_chain/persym_golive_2026101[34]*.json"))):
        try:
            for k, r in (json.load(open(gp)).get("sym_sides") or {}).items():
                golive[k] = {"file": Path(gp).name, "result": r.get("result"), "why": str(r.get("reason") or r.get("refuse_reason") or r.get("skip") or "")[:120]}
        except Exception:
            pass
    board = json.load(open(a.board))
    s2 = json.load(open(a.s2)) if a.s2 and os.path.exists(a.s2) else {}
    tradeable = set(json.load(open(ROOT / "tradeable_keys.json")))
    lt = live_trips(fills, bnb)
    logs = parse_logs()
    by_ss = collections.defaultdict(list)
    for t in lt:
        by_ss[f"{t['symbol']}_{t['side']}"].append(t)
    rows, detail = [], {}
    all_ss = sorted(set(act) | {k for k in by_ss})
    for ss in all_ss:
        v = act.get(ss)
        rec = {"sym_side": ss, "live_set": bool(v)}
        if v:
            cur = md5(v["overrides"])
            since, gaps, seq = history_since(con, ss, cur, horizon)
            if since is None:
                since = max(horizon, dt.datetime.fromisoformat(v["updated_at"].replace("Z", "+00:00")).timestamp())
            pb = pub.get(ss) or {}
            pb_ov = {k: x for k, x in (pb.get("overrides") or {}).items() if not (k in ("MODE", "SIMPLE_PRICE_GT0_ENABLED", "VENUE", "IS_TRADIER", "MAX_AUGMENTS_PER_POSITION") or k.startswith("ABLATION_"))}
            live_cmp = {k: x for k, x in v["overrides"].items() if not (k in ("MODE", "SIMPLE_PRICE_GT0_ENABLED", "VENUE", "IS_TRADIER", "MAX_AUGMENTS_PER_POSITION") or k.startswith("ABLATION_"))}
            n_diff = sum(1 for k in set(pb_ov) | set(live_cmp) if json.dumps(pb_ov.get(k), default=str) != json.dumps(live_cmp.get(k), default=str)) if pb else None
            set_status = "NO_PUBLISHED_BOARD" if not pb else ("LIVE_EQ_LATEST_BOARD" if n_diff == 0 else f"LIVE_NE_LATEST_BOARD_{n_diff}keys")
            rec.update(set_md5=cur[:8], set_updated_at=v["updated_at"], set_status=set_status, published_at=pb.get("published_at"), golive=golive.get(ss), board_live_since=iso(since), board_gain_pct=v.get("acc_gain_pct"), stale_set_gaps=[(iso(g0), iso(g1), (m or "")[:8]) for g0, g1, m in gaps])
        else:
            since, gaps = horizon, []
            rec.update(set_md5="", board_live_since="NOT_LIVE", stale_set_gaps=[])
        b = board.get(ss) or {}
        bl = (b.get("live") or {})
        store_last = float(b.get("store_last") or 0) + BAR_S if b.get("store_last") else None
        btrips = [t for t in board_trips(bl.get("ledger") or []) if t["entry_ts"] >= since] if bl else []
        rec["board_source"] = "twin_store" if bl else ("NO_FROZEN_STORE" if b.get("error") else "NONE")
        rec["board_data_until"] = iso(store_last) if store_last else ""
        s2b = s2.get(ss) or {}
        s2l = s2b.get("ledger") or []
        s2_end = max((float(e.get("ts") or 0) for e in s2l), default=0.0) + BAR_S if s2l else None
        if s2l and store_last:
            s2_trips = [t for t in board_trips(s2l) if since <= t["entry_ts"] <= s2_end]
            mac_same = [t for t in btrips if t["entry_ts"] <= s2_end]
            rec["s2_npz_board_trips_overlap"] = len(s2_trips)
            rec["twin_store_board_trips_overlap"] = len(mac_same)
            rec["s2_npz_until"] = iso(s2_end)
        accts = sorted({t["acct"] for t in by_ss.get(ss, [])} | {k.split(":")[0] for k in tradeable if k.split(":", 1)[1] == ss})
        per_acct = {}
        for acct in accts:
            lts = [t for t in by_ss.get(ss, []) if t["acct"] == acct and (t["entry_ts"] >= since or (t["exit_ts"] or now) >= since)]
            lts_in = [t for t in lts if t["entry_ts"] >= since and not t["pre_window"]]
            carried = [t for t in lts if t not in lts_in]
            bt = [t for t in btrips if store_last is None or t["entry_ts"] <= store_last]
            matched, used = [], set()
            for t in sorted(lts_in, key=lambda x: x["entry_ts"]):
                best, bd = None, None
                for i, bt_ in enumerate(bt):
                    if i in used:
                        continue
                    d = abs(bt_["entry_ts"] - t["entry_ts"])
                    if d <= TOL_S and (bd is None or d < bd):
                        best, bd = i, d
                if best is not None:
                    used.add(best)
                    matched.append((t, bt[best]))
            live_only = [t for t in lts_in if t not in [m[0] for m in matched]]
            board_only = [bt[i] for i in range(len(bt)) if i not in used]
            evs = logs.get((acct, ss), [])
            exit_dis = []
            for t, b_ in matched:
                lx, bx = t["exit_ts"], b_["exit_ts"]
                if (lx is None) != (bx is None) or (lx and bx and abs(lx - bx) > TOL_S):
                    exit_dis.append((t, b_))
            def lt_view(t):
                ctx = window_events(evs, t["entry_ts"] - 1500, t["entry_ts"] + 120)
                xctx = window_events(evs, (t["exit_ts"] or now) - 900, (t["exit_ts"] or now) + 120) if t["exit_ts"] else []
                gap = next(((iso(g0), iso(g1), (m or "")[:8]) for g0, g1, m in gaps if g0 <= t["entry_ts"] <= g1), None)
                return {"entry": iso(t["entry_ts"]), "exit": iso(t["exit_ts"]) if t["exit_ts"] else ("OPEN" if t["is_open"] else ""), "entry_px": t["entry_px"], "exit_px": t["exit_px"], "qty": round(t["open_qty"], 6), "notional": round(t["notional"], 2), "net_pnl": round(t["net_pnl"], 4), "fees": round(t["fees"], 4), "net_pct": round(t["net_pct"], 3) if t["net_pct"] is not None else None, "adds": t["n_adds"], "trade_ids": t["fills"][:3] + (["..."] if len(t["fills"]) > 3 else []) + (t["fills"][-1:] if len(t["fills"]) > 3 else []), "entry_orders": t["entry_orders"][:3], "exit_orders": t["exit_orders"][-3:], "entry_log": [e[3] for e in ctx if e[1] == "LOG" and ("✅" in e[3] or "VEC_EXACT_EXEC" in e[3] or "result=" in e[3])][-3:], "entry_decisions": [(iso(e[2]), e[3]) for e in ctx if e[1] == "DECIDE"][-2:], "exit_log": [e[3] for e in xctx if e[1] == "LOG" and ("CLOSE" in e[3] or "REDUCE" in e[3] or "FIXED_STOP" in e[3] or "STALE_FLATTEN" in e[3] or "EXIT_OWED" in e[3])][-3:], "stale_set_gap": gap}
            def bt_view(b_):
                ctx = window_events(evs, b_["entry_ts"] - 1800, b_["entry_ts"] + 3600)
                return {"bar": iso(b_["bar_ts"]), "entry": iso(b_["entry_ts"]), "exit": iso(b_["exit_ts"]) if b_["exit_ts"] else "OPEN_AT_END", "entry_reason": b_["entry_reason"][:60], "exit_reason": (b_["exit_reason"] or "")[:60], "pct": round(b_["pct"], 3) if b_["pct"] is not None else None, "adds": b_["n_adds"], "live_decisions": [(iso(e[2]), e[3][:160]) for e in ctx if e[1] == "DECIDE"][:3], "live_outcome": [e[3][:260] for e in ctx if e[1] == "LOG" and any(x in e[3] for x in ("result=", "REFUSED", "SIZE_FLOOR", "OPEN_ON_LEFTOVER", "BLOCKED", "CANCELLED", "SKIPPED"))][:4], "tradeable_now": f"{acct}:{ss}" in tradeable}
            live_pnl = sum(t["net_pnl"] for t in lts_in if not t["is_open"])
            board_same = sum((b_["pct"] or 0) / 100.0 * t["notional"] for t, b_ in matched if b_["pct"] is not None)
            live_same = sum(t["net_pnl"] for t, b_ in matched if not t["is_open"])
            per_acct[acct] = {"n_live": len(lts_in), "n_board": len(bt), "matched": len(matched), "live_only": len(live_only), "board_only": len(board_only), "exit_disagree": len(exit_dis), "carried_in": len(carried), "live_net_pnl": round(live_pnl, 4), "matched_live_net_pnl": round(live_same, 4), "matched_board_pnl_at_live_size": round(board_same, 4), "live_only_net_pnl": round(sum(t["net_pnl"] for t in live_only), 4), "board_only_pct_sum": round(sum((b_["pct"] or 0) for b_ in board_only), 3), "live_only_trips": [lt_view(t) for t in live_only], "board_only_trips": [bt_view(b_) for b_ in board_only], "matched_trips": [{"live": lt_view(t), "board": bt_view(b_)} for t, b_ in matched], "exit_disagree_trips": [{"live": lt_view(t), "board": bt_view(b_)} for t, b_ in exit_dis], "carried_in_trips": [lt_view(t) for t in carried], "tradeable_now": f"{acct}:{ss}" in tradeable}
        rec["accounts"] = per_acct
        detail[ss] = rec
        agg = collections.Counter()
        for p in per_acct.values():
            for k in ("n_live", "matched", "live_only", "board_only", "exit_disagree", "carried_in"):
                agg[k] += p[k]
            for k in ("live_net_pnl", "matched_live_net_pnl", "matched_board_pnl_at_live_size", "live_only_net_pnl"):
                agg[k] += p[k]
        rows.append({"sym_side": ss, "live_set": rec["live_set"], "set_md5": rec.get("set_md5", ""), "set_status": rec.get("set_status", "NO_LIVE_SET"), "golive_last": ((rec.get("golive") or {}).get("result") or ""), "board_live_since": rec["board_live_since"], "stale_set_gaps": len(rec["stale_set_gaps"]), "board_source": rec["board_source"], "board_data_until": rec["board_data_until"], "accounts": "|".join(sorted(per_acct)), "n_board_trips": len(btrips), "n_live_trips": agg["n_live"], "matched": agg["matched"], "live_only": agg["live_only"], "board_only": sum(p["board_only"] for p in per_acct.values()), "exit_disagree": agg["exit_disagree"], "carried_in": agg["carried_in"], "live_net_pnl_binance": round(agg["live_net_pnl"], 4), "matched_live_net_pnl": round(agg["matched_live_net_pnl"], 4), "matched_board_pnl_at_live_size": round(agg["matched_board_pnl_at_live_size"], 4), "live_only_net_pnl": round(agg["live_only_net_pnl"], 4), "s2_npz_board_trips_overlap": rec.get("s2_npz_board_trips_overlap", ""), "twin_store_board_trips_overlap": rec.get("twin_store_board_trips_overlap", "")})
    Path(a.out_json).write_text(json.dumps({"generated_utc": iso(now), "now": now, "horizon": horizon, "trips_total": len(lt), "detail": detail}, default=str, indent=1))
    with open(a.out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {a.out_json} {a.out_csv} rows={len(rows)} live_trips={len(lt)}")


if __name__ == "__main__":
    main()
