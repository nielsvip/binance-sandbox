"""v20_switch_absorb — automate switch absorption into TEMPLATE_* (and stage the v12_quick_engine side).

USER 2026-10-27: "can you automate switch absorption into TEMPLATE_ and v12_quick_engine for the pending paths somehow?"

The V20 heads can only learn about a switch that has a TEMPLATE row, because a row is what produces the
(switch=cand, filter=opt) delta cells the heads train on. tools/v20_domain_census.py measured the pending paths:
~2,360 live behaviour switches per cat_side have no row at all. This tool turns that census into queued rows
without a human typing one --switch at a time, and it refuses every switch whose row would measure nothing.

WHAT IT NEVER DOES
  * It never writes a TEMPLATE_*.xlsx. Every row goes through tools/v15_switch_add.py --enqueue, the one sanctioned
    adder (BIBLE §71/§94 persistent row intake): S1 owns data/template_row_queue.json and the chain installs from it,
    so a row written only on the Mac can no longer be lost to a template pull.
  * It never invents a candidate value. Options come from the registry's valid_options, or — for a bool and only for
    a bool — from [default, not default], which is the complete option set, not a guess. A numeric switch with no
    declared ladder is REPORTED, never laddered: a made-up 0.5/1.0/2.0 is exactly the fabricated delta BIBLE §14 bans.
  * It never edits v12_quick_engine.py. The engine side is emitted as a reviewable patch (--emit-engine-patch),
    because the Mac tree is mirrored to S1 every 120 s (see memory mac_livecode_push_is_a_live_deploy_20261022):
    an auto-edit to the engine is a live deploy.

THE ONE GATE THAT MATTERS (stricter than the adder's)
  A row is worth installing only if the VECTOR engine reads the switch. 80-94% of existing BINDS cells already
  measure a no-op; installing rows for switches v12_quick_engine never reads would make that worse while looking
  like progress. So a proposal needs real entries in the bible's vec_reads (a non-empty list, not a truthy
  count dict -- v15_switch_add accepts `vec_read_count` {'reachable':0,'total':0}, which is zero reads).

TIERS (written to the report)
  READY_REGISTRY_OPTIONS  vec+live+config+QuickConfig+home tab+>=2 valid_options  -> enqueued
  READY_BOOL_DERIVED      as above but options derived [default, not default]      -> enqueued
  NEEDS_OPTION_LADDER     fully wired, non-bool, no declared option set            -> worklist (operator picks values)
  NEEDS_ENGINE_WIRING     live-read but ZERO vec reads                             -> worklist (the V21 domain blocker)
  NEEDS_QUICKCONFIG       venue config declares it, QuickConfig does not           -> worklist + optional patch
  NOT_A_CANDIDATE         no live reads / dead / no bible entry

Usage:
  python3 tools/v20_switch_absorb.py                      # dry run: validate every proposal, write the report
  python3 tools/v20_switch_absorb.py --apply --push s1-pub  # enqueue + rsync the inbox entries to the source host
  python3 tools/v20_switch_absorb.py --census             # refresh data/learn/v20_domain_gap_*.json first
  python3 tools/v20_switch_absorb.py --emit-engine-patch OUT.diff
"""
import argparse
import ast
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
# The bible's own `kind` classification -> home tab, used ONLY when the registry suggested no tab. A bool gate that
# blocks an entry is a confirmation gate, not a reversal/breakout trigger, so `entry` lands on the gates tab; the
# pilot's greedy chain reaches it either way. 'unclassified' is refused rather than guessed.
KIND_TAB = {"entry": "ENTRY_CONFIRMATION_GATES", "exit": "EXIT_STRUCTURAL", "reentry": "REENTRY_WINDOWED", "augment": "AUGMENT_TREND", "reduce": "REDUCE_PROFIT_LOCK", "sizing": "AUGMENT_RISK_SIZING", "global": "GLOBAL_RISK_GATES"}
LEDGER = ROOT / "data" / "reports" / "v20_switch_absorb.jsonl"


def now():
    return datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


def vec_read_sites(entry):
    """Real vector read sites. A count dict of zeros is NOT a read."""
    vr = entry.get("vec_reads")
    if isinstance(vr, list):
        return [str(x) for x in vr]
    if isinstance(vr, dict):
        return [str(x) for v in vr.values() for x in (v if isinstance(v, list) else [v])]
    return []


def live_read_total(entry):
    return sum(int(v or 0) for v in (entry.get("live_read_count") or {}).values())


def reachable_vec_sites(entry):
    """Sites the bible did not mark [UNREACHABLE] — an unreachable read cannot move a delta."""
    return [s for s in vec_read_sites(entry) if "UNREACHABLE" not in s]


def venue_defaults(entry):
    d = entry.get("defaults") or {}
    out = {}
    for venue, key in (("CRYPTO", "config.py"), ("STOCKS", "config_tradier.py")):
        v = d.get(key)
        if v is None or v == "<absent>":
            continue
        out[venue] = v
    return out


def fmt_opt(v):
    if isinstance(v, bool):
        return "True" if v else "False"
    if isinstance(v, float) and v.is_integer():
        return str(int(v)) if abs(v) < 1e15 else repr(v)
    return str(v)


def load_gap():
    gap, missing = {}, []
    for cs in CAT_SIDES:
        p = ROOT / "data" / "learn" / f"v20_domain_gap_{cs}.json"
        if not p.exists():
            missing.append(str(p))
            continue
        d = json.loads(p.read_text())
        if d.get("meta", {}).get("contract") != "v20_domain_gap_v1":
            sys.exit(f"REFUSED: {p} is not contract v20_domain_gap_v1")
        gap[cs] = {g["switch"]: g for g in d["gap"]}  # name -> record; membership means "this cat_side has NO row"
    if missing:
        sys.exit(f"REFUSED: run tools/v20_domain_census.py first (missing {missing})")
    return gap


def already_queued():
    """Switch names that are already queued or sitting in the inbox — never enqueue a duplicate."""
    seen = set()
    q = ROOT / "data" / "template_row_queue.json"
    if q.exists():
        try:
            d = json.loads(q.read_text())
            for e in (d.get("entries") if isinstance(d, dict) else d) or []:
                if isinstance(e, dict) and e.get("switch"):
                    seen.add(str(e["switch"]).upper())
        except Exception:
            pass
    inbox = ROOT / "data" / "template_row_queue_inbox"
    if inbox.is_dir():
        for f in inbox.glob("enqueue_*.json"):
            try:
                for e in (json.loads(f.read_text()).get("entries") or []):
                    if e.get("switch"):
                        seen.add(str(e["switch"]).upper())
            except Exception:
                pass
    return seen


def missing_cat_sides(name, gap, dflts):
    """The cat_sides whose TEMPLATE actually lacks a row for this switch, intersected with the venues whose config
    declares it.

    The first version unioned the four gaps and then queued every switch with --venues both --sides both. A switch
    missing only from STOCKS_SHORT was therefore queued for all four workbooks, where three already held it: the
    2026-10-27 dry run came back `already_present: 293` of 328. A row must be installed exactly where it is absent.
    """
    out = []
    for cat_side, rows in gap.items():
        if name not in rows:
            continue
        venue = "CRYPTO" if cat_side.startswith("CRYPTO") else "STOCKS"
        if venue in dflts:
            out.append(cat_side)
    return out


def venue_side_calls(cat_sides):
    """Group cat_sides into the fewest --venues/--sides calls the adder can express."""
    venues = {c.split("_")[0] for c in cat_sides}
    sides = {c.split("_", 1)[1] for c in cat_sides}
    if len(venues) * len(sides) == len(cat_sides):
        return [("both" if len(venues) == 2 else next(iter(venues)).lower(),
                 "both" if len(sides) == 2 else next(iter(sides)).lower())]
    return [(c.split("_")[0].lower(), c.split("_", 1)[1].lower()) for c in cat_sides]


def propose(name, entry, gap):
    """-> (tier, proposal|None, reason). The strict local gates; v15_switch_add has the last word."""
    if str(entry.get("status") or "").upper().startswith("DEAD"):
        return "NOT_A_CANDIDATE", None, f"bible status {entry.get('status')}"
    ic = entry.get("in_config") or {}
    if live_read_total(entry) <= 0:
        return "NOT_A_CANDIDATE", None, "no live reads — not a live behaviour switch"
    if not (ic.get("config.py") or ic.get("config_tradier.py")):
        return "NOT_A_CANDIDATE", None, "no venue config field"
    if not ic.get("QuickConfig"):
        return "NEEDS_QUICKCONFIG", None, "venue config declares it, QuickConfig does not — the engine cannot receive an override"
    sites = reachable_vec_sites(entry)
    if not sites:
        n_un = len(vec_read_sites(entry))
        return "NEEDS_ENGINE_WIRING", None, f"zero reachable vector reads ({n_un} unreachable) — a row here would measure a no-op"
    dflts = venue_defaults(entry)
    if not dflts:
        return "NOT_A_CANDIDATE", None, "no scalar venue config default"
    for v in dflts.values():
        if isinstance(v, (list, dict, tuple)) or v is None:
            return "NOT_A_CANDIDATE", None, f"config default {v!r} is not a scalar"
    vals = {fmt_opt(v) for v in dflts.values()}
    if len(vals) > 1:
        return "NOT_A_CANDIDATE", None, f"venue defaults disagree {dflts!r} — align config first"
    default = fmt_opt(list(dflts.values())[0])
    reg = entry.get("agent_c_registry") or {}
    tab = reg.get("suggested_home_tab") or KIND_TAB.get(str(entry.get("kind") or "").lower())
    if not tab:
        return "NEEDS_OPTION_LADDER", None, f"no home tab (registry silent, bible kind {entry.get('kind')!r} does not map)"
    if tab not in SWITCH_SHEETS:
        return "NOT_A_CANDIDATE", None, f"derived tab {tab} is not one of the 13 SWITCH_SHEETS"
    opts = [fmt_opt(o) for o in (reg.get("valid_options") or [])]
    opts = list(dict.fromkeys(opts))
    tier = "READY_REGISTRY_OPTIONS"
    if len(opts) < 2:
        if str(entry.get("type") or "").lower() != "bool":
            return "NEEDS_OPTION_LADDER", None, f"type {entry.get('type')!r} with no declared option set — a numeric ladder would be invented"
        opts = ["True", "False"]
        tier = "READY_BOOL_DERIVED"
    if default not in opts:
        opts = [default] + [o for o in opts if o != default]
    cat_sides = missing_cat_sides(name, gap, dflts)
    if not cat_sides:
        return "ALREADY_HAS_ROWS", None, "every cat_side whose config declares it already has a row"
    return tier, {"switch": name, "tab": tab, "candidates": opts, "default": default,
                  "cat_sides": cat_sides, "calls": venue_side_calls(cat_sides),
                  "vec_sites": sites[:3], "live_reads": live_read_total(entry)}, ""


_BAD_CAND = re.compile(r"candidate '([^']+)' is not")


def run_adder(p, inbox, push=None):
    """Validate + enqueue through the ONE sanctioned adder. Dry runs use a scratch inbox, so validation is identical.

    The registry proposes a handful of values its own field type refuses (17.5 on an int K_ZONE threshold). When the
    validator names such a candidate, drop THAT candidate and retry: discarding an option the one validator refuses
    is not the same as inventing one, and the remaining options still measure a real delta. The default is never dropped,
    and a switch that falls below two surviving options is reported, not installed."""
    cands = list(p["candidates"])
    failed, done = [], []
    for venue, side in p.get("calls") or [("both", "both")]:
        out, pair_ok = "", False
        for _ in range(4):
            cmd = [sys.executable, str(ROOT / "tools" / "v15_switch_add.py"), "--enqueue", "--switch", p["switch"], "--tab", p["tab"], "--candidates", ",".join(cands), "--default", p["default"], "--inbox", str(inbox), "--venues", venue, "--sides", side]
            if push:
                cmd += ["--push", push]
            r = subprocess.run(cmd, capture_output=True, text=True, cwd=str(ROOT))
            out = ((r.stdout or "") + (r.stderr or "")).strip()
            if r.returncode == 0:
                pair_ok = True
                break
            m = _BAD_CAND.search(out)
            if not m or m.group(1) == p["default"]:
                break
            nxt = [c for c in cands if c != m.group(1)]
            if len(nxt) < 2 or p["default"] not in nxt or nxt == cands:
                break
            cands = nxt
        if pair_ok:
            done.append(f"{venue}/{side}")
        else:
            failed.append(f"{venue}/{side}: {out[-300:]}")
    if done:
        p["candidates"] = cands
    if failed:
        return False, f"FAILED pairs {'; '.join(failed)} | ok pairs {done or 'none'}"
    return True, f"ok pairs {done}"


def quickconfig_patch(names, entries):
    """A reviewable unified diff that declares the missing fields on QuickConfig, mirroring the venue default.
    NOT applied: the Mac tree is a live deploy. A declaration alone makes the field overridable; the engine still
    has to read it before a row can measure anything."""
    src = (ROOT / "v12_quick_engine.py").read_text().splitlines()
    tree = ast.parse("\n".join(src))
    qc = next((n for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "QuickConfig"), None)
    if qc is None:
        return None, "QuickConfig class not found"
    last = max((n.end_lineno or n.lineno) for n in qc.body)
    adds = []
    for n in sorted(names):
        d = venue_defaults(entries[n])
        if not d:
            continue
        v = list(d.values())[0]
        ann = "bool" if isinstance(v, bool) else ("int" if isinstance(v, int) else ("float" if isinstance(v, float) else "str"))
        adds.append(f"    {n}: {ann} = {v!r}")
    if not adds:
        return None, "nothing to add"
    body = [f"--- a/v12_quick_engine.py", f"+++ b/v12_quick_engine.py", f"@@ -{last},0 +{last + 1},{len(adds)} @@"]
    body += [f"+{a}" for a in adds]
    return "\n".join(body) + "\n", f"{len(adds)} field(s) after line {last}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="enqueue into data/template_row_queue_inbox (default: validate only)")
    ap.add_argument("--push", help="with --apply: rsync each inbox entry to HOST:binance-sandbox/data/template_row_queue_inbox/ (the source host owns the queue)")
    ap.add_argument("--census", action="store_true", help="refresh data/learn/v20_domain_gap_*.json first")
    ap.add_argument("--limit", type=int, default=0, help="stop after N enqueued rows (0 = no limit)")
    ap.add_argument("--only", help="comma-separated switch names, for a single-switch check")
    ap.add_argument("--report", default=str(ROOT / "data" / "learn" / "v20_switch_absorb_latest.json"))
    ap.add_argument("--emit-engine-patch", help="write the QuickConfig declaration patch here (never applied)")
    a = ap.parse_args()

    if a.census:
        r = subprocess.run([sys.executable, str(ROOT / "tools" / "v20_domain_census.py")], cwd=str(ROOT))
        if r.returncode != 0:
            sys.exit("REFUSED: census failed")

    bible = json.loads((ROOT / "data" / "SWITCH_BIBLE.json").read_text())["switches"]
    gap = load_gap()
    union = sorted(set().union(*[set(g) for g in gap.values()]))
    if a.only:
        want = {s.strip().upper() for s in a.only.split(",")}
        union = [s for s in union if s in want]
    skip = already_queued()

    tiers, proposals, refused = {}, [], []
    for name in union:
        entry = bible.get(name)
        if not entry:
            tiers.setdefault("NOT_A_CANDIDATE", []).append({"switch": name, "reason": "no SWITCH_BIBLE entry — rebuild the bible first"})
            continue
        tier, prop, reason = propose(name, entry, gap)
        if prop and name in skip:
            tier, prop, reason = "ALREADY_QUEUED", None, "already in the row queue or inbox"
        rec = {"switch": name, "reason": reason}
        if prop:
            rec.update({"tab": prop["tab"], "candidates": prop["candidates"], "default": prop["default"], "cat_sides": prop["cat_sides"], "calls": prop["calls"], "vec_sites": prop["vec_sites"]})
            proposals.append((tier, prop))
        tiers.setdefault(tier, []).append(rec)

    inbox_real = ROOT / "data" / "template_row_queue_inbox"
    tmp = tempfile.mkdtemp(prefix="v20_absorb_dry_")
    enqueued, n = [], 0
    for tier, p in proposals:
        if a.limit and n >= a.limit:
            refused.append({"switch": p["switch"], "why": "limit reached"})
            continue
        ok, out = run_adder(p, inbox_real if a.apply else tmp, a.push if a.apply else None)
        if ok:
            enqueued.append({"switch": p["switch"], "tier": tier, "tab": p["tab"], "candidates": p["candidates"], "default": p["default"], "cat_sides": p["cat_sides"], "calls": p["calls"]})
            n += 1
        else:
            refused.append({"switch": p["switch"], "tier": tier, "tab": p["tab"], "why": out})

    engine_patch = None
    if a.emit_engine_patch:
        names = [r["switch"] for r in tiers.get("NEEDS_QUICKCONFIG", [])]
        diff, note = quickconfig_patch(names, bible)
        if diff:
            Path(a.emit_engine_patch).write_text(diff)
        engine_patch = {"path": a.emit_engine_patch if diff else None, "note": note, "switches": len(names)}

    report = {"contract": "v20_switch_absorb_v1", "ts": now(), "applied": bool(a.apply), "push": a.push, "gap_union": len(union),
              "counts": {k: len(v) for k, v in sorted(tiers.items())}, "enqueued": enqueued, "refused": refused,
              "engine_patch": engine_patch, "tiers": tiers}
    Path(a.report).write_text(json.dumps(report, indent=1, sort_keys=True))
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a") as fh:
        fh.write(json.dumps({"ts": now(), "applied": bool(a.apply), "gap_union": len(union), "counts": report["counts"], "enqueued": len(enqueued), "refused": len(refused)}) + "\n")

    print(f"[absorb] gap union {len(union)} switches")
    for k, v in sorted(report["counts"].items(), key=lambda x: -x[1]):
        print(f"[absorb]   {k:24s} {v}")
    print(f"[absorb] {'ENQUEUED' if a.apply else 'would enqueue'} {len(enqueued)}, refused by the adder {len(refused)}")
    for r in refused[:10]:
        print(f"[absorb]   REFUSED {r['switch']}: {r['why'][:160]}")
    print(f"[absorb] report {a.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
