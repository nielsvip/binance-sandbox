#!/usr/bin/env python3
"""parity_since_board — vintage-anchored vector-vs-live parity per sym_side.

For each sym_side, the vector engine replays the CURRENT published board
(cumulative_overrides) over 30D with a full ledger; every ledger event at or
after the board's own calculation timestamp (result_done_utc, else file mtime)
is compared against /history trade events in the same window.

  --dump-ledgers --keys K --boards B --out O   # needs NPZs: run where they live
  --match --ledgers L --out O                  # needs data/history: run on Mac

Match rule (USER 2026-10-21: same bars, same reasons, entries AND exits):
same type-class (entry OPEN/AUGMENT, exit REDUCE/CLOSE) within +-1 closed bar
(900 s) with the same reason family. Quantities are NOT compared (USER
2026-10-20: same bars not same quantities). Bookkeeping reasons
(BROKER_*/SYNC/VEC_CONVERGE_OPEN/REENTRY_*/B_KZONE echoes/VEC_EXIT_OWED) are
excluded on the live side; they are convergence machinery, not decisions.
"""
import argparse, datetime as dt, json, os, re, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

BOOK_LIVE = ("BROKER_RECONCILED", "Broker sync", "BROKER_SYNC",
             "SYNC_DETECTION",
             "VEC_CONVERGE_OPEN", "REENTRY_HIGHER_LOW", "REENTRY_LOWER_HIGH",
             "B_KZONE", "VEC_DRIVEN_CLOSE_RECONCILE", "VEC_EXIT_OWED",
             "VEC_STALE_FLATTEN", "VEC_DRIVEN_OPEN_RECONCILE_")
TOL_S = 900.0


def family(reason):
    r = re.sub(r"\|VEC_EXACT.*$", "", str(reason or ""))
    r = re.sub(r"\|SW=.*$", "", r)
    r = re.sub(r"^MAKER_PROFIT_EXIT_", "", r.strip())
    fam = r.split(" ")[0].split("|")[0][:48]
    fam = re.sub(r"_px[0-9.]+.*$", "", fam)
    fam = re.sub(r"_g-?[0-9.]+%?.*$", "", fam)
    if fam.startswith("E3_STRUCT_EXIT") or fam.startswith("E_3_STRUCTURE_EXIT"):
        return "E_3_STRUCTURE_EXIT"
    if fam.startswith("VIGILANCE_DC4_15m"):
        return "VIGILANCE_DC4_15m"
    if fam.startswith("VIGILANCE_MAX_LOSS"):
        return "VIGILANCE_MAX_LOSS"
    if fam.startswith("SATOSHIT"):
        return "SATOSHIT_EXIT_PARTIAL"
    if fam.startswith("VEC_EXACT_CLOSE_"):
        fam = fam[len("VEC_EXACT_CLOSE_"):]
    elif fam.startswith("VEC_EXACT_OPEN_"):
        fam = fam[len("VEC_EXACT_OPEN_"):]
    if fam.startswith("E3_STRUCT_EXIT") or fam.startswith("E_3_STRUCTURE_EXIT"):
        return "E_3_STRUCTURE_EXIT"
    m = re.match(r"^(CYCLE_TP_TIERED)_T\d+.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(MANDATORY_REENTRY)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(WT_2of3_REENTRY)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(SENT_STRAT)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(WT_PERCENTILE)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(DC_HOPELESS)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(E_1_WT_DELTA_EXIT)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(FIXED_STOP)_(LONG|SHORT)$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(DAYTRADE_TARGET)_.*$", fam)
    if m:
        return m.group(1)
    # Haiku overseer sends orders as HAIKU_WINNER_AUG_*/HAIKU_REDUCE_* (vec
    # haiku_augment twin, ez_manage:8346) but history carries the human
    # decision-log strings "Winner augment:/reduce:". Compare the order
    # (checked on the raw reason: fam truncates at the first space).
    raw = str(reason or "").strip()
    if raw.startswith("Winner augment:"):
        return "HAIKU_WINNER_AUG"
    if raw.startswith("Winner reduce:"):
        return "HAIKU_REDUCE"
    # modeled in v12 (HARDCODED_RALLY_REENTRY x12, LH_LL_TOP_EXIT x14):
    # live appends entry-price params; compare the signal.
    m = re.match(r"^(HARDCODED_RALLY_REENTRY)_.*$", fam)
    if m:
        return m.group(1)
    m = re.match(r"^(LH_LL_TOP_EXIT)_.*$", fam)
    if m:
        return m.group(1)
    # v12 folds target+stop legs into one TECHNICAL_EXIT dc_* computation
    # (v12:22364-22448); live splits the label. Compare the computation.
    m = re.match(r"^(TECHNICAL_TARGET)_.*$", fam)
    if m:
        return "TECHNICAL_EXIT"
    return fam


def tclass(t):
    t = str(t or "").upper()
    if t in ("OPEN", "AUGMENT"):
        return "entry"
    if t in ("REDUCE", "CLOSE"):
        return "exit"
    return ""


def dump_ledgers(keys, boards, out, workers=8):
    os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
    import concurrent.futures as cf
    from tools.opt.v12_pilot import prepare_batch, evaluate_prepared_sanitized

    def one(ss):
        b = boards.get(ss)
        if not b or "ov" not in b:
            return ss, {"error": "no board"}
        try:
            prep = prepare_batch(ss, 30)
            ev = evaluate_prepared_sanitized(prep, dict(b["ov"]), 30,
                                             include_ledger=True)
            led = ev.get("ledger") or []
            evs = []
            for t in led:
                ts = float(t.get("ts") or 0)
                if ts > 1e12:
                    ts /= 1000
                evs.append({"t": ts, "type": t.get("type"),
                            "reason": str(t.get("reason", ""))[:160]})
            return ss, {"trades": ev.get("trades"), "gain": ev.get("gain_pct"),
                        "valid": ev.get("valid"),
                        "invalid": ev.get("invalid_reason"), "events": evs}
        except Exception as e:
            return ss, {"error": str(e)[:200]}

    res = {}
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(one, ss): ss for ss in keys}
        for i, f in enumerate(cf.as_completed(futs)):
            ss, rec = f.result()
            res[ss] = rec
            if (i + 1) % 25 == 0:
                print(f"[dump] {i + 1}/{len(keys)}", flush=True)
    json.dump(res, open(out, "w"))
    print(f"[dump] wrote {out} keys={len(res)}")


def match_ledgers(ledgers, out):
    boards = json.load(open("/tmp/board_slim_all.json"))
    now = dt.datetime.now(dt.timezone.utc).timestamp()
    rows = []
    for ss, rec in sorted(ledgers.items()):
        b = boards.get(ss, {})
        vint = None
        for k in ("result_done_utc",):
            v = b.get(k)
            if v:
                try:
                    vint = dt.datetime.fromisoformat(
                        str(v).replace("Z", "+00:00")).timestamp()
                    break
                except Exception:
                    pass
        if vint is None and b.get("mtime"):
            vint = float(b["mtime"])
        if vint is None or vint < now - 30 * 86400:
            vint = now - 30 * 86400
        vec = [e for e in (rec.get("events") or []) if e["t"] >= vint
               and family(e["reason"]) != "FINAL_MTM"] \
            if "error" not in rec else []
        live = []
        for acc in ("ang", "men", "flz", "fin", "inf", "tra", "trb", "trc"):
            p = ROOT / "data" / "history" / acc / (ss + ".jsonl")
            if not p.exists():
                continue
            for line in open(p):
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if not tclass(e.get("type")):
                    continue
                try:
                    t = dt.datetime.fromisoformat(
                        str(e["ts"]).replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if t < vint:
                    continue
                r = str(e.get("reason", ""))
                if any(x in r for x in BOOK_LIVE):
                    continue
                live.append({"t": t, "type": e["type"],
                             "reason": r[:160]})
        used = [False] * len(vec)
        m_live = 0
        live_only = []
        for le in live:
            hit = -1
            for j, ve in enumerate(vec):
                if used[j] or tclass(ve["type"]) != tclass(le["type"]):
                    continue
                if abs(ve["t"] - le["t"]) > TOL_S:
                    continue
                if family(ve["reason"]) != family(le["reason"]):
                    continue
                hit = j
                break
            if hit >= 0:
                used[hit] = True
                m_live += 1
            else:
                live_only.append(le)
        vec_only = [vec[j] for j in range(len(vec)) if not used[j]]
        live_fams = {family(e["reason"]) + "/" + tclass(e["type"]) for e in live}
        vec_fams = {family(e["reason"]) + "/" + tclass(e["type"]) for e in vec}
        both = live_fams & vec_fams
        near = 0
        for le in live_only:
            fl, cl = family(le["reason"]), tclass(le["type"])
            if any(tclass(ve["type"]) == cl and family(ve["reason"]) == fl
                   and abs(ve["t"] - le["t"]) <= 4 * TOL_S for ve in vec):
                near += 1
        rows.append({"ss": ss, "vintage": vint, "board_trades": rec.get("trades"),
                     "live_n": len(live), "vec_n": len(vec),
                     "matched": m_live,
                     "match_pct": round(100.0 * m_live / len(live), 1) if live else None,
                     "vec_used_pct": round(100.0 * sum(used) / len(vec), 1) if vec else None,
                     "fam_recall": round(100.0 * len(both) / len(live_fams), 1) if live_fams else None,
                     "fam_precision": round(100.0 * len(both) / len(vec_fams), 1) if vec_fams else None,
                     "near_miss": near,
                     "live_only_fams": sorted(live_fams - vec_fams),
                     "vec_only_fams": sorted(vec_fams - live_fams),
                     "error": rec.get("error"),
                     "live_only": live_only[:12],
                     "vec_only": vec_only[:12]})
    json.dump(rows, open(out, "w"), indent=1)
    nn = [r for r in rows if r["live_n"]]
    full = sum(1 for r in nn if r["match_pct"] == 100.0)
    print(f"keys={len(rows)} with_live={len(nn)} full100={full}")
    for r in sorted(nn, key=lambda r: (r["match_pct"] if r["match_pct"] is not None else -1))[:30]:
        print(f"  {r['ss']} live={r['live_n']} vec={r['vec_n']} match={r['match_pct']}% used={r['vec_used_pct']}%")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump-ledgers", action="store_true")
    ap.add_argument("--match", action="store_true")
    ap.add_argument("--keys", default=None)
    ap.add_argument("--boards", default="/tmp/board_slim_all.json")
    ap.add_argument("--ledgers", default=None)
    ap.add_argument("--out", default=None)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args()
    if a.dump_ledgers:
        keys = json.load(open(a.keys))
        boards = json.load(open(a.boards))
        dump_ledgers(keys, boards, a.out or "/tmp/vec_ledgers.json",
                     workers=a.workers)
    elif a.match:
        match_ledgers(json.load(open(a.ledgers)), a.out or "/tmp/parity_match.json")
