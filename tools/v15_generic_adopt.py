"""Generic-vs-book 365D adoption probe (USER 2026-10-07, report-only v1 — NEVER writes books).

Hypothesis: per-sym books overfit 30D; cat_side generic defaults may generalize better to 365D.
For each side:
  1. SCAN (cheap): eval generic on 365D (+30D); reuse book 365D/30D from s6 verdicts (no recompute).
  2. If generic NOT better on 365D -> KEEP_BOOK.
  3. If better -> BISECT the book-vs-generic diff on 365D (halving, budgeted) to find the switches that matter.
  4. 30D-safety: adopted set must keep 30D compliant and within -0.3pp of book 30D -> ADOPT, else TRADEOFF (honest numbers).
Outputs {ss}_adopt.json + adopt.md. No book writes, no live impact. S1/s6-portable (ROOT-relative).
"""
import argparse
import concurrent.futures as cf
import json
import sqlite3
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))  # ROOT first: stale tools/cat_side_defaults.py must never shadow the real one

import v15_pilot as P
from tools.opt import v12_pilot as VP
from tools import v15_diagnose_repair as DR

BOOK_DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
CATDEF = ROOT / "data" / "per_sym_settings.json"
GAP_PP = 0.5
DD_TOL = 2.0
REGRESS_30D = 0.3


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except (OSError, ValueError):
        return None


def metrics365(r):
    return {"gain": r.get("gain_pct"), "trades": r.get("trades"), "tim": r.get("tim_pct"), "dd": r.get("max_dd_pct"),
            "valid": r.get("valid"), "bh": r.get("bh_pct")}


def generic_better(g, gm, b, qg, qb):
    if qg and not qb:
        return True, "q365-flip"
    if (g.get("gain") or -1e9) >= (b.get("gain") or -1e9) + GAP_PP and (g.get("dd") or 1e9) <= (b.get("dd") or 1e9) + DD_TOL \
            and (g.get("trades") or 0) >= 30 and g.get("valid"):
        return True, f"+{(g['gain'] - b['gain']):.2f}pp@365D"
    if not g.get("valid") and not b.get("valid") and (g.get("gain") or -1e9) >= (b.get("gain") or -1e9) + 2.0 \
            and (g.get("dd") or 1e9) <= (b.get("dd") or 1e9) + 5.0 and (g.get("trades") or 0) >= 30:
        return True, f"damage-reduction@365D +{(g['gain'] - b['gain']):.2f}pp (both vomit)"
    return False, "generic-not-better"


def run_side(ss, outdir, verdict_dir, max_bisect=20):
    t00 = time.time()
    logl = []
    def log(m):
        line = f"[{ss}] {m}"
        print(line, flush=True)
        logl.append(line)
    con = sqlite3.connect(f"file:{BOOK_DB}?mode=ro", uri=True)
    row = con.execute("SELECT full_config_json, cat_side FROM per_sym_active WHERE sym_side=?", (ss,)).fetchone()
    con.close()
    if not row or not row[0]:
        return {"sym_side": ss, "error": "no book config"}
    import per_sym_store as pss
    book = json.loads(pss._maybe_decompress(row[0]) or "{}")
    cat_side = row[1] or P.map_key_for_symside(ss)
    catdefs = json.loads(CATDEF.read_text())
    generic = dict(catdefs[cat_side])
    D = sorted(k for k in set(book) | set(generic) if not P.same_val(book.get(k), generic.get(k)))
    verdict = _load(Path(verdict_dir) / f"{ss}_365.json") or {}
    vw30, vw365 = verdict.get("w30") or {}, verdict.get("w365") or {}
    B30 = {"gain": vw30.get("gain_pct"), "trades": vw30.get("trades"), "tim": vw30.get("tim_pct"), "dd": vw30.get("max_dd_pct")} if vw30 else None
    B365 = {"gain": vw365.get("gain_pct"), "trades": vw365.get("trades"), "tim": vw365.get("tim_pct"), "dd": vw365.get("max_dd_pct"),
            "valid": vw365.get("valid"), "bh": vw365.get("bh_pct")} if vw365 else None
    prep30, prep365 = VP.prepare_batch(ss, 30), VP.prepare_batch(ss, 365)
    npzp = (prep365 or {}).get("npz_prepared") or {}
    try:
        t = npzp.get("timestamps")
        t = [x / 1000 if x > 1e11 else x for x in (list(t[-1:]) + list(t[:1]))]
        span = float((t[0] - t[1]) / 86400)
    except Exception:
        span = 0.0
    ex = cf.ThreadPoolExecutor(max_workers=2)
    def one(prep, ov, wd, to):
        if prep is None:
            return None
        f = ex.submit(VP.evaluate_prepared_sanitized, prep, dict(ov), wd)
        try:
            return f.result(timeout=to)
        except Exception as e:
            log(f"EVAL_{wd}D_RAISED {type(e).__name__}: {str(e)[:100]}")
            return None
    if B365 is None or B365.get("gain") is None:
        r = one(prep365, book, 365, 300.0)
        B365 = metrics365(r or {})
    if B30 is None or B30.get("gain") is None:
        r = one(prep30, book, 30, 120.0)
        B30 = {"gain": (r or {}).get("gain_pct"), "trades": (r or {}).get("trades"), "tim": (r or {}).get("tim_pct"), "dd": (r or {}).get("max_dd_pct")}
    r = one(prep365, generic, 365, 300.0)
    G365 = metrics365(r or {})
    qg, whyg = P._qualifies_365d(r or {}, span)
    qb = bool(verdict.get("q365")) if verdict.get("q365") is not None else P._qualifies_365d({"gain_pct": B365.get("gain"), "trades": B365.get("trades"), "tim_pct": B365.get("tim"), "max_dd_pct": B365.get("max_dd_pct"), "valid": B365.get("valid")}, span)[0]
    r30 = one(prep30, generic, 30, 120.0)
    G30 = {"gain": (r30 or {}).get("gain_pct"), "trades": (r30 or {}).get("trades"), "tim": (r30 or {}).get("tim_pct"), "dd": (r30 or {}).get("max_dd_pct")}
    log(f"book365={B365.get('gain')} generic365={G365.get('gain')} q={qb}->{qg} diff_keys={len(D)} book30={B30.get('gain')} generic30={G30.get('gain')}")
    rep = {"sym_side": ss, "cat_side": cat_side, "span_365": round(span, 1), "diff_keys": len(D), "B30": B30, "B365": B365, "q365_book": qb,
           "G30": G30, "G365": G365, "q365_generic": qg, "why365_generic": whyg, "n_evals_365": 1, "n_evals_30": 1, "verdict": "KEEP_BOOK"}
    better, why = generic_better(G365, None, B365, qg, qb)
    if not better:
        rep["reason"] = why
        (outdir / f"{ss}_adopt.json").write_text(json.dumps(rep, indent=1, default=str))
        return rep
    rep["better_why"] = why
    gap = (G365.get("gain") or 0) - (B365.get("gain") or 0)
    C, ev365 = [], [1]
    def ev365of(ov):
        r = one(prep365, ov, 365, 300.0)
        ev365[0] += 1
        return metrics365(r or {})
    def recover(keys):
        ov = dict(book)
        for k in keys:
            ov[k] = generic[k]
        return ev365of(ov)
    todo = [D]
    while todo and ev365[0] < max_bisect and len(C) < 12:
        keys = todo.pop(0)
        if not keys:
            continue
        if len(keys) == 1:
            m = recover(keys)
            if (m.get("gain") or -1e9) >= (B365.get("gain") or -1e9) + 0.3:
                C.append({"switch": keys[0], "book": book.get(keys[0]), "generic": generic.get(keys[0]), "gain365": m.get("gain")})
            continue
        h = len(keys) // 2
        halves = [keys[:h], keys[h:]]
        scored = []
        for half in halves:
            if ev365[0] >= max_bisect:
                break
            m = recover(half)
            scored.append(((m.get("gain") or -1e9) - (B365.get("gain") or -1e9), half, m))
        scored.sort(reverse=True)
        if not scored:
            break
        if scored[0][0] >= max(0.3, 0.4 * gap):
            todo.append(scored[0][1])
            if len(scored) > 1 and scored[1][0] >= max(0.3, 0.4 * gap) and len(todo) < 4:
                todo.append(scored[1][1])
        elif sum(s[0] for s in scored) < 0.3:
            rep.setdefault("interactions", []).append({"keys": len(keys), "halves_gain": [round(s[0], 2) for s in scored]})
            break
        else:
            todo.extend([s[1] for s in scored if len(s[1]) <= 4])
            if all(len(s[1]) > 4 for s in scored):
                todo.append(scored[0][1])
    rep["candidates"] = C
    rep["n_evals_365"] = ev365[0]
    log(f"bisect done: {len(C)} candidate switches in {ev365[0]} 365D evals")
    block_mode = not C
    if block_mode:
        rep["reason"] = "generic better but no single/small cause (diffuse/interaction) — full-generic block reuses G30/G365 (zero new evals)"
        C = [{"switch": k, "book": book.get(k), "generic": generic.get(k), "gain365": None} for k in D]
    ov = dict(book)
    for c in C:
        ov[c["switch"]] = c["generic"]
    if block_mode:
        A30, A365 = dict(G30), dict(G365)
    else:
        r = one(prep30, ov, 30, 120.0)
        A30 = {"gain": (r or {}).get("gain_pct"), "trades": (r or {}).get("trades"), "tim": (r or {}).get("tim_pct"), "dd": (r or {}).get("max_dd_pct")}
        r = one(prep365, ov, 365, 300.0)
        A365 = metrics365(r or {})
    rep["A30"] = A30
    rep["n_evals_30"] = 1 if block_mode else 2
    m = DR.metrics({"gain_pct": A30.get("gain"), "trades": A30.get("trades"), "tim_pct": A30.get("tim"), "max_dd_pct": A30.get("dd"), "valid": True}, None)
    ok30 = DR.compliant(m) and (A30.get("gain") or -1e9) >= (B30.get("gain") or -1e9) - REGRESS_30D
    rep["A365"] = A365
    rep["n_evals_365"] = ev365[0] + (0 if block_mode else 1)
    log(f"adopt-set: 30D {B30.get('gain')}->{A30.get('gain')} 365D {B365.get('gain')}->{A365.get('gain')}")
    up365 = (A365.get("gain") or -1e9) >= (B365.get("gain") or -1e9) + 0.3
    up30 = (A30.get("gain") or -1e9) >= (B30.get("gain") or -1e9) - 1e-9
    if ok30 and up365:
        rep["verdict"] = "ADOPT"
    elif up365 and up30:
        rep["verdict"] = "ADOPT_UNCOMPLIANT"
        rep["reason"] = "both windows up, 30D still red (progress, needs more work)"
    elif up365:
        rep["verdict"] = "TRADEOFF_30D_DOWN"
    else:
        rep["verdict"] = "KEEP_BOOK"
        rep["reason"] = "adopt-set lost the 365D gain (interaction)"
    try:
        ex.shutdown(wait=False, cancel_futures=True)
    except Exception:
        pass
    (outdir / f"{ss}_adopt.json").write_text(json.dumps(rep, indent=1, default=str))
    rep["secs"] = round(time.time() - t00, 1)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sides", required=True)
    ap.add_argument("--out", default=str(ROOT / "data" / "generic_adopt"))
    ap.add_argument("--verdict-dir", default=str(ROOT / "data" / "s6_365"))
    ap.add_argument("--max-bisect", type=int, default=20)
    ap.add_argument("--jobs", type=int, default=2)
    a = ap.parse_args()
    outdir = Path(a.out)
    outdir.mkdir(parents=True, exist_ok=True)
    sides = [s.strip() for s in a.sides.split(",") if s.strip()]
    done = {}
    with cf.ThreadPoolExecutor(max_workers=max(1, a.jobs)) as pool:
        futs = {pool.submit(run_side, ss, outdir, a.verdict_dir, a.max_bisect): ss for ss in sides}
        for f in cf.as_completed(futs):
            try:
                r = f.result()
            except Exception as e:
                r = {"sym_side": futs[f], "error": str(e)[:200]}
            done[r.get("sym_side")] = r
            print(f"[adopt] DONE {r.get('sym_side')} verdict={r.get('verdict', r.get('error'))}", flush=True)
    L = [f"# GENERIC ADOPT — {len(done)} sides", ""]
    for ss in sides:
        r = done.get(ss, {})
        L.append(f"## {ss}: {r.get('verdict', r.get('error', '?'))}")
        L.append(f"book30={((r.get('B30') or {}).get('gain'))} generic30={((r.get('G30') or {}).get('gain'))} "
                 f"adopt30={((r.get('A30') or {}).get('gain'))}")
        L.append(f"book365={((r.get('B365') or {}).get('gain'))} generic365={((r.get('G365') or {}).get('gain'))} "
                 f"adopt365={((r.get('A365') or {}).get('gain'))} q={r.get('q365_book')}->{r.get('q365_generic')}")
        for c in r.get("candidates") or []:
            L.append(f"- ADOPT {c['switch']}: {c['book']} -> {c['generic']} (365D {c.get('gain365')})")
        if r.get("reason"):
            L.append(f"reason: {r['reason']}")
        L.append("")
    (outdir / "adopt.md").write_text("\n".join(L))
    print(f"[adopt] finished {len(done)} sides -> {outdir}", flush=True)


if __name__ == "__main__":
    main()
