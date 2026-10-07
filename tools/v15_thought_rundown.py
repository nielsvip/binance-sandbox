"""Per-sym_side THOUGHT PROCESS rundown: deficiencies -> search -> selection -> deltas.

Reads a completed side's result files ({ss}_365.json + {ss}_gs.json) and the
live book (per_sym_store.db), renders:
  {ss}_thought.md    human-readable fault -> switch -> delta narrative
  {ss}_thought.json  structured trace consumed by v15_lever_inventory.py

Honesty rules: every section tags its source as RECORDED (system wrote it),
DERIVED (computed now from recorded numbers), or NOT RECORDED (absent in this
side's files, usually because it finished before the trace fields existed).
Nothing is fabricated: unknown 365D is printed as NOT MEASURED, never as fail.
No recompute: pure rendering. Never raises on a missing file.
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BOOK = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"

W30 = {"gain_pct": "gain", "trades": "trades", "tim_pct": "tim", "max_dd_pct": "dd"}


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def _fmt(v, nd=2):
    if v is None:
        return "?"
    return f"{v:.{nd}f}" if isinstance(v, float) else str(v)


def _sgn(v):
    if v is None:
        return "?"
    return f"{v:+.2f}" if isinstance(v, float) else f"{v:+d}" if isinstance(v, int) else str(v)


def _norm_w(w):
    return {v: (w or {}).get(k) for k, v in W30.items()} if w else {}


def book_config(ss):
    try:
        con = sqlite3.connect(f"file:{BOOK}?mode=ro", uri=True)
        row = con.execute("select full_config_json from per_sym_active where sym_side=?", (ss,)).fetchone()
        con.close()
        return json.loads(row[0]) if row and row[0] else None
    except Exception:
        return None


def derive_gaps(w30, w365, q365):
    gaps = []
    if (w30.get("gain") or 0) < 0:
        gaps.append(("NEGATIVE_30D", f"gain {w30.get('gain')}"))
    if (w30.get("trades") or 0) < 30:
        gaps.append(("LOW_TRADES", f"{w30.get('trades')} trades/30D"))
    tim = w30.get("tim")
    if tim is not None and not 20 <= tim <= 80:
        gaps.append(("TIM_OUT_OF_BAND", f"TIM {tim}"))
    dd = w30.get("dd")
    if dd is not None and dd > 30:
        gaps.append(("HIGH_DD_30D", f"DD {dd}"))
    if q365 is False:
        g = (w365 or {}).get("gain")
        gaps.append(("Q365_FAIL", f"365D gain {g}"))
    return gaps


def render(ss, verdict, gs):
    cat = "crypto_" + ("short" if ss.endswith("_SHORT") else "long")
    v = verdict or {}
    g = gs or {}
    w30, w365 = _norm_w(v.get("w30")), _norm_w(v.get("w365"))
    before, after = g.get("before") or {}, g.get("after") or {}
    m365 = g.get("after_365")
    L = [f"# THOUGHT PROCESS — {ss}",
         f"cat_side={cat} gs_build={(g or {}).get('gs_build', '?')} rendered={datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}", ""]
    L.append("## 1. STARTING POINT (book, as traded live) [RECORDED]")
    L.append(f"30D: gain={_fmt(w30.get('gain'))} trades={w30.get('trades', '?')} "
             f"tim={_fmt(w30.get('tim'))} dd={_fmt(w30.get('dd'))}")
    L.append(f"365D: gain={_fmt(w365.get('gain'))} trades={w365.get('trades', '?')} "
             f"tim={_fmt(w365.get('tim'))} dd={_fmt(w365.get('dd'))} q365={v.get('q365', '?')} span_365d={v.get('span_365', '?')}"
             + (" SHORT_SPAN(<300d)" if isinstance(v.get("span_365"), (int, float)) and v["span_365"] < 300 else ""))
    for w in v.get("why365") or []:
        L.append(f"  why365: {w}")
    L.append("")
    faults, fsrc = [], "NOT RECORDED"
    iters = g.get("iterations") or []
    if iters and iters[0].get("faults"):
        faults = [{"fault": f, "src": "recorded"} for f in iters[0]["faults"]]
        fsrc = "RECORDED"
    else:
        faults = [{"fault": f, "mag": m, "src": "derived"} for f, m in derive_gaps(w30, w365, v.get("q365"))]
        fsrc = "DERIVED (side predates iterations trace)"
    L.append(f"## 2. DEFICIENCIES FOUND [{fsrc}]")
    for f in faults:
        L.append(f"- {f['fault']}" + (f" ({f['mag']})" if f.get("mag") else ""))
    if not faults:
        L.append("- none (book already compliant)")
    L.append("")
    L.append("## 3. SEARCH TRACE [RECORDED]")
    L.append(f"n_evals={g.get('n_evals', '?')} n_365={g.get('n_evals_365', '?')} secs={g.get('secs', '?')}")
    if g.get("phase_secs") or g.get("phase_skips"):
        L.append(f"phase_secs={json.dumps(g.get('phase_secs'))} phase_skips={g.get('phase_skips')}")
    for it in iters:
        L.append(f"- it{it.get('it')}: faults={it.get('faults')} gain={_fmt(it.get('gain'))} trades={it.get('trades')}")
    for s in g.get("steps", []) or []:
        L.append(f"- [{s.get('phase')}] {s.get('applied')} gain={_fmt(s.get('gain'))} "
                 f"trades={s.get('trades')} tim={_fmt(s.get('tim'))} dd={_fmt(s.get('dd'))}")
    for mac in g.get("macro", []) or []:
        L.append(f"- [MACRO] {json.dumps(mac, default=str)[:220]}")
    L.append("")
    fins = g.get("finalists") or []
    L.append(f"## 4. SELECTION [{('RECORDED') if fins else 'NOT RECORDED'}]")
    L.append("rule: max(compliant30, q365, score30+365, -n_changes); accept if status_up or gain_up>=0.5pp")
    for i, f in enumerate(fins):
        m, m3 = f.get("m") or {}, f.get("m365") or {}
        star = " <-- WINNER" if i > 0 and f.get("changes") == g.get("changes") else (" <-- ORIGIN" if i == 0 else "")
        L.append(f"- [{f.get('prov', '?')}] changes={f.get('changes')}{star}")
        L.append(f"    30D gain={_fmt(m.get('gain'))} tr={m.get('trades')} | "
                 f"365D gain={_fmt(m3.get('gain')) if m3 else 'NOT MEASURED'} q365={f.get('q365')} {f.get('why365')}")
    if not fins:
        L.append(f"- winner changes={g.get('changes')} reason={g.get('accept_reason')} (finalists predate trace)")
    L.append("")
    L.append("## 5. WINNER vs BOOK (what actually changes in live config) [DERIVED]")
    book = book_config(ss)
    wdiff = []
    bo = g.get("best_overrides") or {}
    if book is None:
        L.append("- book NOT FOUND locally (remote side or DB unsynced)")
    elif not bo:
        L.append("- no best_overrides (origin best, nothing accepted)")
    else:
        for k in sorted(set(bo) | set(book)):
            a, b = book.get(k), bo.get(k)
            if str(a) != str(b):
                wdiff.append({"switch": k, "book": a, "new": b})
        for d in wdiff:
            L.append(f"- {d['switch']}: {d['book']} -> {d['new']}")
        if not wdiff:
            L.append("- identical to book (no changes)")
        _listed = set(g.get("changes") or [])
        _valued = {d["switch"] for d in wdiff}
        for k in sorted(_listed - _valued):
            L.append(f"- {k}: LISTED in changes but book value identical (type-only or book drift — no live effect)")
            wdiff.append({"switch": k, "book": book.get(k), "new": bo.get(k), "note": "no-value-diff"})
    L.append("")
    L.append("## 6. ABLATION (what actually mattered) [RECORDED]")
    abl = g.get("ablation_top") or g.get("ablation") or []
    abl_rows = []
    for a in abl[:16]:
        d = a.get("d") if isinstance(a, dict) else None
        if isinstance(a, dict) and isinstance(d, dict):
            abl_rows.append({"group": a.get("group"), "mode": a.get("mode"), "changed": a.get("changed"),
                             **{k: d.get(k) for k in ("gain", "trades", "tim", "dd")}})
            L.append(f"- {a.get('group')} [{a.get('mode')}] via {a.get('changed')}: d_gain={_sgn(d.get('gain'))} "
                     f"d_tr={_sgn(d.get('trades'))} d_tim={_sgn(d.get('tim'))} d_dd={_sgn(d.get('dd'))}")
        elif isinstance(a, dict):
            abl_rows.append(a)
            L.append(f"- {a.get('group', '?')}: {json.dumps(a)[:160]}")
    if not abl:
        L.append("- NOT RECORDED")
    L.append("")
    L.append("## 7. VERDICT [RECORDED + DERIVED]")
    L.append(f"30D: gain {_fmt(before.get('gain'))} -> {_fmt(after.get('gain'))} "
             f"({_sgn((after.get('gain') or 0) - (before.get('gain') or 0))}), "
             f"trades {before.get('trades', '?')} -> {after.get('trades', '?')}")
    if m365:
        L.append(f"365D: gain={_fmt(m365.get('gain'))} dd={_fmt(m365.get('dd'))} q365={g.get('q365_after')}")
        if m365.get("trades") == after.get("trades") and m365.get("gain") == after.get("gain") and (after.get("trades") or 0) > 0:
            L.append("SPAN_CONCENTRATED [DERIVED]: all 365D trades sit inside the recent 30D window (identical gain/trades; TIM diluted ×30/365) — "
                     "passes the gain>0 gate but has zero out-of-window evidence; treat as regime-specific until monthly spread is proven")
    else:
        L.append("365D: NOT MEASURED for winner (m365=None) — 30D-only evidence" +
                 (" while base 365D VOMITS" if (w365.get("dd") or 0) >= 100 else ""))
    L.append(f"accepted={g.get('accepted')} reason={g.get('accept_reason')}")
    res = g.get("diagnosis_after")
    if res:
        L.append(f"residual_faults [RECORDED]: {json.dumps(res)[:300]}")
    else:
        rg = derive_gaps({k: after.get(k) for k in ("gain", "trades", "tim", "dd")}, m365 or {}, g.get("q365_after"))
        L.append(f"residual_gaps [DERIVED]: {rg or 'none — fully repaired on recorded metrics'}")
    tradeable = bool(g.get("accepted")) and bool(g.get("q365_after")) and m365 is not None
    L.append(f"TRADEABLE_365: {'YES' if tradeable else 'NO'}")
    L.append("")
    L.append("## 8. HEAL_365 + MONTHLY [RECORDED]")
    for h in g.get("heal_rounds", []) or []:
        L.append(f"- round{h.get('round')}: faults={h.get('faults')} monthly={h.get('monthly')} worst={h.get('worst')} "
                 f"g30={_fmt(h.get('gain30'))} g365={_fmt(h.get('gain365'))} accepted={h.get('accepted', '?')}{(' ' + h.get('why', '')) if h.get('why') else ''}")
    mt = g.get("monthly") or {}
    if mt.get("slices"):
        L.append(f"monthly mode={mt.get('mode')}:")
        for s in mt["slices"]:
            L.append(f"  {s.get('id')}: n={s.get('n')} pnl={s.get('pnl'):+} wr={s.get('wr')} losers={s.get('losers')} hold={s.get('avg_hold')} exits={s.get('top_exits')}")
        w = mt.get("worst") or {}
        L.append(f"worst slice: {w.get('id')} (pnl={w.get('pnl')})")
    elif mt:
        L.append(f"monthly: {mt.get('mode', '?')}")
    else:
        L.append("- no heal rounds (365D green/unmeasured or pre-HEAL build)")
    thought = {"ss": ss, "cat_side": cat, "w30": w30, "w365": w365, "q365_base": v.get("q365"),
               "faults": faults, "fault_src": fsrc.split()[0].lower(),
               "n_evals": g.get("n_evals"), "secs": g.get("secs"),
               "finalists": [{"prov": f.get("prov"), "changes": f.get("changes"),
                              "g30": (f.get("m") or {}).get("gain"), "t30": (f.get("m") or {}).get("trades"),
                              "g365": (f.get("m365") or {}).get("gain") if f.get("m365") else None,
                              "q365": f.get("q365")} for f in fins],
               "changes": g.get("changes"), "winner_vs_book": wdiff,
               "ablation": abl_rows, "before": {k: before.get(k) for k in ("gain", "trades", "tim", "dd", "wr")},
               "after": {k: after.get(k) for k in ("gain", "trades", "tim", "dd", "wr")},
               "m365": {k: m365.get(k) for k in ("gain", "trades", "tim", "dd")} if m365 else None,
               "q365_after": g.get("q365_after"), "accepted": g.get("accepted"),
               "accept_reason": g.get("accept_reason"), "tradeable_365": tradeable, "gs_build": g.get("gs_build"),
               "heal_rounds": g.get("heal_rounds"), "monthly": g.get("monthly"), "phase_secs": g.get("phase_secs"),
               "phase_skips": g.get("phase_skips")}
    return "\n".join(L) + "\n", thought


def rundown_side(ss, d):
    d = Path(d)
    verdict = _load(d / f"{ss}_365.json")
    gs = _load(d / f"{ss}_gs.json")
    if verdict is None and gs is None:
        return None, None
    md, thought = render(ss, verdict, gs)
    (d / f"{ss}_thought.md").write_text(md)
    (d / f"{ss}_thought.json").write_text(json.dumps(thought, indent=1, default=str))
    return md, thought


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("dir")
    ap.add_argument("--side", default=None)
    a = ap.parse_args()
    d = Path(a.dir)
    sides = [a.side] if a.side else sorted({p.name[:-9] for p in d.glob("*_365.json")})
    n = 0
    for ss in sides:
        md, _ = rundown_side(ss, d)
        if md:
            n += 1
            print(f"--- {ss} ---")
            print(md)
    print(f"rundowns={n}")


if __name__ == "__main__":
    main()
