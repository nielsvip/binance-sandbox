#!/usr/bin/env python3
"""Prove plots == qualified == traded == swept, for ang (crypto) and trb (stocks).

USER 2026-10-17: "verify that the winners and losers in the /plots directories are the
actual winners and losers ... that are qualified to trade (and updated constantly) in the
symbols_ang/trb_long_short json files and that ang and trb are actually live trading these
sym_sides with the latest settings".

Read-only. Six checks per venue, each either PASS or a named finding:

  PLOTS        every W/L/WR/LR rank slot in the plots dir is a qualified sym_side, and
               every qualified top-N symbol has a plot
  LISTS        symbols_{ang,trb}_{long,short}.json match the qualification artifact, hold
               no symbol on both sides, and respect the cap
  FRESH        the ranking loop is still writing (artifact + lists inside --max-age-h)
  SETTINGS     every traded sym_side has a per_sym live set, no older than --settings-age-h
  PRIORITY     tools/v15_ranking_priority.py actually sees these sym_sides (this is what
               the S1 fleet scheduler reads; it returned 0/0/0 before 2026-10-17)
  SCHEDULER    the sym_sides the scheduler measured most recently, oldest first

Usage:
  python3 tools/rankings_live_audit.py                     # both venues, text
  python3 tools/rankings_live_audit.py --venue crypto      # one venue
  python3 tools/rankings_live_audit.py --json              # machine-readable
Exit code 0 = no findings, 1 = findings, 2 = the audit itself could not run.
"""

import argparse
import datetime
import json
import os
import re
import sqlite3
import sys
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import rankings_qualify  # noqa: E402

VENUES = {
    "crypto": {
        "account": "ang",
        "long_file": "symbols_ang_long.json",
        "short_file": "symbols_ang_short.json",
        "plots_dir": "plots",
    },
    "stocks": {
        "account": "trb",
        "long_file": "symbols_trb_long.json",
        "short_file": "symbols_trb_short.json",
        "plots_dir": "plots_tradier",
    },
}
RANK_PLOT_RE = re.compile(r"^(WR|LR|W|L)(\d{2})_(.+)\.png$")
PER_SYM_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"


def _age_h(ts):
    try:
        return (time.time() - float(ts)) / 3600.0
    except (TypeError, ValueError):
        return None


def _mtime_age_h(path):
    try:
        return (time.time() - path.stat().st_mtime) / 3600.0
    except OSError:
        return None


def _read_list(path):
    try:
        data = json.loads(path.read_text())
        return [str(s).upper() for s in data] if isinstance(data, list) else []
    except Exception:
        return []


def _live_set_ages():
    """-> {SYM_SIDE: age_hours} from the per_sym store (the set live actually resolves)."""
    out = {}
    if not PER_SYM_DB.exists():
        return out
    try:
        con = sqlite3.connect("file:%s?mode=ro" % PER_SYM_DB, uri=True)
        rows = con.execute("select sym_side, updated_at from per_sym_active").fetchall()
        con.close()
    except Exception:
        return out
    now = time.time()
    for sym_side, updated_at in rows:
        stamp = None
        if isinstance(updated_at, str):
            try:
                parsed = datetime.datetime.fromisoformat(updated_at.replace("Z", "+00:00"))
                if parsed.tzinfo is None:
                    parsed = parsed.replace(tzinfo=datetime.timezone.utc)
                stamp = parsed.timestamp()
            except ValueError:
                stamp = None
        else:
            try:
                stamp = float(updated_at)
            except (TypeError, ValueError):
                stamp = None
        if stamp is not None:
            out[str(sym_side).upper()] = (now - stamp) / 3600.0
    return out


def _news_injected():
    """-> {SYM_SIDE} that ez_news_scanner currently holds as an active injection."""
    try:
        active = json.loads((ROOT / "data" / "news_injections.json").read_text()).get("active", [])
    except Exception:
        return set()
    return {"%s_%s" % (str(i.get("symbol", "")).upper(), str(i.get("side", "")).upper()) for i in active if isinstance(i, dict)}


def _plot_slots(plots_dir):
    """-> {prefix: {rank: symbol}} plus the full (prefix, rank, symbol) triples present."""
    slots = defaultdict(dict)
    triples = []
    if not plots_dir.is_dir():
        return slots, triples
    for entry in plots_dir.glob("*.png"):
        match = RANK_PLOT_RE.match(entry.name)
        if not match:
            continue
        prefix, rank, symbol = match.group(1), int(match.group(2)), match.group(3).upper()
        triples.append((prefix, rank, symbol, entry.stat().st_mtime))
        slots[prefix][rank] = symbol
    return slots, triples


def audit_venue(venue, args, ranking_sets):
    spec = VENUES[venue]
    report = {"venue": venue, "account": spec["account"], "findings": [], "stats": {}}

    def finding(check, detail):
        report["findings"].append({"check": check, "detail": detail})

    qualified = rankings_qualify.load_artifact(venue, root=ROOT)
    long_path, short_path = ROOT / spec["long_file"], ROOT / spec["short_file"]
    traded_long, traded_short = _read_list(long_path), _read_list(short_path)
    report["stats"]["traded"] = {"long": len(traded_long), "short": len(traded_short)}

    # --- FRESH -------------------------------------------------------------
    for label, path in (("long_list", long_path), ("short_list", short_path)):
        age = _mtime_age_h(path)
        report["stats"].setdefault("age_h", {})[label] = None if age is None else round(age, 2)
        if age is None:
            finding("FRESH", "%s missing: %s" % (label, path.name))
        elif age > args.max_age_h:
            finding("FRESH", "%s is %.1fh old (limit %.0fh) — the ranking loop is not writing it" % (label, age, args.max_age_h))
    if qualified is None:
        finding("FRESH", "data/rankings_qualified_%s.json absent — the qualification gate has not published yet" % venue)
    else:
        age = _age_h(qualified.get("generated_at"))
        report["stats"].setdefault("age_h", {})["qualified"] = None if age is None else round(age, 2)
        if age is not None and age > args.max_age_h:
            finding("FRESH", "qualification artifact is %.1fh old (limit %.0fh)" % (age, args.max_age_h))

    # --- LISTS -------------------------------------------------------------
    both = sorted(set(traded_long) & set(traded_short))
    if both:
        finding("LISTS", "%d symbol(s) qualified LONG *and* SHORT at once: %s" % (len(both), ", ".join(both)))
    for label, lst in (("long", traded_long), ("short", traded_short)):
        dupes = sorted({s for s in lst if lst.count(s) > 1})
        if dupes:
            finding("LISTS", "duplicate entries in %s list: %s" % (label, ", ".join(dupes)))
    if qualified is not None:
        q_long = rankings_qualify.symbols(qualified, "long")
        q_short = rankings_qualify.symbols(qualified, "short")
        injected = _news_injected()
        report["stats"]["news_injected"] = {}
        for label, traded, qual in (("long", traded_long, q_long), ("short", traded_short, q_short)):
            extra = [s for s in traded if s not in qual]
            # ez_news_scanner legitimately appends conviction picks between ranking cycles. Those are
            # recorded in data/news_injections.json; anything else on the list is unexplained drift.
            news = [s for s in extra if "%s_%s" % (s, label.upper()) in injected]
            drift = [s for s in extra if s not in news]
            report["stats"]["news_injected"][label] = len(news)
            missing = [s for s in qual if s not in traded]
            if drift:
                finding("LISTS", "%s list holds %d symbol(s) the gate did not qualify and news did not inject: %s" % (label, len(drift), ", ".join(drift[:12])))
            if missing:
                finding("LISTS", "%s list is missing %d qualified symbol(s): %s" % (label, len(missing), ", ".join(missing[:12])))
        cap = qualified.get("cap")
        if cap:
            for label, traded in (("long", traded_long), ("short", traded_short)):
                allowance = cap + report["stats"]["news_injected"].get(label, 0)
                if len(traded) > allowance:
                    finding("LISTS", "%s list holds %d symbols over the cap of %d (+%d news-injected)" % (label, len(traded), cap, allowance - cap))

    # --- PLOTS -------------------------------------------------------------
    plots_dir = ROOT / spec["plots_dir"]
    slots, triples = _plot_slots(plots_dir)
    report["stats"]["plot_files"] = len(triples)
    qualified_pairs = {("W", s) for s in traded_long} | {("WR", s) for s in traded_long}
    qualified_pairs |= {("L", s) for s in traded_short} | {("LR", s) for s in traded_short}
    if not triples:
        finding("PLOTS", "no W/L/WR/LR rank plots in %s/ — nothing to verify" % spec["plots_dir"])
    else:
        unqualified = sorted({"%s%02d_%s" % (p, r, s) for p, r, s, _ in triples if (p, s) not in qualified_pairs})
        if unqualified:
            finding("PLOTS", "%d plot(s) are NOT in the traded lists: %s" % (len(unqualified), ", ".join(unqualified[:15])))
        # A symbol holding more than one rank slot under the same prefix = a stale cycle left behind.
        per_prefix = defaultdict(lambda: defaultdict(list))
        for prefix, rank, symbol, _ in triples:
            per_prefix[prefix][symbol].append(rank)
        ghosts = ["%s:%s@%s" % (p, s, "/".join(str(r) for r in sorted(rs))) for p, syms in per_prefix.items() for s, rs in syms.items() if len(rs) > 1]
        if ghosts:
            finding("PLOTS", "%d symbol(s) occupy several rank slots at once (superseded plots never pruned): %s" % (len(ghosts), ", ".join(sorted(ghosts)[:12])))
        top_n = (qualified or {}).get("plot_n") or args.plot_n
        for prefix, source in (("W", traded_long), ("L", traded_short)):
            drawn = set(slots.get(prefix, {}).values())
            undrawn = [s for s in source[:top_n] if s not in drawn]
            if undrawn:
                finding("PLOTS", "%d of the top %d %s symbols have no %s plot: %s" % (len(undrawn), top_n, "winner" if prefix == "W" else "loser", prefix, ", ".join(undrawn[:12])))
        stalest = max((m for _, _, _, m in triples), default=0)
        oldest = min((m for _, _, _, m in triples), default=0)
        report["stats"]["plot_age_h"] = {"newest": round((time.time() - stalest) / 3600.0, 2), "oldest": round((time.time() - oldest) / 3600.0, 2)}

    # --- SETTINGS ----------------------------------------------------------
    ages = _live_set_ages()
    traded_sides = ["%s_LONG" % s for s in traded_long] + ["%s_SHORT" % s for s in traded_short]
    no_set = [ss for ss in traded_sides if ss not in ages]
    stale = sorted(((ages[ss], ss) for ss in traded_sides if ss in ages and ages[ss] > args.settings_age_h), reverse=True)
    report["stats"]["settings"] = {"traded_sides": len(traded_sides), "no_live_set": len(no_set), "stale": len(stale)}
    if not ages:
        finding("SETTINGS", "per_sym store unreadable at %s — cannot prove live settings" % PER_SYM_DB)
    if no_set:
        finding("SETTINGS", "%d traded sym_side(s) have NO live set at all: %s" % (len(no_set), ", ".join(no_set[:15])))
    if stale:
        finding("SETTINGS", "%d traded sym_side(s) are older than %.0fh (user wants new settings daily): %s" % (len(stale), args.settings_age_h, ", ".join("%s %.0fh" % (ss, age) for age, ss in stale[:15])))

    # --- PRIORITY ----------------------------------------------------------
    seen = ranking_sets["long"] | ranking_sets["short"]
    unseen = [ss for ss in traded_sides if ss not in seen]
    report["stats"]["priority"] = {"source": ranking_sets.get("source"), "seen": len(traded_sides) - len(unseen), "of": len(traded_sides)}
    if ranking_sets.get("source") in ("none", "disabled"):
        finding("PRIORITY", "v15_ranking_priority resolves no source (%s) — the fleet scheduler prioritises nothing" % ranking_sets.get("source"))
    elif len(unseen) == len(traded_sides) and traded_sides:
        finding("PRIORITY", "the scheduler sees none of this venue's %d traded sym_sides" % len(traded_sides))

    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--venue", choices=sorted(VENUES), action="append", help="default: both")
    parser.add_argument("--max-age-h", type=float, default=float(os.environ.get("RANKINGS_AUDIT_MAX_AGE_H", 6.0)), help="list/artifact freshness limit (default 6)")
    parser.add_argument("--settings-age-h", type=float, default=float(os.environ.get("RANKINGS_AUDIT_SETTINGS_AGE_H", 24.0)), help="per_sym live-set age limit (default 24)")
    parser.add_argument("--plot-n", type=int, default=20, help="how many ranks per side are expected to be plotted")
    parser.add_argument("--json", action="store_true", help="emit the report as JSON")
    args = parser.parse_args()

    try:
        import v15_ranking_priority

        ranking_sets = v15_ranking_priority.ranking_sets()
    except Exception as exc:
        print("FATAL: cannot load v15_ranking_priority: %s" % exc, file=sys.stderr)
        return 2

    reports = [audit_venue(v, args, ranking_sets) for v in (args.venue or sorted(VENUES))]
    if args.json:
        print(json.dumps({"generated_at": time.time(), "priority_source": ranking_sets.get("source"), "reports": reports}, indent=1))
        return 1 if any(r["findings"] for r in reports) else 0

    print("RANKINGS LIVE AUDIT  %s" % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%MZ"))
    print("scheduler priority source: %s (%d long / %d short / %d pairs)" % (ranking_sets.get("source"), len(ranking_sets["long"]), len(ranking_sets["short"]), len(ranking_sets["pairs"])))
    for report in reports:
        stats = report["stats"]
        print("\n=== %s (%s) — %d long / %d short traded, %d plot files" % (report["venue"].upper(), report["account"], stats["traded"]["long"], stats["traded"]["short"], stats.get("plot_files", 0)))
        print("    ages(h): %s" % json.dumps(stats.get("age_h", {})))
        print("    settings: %d sides, %d without a live set, %d stale" % (stats["settings"]["traded_sides"], stats["settings"]["no_live_set"], stats["settings"]["stale"]))
        print("    priority: %d/%d sides visible to the scheduler" % (stats["priority"]["seen"], stats["priority"]["of"]))
        if not report["findings"]:
            print("    PASS — plots, lists, freshness, settings and priority all agree")
        for item in report["findings"]:
            print("    %-9s %s" % (item["check"], item["detail"]))
    total = sum(len(r["findings"]) for r in reports)
    print("\n%d finding(s)" % total)
    return 1 if total else 0


if __name__ == "__main__":
    sys.exit(main())
