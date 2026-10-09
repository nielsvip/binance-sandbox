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


def scan_autopsy(dirs):
    obs = []
    for dd in dirs:
        for f in glob.glob(os.path.join(dd, "*_autopsy.json")):
            try:
                d = json.load(open(f))
            except Exception:
                continue
            mfs = d.get("missing_functions") or []
            ss = d.get("symside", "")
            direction = (
                "SHORT"
                if ss.upper().endswith("_SHORT")
                else ("LONG" if ss.upper().endswith("_LONG") else "")
            )
            for mf in mfs if isinstance(mfs, list) else []:
                name = mf.get("name") if isinstance(mf, dict) else str(mf)
                w = 1.0
                if isinstance(mf, dict):
                    for k in ("losers", "n_losers", "weight", "trades"):
                        if isinstance(mf.get(k), (int, float)):
                            w = max(w, float(mf[k]))
                obs.append(
                    {
                        "sig": ("NEW_SWITCH", direction, _slug(name)),
                        "kind": "NEW_SWITCH",
                        "title": f"autopsy missing: {str(name)[:120]}",
                        "symside": ss,
                        "direction": direction,
                        "score": w,
                        "evidence": {
                            "src": "autopsy",
                            "file": f,
                            "symside": ss,
                            "missing": mf,
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


def upsert(backlog, observations):
    by_sig = {}
    for pid, p in backlog["proposals"].items():
        by_sig[tuple(p.get("sig") or ())] = pid
    added = updated = 0
    for o in observations:
        sig = tuple(o["sig"])
        if sig in by_sig:
            p = backlog["proposals"][by_sig[sig]]
            if p["status"] in ("PROMOTED", "RETIRED"):
                continue
            p["evidence"].append(o["evidence"])
            p["evidence"] = p["evidence"][-20:]
            p["n_evidence"] = p.get("n_evidence", 1) + 1
            p["score"] = max(p.get("score", 0), o["score"])
            p["symsides"] = sorted(
                {e.get("symside") for e in p["evidence"] if e.get("symside")}
            )
            updated += 1
        else:
            backlog["counter"] += 1
            pid = f"INV-{backlog['counter']:04d}"
            by_sig[sig] = pid
            backlog["proposals"][pid] = {
                "id": pid,
                "sig": list(sig),
                "kind": o["kind"],
                "title": o["title"],
                "symside": o.get("symside", ""),
                "direction": o.get("direction", ""),
                "status": "OBSERVED",
                "score": o["score"],
                "evidence": [o["evidence"]],
                "n_evidence": 1,
                "symsides": [o["symside"]] if o.get("symside") else [],
                "spec_path": None,
                "switch": None,
                "history": [
                    {"ts": utcnow(), "from": None, "to": "OBSERVED", "note": "mined"}
                ],
            }
            added += 1
    return added, updated


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
            f"2. `python3 tools/v15_switch_add.py --switch {sw} --tab {tab} --candidates {cands} --default <config-default> --venues both --sides both` (dry-run), review, then `--apply` after user unlocks the template."
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
            "4. Rows: v15_switch_add.py dry-run, review, --apply (template unlock needed)."
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


def promote_check(data_dir, progress_dir, pos_min=3, mean_min=0.0, retire_n=10):
    d = load_backlog(data_dir)
    out = []
    for pid, p in d["proposals"].items():
        if p["status"] != "TESTED" or not p.get("switch"):
            continue
        ev = sweep_evidence(progress_dir, p["switch"])
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
    }


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="observation-to-tested-switch invention backlog"
    )
    ap.add_argument("--data-dir", default=str(ROOT))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p1 = sub.add_parser("mine", help="scan observation sources into the backlog")
    p1.add_argument("--write", action="store_true")
    p1.add_argument("--progress-dir", default=None)
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
    sub.add_parser("report", help="backlog counts + top opportunities")
    a = ap.parse_args(argv)
    dd = a.data_dir
    home = os.path.expanduser("~")
    bible = {}
    try:
        bible = json.load(open(os.path.join(dd, "data", "SWITCH_BIBLE.json")))
    except Exception:
        pass
    if a.cmd == "mine":
        obs = []
        obs += scan_missed_trend(
            [
                os.path.join(dd, "data", "reports", "v15_missed_trend"),
                os.path.join(home, "v15_missed_trend"),
            ]
        )
        obs += scan_autopsy(
            [
                os.path.join(home, "v15_autopsy_first"),
                os.path.join(dd, "data", "reports", "v15_autopsy_first"),
            ]
        )
        pdir = a.progress_dir or os.path.join(dd, "data", "reports", "lifecycle_pilot")
        obs += scan_gaps(sorted(glob.glob(os.path.join(pdir, "*_v14_progress.json"))))
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
        if not a.write:
            kinds = defaultdict(int)
            for o in obs:
                kinds[o["kind"]] += 1
            print(
                f"[mine] dry-run: {len(obs)} observations {dict(kinds)} (use --write to persist)"
            )
            return 0
        d = load_backlog(dd)
        added, updated = upsert(d, obs)
        save_backlog(dd, d)
        print(
            f"[mine] observations={len(obs)} added={added} updated={updated} proposals={len(d['proposals'])}"
        )
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
            a.progress_dir or os.path.join(dd, "data", "reports", "lifecycle_pilot"),
            a.pos_min,
            0.0,
            a.retire_n,
        )
        print(f"[promote-check] transitions={len(out)}")
        for pid, to, ev in out:
            print(f"  {pid} -> {to} {ev}")
        return 0
    r = report(dd)
    print(f"[report] n={r['n']} {r['by_status']} updated={r['updated']}")
    for t in r["top"]:
        print(f"  {t[0]} {t[1]} {t[2]} score={t[3]} :: {t[4]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
