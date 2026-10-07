#!/usr/bin/env python3
"""v15_orange_audit — the GATE instrument for retiring EMA_BLANKET (USER 2026-09-30).

The operator's directive: DESTROY EMA_BLANKET_FILTER only AFTER (1) every other orange-row filter is
correctly connected in crypto AND stocks, and (2) ALL orange rows are duly tested in EVERY run — with the
KINDERGARTEN filters (KINDERGARTEN EMA gate + EMA 9/21 + …) getting top priority on the ENTRY tabs, which
today ignore most filter tests. This script measures that gate objectively and re-runnably, so the
destruction step is only taken once the gate is verifiably green.

Method (all from data already produced — no new sweep needed):
  * Orange-row universe = rows with the orange fill (FFE699) in each TEMPLATE_{cat_side}.xlsx, per SWITCH
    tab. Orange rows are tested as their own `NAME=value` candidates, so a base name is TESTED this round
    iff it appears in SPREADSHEETS/v15_avg_delta_latest.xlsx for that cat_side with n>0.
  * CONNECTED (vec) = at least one value of the base moves gain (|avg_delta|>=NOOP). A tested-but-NOOP base
    is wired in name only — not correctly connected. (Authoritative crypto/stocks LIVE wiring still needs a
    4-surface code trace per memory switch_wiring_pattern_20260930; this flags the vec truth, which is a
    necessary condition and immune to the stub-farm grep trap.)
  * Per cat_side and with an ENTRY-tab focus; KINDERGARTEN / retiring(EMA_BLANKET) sets from
    data/kindergarten_filters.json.

Output: data/reports/v15_orange_audit_{YYYYMMDD}.md + a one-line GATE verdict (exit 0 = gate GREEN, safe to
retire EMA_BLANKET; exit 2 = gate RED). Read-only; never edits templates/config/live.
"""
import argparse
import datetime
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_morning_report as MR  # noqa: E402
from v15_template_restructure import is_orange  # noqa: E402
from v15_avg_delta_apply import SWITCH_SHEETS, is_grey  # noqa: E402

CAT_SIDES = MR.CAT_SIDES
TEMPLATES = {"CRYPTO_LONG": "TEMPLATE_CRYPTO_LONG.xlsx", "CRYPTO_SHORT": "TEMPLATE_CRYPTO_SHORT.xlsx",
             "STOCKS_LONG": "TEMPLATE_STOCKS_LONG.xlsx", "STOCKS_SHORT": "TEMPLATE_STOCKS_SHORT.xlsx"}
KG = json.loads((ROOT / "data" / "kindergarten_filters.json").read_text())
ENTRY_TABS = set(KG.get("entry_tabs") or [])
KG_BASES = set(KG.get("kindergarten_bases") or [])
RETIRING = set(KG.get("retiring_bases") or [])
NOOP = MR.NOOP_AVG


def orange_universe(cat_side):
    """{base_name: {"tabs": set, "entry": bool, "n_value_rows": int}} for orange rows in the template."""
    import openpyxl
    p = ROOT / "SPREADSHEETS" / TEMPLATES[cat_side]
    out = {}
    if not p.exists():
        return out
    wb = openpyxl.load_workbook(str(p), data_only=True)
    for tab in SWITCH_SHEETS:
        if tab not in wb.sheetnames:
            continue
        ws = wb[tab]
        for r in range(3, ws.max_row + 1):
            v = ws.cell(row=r, column=1).value
            if v in (None, "") or not is_orange(ws, r):
                continue
            base = str(v).split("=", 1)[0].strip()
            rec = out.setdefault(base, {"tabs": set(), "entry": False, "n_value_rows": 0, "n_grey": 0})
            rec["tabs"].add(tab)
            rec["n_value_rows"] += 1
            if is_grey(ws, r):
                rec["n_grey"] += 1
            if tab in ENTRY_TABS:
                rec["entry"] = True
    wb.close()
    # a base is "parked" (excluded from the gate) when every one of its value rows is greyed
    for rec in out.values():
        rec["greyed"] = rec["n_grey"] >= rec["n_value_rows"] and rec["n_value_rows"] > 0
    return out


def classify(cat_side, pertab):
    """Per orange base AND per tab it lives on: TESTED this run? CONNECTED (vec moves gain)? Returns
    {base: {"entry":bool, "n_value_rows":int, "tabs":{tab:{tested,connected,n,pos,max_abs}}}}.
    Per-tab is the point (USER 2026-09-30): a filter tested on EXIT does not count as tested on ENTRY."""
    uni = orange_universe(cat_side)
    # index per-tab stats by (tab, base)
    stats = {}
    for (tab, name), e in pertab[cat_side].items():
        stats.setdefault((tab, MR.base_of(name)), []).append(e)
    out = {}
    for base, rec in uni.items():
        tabrecs = {}
        for tab in rec["tabs"]:
            rows = stats.get((tab, base), [])
            n = sum(r["n"] for r in rows)
            mx = max((abs(r["avg"]) for r in rows), default=0.0)
            pos = sum(r["pos_sym"] for r in rows)
            tabrecs[tab] = {"tested": n > 0, "connected": n > 0 and mx >= NOOP, "n": n, "pos": pos, "max_abs": mx}
        out[base] = {"entry": rec["entry"], "n_value_rows": rec["n_value_rows"], "greyed": rec["greyed"],
                     "tabs": tabrecs,
                     "tested_anywhere": any(t["tested"] for t in tabrecs.values()),
                     "connected_anywhere": any(t["connected"] for t in tabrecs.values())}
    return out


def _cell_lists(recs):
    """Per-(base,tab) cells excluding RETIRING + greyed bases. Returns (untested, noop, entry_untested, total,
    disconnected_bases). untested/noop/entry_untested are lists of (base,tab); disconnected_bases = bases that are
    tested somewhere but NOOP on ALL tabs (the real 'not connected' wiring defect, vs. placement NOOP on one tab)."""
    untested, noop, entry_untested, disconnected, total = [], [], [], [], 0
    for base, r in recs.items():
        if base in RETIRING or r.get("greyed"):
            continue
        if r["tested_anywhere"] and not r["connected_anywhere"]:
            disconnected.append(base)
        for tab, t in r["tabs"].items():
            total += 1
            if not t["tested"]:
                untested.append((base, tab))
                if tab in ENTRY_TABS:
                    entry_untested.append((base, tab))
            elif not t["connected"]:
                noop.append((base, tab))
    return untested, noop, entry_untested, total, sorted(disconnected)


def build(strict_connected):
    day = datetime.date.today().strftime("%Y%m%d")
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%MZ")
    pertab = MR.load_avg_pertab(MR.AVG_LATEST)
    per_cs = {cs: classify(cs, pertab) for cs in CAT_SIDES}

    L = [f"# v15 Orange-Row Audit (per-tab) — {day}", "",
         f"_Generated {stamp}. GATE for retiring `EMA_BLANKET_FILTER` (USER 2026-09-30): destroy it only once "
         f"every OTHER orange-row filter is connected (vec-moves) AND tested **on every tab it lives on** this "
         f"run. Coverage is per (filter, tab) — a filter tested on EXIT does NOT count as tested on ENTRY. "
         f"Source: templates + per-tab `v15_avg_delta_latest.xlsx`. CONNECTED = vec moves gain ≥{NOOP}%; "
         f"crypto/stocks LIVE wiring still needs a per-filter 4-surface trace to count as fully connected._", ""]

    gate_ok = True
    cells = {}
    L.append("## Coverage by cat_side (per filter×tab cell)")
    L.append("")
    L.append("| cat_side | filter×tab cells | untested | ENTRY-tab untested | disconnected (NOOP on ALL tabs) | placement-NOOP (some tabs) |")
    L.append("|---|---|---|---|---|---|")
    for cs in CAT_SIDES:
        untested, noop, entry_untested, total, disconnected = _cell_lists(per_cs[cs])
        cells[cs] = (untested, noop, entry_untested, total, disconnected)
        if untested or disconnected:
            gate_ok = False
        L.append(f"| {cs} | {total} | {len(untested)} | {len(entry_untested)} | {len(disconnected)} | {len(noop)} |")
    L.append("")
    L.append("_'disconnected' = a filter that moves nothing on ANY of its tabs (real wiring defect). "
             "'placement-NOOP' = does nothing on some tab but works on another (informational, not a gate fail)._")
    L.append("")

    # KINDERGARTEN priority status ON ENTRY TABS specifically
    L.append("## KINDERGARTEN + EMA family — ENTRY-tab coverage (top priority)")
    L.append("")
    L.append("| base | set | entry tabs tested (cat_side:tabs) | entry tabs connected |")
    L.append("|---|---|---|---|")
    sh = lambda n: n.replace("CRYPTO_", "C_").replace("STOCKS_", "S_").replace("ENTRY_", "")

    def entry_cov(base, want_connected):
        got = []
        for cs in CAT_SIDES:
            r = per_cs[cs].get(base)
            if not r:
                continue
            tt = [t for t in r["tabs"] if t in ENTRY_TABS and (r["tabs"][t]["connected"] if want_connected else r["tabs"][t]["tested"])]
            if tt:
                got.append(sh(cs) + ":" + "/".join(sh(t) for t in sorted(tt)))
        return ", ".join(got) or "—"

    for base in sorted(KG_BASES | RETIRING):
        tag = "RETIRING" if base in RETIRING else "KG"
        # only show bases that are actually orange rows somewhere
        present = any(base in per_cs[cs] for cs in CAT_SIDES)
        note = "" if present else " _(not an orange row in any template — ADD it)_"
        L.append(f"| `{base}` | {tag} | {entry_cov(base, False)}{note} | {entry_cov(base, True)} |")
    L.append("")

    # KINDERGARTEN entry-tab requirement (USER 2026-09-30: KG top priority, tested+connected on entry tabs
    # EVERYWHERE — all 4 cat_sides, all 3 entry tabs, all TFs). Every gap keeps the gate RED.
    kg_gaps = []
    for base in sorted(KG_BASES):
        for cs in CAT_SIDES:
            r = per_cs[cs].get(base)
            for tab in sorted(ENTRY_TABS):
                ok = bool(r) and tab in r["tabs"] and r["tabs"][tab]["connected"]
                if not ok:
                    present = bool(r) and tab in (r or {}).get("tabs", {})
                    kg_gaps.append((base, cs, tab, "not-connected" if present else "ABSENT"))
    if kg_gaps:
        gate_ok = False
    L.append(f"## KINDERGARTEN entry-tab gaps (must be 0): **{len(kg_gaps)}**")
    L.append("")
    if kg_gaps:
        absent = [g for g in kg_gaps if g[3] == "ABSENT"]
        notconn = [g for g in kg_gaps if g[3] == "not-connected"]
        L.append(f"- **ABSENT (KG base not an orange row on that entry tab — ADD it): {len(absent)}** "
                 f"cells across {len({g[0] for g in absent})} bases × cat_sides × entry tabs.")
        L.append(f"- **present but NOT vec-connected: {len(notconn)}** — wire/verify.")
        miss_bases = sorted({g[0] for g in kg_gaps})
        L.append("- KG bases with gaps: " + ", ".join(f"`{b}`" for b in miss_bases))
    L.append("")

    # worklist
    L.append("## Fix worklist (must clear before EMA_BLANKET can be destroyed)")
    L.append("")
    for cs in CAT_SIDES:
        untested, noop, entry_untested, total, disconnected = cells[cs]
        if disconnected:
            L.append(f"### {cs} — disconnected bases (NOOP on ALL tabs): {', '.join('`'+b+'`' for b in disconnected)}")
        if not untested and not disconnected:
            L.append(f"### {cs} — ✅ every filter×tab cell tested & vec-connected")
            L.append("")
            continue
        L.append(f"### {cs}")
        if entry_untested:
            L.append(f"- **ENTRY-tab UNTESTED ({len(entry_untested)})** — highest priority (the entry-tab test gap "
                     f"the emergency blanket was patching):")
            for b, t in sorted(entry_untested):
                L.append(f"    - `{b}` on `{t}`")
        other_unt = [(b, t) for b, t in untested if t not in ENTRY_TABS]
        if other_unt:
            L.append(f"- **UNTESTED on non-entry tabs ({len(other_unt)})** — make the pilot test every orange row "
                     f"every run (YELLOW-ONLY law + `wired_filters.json` allowlist currently skip these):")
            for i in range(0, len(other_unt), 4):
                L.append("    - " + ", ".join(f"`{b}`@`{t}`" for b, t in sorted(other_unt)[i:i + 4]))
        if noop:
            L.append(f"- placement-NOOP cells (filter does nothing on that specific tab but works on another): "
                     f"{len(noop)} — informational; a later pass can prune these rows from tabs where they are inert.")
        L.append("")

    L.append("## GATE verdict")
    L.append("")
    if gate_ok:
        L.append("✅ **GREEN — every other orange-row filter is tested & vec-connected on every tab it lives on.** "
                 "Precondition for retiring `EMA_BLANKET_FILTER` (by vec coverage) is met. Confirm crypto+stocks "
                 "LIVE wiring per filter (4-surface trace) before the final removal.")
    else:
        L.append("🔴 **RED — do NOT destroy `EMA_BLANKET_FILTER` yet.** See worklist; re-run until GREEN.")
    L.append("")
    out = ROOT / "data" / "reports" / f"v15_orange_audit_{day}.md"
    out.write_text("\n".join(L) + "\n")
    return out, gate_ok, per_cs, cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--strict-connected", action="store_true",
                    help="(reserved) require live crypto+stocks wiring, not just vec, for CONNECTED")
    args = ap.parse_args()
    out, gate_ok, per_cs, cells = build(args.strict_connected)
    print(f"[written] {out}")
    for cs in CAT_SIDES:
        untested, noop, entry_untested, total, disconnected = cells[cs]
        print(f"  {cs}: cells={total} untested={len(untested)} (entry {len(entry_untested)}) "
              f"disconnected={len(disconnected)} placement-noop={len(noop)}")
    print(f"[gate] {'GREEN — EMA_BLANKET may be retired (after live-wiring trace)' if gate_ok else 'RED — fix worklist first'}")
    return 0 if gate_ok else 2


if __name__ == "__main__":
    sys.exit(main())
