#!/usr/bin/env python3
"""paper_forward_comprehensive — the ONE forward paper test that runs indefinitely.

USER 2026-10-22: parity between forward per_sym backtest paper trades and live is
total, so every disabled / non-vectorizable / low-timeframe path must be
paper-traded in parallel, per sym_side, with per_sym AND pooled avg deltas
tracked forward for days. A small version runs (paper_nonvec + paper_shadow +
ltf_flip); this is the comprehensive replacement that inventories EVERY path.

Inventory (rebuilt fresh on every run, never a frozen list):
  A. forward_parity/families.py FAMILIES registry (reason token -> TF/vec_twin/gate)
  B. _NON_VEC_KNOBS_EZ in ez_manage.py (Master-2 mask)
  C. SUB15M_BLOCKED_SWITCHES in ez_manage.py + tradier_manage.py (Master-1/3)
  D. live_twins kills + violations (data/vector_kills.json, data/vector_violations.jsonl)
  E. observed paper_nonvec fn families (canonicalised; catches paths newer than
     every static list, e.g. the 879 raw families seen 2026-10-17..22)
  F. TRADE_PATHS forward-test queue IDs from SPREADSHEETS/SWITCH_COVERAGE_*.xlsx
     when present (static fallback only)

Scoring reuses the sanctioned methodology (tools/paper_nonvec_eval.py):
  exit  : paper close at refusal bar vs real close of that lifecycle, side-signed
  entry : forward return over --horizon-bars 15m bars, side-signed, net costs
          (crypto 0.10 %, stocks 0.02 %)
Fixes applied here (never in locked live files):
  * venue alias "tradier" -> "stocks" (one tradier call site records "tradier",
    which paper_nonvec_eval.py maps to the wrong klines path and silently drops)
  * canonical family via loss_prone.fn_family (collapses ~879 raw singletons
    with embedded px/pct/age params into comparable families)

Promotion is a DECISION, never automatic (gate discipline: only
tools/v15_version_gate.py submit on S2 ships a switch change). This tool writes
data/paper_forward_comprehensive/promote_candidates.json + per_sym files listing paths with a
positive per_sym delta at sufficient sample; the pilot/gate consumes them.

Read-only re live: never places/cancels/sizes orders, never writes per-sym
sets. Exit 0 always (a scorer must never fail a cron chain).

Usage: python3 tools/paper_forward_comprehensive.py [--days 14] [--horizon-bars 16]
         [--min-days 7] [--min-syms 5] [--min-events 3]
       python3 tools/paper_forward_comprehensive.py --report [PATH]
Cron (6-hourly, next to PAPER_NONVEC_EVAL / PAPER_SHADOW):
  45 2,8,14,20 * * * cd /Users/niels/Documents/binance && /opt/anaconda3/envs/binance_env/bin/python tools/paper_forward_comprehensive.py >> /tmp/paper_forward_comprehensive.log 2>&1 # PAPER_FORWARD_COMPREHENSIVE
"""

from __future__ import annotations

import argparse
import ast
import bisect
import collections
import glob
import json
import re
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

OUT = (
    ROOT / "data" / "paper_forward_comprehensive"
)  # NOT data/paper_forward (broken ext-drive symlink, 2026-06 archive)
NONVEC_DIR = ROOT / "data" / "paper_nonvec"
COST = {"crypto": 0.10, "stocks": 0.02}
BAR = 900
CRYPTO_SUFFIX = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
_KL: dict = {}


def _ep(s) -> float:
    return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()


def canon_venue(v) -> str:
    v = str(v or "").lower()
    if v in ("tradier", "stock", "stocks", "equity"):
        return "stocks"
    return "crypto"


def canon_family(fn: str) -> str:
    """Grouping key = loss_prone.fn_family + param-value fold.

    fn_family is the ONE classifier (flag lookups use it raw); the fold only
    collapses embedded numeric params that otherwise explode into one family
    per event: -/=values (vel=16.1, g=0.36%, move-3.29), digits glued to a
    lowercase key (adx63->adx), and standalone _NN tokens (ENTRY_57). Kept:
    TF tokens (3M/5M: digit follows _ or uppercase), B-names (B12: single
    uppercase + digits), single-digit structure ids (E_1 vs E_3 stay distinct).
    """
    try:
        from loss_prone import fn_family as _ff

        r = _ff(fn)
    except Exception:
        r = re.sub(
            r"^VEC_EXACT_(OPEN|AUGMENT|REDUCE|CLOSE)_", "", str(fn or "").strip()
        )
        r = re.split(r"[ |:(]", r)[0] if r else ""
        r = re.sub(
            r"_(px|lvl|g|cur|k3m|k15m|qty|mult|arm|leg|tgt|act|dev|pct|gain|age|m)[-+]?[\d.].*$",
            "",
            r,
            flags=re.I,
        )
        r = re.sub(r"_(LONG|SHORT)$", "", r)
        r = re.sub(r"_[-+]?\d+(\.\d+)?%?$", "", r).rstrip("_")
    r = re.sub(r"[-=][-+]?\d+(\.\d+)?%?[a-z]?", "", str(r or ""))
    r = re.sub(r"(?<!\d)([a-z]{2,})\d+(\.\d+)?(?![a-z])", r"\1", r)
    r = re.sub(r"_\d{2,}(?=_|$)", "", r)
    r = re.sub(r"_+", "_", r).strip("_")
    return r or "UNKNOWN"


def split_sym_side(sym_side: str):
    s = str(sym_side or "")
    if s.endswith("_LONG"):
        side, sym = "LONG", s[:-5]
    elif s.endswith("_SHORT"):
        side, sym = "SHORT", s[:-6]
    else:
        side, sym = "", s
    venue = "crypto" if sym.endswith(CRYPTO_SUFFIX) else "stocks"
    return venue, sym, side


def closes(venue: str, symbol: str):
    venue = canon_venue(venue)
    k = (venue, symbol)
    if k not in _KL:
        sub = "tradier" if venue == "stocks" else ""
        p = ROOT / "klines_cache" / sub / f"{symbol}_15m.json"
        ts, cl = [], []
        try:
            rows = json.loads(p.read_text())
            rows = (
                rows
                if isinstance(rows, list)
                else (rows.get("data") or rows.get("klines") or [])
            )
            for r in rows:
                ts.append(_ep(r["timestamp"]))
                cl.append(float(r["close"]))
        except Exception:
            pass
        _KL[k] = (ts, cl)
    return _KL[k]


def close_at(venue, symbol, t, offset=0):
    ts, cl = closes(venue, symbol)
    if not ts:
        return None, None
    i = bisect.bisect_right(ts, t) - 1 + offset
    if i < 0 or i >= len(ts):
        return None, None
    return cl[i], ts[i]


def real_closes(account, sym_side):
    out = []
    p = ROOT / "data" / "history" / account / f"{sym_side}.jsonl"
    try:
        for ln in open(p, errors="replace"):
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if "CLOSE" in str(r.get("type", "")).upper() and r.get("price"):
                out.append((_ep(r["ts"]), float(r["price"])))
    except OSError:
        pass
    return sorted(out)


# ── inventory ──


def _frozen_set_from_py(path: Path, var: str) -> list:
    """Names inside VAR = frozenset([...]) / {...} in a live file (regex, never import)."""
    try:
        txt = path.read_text(errors="ignore")
    except OSError:
        return []
    m = re.search(
        rf"{re.escape(var)}\s*=\s*(?:frozenset\(\[|\{{)(.*?)(?:\]\)|\}})", txt, re.S
    )
    if not m:
        return []
    return sorted(set(re.findall(r'"([A-Z][A-Z0-9_]{5,})"', m.group(1))))


def _families_registry() -> list:
    """Rows of tools/forward_parity/families.py FAMILIES via AST (no live import)."""
    p = ROOT / "tools" / "forward_parity" / "families.py"
    try:
        tree = ast.parse(p.read_text(errors="ignore"))
    except OSError:
        return []
    rows = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Assign) and getattr(n.targets[0], "id", "") == "FAMILIES":
            for e in getattr(n.value, "elts", []):
                try:
                    vals = [
                        c.value if isinstance(c, ast.Constant) else None for c in e.elts
                    ]
                    if len(vals) >= 6 and vals[0]:
                        rows.append(
                            {
                                "token": vals[0],
                                "family": vals[1],
                                "tf": vals[2],
                                "vec_twin": bool(vals[3]),
                                "gate": vals[4],
                                "kind": vals[5],
                            }
                        )
                except Exception:
                    continue
    return rows


def build_inventory() -> dict:
    """path_id -> {sources, kind, venue, gate_knob, tf, vec_twin}."""
    inv: dict = {}

    def add(pid, **kw):
        d = inv.setdefault(
            pid,
            {
                "sources": [],
                "kind": "",
                "venue": "both",
                "gate_knob": None,
                "tf": None,
                "vec_twin": None,
            },
        )
        for s in kw.pop("sources", []):
            if s not in d["sources"]:
                d["sources"].append(s)
        for k, v in kw.items():
            if v is not None and not d.get(k):
                d[k] = v

    for r in _families_registry():
        tf = str(r["tf"] or "")
        low_tf = tf in ("1m", "3m", "5m")
        kind = "low_tf" if low_tf else ("nonvec" if not r["vec_twin"] else "disabled")
        fam = canon_family(r["family"] or r["token"])
        add(
            fam,
            sources=["families.py"],
            kind=kind,
            gate_knob=r["gate"],
            tf=tf or None,
            vec_twin=r["vec_twin"],
        )
    for knob in _frozen_set_from_py(ROOT / "ez_manage.py", "_NON_VEC_KNOBS_EZ"):
        add(
            canon_family(knob.replace("_ENABLED", "")),
            sources=["_NON_VEC_KNOBS_EZ"],
            kind="nonvec",
            venue="crypto",
            gate_knob=knob,
        )
    for f, venue in (("ez_manage.py", "crypto"), ("tradier_manage.py", "stocks")):
        for knob in _frozen_set_from_py(ROOT / f, "SUB15M_BLOCKED_SWITCHES"):
            add(
                canon_family(knob.replace("_ENABLED", "")),
                sources=[f"SUB15M:{f}"],
                kind="low_tf",
                venue=venue,
                gate_knob=knob,
                tf="1m/3m/5m",
            )
    try:
        kills = json.loads((ROOT / "data" / "vector_kills.json").read_text())
        for fam in kills:
            add(canon_family(fam), sources=["vector_kills"], kind="disabled")
    except Exception:
        pass
    try:
        seen = set()
        for ln in open(ROOT / "data" / "vector_violations.jsonl", errors="replace"):
            try:
                fam = (json.loads(ln).get("family") or "").strip()
            except Exception:
                continue
            if fam and fam not in seen:
                seen.add(fam)
                add(canon_family(fam), sources=["vector_violations"], kind="disabled")
    except OSError:
        pass
    n_obs = 0
    for f in sorted(glob.glob(str(NONVEC_DIR / "events_*.jsonl"))):
        try:
            for ln in open(f, errors="replace"):
                try:
                    fn = json.loads(ln).get("fn", "")
                except Exception:
                    continue
                cf = canon_family(fn)
                if cf and cf != "UNKNOWN":
                    n_obs += 1
                    add(
                        cf,
                        sources=["observed:paper_nonvec"],
                        kind=inv.get(cf, {}).get("kind") or "disabled",
                    )
        except OSError:
            continue
    for f in sorted(glob.glob(str(ROOT / "SPREADSHEETS" / "SWITCH_COVERAGE_*.xlsx"))):
        add("__coverage_workbook__", sources=[Path(f).name], kind="meta")
        break
    inv["_meta"] = {
        "observed_raw_events": n_obs,
        "built_at": datetime.now(timezone.utc).isoformat(),
    }
    return inv


# ── scoring ──


def load_events(days: int):
    now = datetime.now(timezone.utc).timestamp()
    events = []
    for f in sorted(glob.glob(str(NONVEC_DIR / "events_*.jsonl"))):
        for ln in open(f, errors="replace"):
            try:
                r = json.loads(ln)
                r["_t"] = _ep(r["ts"])
            except Exception:
                continue
            if now - r["_t"] <= days * 86400:
                r["_venue"] = canon_venue(r.get("venue"))
                r["_fam"] = canon_family(r.get("fn"))
                events.append(r)
    events.sort(key=lambda r: r["_t"])
    return events


def score(events, horizon_bars: int):
    """(per_sym_rows, path_rows): per_sym_rows[(path, sym_side)] -> stats; path pooled."""
    per: dict = collections.defaultdict(list)
    first_seen: dict = {}
    seen_exit, seen_entry = set(), {}
    rc_cache: dict = {}
    for e in events:
        venue, kind, fam = e["_venue"], e.get("kind"), e["_fam"]
        if not fam or fam == "UNKNOWN":
            continue
        pk = e.get("position_key", "")
        acct, _, ss = pk.partition(":")
        if not ss or "_" not in ss:
            continue
        symbol, side = (ss.rsplit("_", 1) + [""])[:2]
        is_long = side == "LONG"
        key = (venue, kind, fam)
        first_seen.setdefault(key, e["_t"])
        px, _ = close_at(venue, symbol, e["_t"])
        if not px:
            continue
        if kind == "exit":
            if (acct, ss) not in rc_cache:
                rc_cache[(acct, ss)] = real_closes(acct, ss)
            rcs = rc_cache[(acct, ss)]
            i = bisect.bisect_right([t for t, _ in rcs], e["_t"])
            lifecycle = (
                f"{acct}:{ss}@{rcs[i][0]}" if i < len(rcs) else f"{acct}:{ss}@open"
            )
            dk = (lifecycle, fam)
            if dk in seen_exit:
                continue
            seen_exit.add(dk)
            if i >= len(rcs):
                per[(fam, venue, kind, ss)].append((None, True))
                continue
            real = rcs[i][1]
            d = (px - real) / real if is_long else (real - px) / real
            per[(fam, venue, kind, ss)].append((d, False))
        else:
            wk = (fam, ss, int(e["_t"] // (BAR * horizon_bars)))
            if wk in seen_entry:
                continue
            seen_entry[wk] = True
            ch, _ = close_at(venue, symbol, e["_t"], offset=horizon_bars)
            if not ch:
                per[(fam, venue, kind, ss)].append((None, True))
                continue
            cost = COST.get(venue, 0.10) / 100.0
            s = 1.0 if is_long else -1.0
            per[(fam, venue, kind, ss)].append((s * (ch - px) / px - cost, False))
    return per, first_seen


# ── aggregation + persistence ──


def aggregate(per, first_seen, now, min_days, min_syms, min_events):
    per_sym_rows = []
    by_path: dict = collections.defaultdict(list)
    for (fam, venue, kind, ss), vals in per.items():
        scored = [d for d, pend in vals if not pend and d is not None]
        pending = sum(1 for _, pend in vals if pend)
        if not scored:
            mean = None
        else:
            mean = sum(scored) / len(scored)
        row = {
            "path": fam,
            "venue": venue,
            "kind": kind,
            "sym_side": ss,
            "events": len(vals),
            "scored": len(scored),
            "pending": pending,
            "mean_delta": mean,
            "pos": mean is not None and mean > 0,
        }
        per_sym_rows.append(row)
        if scored:
            by_path[(fam, venue, kind)].append(row)
    path_rows = []
    for (fam, venue, kind), rows in sorted(by_path.items()):
        syms = len(rows)
        pos = sum(1 for r in rows if r["pos"])
        pooled = sum(r["mean_delta"] * r["scored"] for r in rows) / max(
            1, sum(r["scored"] for r in rows)
        )
        days = (now - first_seen.get((venue, kind, fam), now)) / 86400.0
        if days >= min_days and syms >= min_syms and pos > syms / 2 and pooled > 0:
            verdict = "PROMOTE_CANDIDATE"
        elif days >= min_days and syms >= min_syms and pos <= syms / 2:
            verdict = "REJECT"
        else:
            verdict = "WATCH"
        path_rows.append(
            {
                "path": fam,
                "venue": venue,
                "kind": kind,
                "sym_sides": syms,
                "pos_syms": pos,
                "events": sum(r["events"] for r in rows),
                "scored": sum(r["scored"] for r in rows),
                "pooled_delta": pooled,
                "days": round(days, 1),
                "verdict": verdict,
            }
        )
    path_rows.sort(
        key=lambda r: (
            r["verdict"] != "PROMOTE_CANDIDATE",
            -(r["pooled_delta"] or -9e9),
        )
    )
    per_sym_rows.sort(
        key=lambda r: (-(r["mean_delta"] or -9e9), r["path"], r["sym_side"])
    )
    return per_sym_rows, path_rows


def shadow_leg_status():
    """Freshness of the faster-TF timing leg (paper_shadow). STALE, not zero, when old."""
    try:
        db = ROOT / "data" / "paper_shadow" / "shadow.db"
        age_h = (datetime.now(timezone.utc).timestamp() - db.stat().st_mtime) / 3600.0
        return {"db": str(db), "age_h": round(age_h, 1), "fresh": age_h < 30.0}
    except OSError:
        return {"db": None, "age_h": None, "fresh": False}


def persist(inv, per_sym_rows, path_rows, args, n_events):
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "per_sym").mkdir(parents=True, exist_ok=True)
    now_iso = datetime.now(timezone.utc).isoformat()
    (OUT / "inventory.json").write_text(json.dumps(inv, indent=1, sort_keys=True))
    scores = {
        "generated_at": now_iso,
        "criteria": {
            "days": args.days,
            "horizon_bars": args.horizon_bars,
            "min_days": args.min_days,
            "min_syms": args.min_syms,
            "min_events": args.min_events,
        },
        "n_events": n_events,
        "n_paths": len([k for k in inv if not k.startswith("_")]),
        "per_sym": per_sym_rows,
        "paths": path_rows,
        "shadow_leg": shadow_leg_status(),
    }
    (OUT / "scores.json").write_text(json.dumps(scores, indent=1))
    cands: dict = collections.defaultdict(list)
    for r in per_sym_rows:
        if (
            r["mean_delta"] is not None
            and r["mean_delta"] > 0
            and r["scored"] >= args.min_events
        ):
            cands[r["sym_side"]].append(
                {
                    "path": r["path"],
                    "venue": r["venue"],
                    "kind": r["kind"],
                    "mean_delta": round(r["mean_delta"], 6),
                    "scored": r["scored"],
                }
            )
    for ss in cands:
        cands[ss].sort(key=lambda d: -d["mean_delta"])
        (OUT / "per_sym" / f"{ss}.json").write_text(
            json.dumps(
                {
                    "sym_side": ss,
                    "generated_at": now_iso,
                    "paths": cands[ss],
                    "note": "candidate enable list for v15_version_gate submit on S2; never auto-applied live",
                },
                indent=1,
            )
        )
    (OUT / "promote_candidates.json").write_text(
        json.dumps(
            {
                "generated_at": now_iso,
                "blanket": [
                    r for r in path_rows if r["verdict"] == "PROMOTE_CANDIDATE"
                ],
                "per_sym_sides": len(cands),
                "note": "blanket = pooled rule (>=min_days, >=min_syms, >half pos, pooled>0); per_sym/*.json = per-sym_side pos-delta enable lists",
            },
            indent=1,
        )
    )
    lines = [
        f"# Paper forward comprehensive — {now_iso}",
        "",
        f"events={n_events} window={args.days}d horizon={args.horizon_bars} bars paths={scores['n_paths']}",
        f"shadow leg (paper_shadow timing): {scores['shadow_leg']}",
        "",
        "| path | venue | kind | sym_sides | pos | pooled Δ | days | verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in path_rows[:120]:
        pd = f"{r['pooled_delta']:+.4f}" if r["pooled_delta"] is not None else "n/a"
        lines.append(
            f"| {r['path']} | {r['venue']} | {r['kind']} | {r['sym_sides']} | {r['pos_syms']} | {pd} | {r['days']} | {r['verdict']} |"
        )
    if len(path_rows) > 120:
        lines.append(f"| … {len(path_rows) - 120} more in scores.json | | | | | | | |")
    lines += [
        "",
        "## Per-sym_side positive deltas (top 60)",
        "| path | sym_side | mean Δ | scored |",
        "|---|---|---|---|",
    ]
    pos_rows = [
        r for r in per_sym_rows if r["mean_delta"] is not None and r["mean_delta"] > 0
    ][:60]
    for r in pos_rows:
        lines.append(
            f"| {r['path']} | {r['sym_side']} | {r['mean_delta']:+.4f} | {r['scored']} |"
        )
    if not pos_rows:
        lines.append("| (none yet — needs days of forward data) | | | |")
    (OUT / "verdict.md").write_text("\n".join(lines) + "\n")
    con = sqlite3.connect(str(OUT / "forward.db"))
    try:
        con.execute(
            "CREATE TABLE IF NOT EXISTS inventory (path TEXT PRIMARY KEY, kind TEXT, venue TEXT, gate_knob TEXT, tf TEXT, sources TEXT)"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS per_sym_delta (path TEXT, venue TEXT, kind TEXT, sym_side TEXT, events INT, scored INT, pending INT, mean_delta REAL, PRIMARY KEY (path, venue, kind, sym_side))"
        )
        con.execute(
            "CREATE TABLE IF NOT EXISTS path_summary (path TEXT, venue TEXT, kind TEXT, sym_sides INT, pos_syms INT, events INT, scored INT, pooled_delta REAL, days REAL, verdict TEXT, PRIMARY KEY (path, venue, kind))"
        )
        con.execute("DELETE FROM inventory")
        con.execute("DELETE FROM per_sym_delta")
        con.execute("DELETE FROM path_summary")
        for pid, d in inv.items():
            if pid.startswith("_"):
                continue
            con.execute(
                "INSERT INTO inventory VALUES (?,?,?,?,?,?)",
                (
                    pid,
                    d.get("kind"),
                    d.get("venue"),
                    d.get("gate_knob"),
                    d.get("tf"),
                    ",".join(d.get("sources", [])),
                ),
            )
        for r in per_sym_rows:
            con.execute(
                "INSERT INTO per_sym_delta VALUES (?,?,?,?,?,?,?,?)",
                (
                    r["path"],
                    r["venue"],
                    r["kind"],
                    r["sym_side"],
                    r["events"],
                    r["scored"],
                    r["pending"],
                    r["mean_delta"],
                ),
            )
        for r in path_rows:
            con.execute(
                "INSERT INTO path_summary VALUES (?,?,?,?,?,?,?,?,?,?)",
                (
                    r["path"],
                    r["venue"],
                    r["kind"],
                    r["sym_sides"],
                    r["pos_syms"],
                    r["events"],
                    r["scored"],
                    r["pooled_delta"],
                    r["days"],
                    r["verdict"],
                ),
            )
        con.commit()
    finally:
        con.close()
    return scores


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--horizon-bars", type=int, default=16)
    ap.add_argument("--min-days", type=float, default=7.0)
    ap.add_argument("--min-syms", type=int, default=5)
    ap.add_argument("--min-events", type=int, default=3)
    ap.add_argument("--report", nargs="?", const="", default=None)
    a = ap.parse_args()
    if a.report is not None:
        try:
            scores = json.loads((OUT / "scores.json").read_text())
        except Exception:
            print("no scores yet — run without --report first")
            return
        filt = (a.report or "").upper()
        rows = [
            r
            for r in scores["per_sym"]
            if not filt or filt in r["path"].upper() or filt in r["sym_side"].upper()
        ]
        print(
            f"per_sym avg-delta rows: {len(rows)} (generated {scores['generated_at']})"
        )
        for r in rows[:80]:
            md = f"{r['mean_delta']:+.4f}" if r["mean_delta"] is not None else "n/a"
            print(
                f"{r['path'][:44]:44s} {r['sym_side'][:22]:22s} {r['kind']:5s} mean={md} n={r['scored']}/{r['events']} pend={r['pending']}"
            )
        return
    inv = build_inventory()
    events = load_events(a.days)
    per, first_seen = score(events, a.horizon_bars)
    now = datetime.now(timezone.utc).timestamp()
    per_sym_rows, path_rows = aggregate(
        per, first_seen, now, a.min_days, a.min_syms, a.min_events
    )
    scores = persist(inv, per_sym_rows, path_rows, a, len(events))
    n_prom = sum(1 for r in path_rows if r["verdict"] == "PROMOTE_CANDIDATE")
    n_pos_sym = sum(
        1 for r in per_sym_rows if r["mean_delta"] is not None and r["mean_delta"] > 0
    )
    print(
        f"[paper_forward] events={len(events)} paths={scores['n_paths']} per_sym_rows={len(per_sym_rows)} "
        f"pos_sym_rows={n_pos_sym} blanket_promote={n_prom} shadow={scores['shadow_leg']} -> {OUT}"
    )


if __name__ == "__main__":
    main()
