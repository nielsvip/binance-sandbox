#!/usr/bin/env python3
"""v15_invention_loop — the observation-to-tested-switch backlog (USER 2026-10-09).

The fleet PRODUCES invention observations (missed-trend PROPOSED_NEW_SWITCH specs, trade-autopsy
missing_functions, diagnose MISSING_LEVER gaps, verify COVERAGE backlog) but nothing collected them,
ranked them, or tracked them into tested switches — §71 runs per-incident, by hand. This closes that
loop WITHOUT touching locked code itself:

  mine           scan observation sources -> ranked backlog (safe to cron; reads + backlog write only)
  spec <id>      render a §71 Muse work order (the intelligence step: paste into a Muse session)
  ingest         validate a landing (WIRED via SWITCH_BIBLE.json status, TESTED via ledger-flip proof)
  promote-check  TESTED -> PROMOTED/RETIRED from sweep evidence (done-row deltas, pos_sym rule)
  report         backlog counts + top opportunities

State: data/invention/backlog.json (+ orders/, proofs/). Statuses: OBSERVED -> SPECD -> WIRED ->
TESTED -> PROMOTED | RETIRED. RETIRED never deletes code (DEATH PENALTY) — it records the verdict
so no future cycle re-proposes the same dead end; the eval-time skipping (zero-formula/unwired/
possym/inert) remains the discard mechanism.

2026-10-13 fixes (self-improvement lane):
- autopsy schema: the trade autopsy writes `missing_entry_filters` / `missing_exit_confirmations`
  (feature, op, thr, at, bad_removed/bad_total, good_removed/good_total, net_pnl [gain_pp], family,
  status MISSING|FAMILY_EXISTS_NO_FIX). The old reader looked for `missing_functions` and produced 0
  observations ever. Rules are aggregated by (at, cat_side, feature, op) over DISTINCT sym_sides
  (newest autopsy file per sym_side wins when several dirs hold the same board).
- score = move magnitude x distinct sym_side count. Autopsy: mean net_pnl (pp) x n distinct syms.
  Missed-trend: max move_pct x n distinct sym_sides. Gaps / coverage: fixed unit (2 / 5 / 1) x syms.
- evidence is deduped by an evidence signature (replace in place, never blind append); `symsides`
  is the UNION of everything ever seen (not recomputed from the truncated evidence tail).
- every autopsy-sourced proposal is `[DIAGNOSTIC ONLY]`: single-feature, in-sample thresholds per
  sym_side; nothing is promoted from it without a real v12_quick_engine + backtest_v12_engine proof.
- template ROW status is never claimed: `rows_status` comes from opening the templates read-only
  (ROWS_INSTALLED / ROWS_PARTIAL / ROWS_QUEUED via data/template_row_queue.json / ROWS_MISSING).
- source dirs: --autopsy-dirs / --progress-dirs / --missed-dirs (comma list, globs ok) or env
  V15_INVENT_AUTOPSY_DIRS / V15_INVENT_PROGRESS_DIRS / V15_INVENT_MISSED_DIRS; absent dirs fail open.
"""

import argparse
import csv
import glob
import json
import os
import re
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATUSES = ("OBSERVED", "SPECD", "WIRED", "TESTED", "PROMOTED", "RETIRED")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_backlog(data_dir):
    p = os.path.join(data_dir, "data", "invention", "backlog.json")
    if os.path.exists(p):
        try:
            d = json.load(open(p))
            d.setdefault("proposals", {})
            d.setdefault("counter", 0)
            return d
        except Exception:
            pass
    return {"updated_utc": utcnow(), "counter": 0, "proposals": {}}


def save_backlog(data_dir, d):
    d["updated_utc"] = utcnow()
    out = os.path.join(data_dir, "data", "invention", "backlog.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    tmp = out + ".tmp"
    json.dump(d, open(tmp, "w"), indent=1, default=str)
    os.replace(tmp, out)


def _slug(s, n=48):
    s = re.sub(r"[^A-Za-z0-9]+", "_", str(s or "")).strip("_").upper()
    return s[:n] or "GENERIC"


def trigger_family(rule):
    r = str(rule or "").lower()
    if "lower high" in r:
        return "LH_TRIGGER"
    if "higher low" in r:
        return "HL_TRIGGER"
    if "reentry" in r or "re-enter" in r or "reenter" in r:
        return "REENTRY_TRIGGER"
    if "breakout" in r or "breakdown" in r:
        return "BREAK_TRIGGER"
    return "GENERIC"


def scan_missed_trend(dirs):
    obs = []
    for dd in dirs:
        for f in glob.glob(os.path.join(dd, "*_missed_trend.json")):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            m = d.get("missing") or {}
            ss = d.get("symside", "")
            direction = (
                "SHORT"
                if ss.upper().endswith("_SHORT")
                else ("LONG" if ss.upper().endswith("_LONG") else "")
            )
            if m.get("status") == "PROPOSED_NEW_SWITCH":
                fam = trigger_family(m.get("rule"))
                t = m.get("trend") or {}
                obs.append(
                    {
                        "sig": ("NEW_SWITCH", direction, fam),
                        "kind": "NEW_SWITCH",
                        "title": f"{direction or 'DIR?'} {fam}: {str(m.get('rule') or '')[:120]}",
                        "symside": ss,
                        "direction": direction,
                        "score": float(t.get("move_pct") or 0),
                        "evidence": {
                            "src": "missed_trend",
                            "file": f,
                            "symside": ss,
                            "trend": t,
                            "rule": m.get("rule"),
                        },
                    }
                )
                continue
            if d.get("revise"):
                trends = d.get("trends") or []
                big = trends[0] if trends else {}
                if (big.get("late_by_bars") or 0) >= 100:
                    fam = (
                        "LH_TRIGGER"
                        if direction == "SHORT"
                        else ("HL_TRIGGER" if direction == "LONG" else "GENERIC")
                    )
                    obs.append(
                        {
                            "sig": ("NEW_SWITCH", direction, fam),
                            "kind": "NEW_SWITCH",
                            "title": f"{direction} {fam}: first entry {big.get('late_by_bars')} bars after a {big.get('move_pct')}% extreme (late capture)",
                            "symside": ss,
                            "direction": direction,
                            "score": float(big.get("move_pct") or 0),
                            "evidence": {
                                "src": "missed_trend_late",
                                "file": f,
                                "symside": ss,
                                "trend": big,
                                "rule": f"entry trigger nearer the extreme at bar {big.get('b0')}",
                            },
                        }
                    )
    return obs


CRYPTO_QUOTES = ("USDT", "USDC", "BUSD", "FDUSD")
AUTOPSY_RULE_KEYS = ("missing_entry_filters", "missing_exit_confirmations")
DIAG = "[DIAGNOSTIC ONLY]"


def _direction(ss):
    u = str(ss or "").upper()
    return "SHORT" if u.endswith("_SHORT") else ("LONG" if u.endswith("_LONG") else "")


def cat_side_of(ss):
    u = str(ss or "").upper()
    d = _direction(u)
    sym = u.rsplit("_", 1)[0] if d else u
    venue = "CRYPTO" if sym.endswith(CRYPTO_QUOTES) else "STOCKS"
    return f"{venue}_{d}" if d else venue


def _expand_dirs(dirs):
    out = []
    for dd in dirs or []:
        for x in sorted(glob.glob(os.path.expanduser(str(dd)))) or []:
            if os.path.isdir(x) and x not in out:
                out.append(x)
    return out


def _median(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2.0


def scan_autopsy(dirs, min_syms=1):
    """Aggregate autopsy missing rules by (at, cat_side, feature, op) over DISTINCT sym_sides.

    Newest `*_autopsy.json` per sym_side wins (the same board can sit in several autopsy dirs).
    One observation per rule key: score = mean net_pnl (pp) x n distinct sym_sides. [DIAGNOSTIC ONLY].
    """
    newest = {}
    for dd in _expand_dirs(dirs):
        for f in glob.glob(os.path.join(dd, "*_autopsy.json")):
            try:
                mt = os.path.getmtime(f)
            except OSError:
                continue
            ss = os.path.basename(f)[: -len("_autopsy.json")]
            if ss not in newest or mt > newest[ss][0]:
                newest[ss] = (mt, f)
    agg = {}
    for ss, (_mt, f) in sorted(newest.items()):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ss = d.get("symside") or ss
        cs = cat_side_of(ss)
        seen = set()
        for rk in AUTOPSY_RULE_KEYS:
            rules = d.get(rk) or []
            for r in rules if isinstance(rules, list) else []:
                if not isinstance(r, dict) or not r.get("feature") or not r.get("op"):
                    continue
                at = str(r.get("at") or ("entry" if "entry" in rk else "exit")).lower()
                key = (at, cs, str(r["feature"]), str(r["op"]))
                if (key, ss) in seen:
                    continue
                seen.add((key, ss))
                a = agg.setdefault(
                    key,
                    {
                        "syms": set(),
                        "net": [],
                        "thr": [],
                        "bad_removed": 0,
                        "bad_total": 0,
                        "good_removed": 0,
                        "good_total": 0,
                        "status": defaultdict(int),
                        "family": defaultdict(int),
                        "files": 0,
                    },
                )
                a["syms"].add(ss)
                a["files"] += 1
                for k in ("bad_removed", "bad_total", "good_removed", "good_total"):
                    if isinstance(r.get(k), (int, float)):
                        a[k] += r[k]
                if isinstance(r.get("net_pnl"), (int, float)):
                    a["net"].append(float(r["net_pnl"]))
                if isinstance(r.get("thr"), (int, float)):
                    a["thr"].append(float(r["thr"]))
                a["status"][str(r.get("status") or "?")] += 1
                a["family"][str(r.get("family") or "?")] += 1
    obs = []
    for (at, cs, feat, op), a in sorted(agg.items()):
        n = len(a["syms"])
        if n < min_syms:
            continue
        mean_net = (sum(a["net"]) / len(a["net"])) if a["net"] else 0.0
        missing = a["status"].get("MISSING", 0) > 0
        kind = "AUTOPSY_NEW_RULE" if missing else "AUTOPSY_FAMILY_EXTEND"
        fam = max(a["family"].items(), key=lambda kv: kv[1])[0] if a["family"] else "?"
        thr = _median(a["thr"])
        verb = "block entry" if at == "entry" else "hold (do not exit)"
        title = (
            f"{DIAG} {cs} {verb} when {feat} {op} ~{thr:.4g} ({n} syms, mean {mean_net:+.2f}pp in-sample, family {fam})"
            if thr is not None
            else f"{DIAG} {cs} {verb} on {feat} {op} ({n} syms)"
        )
        obs.append(
            {
                "sig": ("AUTOPSY_RULE", at.upper(), cs, feat, op),
                "kind": kind,
                "title": title,
                "symside": "",
                "symsides": sorted(a["syms"]),
                "direction": cs.rsplit("_", 1)[-1] if "_" in cs else "",
                "score": round(mean_net * n, 4),
                "score_mode": "replace",
                "diagnostic_only": True,
                "evidence": {
                    "src": "autopsy",
                    "ev_sig": f"autopsy|{at}|{cs}|{feat}|{op}",
                    "cat_side": cs,
                    "at": at,
                    "feature": feat,
                    "op": op,
                    "thr_median": thr,
                    "thr_min": min(a["thr"]) if a["thr"] else None,
                    "thr_max": max(a["thr"]) if a["thr"] else None,
                    "n_syms": n,
                    "mean_net_pp": round(mean_net, 4),
                    "bad_removed": a["bad_removed"],
                    "bad_total": a["bad_total"],
                    "good_removed": a["good_removed"],
                    "good_total": a["good_total"],
                    "status": dict(a["status"]),
                    "family": fam,
                    "symsides_sample": sorted(a["syms"])[:25],
                    "note": f"{DIAG} single-feature in-sample thresholds per sym_side; removes good trades too — engine-verify via v12_quick_engine + backtest_v12_engine before any promotion",
                    "mined_utc": utcnow(),
                },
            }
        )
    return obs


def _gap_symside(path, d):
    return d.get("symside") or os.path.basename(path).replace(
        "_v14_progress.json", ""
    ).replace(".json", "")


def scan_gaps(files):
    obs = []
    for f in files:
        try:
            d = json.load(open(f))
        except Exception:
            continue
        gaps = list(d.get("gaps") or []) + list(
            (d.get("diagnose_repair") or {}).get("gaps") or []
        )
        ss = _gap_symside(f, d)
        for g in gaps:
            if not isinstance(g, dict):
                continue
            st = g.get("status")
            if st == "MISSING_LEVER":
                obs.append(
                    {
                        "sig": (
                            "NEW_SWITCH",
                            _slug(g.get("fault")),
                            _slug(g.get("metric")),
                        ),
                        "kind": "NEW_SWITCH",
                        "title": f"{g.get('fault')}/{g.get('metric')}: no lever — {str(g.get('note') or '')[:100]}",
                        "symside": ss,
                        "direction": "",
                        "score": 2.0,
                        "evidence": {"src": "gaps", "file": f, "symside": ss, "gap": g},
                    }
                )
            elif st == "LEVER_EXISTS_BUT_COSTLY":
                obs.append(
                    {
                        "sig": ("FORMULA_FIX", _slug(str(g.get("best")).split("=")[0])),
                        "kind": "FORMULA_FIX",
                        "title": f"costly lever {g.get('best')}: move {g.get('move')} gain_at {g.get('gain_at')}",
                        "symside": ss,
                        "direction": "",
                        "score": 1.0,
                        "evidence": {"src": "gaps", "file": f, "symside": ss, "gap": g},
                    }
                )
    return obs


def scan_coverage(verify_path, bible):
    obs = []
    try:
        v = json.load(open(verify_path))
    except Exception:
        return obs
    switches = (bible or {}).get("switches", {})
    for entry in (v.get("checks") or {}).get("COVERAGE") or []:
        m = re.match(r"([A-Za-z0-9_]+)\s*:", str(entry))
        if not m:
            continue
        sw = m.group(1)
        reg = (switches.get(sw) or {}).get("agent_c_registry") or {}
        obs.append(
            {
                "sig": ("ROW_ONLY", sw),
                "kind": "ROW_ONLY",
                "title": f"{sw}: wired live+vec but in no template",
                "symside": "",
                "direction": "",
                "score": 5.0,
                "evidence": {
                    "src": "coverage",
                    "switch": sw,
                    "note": str(entry)[:250],
                    "home_tab": reg.get("suggested_home_tab"),
                    "live_reads": ((switches.get(sw) or {}).get("live_reads") or {}),
                    "vec_reads": ((switches.get(sw) or {}).get("vec_reads") or [])[:4],
                },
            }
        )
    return obs


EVIDENCE_CAP = 50


def ev_sig(e):
    """Stable identity of one piece of evidence (dedupe key; re-mining the same file never duplicates)."""
    if not isinstance(e, dict):
        return json.dumps(e, sort_keys=True, default=str)[:200]
    if e.get("ev_sig"):
        return str(e["ev_sig"])
    src = e.get("src") or "?"
    if src.startswith("missed_trend"):
        t = e.get("trend") or {}
        return (
            f"{src}|{e.get('symside')}|{t.get('b0')}|{t.get('b1')}|{t.get('move_pct')}"
        )
    if src == "gaps":
        g = e.get("gap") or {}
        return f"gaps|{e.get('symside')}|{g.get('fault')}|{g.get('metric')}|{g.get('status')}|{g.get('best')}"
    if src == "coverage":
        return f"coverage|{e.get('switch')}"
    if src == "autopsy":
        m = e.get("missing")
        return f"autopsy|{e.get('symside')}|{json.dumps(m, sort_keys=True, default=str)[:160]}"
    return f"{src}|{e.get('symside') or e.get('switch')}|{json.dumps(e, sort_keys=True, default=str)[:160]}"


def _merge_evidence(p, new_ev):
    """Replace-or-append by ev_sig; returns True when the signature is new."""
    evs = p.get("evidence") or []
    sigs = [ev_sig(e) for e in evs]
    s = ev_sig(new_ev)
    known = set(p.get("ev_sigs") or sigs)
    is_new = s not in known
    if s in sigs:
        evs[sigs.index(s)] = new_ev
    else:
        evs.append(new_ev)
    p["evidence"] = evs[-EVIDENCE_CAP:]
    known.add(s)
    p["ev_sigs"] = sorted(known)
    p["n_evidence"] = len(known)
    return is_new


def normalize_proposal(p):
    """One-time repair of pre-2026-10-13 blind appends: dedupe evidence, symsides = union, n_evidence = distinct."""
    evs = p.get("evidence") or []
    seen, out = {}, []
    for e in evs:
        s = ev_sig(e)
        if s in seen:
            out[seen[s]] = e
        else:
            seen[s] = len(out)
            out.append(e)
    p["evidence"] = out[-EVIDENCE_CAP:]
    p["ev_sigs"] = sorted(set(p.get("ev_sigs") or []) | set(seen))
    p["n_evidence"] = len(p["ev_sigs"])
    ss = set(p.get("symsides") or [])
    ss |= {e.get("symside") for e in out if isinstance(e, dict) and e.get("symside")}
    p["symsides"] = sorted(x for x in ss if x)
    return p


def _rescore(p, o):
    if o.get("score_mode") == "replace":
        p["score"] = float(o["score"])
        return
    p["unit_score"] = max(
        float(p.get("unit_score") or 0.0), float(o.get("score") or 0.0)
    )
    p["score"] = round(p["unit_score"] * max(1, len(p.get("symsides") or [])), 4)


def upsert(backlog, observations):
    by_sig = {}
    for pid, p in backlog["proposals"].items():
        by_sig[tuple(p.get("sig") or ())] = pid
        normalize_proposal(p)
    added = updated = 0
    for o in observations:
        sig = tuple(o["sig"])
        new_ss = set(o.get("symsides") or []) | (
            {o["symside"]} if o.get("symside") else set()
        )
        if sig in by_sig:
            p = backlog["proposals"][by_sig[sig]]
            if p["status"] in ("PROMOTED", "RETIRED"):
                continue
            changed = _merge_evidence(p, o["evidence"])
            before = set(p.get("symsides") or [])
            p["symsides"] = sorted(before | new_ss)
            if o.get("score_mode") == "replace":
                p["title"] = o["title"]
            _rescore(p, o)
            if o.get("diagnostic_only"):
                p["diagnostic_only"] = True
            if changed or set(p["symsides"]) != before:
                updated += 1
        else:
            backlog["counter"] += 1
            pid = f"INV-{backlog['counter']:04d}"
            by_sig[sig] = pid
            p = {
                "id": pid,
                "sig": list(sig),
                "kind": o["kind"],
                "title": o["title"],
                "symside": o.get("symside", ""),
                "direction": o.get("direction", ""),
                "status": "OBSERVED",
                "score": 0.0,
                "evidence": [],
                "n_evidence": 0,
                "symsides": sorted(new_ss),
                "spec_path": None,
                "switch": None,
                "history": [
                    {"ts": utcnow(), "from": None, "to": "OBSERVED", "note": "mined"}
                ],
            }
            if o.get("diagnostic_only"):
                p["diagnostic_only"] = True
            _merge_evidence(p, o["evidence"])
            _rescore(p, o)
            backlog["proposals"][pid] = p
            added += 1
    return added, updated


TEMPLATE_SETS = (
    os.path.join("SPREADSHEETS", "TEMPLATE_FINAL_NORM"),
    "SPREADSHEETS",
)
TEMPLATE_FILES = (
    "TEMPLATE_CRYPTO_LONG.xlsx",
    "TEMPLATE_CRYPTO_SHORT.xlsx",
    "TEMPLATE_STOCKS_LONG.xlsx",
    "TEMPLATE_STOCKS_SHORT.xlsx",
)


def template_row_counts(data_dir, switches):
    """READ-ONLY: count column-A rows per switch in each of the 8 templates. Never writes."""
    out = {}
    want = set(switches or [])
    if not want:
        return out
    try:
        import openpyxl
    except Exception:
        return out
    for tset in TEMPLATE_SETS:
        for fn in TEMPLATE_FILES:
            path = os.path.join(data_dir, tset, fn)
            if not os.path.exists(path):
                continue
            rel = os.path.join(tset, fn)
            counts = {sw: 0 for sw in want}
            try:
                wb = openpyxl.load_workbook(path, read_only=True)
            except Exception:
                continue
            try:
                for ws in wb.worksheets:
                    for (v,) in ws.iter_rows(min_row=3, max_col=1, values_only=True):
                        if v in want:
                            counts[v] += 1
            finally:
                wb.close()
            out[rel] = counts
    return out


def queued_switches(data_dir):
    p = os.path.join(data_dir, "data", "template_row_queue.json")
    try:
        q = json.load(open(p))
    except Exception:
        return set()
    items = (
        q
        if isinstance(q, list)
        else (q.get("queue") or q.get("entries") or q.get("rows") or [])
    )
    if isinstance(items, dict):
        items = list(items.values())
    return {
        str(e.get("switch")) for e in items if isinstance(e, dict) and e.get("switch")
    }


def rows_status_for(counts, switches, queued):
    """ROWS_INSTALLED only when every switch has rows in all 4 FINAL_NORM files present (opened, counted)."""
    norm = {k: v for k, v in counts.items() if "TEMPLATE_FINAL_NORM" in k}
    if norm and all(all(v.get(sw, 0) > 0 for v in norm.values()) for sw in switches):
        return "ROWS_INSTALLED"
    if any(v.get(sw, 0) > 0 for v in counts.values() for sw in switches):
        return "ROWS_PARTIAL"
    if switches and all(sw in queued for sw in switches):
        return "ROWS_QUEUED"
    return "ROWS_MISSING"


def rows_check(data_dir, backlog):
    """Annotate every WIRED+ proposal with real template row counts (read-only) -> rows_status."""
    queued = queued_switches(data_dir)
    res = {}
    for pid, p in backlog["proposals"].items():
        sws = list(p.get("switches") or ([p["switch"]] if p.get("switch") else []))
        if not sws or p.get("status") not in ("WIRED", "TESTED", "PROMOTED"):
            continue
        counts = template_row_counts(data_dir, sws)
        st = rows_status_for(counts, sws, queued)
        prev = p.get("rows_status")
        p["template_rows"] = counts
        p["rows_status"] = st
        p["rows_checked_utc"] = utcnow()
        if prev != st:
            p["history"].append(
                {
                    "ts": utcnow(),
                    "from": prev,
                    "to": st,
                    "note": "rows-check (templates opened read-only)",
                }
            )
        res[pid] = st
    return res


ORDER_PREAMBLE = """Paste this file into a Muse session running in the repo root. It is a complete §71 work
order: goal, evidence, code anchors, procedure, acceptance. Repo law applies: LOCKED_FILES.md first
(locked file needs user "unlock <file>" in the SAME message before you touch it), backup before every
edit (cp <file> backups/before_<desc>_<YYYYMMDDHHMM>.py), black formatting, compile check, smallest
focused test with the change, NEVER revert/rollback, never fabricate a number.

"""


def neighbors_for(bible, kind, direction, family, tab_hint=None, k=3):
    """Nearest wired switches to anchor the work: same kind/tab family, WIRED status, with reads."""
    switches = (bible or {}).get("switches", {})
    want_tab = tab_hint or (
        "ENTRY" if "TRIGGER" in family or kind == "NEW_SWITCH" else ""
    )
    scored = []
    for name, s in switches.items():
        st = s.get("status") or {}
        if not any("WIRED" in str(x) for x in st.values()):
            continue
        score = 0
        tabs = set()
        for tinfo in (s.get("template") or {}).values():
            for r in tinfo.get("rows") or []:
                tabs.add(r.get("tab", ""))
        if want_tab and any(want_tab in t for t in tabs):
            score += 2
        if direction and direction in (s.get("sides") or []):
            score += 1
        if family.split("_")[0] in name:
            score += 1
        if score:
            scored.append((score, name, s, sorted(tabs)[:3]))
    scored.sort(reverse=True)
    return scored[:k]


def render_order(prop, bible):
    p = prop
    ev0 = (p.get("evidence") or [{}])[0]
    L = [ORDER_PREAMBLE, f"# Work order {p['id']}: {p['title']}", ""]
    L.append(
        f"Kind: {p['kind']} | direction: {p.get('direction') or 'n/a'} | evidence: {p.get('n_evidence', 1)} obs from {len(p.get('symsides') or [])} sym_sides"
    )
    L.append("")
    L.append("## Observation (measured, not guessed)")
    L.append("")
    for e in (p.get("evidence") or [])[:5]:
        L.append(
            f"- [{e.get('src')}] {e.get('symside') or e.get('switch') or ''} :: {json.dumps({k: v for k, v in e.items() if k not in ('src', 'file')}, default=str)[:400]}"
        )
    L.append("")
    if p["kind"] == "ROW_ONLY":
        sw = ev0.get("switch", "")
        s = ((bible or {}).get("switches") or {}).get(sw, {})
        tab = ev0.get("home_tab") or "ENTRY_REVERSAL_BOUNCE"
        typ = s.get("type", "bool")
        cands = "True,False" if typ == "bool" else "OFF,15m,1h,4h,D"
        L.append("## Task (no engine work — rows only)")
        L.append("")
        L.append(
            f"1. Confirm `{sw}` is wired (SWITCH_BIBLE status + live/vec reads below)."
        )
        L.append(
            f"2. Queue the rows in `data/template_row_queue.json` (switch {sw}, tab {tab}, candidates {cands}, default = config default); S1 chain step 2a (`tools/v15_template_row_intake.py`) installs them through `tools/v15_switch_add.py` on the template source host BEFORE the Mac pull, so they can never be overwritten."
        )
        L.append(
            "3. `python3 tools/build_switch_bible.py && python3 tools/verify_switch_bible.py` green for the switch; ledger-flip proof; pilot smoke."
        )
        L.append("")
        L.append(f"Live reads: {str(ev0.get('live_reads'))[:300]}")
        L.append(f"Vec reads: {str(ev0.get('vec_reads'))[:300]}")
    elif p["kind"] == "FORMULA_FIX":
        L.append("## Task (diagnose the costly lever, fix the formula or retire it)")
        L.append("")
        L.append(
            "1. Reproduce: evaluate the lever vs a base on one quoted sym_side; confirm costly-but-moving."
        )
        L.append(
            "2. Trace WHY it costs (wrong TF? inverted gate? double-counted exit?) in the live function first."
        )
        L.append(
            "3. Fix the formula (locked files need unlock) or, if the lever is structurally lossy, retire the proposal with evidence (no code deletion)."
        )
        L.append(
            "4. Proof: re-screen shows cost gone (or retirement note with numbers)."
        )
    elif p["kind"].startswith("AUTOPSY_"):
        L.append(
            f"## Task ({DIAG} autopsy rule — engine verification required before anything else)"
        )
        L.append("")
        L.append(
            "1. Re-measure the rule on real autopsy bases with v12_quick_engine evaluate_prepared_sanitized (tools/v15_switch_flip_study.py path); report d_gain/d_tim/d_dd/d_wr per cat_side."
        )
        L.append(
            "2. If it holds out-of-sample (365D), extend the existing family (AUTOPSY_FAMILY_EXTEND: new TF/threshold candidates) or wire a new §71 switch (AUTOPSY_NEW_RULE). Hold rules must NEVER suppress FIXED_STOP / DC loss exits / hard stops."
        )
        L.append("3. Locked engine/config files need the user's unlock first.")
    else:
        fam = (p.get("sig") or ["", "", "GENERIC"])[2]
        L.append("## Task (full §71 new switch — Muse intelligence required)")
        L.append("")
        L.append(
            f"1. NAME the switch per convention (family {fam}, direction {p.get('direction') or 'both'}; confirm-or-rename, never collide)."
        )
        L.append(
            "2. Wire code FIRST (BACKTEST_BIBLE §71 step 1): venue config field + QuickConfig field (+apply_tradier_defaults for stocks) + live function on process_position() path + vec_decisions/ predicate + SINGLE call site in v12_quick_engine.py + cat_side_defaults_4 rebuild + behavioral test. Locked files need user unlock first."
        )
        L.append(
            "3. Registry + index: data/wiring/vec_function_registry.json entry, then build_switch_bible.py + verify_switch_bible.py green."
        )
        L.append(
            "4. Rows: queue in data/template_row_queue.json (S1 chain step 2a -> tools/v15_template_row_intake.py -> v15_switch_add.py on S1); never write Mac template copies (the Mac pull overwrites them)."
        )
        L.append(
            "5. Proofs: backtest_v12_engine ledger-flip (flipped vs default) saved to data/invention/proofs/<id>.json {switch, symside, npz, gain_before, gain_after, valid}; pilot smoke; bible green; --fleet sync + md5."
        )
        L.append("")
        L.append("## Nearest wired anchors (start reading here)")
        L.append("")
        for _s, name, s, tabs in neighbors_for(
            bible, p["kind"], p.get("direction", ""), fam
        ):
            lr = (s.get("live_reads") or {}).get("crypto", [])[:2]
            vr = (s.get("vec_reads") or [])[:2]
            L.append(
                f"- `{name}` [{s.get('kind')}/{s.get('type')}] tabs {tabs} :: live {lr} :: vec {vr}"
            )
    L.append("")
    L.append("## Acceptance (all must hold)")
    L.append("")
    L.append(
        "- Every number above reproduced from the quoted evidence before changing behavior."
    )
    L.append(
        "- `v15_invention_loop.py ingest --proposal <id> --switch <NAME>` reports WIRED; with --proof, TESTED."
    )
    L.append("- Committed test covering the new behavior; black-clean; backup taken.")
    return "\n".join(L)


def spec_proposal(data_dir, bible, pid):
    d = load_backlog(data_dir)
    p = d["proposals"].get(pid)
    if p is None:
        return None, f"unknown proposal {pid}"
    if p["status"] not in ("OBSERVED", "SPECD"):
        return None, f"{pid} already {p['status']}"
    text = render_order(p, bible)
    out = os.path.join(data_dir, "data", "invention", "orders", f"{pid}.md")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    Path(out).write_text(text)
    p["spec_path"] = out
    p["history"].append(
        {"ts": utcnow(), "from": p["status"], "to": "SPECD", "note": out}
    )
    p["status"] = "SPECD"
    save_backlog(data_dir, d)
    return out, "ok"


def ingest(data_dir, bible, pid, switch=None, proof=None):
    d = load_backlog(data_dir)
    p = d["proposals"].get(pid)
    if p is None:
        return False, f"unknown proposal {pid}"
    msgs = []
    if switch:
        s = ((bible or {}).get("switches") or {}).get(switch)
        if s is None:
            return (
                False,
                f"{switch} not in SWITCH_BIBLE.json (wire code first, then rebuild the bible)",
            )
        wired = [v for v, st in (s.get("status") or {}).items() if "WIRED" in str(st)]
        incfg = s.get("in_config") or {}
        if not wired or not all(
            incfg.get(k) for k in ("QuickConfig", "config.py", "config_tradier.py")
        ):
            return (
                False,
                f"{switch} not fully wired: status={s.get('status')} in_config={incfg}",
            )
        p["switch"] = switch
        if p["status"] in ("OBSERVED", "SPECD"):
            p["history"].append(
                {"ts": utcnow(), "from": p["status"], "to": "WIRED", "note": switch}
            )
            p["status"] = "WIRED"
        msgs.append(f"WIRED {switch} ({','.join(wired)})")
    if proof:
        try:
            pr = json.load(open(proof))
        except Exception as e:
            return False, f"proof unreadable: {e}"
        gb, ga = pr.get("gain_before"), pr.get("gain_after")
        if gb is None or ga is None or abs(float(ga) - float(gb)) < 1e-9:
            return False, "proof shows no ledger flip (gain_before == gain_after)"
        if not pr.get("valid"):
            return False, "proof set invalid"
        if not pr.get("npz") or not pr.get("switch"):
            return False, "proof needs switch + npz ids"
        p["proof"] = proof
        if p["status"] in ("OBSERVED", "SPECD", "WIRED"):
            p["history"].append(
                {"ts": utcnow(), "from": p["status"], "to": "TESTED", "note": proof}
            )
            p["status"] = "TESTED"
        msgs.append(f"TESTED flip {gb:+.2f}->{ga:+.2f}")
    save_backlog(data_dir, d)
    return True, "; ".join(msgs) or "no-op"


def sweep_evidence(progress_dir, switch):
    """pos_sym + mean delta for SWITCH= rows across progress done rows (TESTED -> PROMOTED rule)."""
    per_sym = defaultdict(list)
    for f in glob.glob(os.path.join(progress_dir, "*_v14_progress.json")):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        ss = d.get("symside") or os.path.basename(f)
        for key, v in (d.get("done") or {}).items():
            if not isinstance(v, dict) or f":{switch}=" not in key:
                continue
            dl = v.get("naked_delta")
            if dl is None:
                dl = v.get("delta")
            if dl is not None:
                per_sym[ss].append(float(dl))
    pos = sum(1 for ds in per_sym.values() if any(x > 1e-9 for x in ds))
    alld = [x for ds in per_sym.values() for x in ds]
    return {
        "n_syms": len(per_sym),
        "pos_sym": pos,
        "mean_delta": (sum(alld) / len(alld)) if alld else 0.0,
    }


def sweep_evidence_multi(progress_dirs, switch):
    """Union over several progress dirs: per sym_side, any positive delta in any dir counts once."""
    per_pos, per_n, alld = set(), set(), []
    dirs = (
        [progress_dirs] if isinstance(progress_dirs, str) else list(progress_dirs or [])
    )
    for pd in _expand_dirs(dirs):
        for f in glob.glob(os.path.join(pd, "*_v14_progress.json")):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            ss = d.get("symside") or os.path.basename(f)
            for key, v in (d.get("done") or {}).items():
                if not isinstance(v, dict) or f":{switch}=" not in key:
                    continue
                dl = v.get("naked_delta")
                if dl is None:
                    dl = v.get("delta")
                if dl is None:
                    continue
                per_n.add(ss)
                alld.append(float(dl))
                if float(dl) > 1e-9:
                    per_pos.add(ss)
    return {
        "n_syms": len(per_n),
        "pos_sym": len(per_pos),
        "mean_delta": (sum(alld) / len(alld)) if alld else 0.0,
    }


def promote_check(data_dir, progress_dir, pos_min=3, mean_min=0.0, retire_n=10):
    d = load_backlog(data_dir)
    out = []
    for pid, p in d["proposals"].items():
        if p["status"] != "TESTED" or not p.get("switch"):
            continue
        ev = sweep_evidence_multi(progress_dir, p["switch"])
        p["sweep"] = ev
        if ev["pos_sym"] >= pos_min and ev["mean_delta"] > mean_min:
            p["history"].append(
                {
                    "ts": utcnow(),
                    "from": "TESTED",
                    "to": "PROMOTED",
                    "note": json.dumps(ev),
                }
            )
            p["status"] = "PROMOTED"
            out.append((pid, "PROMOTED", ev))
        elif ev["n_syms"] >= retire_n and ev["pos_sym"] == 0:
            p["history"].append(
                {
                    "ts": utcnow(),
                    "from": "TESTED",
                    "to": "RETIRED",
                    "note": json.dumps(ev),
                }
            )
            p["status"] = "RETIRED"
            out.append((pid, "RETIRED", ev))
    save_backlog(data_dir, d)
    return out


def report(data_dir):
    d = load_backlog(data_dir)
    ps = list(d["proposals"].values())
    by_status = defaultdict(int)
    for p in ps:
        by_status[p["status"]] += 1
    top = sorted(
        [p for p in ps if p["status"] in ("OBSERVED", "SPECD")],
        key=lambda p: (p.get("score", 0), p.get("n_evidence", 0)),
        reverse=True,
    )[:10]
    return {
        "updated": d.get("updated_utc"),
        "n": len(ps),
        "by_status": dict(by_status),
        "top": [
            (
                p["id"],
                p["status"],
                p["kind"],
                round(p.get("score", 0), 2),
                p["title"][:100],
            )
            for p in top
        ],
        "rows": {p["id"]: p.get("rows_status") for p in ps if p.get("rows_status")},
        "diagnostic_only": sum(1 for p in ps if p.get("diagnostic_only")),
    }


def _split(v):
    return [x.strip() for x in str(v or "").split(",") if x.strip()]


def default_dirs(dd):
    home = os.path.expanduser("~")
    return {
        "autopsy": _split(os.environ.get("V15_INVENT_AUTOPSY_DIRS"))
        or [
            os.path.join(home, "v15_autopsy_*"),
            os.path.join(dd, "data", "reports", "v15_autopsy_first"),
        ],
        "missed": _split(os.environ.get("V15_INVENT_MISSED_DIRS"))
        or [
            os.path.join(dd, "data", "reports", "v15_missed_trend"),
            os.path.join(home, "v15_missed_trend"),
        ],
        "progress": _split(os.environ.get("V15_INVENT_PROGRESS_DIRS"))
        or [os.path.join(dd, "data", "reports", "lifecycle_pilot")]
        + newest_run_progress(int(os.environ.get("V15_INVENT_PROGRESS_RUNS", "3"))),
    }


def newest_run_progress(n):
    home = os.path.expanduser("~")
    runs = [
        d
        for d in glob.glob(os.path.join(home, "v15_run*", "progress"))
        if os.path.isdir(d)
    ]
    runs.sort(key=lambda d: os.path.getmtime(d), reverse=True)
    return runs[: max(0, n)]


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="observation-to-tested-switch invention backlog (score = move x distinct sym_sides)"
    )
    ap.add_argument("--data-dir", default=str(ROOT))
    ap.add_argument(
        "--autopsy-dirs",
        default=None,
        help="comma list/globs (env V15_INVENT_AUTOPSY_DIRS; default ~/v15_autopsy_*)",
    )
    ap.add_argument(
        "--progress-dirs",
        default=None,
        help="comma list/globs (env V15_INVENT_PROGRESS_DIRS; default lifecycle_pilot + newest 3 ~/v15_run*/progress)",
    )
    ap.add_argument(
        "--missed-dirs",
        default=None,
        help="comma list/globs (env V15_INVENT_MISSED_DIRS; default data/reports/v15_missed_trend + ~/v15_missed_trend)",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("mine", help="scan observation sources into the backlog")
    p1.add_argument("--write", action="store_true")
    p1.add_argument("--progress-dir", default=None)
    p1.add_argument(
        "--min-syms",
        type=int,
        default=int(os.environ.get("V15_INVENT_MIN_SYMS", "3")),
        help="autopsy rule needs >= N distinct sym_sides",
    )
    p1.add_argument("--no-rows-check", action="store_true")
    p2 = sub.add_parser("spec", help="render a §71 Muse work order")
    p2.add_argument("pid")
    p3 = sub.add_parser("ingest", help="validate a wired/tested landing")
    p3.add_argument("--proposal", required=True)
    p3.add_argument("--switch", default=None)
    p3.add_argument("--proof", default=None)
    p4 = sub.add_parser(
        "promote-check", help="TESTED -> PROMOTED/RETIRED from sweep evidence"
    )
    p4.add_argument("--progress-dir", default=None)
    p4.add_argument("--pos-min", type=int, default=3)
    p4.add_argument("--retire-n", type=int, default=10)
    sub.add_parser(
        "rows-check",
        help="open the templates read-only and record real rows_status per WIRED proposal",
    )
    sub.add_parser("report", help="backlog counts + top opportunities")
    a = ap.parse_args(argv)
    dd = a.data_dir
    dflt = default_dirs(dd)
    autopsy_dirs = _split(a.autopsy_dirs) or dflt["autopsy"]
    missed_dirs = _split(a.missed_dirs) or dflt["missed"]
    progress_dirs = (
        ([a.progress_dir] if getattr(a, "progress_dir", None) else [])
        or _split(a.progress_dirs)
        or dflt["progress"]
    )
    bible = {}
    try:
        bible = json.load(open(os.path.join(dd, "data", "SWITCH_BIBLE.json")))
    except Exception:
        pass
    if a.cmd == "mine":
        obs = []
        obs += scan_missed_trend(_expand_dirs(missed_dirs))
        obs += scan_autopsy(autopsy_dirs, min_syms=a.min_syms)
        for pd in _expand_dirs(progress_dirs):
            obs += scan_gaps(sorted(glob.glob(os.path.join(pd, "*_v14_progress.json"))))
        obs += scan_gaps(
            sorted(
                glob.glob(
                    os.path.join(dd, "data", "reports", "v15_diag_repair", "*.json")
                )
            )
        )
        obs += scan_coverage(
            os.path.join(dd, "data", "reports", "switch_bible_verify_latest.json"),
            bible,
        )
        kinds = defaultdict(int)
        for o in obs:
            kinds[o["kind"]] += 1
        n_auto = sum(
            1 for o in obs if (o.get("evidence") or {}).get("src") == "autopsy"
        )
        print(
            f"[mine] sources autopsy={_expand_dirs(autopsy_dirs)[:6]}{'...' if len(_expand_dirs(autopsy_dirs)) > 6 else ''} progress={len(_expand_dirs(progress_dirs))} dirs missed={_expand_dirs(missed_dirs)}"
        )
        if not a.write:
            print(
                f"[mine] dry-run: {len(obs)} observations {dict(kinds)} autopsy_rules={n_auto} (use --write to persist)"
            )
            return 0
        d = load_backlog(dd)
        added, updated = upsert(d, obs)
        rows = {} if a.no_rows_check else rows_check(dd, d)
        save_backlog(dd, d)
        print(
            f"[mine] observations={len(obs)} {dict(kinds)} autopsy_rules={n_auto} added={added} updated={updated} proposals={len(d['proposals'])} rows={rows}"
        )
        return 0
    if a.cmd == "rows-check":
        d = load_backlog(dd)
        rows = rows_check(dd, d)
        save_backlog(dd, d)
        print(f"[rows-check] {rows}")
        return 0
    if a.cmd == "spec":
        out, msg = spec_proposal(dd, bible, a.pid)
        print(f"[spec] {msg}: {out}" if out else f"[spec] FAILED {msg}")
        return 0 if out else 1
    if a.cmd == "ingest":
        ok, msg = ingest(dd, bible, a.proposal, a.switch, a.proof)
        print(f"[ingest] {'ok' if ok else 'FAILED'} {msg}")
        return 0 if ok else 1
    if a.cmd == "promote-check":
        out = promote_check(
            dd,
            progress_dirs,
            a.pos_min,
            0.0,
            a.retire_n,
        )
        print(
            f"[promote-check] transitions={len(out)} progress_dirs={len(_expand_dirs(progress_dirs))}"
        )
        for pid, to, ev in out:
            print(f"  {pid} -> {to} {ev}")
        return 0
    r = report(dd)
    print(
        f"[report] n={r['n']} {r['by_status']} updated={r['updated']} diagnostic_only={r['diagnostic_only']} rows={r['rows']}"
    )
    for t in r["top"]:
        print(f"  {t[0]} {t[1]} {t[2]} score={t[3]} :: {t[4]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
