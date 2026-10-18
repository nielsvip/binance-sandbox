#!/usr/bin/env python3
"""claim_backing_gate — no gain number is allowed to exist without trade-by-trade backing.

USER 2026-10-18, verbatim: *"NOTHING EVER EVER CAN CLAIM A GAIN THAT IS NOT COMPLETELY BACKED UP WITH TRADE BY
TRADE XLSX AND HTML AND JSON EVER"*.

THE MEASURED FACT THIS EXISTS FOR: of 248 sets applied to live in the 7 days to 2026-10-18, 9 reproduced their
claimed gain within the 0.48 board-noise tolerance when re-measured with the pilot's own
`evaluate_prepared_sanitized`; 81 claimed POSITIVE and re-measured NEGATIVE. A claim with no evidence attached to
it is indistinguishable from a fabricated one, so this tool asks the only question that cannot be argued with:
**for this live claim, is there a board on disk whose own per-trade rows add up to the number being claimed?**

WHAT A CLAIM IS
    one row of `per_sym_active` (what live ez_manage/_cfg actually reads) — sym_side + acc_gain_pct + trades +
    max_dd_pct. `--history` adds the `per_sym_history` publish rows.

WHAT BACKING IS — all three, for ONE board, or it is not backed (the operator's trio):
    XLSX   {SS}[_{round}]_{name}_30d_matrix.xlsx  — the board workbook, openable (valid zip, >= 10 entries: the
           same validity test v15_pilot._atomic_save applies, so a 225 KB OOM-truncated file never counts).
    HTML   {SS}_{name}_30d_matrix.html (or any {SS}_*.html chart) — and it must pass
           tools.opt.chart_truth.verify_html: a chart whose printed header disagrees with its own trade arrays is
           not evidence of anything.
    JSON   per-trade rows. Resolved in this order and the source is always reported:
             SIDECAR  {stem}_trades.json / {stem}_ledger.json  — a standalone trade-by-trade JSON file
             EMBEDDED the chart's own `const TRADES` / `const EVENTS` — JSON text, per-trade rows, same file
           `--require-standalone-json` demands SIDECAR (the strictest reading of the mandate).

THE NUMBER IS NEVER RE-IMPLEMENTED HERE. gain = net $ / peak cost basis, realised closes only, computed by the
project's own code on the per-trade rows:
    metrics_guard.realised_closes            what counts as a trade (pnl presence; seeds/MTM per METRICS_COUNT_UNREALISED_MTM)
    tools.opt.evaluate_v12._honest_gain_pct  sum(pnl_dollars) / _capital_base
    tools.opt.evaluate_v12._peak_concurrent  the denominator, event-level signed replay
    tools.opt.evaluate_v12._honest_max_dd_pct
    tools.opt.chart_truth.verify_html        header-vs-own-arrays refusal (the pattern this copies)

VERDICTS (per claim)
    BACKED        >= 1 board carries the full trio and its per-trade rows reproduce the claimed gain within --tol.
    UNBACKED      nothing with per-trade rows exists for this sym_side (NO_EVIDENCE), or a board reproduces the
                  number but the trio is incomplete (TRIO_INCOMPLETE: which leg is missing is named).
    CONTRADICTED  per-trade evidence exists for this sym_side and ALL of it says a different number. SIGN_FLIP is
                  called out separately: the claim is positive and the closest evidence is negative.

Exit code 1 when any live claim is UNBACKED or CONTRADICTED. READ-ONLY: it never writes, renames or re-stamps a
board, a manifest or a chart; its only output is a report under data/reports/.

USAGE
    tools/claim_backing_gate.py                                   # every per_sym_active claim
    tools/claim_backing_gate.py --since 2026-10-11 --tol 0.48     # the 7-day window, board-noise tolerance
    tools/claim_backing_gate.py --symside SNXUSDT_SHORT --verbose
    tools/claim_backing_gate.py --history --since 2026-10-11      # + every publish row
    tools/claim_backing_gate.py --require-standalone-json          # mandate read strictly
    tools/claim_backing_gate.py --json data/reports/claim_backing.json --quiet
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import glob
import hashlib
import json
import os
import re
import sqlite3
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

DB = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
SHEET_DIRS = [ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL", ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL_S2"]
CHART_DIRS = [ROOT / "data" / "reports" / "charts_1M", ROOT / "data" / "reports" / "charts_1Y"]
OUT_DIR = ROOT / "data" / "reports"
CACHE = OUT_DIR / "claim_backing_gate_cache.json"
ROUND_TOKEN = re.compile(r"_\d{8}(?=_)")
MIN_ZIP_ENTRIES = 10


def log(msg):
    print(msg, flush=True)


def _r(v, nd=4):
    try:
        return None if v is None else round(float(v), nd)
    except (TypeError, ValueError):
        return None


def board_key(name: str) -> str:
    """One board, whatever extension names it: drop the publish-round token so xlsx/html/manifest collapse."""
    s = name
    for suf in ("_manifest.json", "_trades.json", "_ledger.json", ".xlsx", ".html", ".json"):
        if s.endswith(suf):
            s = s[: -len(suf)]
            break
    return ROUND_TOKEN.sub("", s)


def symside_of(name: str) -> str:
    m = re.match(r"^([A-Z0-9]+_(?:LONG|SHORT))", name)
    return m.group(1) if m else ""


def xlsx_openable(path: Path):
    """The validity test v15_pilot._atomic_save applies — a truncated workbook is not evidence."""
    try:
        with zipfile.ZipFile(path) as z:
            n = len(z.namelist())
        return (n >= MIN_ZIP_ENTRIES, n)
    except Exception as e:
        return (False, str(e)[:80])


def chart_numbers(path: Path):
    """Recompute this chart's gain from the per-trade rows it embeds, with the project's own code."""
    from metrics_guard import realised_closes
    from tools.opt.chart_truth import parse_js_const, verify_html
    from tools.opt.evaluate_v12 import _honest_gain_pct, _honest_max_dd_pct, _peak_concurrent

    txt = path.read_text(encoding="utf-8", errors="replace")
    trades = parse_js_const(txt, "TRADES")
    events = parse_js_const(txt, "EVENTS") or []
    close = parse_js_const(txt, "CLOSE")
    if trades is None or close is None:
        return {"ok": False, "why": "chart embeds no CLOSE/TRADES — no per-trade rows to check"}
    rows_all = list(trades) + list(events)
    keep = {id(r) for r in realised_closes(rows_all)}
    led = [r for r in rows_all if isinstance(r, dict) and (r.get("pnl_dollars") is None or id(r) in keep)]
    emb = {"ledger": led}
    out = {
        "ok": True,
        "gain_pct": _honest_gain_pct(emb),
        "max_dd_pct": _honest_max_dd_pct(emb),
        "peak_capital": _peak_concurrent(led),
        "trades": len(realised_closes(rows_all)),
        "n_rows_embedded": len(rows_all),
        "n_mtm_marks": sum(1 for r in rows_all if isinstance(r, dict) and r.get("unrealised")),
        "bars": len(close),
    }
    try:
        verify_html(txt, symside_of(path.name) or path.stem, require_header=True)
        out["header_truth"] = "ok"
    except Exception as e:
        out["header_truth"] = str(e)[:200]
    return out


class Cache:
    """(path, mtime, size) -> recomputed chart numbers. Charts are immutable once published; a rerender changes mtime."""

    def __init__(self, path: Path, enabled=True):
        self.path, self.enabled, self.dirty = path, enabled, False
        try:
            self.d = json.loads(path.read_text()) if (enabled and path.exists()) else {}
        except Exception:
            self.d = {}

    def get(self, p: Path):
        try:
            st = p.stat()
        except OSError:
            return None
        k = f"{p}|{int(st.st_mtime)}|{st.st_size}"
        if self.enabled and k in self.d:
            return self.d[k]
        v = chart_numbers(p)
        if self.enabled:
            self.d[k] = v
            self.dirty = True
        return v

    def save(self):
        if self.enabled and self.dirty:
            try:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                self.path.write_text(json.dumps(self.d, default=str))
            except Exception as e:
                log(f"[cache-warn] {e}")


def index_artifacts(root: Path = None, symside: str = ""):
    """Every board artifact on this host, grouped per sym_side then per board key."""
    sheet_dirs, chart_dirs = SHEET_DIRS, CHART_DIRS
    if root is not None:
        root = Path(root)
        sheet_dirs = [root / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL", root / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL_S2"]
        chart_dirs = [root / "data" / "reports" / "charts_1M", root / "data" / "reports" / "charts_1Y"]
    idx = collections.defaultdict(lambda: collections.defaultdict(dict))
    for d in sheet_dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            n = p.name
            ss = symside_of(n)
            if not ss:
                continue
            k = board_key(n)
            if n.endswith(".xlsx") and ".tmp" not in n:
                idx[ss][k]["xlsx"] = str(p)
            elif n.endswith(".html"):
                idx[ss][k].setdefault("html", str(p))
            elif n.endswith("_manifest.json"):
                idx[ss][k]["manifest"] = str(p)
            elif n.endswith("_trades.json") or n.endswith("_ledger.json"):
                idx[ss][k]["sidecar"] = str(p)
    for d in chart_dirs:
        if not d.is_dir():
            continue
        for p in d.iterdir():
            n = p.name
            ss = symside_of(n)
            if not ss or not n.endswith(".html"):
                continue
            idx[ss][board_key(n)].setdefault("html", str(p))
    return idx


def claims_active(con, since, cat_side, symside):
    q = ("select sym_side, cat_side, updated_at, winning_tag, acc_gain_pct, trades, max_dd_pct, bh_pct,"
         " baseline_30d, overrides_json, meta_json from per_sym_active where 1=1")
    args = []
    if since:
        q += " and updated_at >= ?"
        args.append(since)
    if cat_side:
        q += " and cat_side like ?"
        args.append(cat_side + "%")
    if symside:
        q += " and sym_side = ?"
        args.append(symside.upper())
    out = []
    for ss, cs, upd, tag, gain, tr, dd, bh, base, ovj, mj in con.execute(q + " order by updated_at desc", args):
        meta = {}
        try:
            meta = json.loads(mj or "{}")
        except Exception:
            pass
        ov = {}
        try:
            ov = json.loads(ovj or "{}")
        except Exception:
            pass
        out.append({"source": "per_sym_active", "sym_side": ss, "cat_side": cs, "updated_at": upd, "winning_tag": tag,
                    "gain_pct": gain, "trades": tr, "max_dd_pct": dd, "bh_pct": bh, "baseline_30d": base,
                    "n_set_keys": len(ov),
                    "set_md5": hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest() if ov else "",
                    "npz_id": str(meta.get("npz_id") or ""), "n_promoted": meta.get("n_promoted")})
    return out


def claims_history(con, since, cat_side, symside):
    q = ("select sym_side, cat_side, updated_at, winning_tag, gain_pct, trades, max_dd_pct, bh_pct, baseline_gain,"
         " overrides_json from per_sym_history where 1=1")
    args = []
    if since:
        q += " and updated_at >= ?"
        args.append(since)
    if cat_side:
        q += " and cat_side like ?"
        args.append(cat_side + "%")
    if symside:
        q += " and sym_side = ?"
        args.append(symside.upper())
    out = []
    for ss, cs, upd, tag, gain, tr, dd, bh, base, ovj in con.execute(q + " order by updated_at desc", args):
        ov = {}
        try:
            ov = json.loads(ovj or "{}")
        except Exception:
            pass
        out.append({"source": "per_sym_history", "sym_side": ss, "cat_side": cs, "updated_at": upd, "winning_tag": tag,
                    "gain_pct": gain, "trades": tr, "max_dd_pct": dd, "bh_pct": bh, "baseline_30d": base,
                    "n_set_keys": len(ov),
                    "set_md5": hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest() if ov else "",
                    "npz_id": "", "n_promoted": None})
    return out


def judge(claim, boards, cache, tol, require_sidecar, max_boards):
    """One claim against every board its sym_side owns. Returns the verdict record."""
    g = claim.get("gain_pct")
    rec = {k: claim.get(k) for k in ("source", "sym_side", "cat_side", "updated_at", "winning_tag", "gain_pct",
                                     "trades", "max_dd_pct", "n_set_keys", "set_md5", "npz_id", "n_promoted")}
    rec["n_boards"] = len(boards)
    if g is None:
        rec.update({"verdict": "UNBACKED", "reason": "NO_CLAIM_VALUE", "detail": "the row carries no gain to back"})
        return rec
    g = float(g)
    keys = sorted(boards, key=lambda k: -max([os.path.getmtime(p) for p in boards[k].values() if os.path.exists(p)] or [0]))
    evaluated, with_rows, reproducing = [], 0, []
    for k in keys[:max_boards]:
        b = boards[k]
        html = b.get("html")
        if not html:
            continue
        num = cache.get(Path(html))
        if not num or not num.get("ok"):
            evaluated.append({"board": k, "html": os.path.basename(html), "why": (num or {}).get("why", "unreadable")})
            continue
        with_rows += 1
        xl = b.get("xlsx")
        xl_ok, xl_n = (xlsx_openable(Path(xl)) if xl else (False, "absent"))
        json_src = "SIDECAR" if b.get("sidecar") else "EMBEDDED"
        e = {"board": k, "html": os.path.basename(html), "xlsx": os.path.basename(xl) if xl else None,
             "xlsx_ok": bool(xl_ok), "xlsx_entries": xl_n, "manifest": bool(b.get("manifest")),
             "json_source": json_src, "evidence_gain_pct": _r(num["gain_pct"]), "evidence_trades": num["trades"],
             "evidence_dd_pct": _r(num["max_dd_pct"]), "peak_capital": _r(num["peak_capital"]),
             "header_truth": num.get("header_truth"), "n_mtm_marks": num.get("n_mtm_marks"),
             "d_gain": _r(float(num["gain_pct"]) - g)}
        missing = []
        if not xl_ok:
            missing.append("XLSX")
        if num.get("header_truth") != "ok":
            missing.append("HTML_HEADER")
        if require_sidecar and json_src != "SIDECAR":
            missing.append("STANDALONE_JSON")
        e["missing"] = missing
        e["reproduces"] = abs(e["d_gain"]) <= tol
        evaluated.append(e)
        if e["reproduces"]:
            reproducing.append(e)
    rec["n_boards_with_rows"] = with_rows
    rec["evidence"] = sorted(evaluated, key=lambda e: abs(e.get("d_gain") if e.get("d_gain") is not None else 9e9))[:6]
    full = [e for e in reproducing if not e["missing"]]
    if full:
        best = min(full, key=lambda e: abs(e["d_gain"]))
        rec.update({"verdict": "BACKED", "reason": "TRIO_REPRODUCES", "board": best["board"],
                    "evidence_gain_pct": best["evidence_gain_pct"], "d_gain": best["d_gain"],
                    "evidence_trades": best["evidence_trades"], "json_source": best["json_source"],
                    "trades_agree": (best["evidence_trades"] == int(claim["trades"])) if claim.get("trades") is not None else None})
        return rec
    if reproducing:
        best = min(reproducing, key=lambda e: abs(e["d_gain"]))
        rec.update({"verdict": "UNBACKED", "reason": "TRIO_INCOMPLETE", "board": best["board"],
                    "detail": "the number reproduces but the evidence trio is incomplete: missing " + "+".join(best["missing"]),
                    "missing": best["missing"], "evidence_gain_pct": best["evidence_gain_pct"], "d_gain": best["d_gain"],
                    "json_source": best["json_source"]})
        return rec
    if with_rows == 0:
        rec.update({"verdict": "UNBACKED", "reason": "NO_EVIDENCE",
                    "detail": f"no board with per-trade rows on this host ({len(boards)} artifact group(s), none with a readable chart)"})
        return rec
    cand = [e for e in evaluated if e.get("d_gain") is not None]
    best = min(cand, key=lambda e: abs(e["d_gain"]))
    sign_flip = g > 0 and float(best["evidence_gain_pct"]) < 0
    rec.update({"verdict": "CONTRADICTED", "reason": "SIGN_FLIP" if sign_flip else "NO_BOARD_REPRODUCES",
                "detail": f"{with_rows} board(s) carry per-trade rows; the closest says {best['evidence_gain_pct']}% "
                          f"(d {best['d_gain']:+.4f}) against a claim of {g:.4f}%",
                "board": best["board"], "evidence_gain_pct": best["evidence_gain_pct"], "d_gain": best["d_gain"],
                "evidence_trades": best["evidence_trades"], "sign_flip": bool(sign_flip)})
    return rec


def check_claim_backed(sym_side, evidence, root=None, tol=0.48, require_sidecar=False, max_boards=40, use_cache=True):
    """THE PRE-WRITE CHECK — the registrar's one question before a gain reaches any live surface.

    Called by switch_parity.register_workbook_result (env CLAIM_BACKING_GATE, default OFF) with the SAME evidence
    dict it is about to store. Returns (ok, why, record): ok only when a board on disk carries the full trio and
    its own per-trade rows reproduce evidence["gain_pct"] within tol. Fail-closed by construction — an evidence
    dict with no gain, a sym_side with no artifacts, and a board whose trades say something else all return False.
    """
    claim = {"source": "register_workbook_result", "sym_side": str(sym_side or "").upper(),
             "cat_side": None, "updated_at": _now(), "winning_tag": evidence.get("tag") if isinstance(evidence, dict) else None,
             "gain_pct": (evidence or {}).get("gain_pct"), "trades": (evidence or {}).get("trades"),
             "max_dd_pct": (evidence or {}).get("max_dd_pct"), "n_set_keys": None, "set_md5": "",
             "npz_id": str((evidence or {}).get("npz_id") or ""), "n_promoted": (evidence or {}).get("n_promoted")}
    idx = index_artifacts(root=root)
    cache = Cache(CACHE if root is None else Path(root) / "data" / "reports" / "claim_backing_gate_cache.json", enabled=use_cache)
    rec = judge(claim, idx.get(claim["sym_side"], {}), cache, tol, require_sidecar, max_boards)
    cache.save()
    ok = rec["verdict"] == "BACKED"
    why = "" if ok else f"{rec['verdict']}/{rec['reason']}: {rec.get('detail') or rec.get('board') or ''}"
    return ok, why, {k: rec.get(k) for k in ("verdict", "reason", "detail", "board", "evidence_gain_pct", "d_gain",
                                             "evidence_trades", "json_source", "missing", "n_boards",
                                             "n_boards_with_rows", "trades_agree")}


def _now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default="", help="updated_at floor, e.g. 2026-10-11")
    ap.add_argument("--cat-side", default="", help="CRYPTO / STOCKS / CRYPTO_LONG ...")
    ap.add_argument("--symside", default="")
    ap.add_argument("--tol", type=float, default=0.48, help="board-noise tolerance in gain pp (BIBLE: identical tree+NPZ differ <= 0.48)")
    ap.add_argument("--history", action="store_true", help="also judge per_sym_history publish rows")
    ap.add_argument("--require-standalone-json", action="store_true", help="the trade JSON must be its own file, not the chart's arrays")
    ap.add_argument("--max-boards", type=int, default=40, help="boards per sym_side to recompute (newest first)")
    ap.add_argument("--no-cache", action="store_true")
    ap.add_argument("--json", default="")
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--top", type=int, default=25)
    ap.add_argument("--exclude-mtm", action="store_true", help="METRICS_COUNT_UNREALISED_MTM=0 (drop the window-edge mark)")
    a = ap.parse_args()
    if a.exclude_mtm:
        os.environ["METRICS_COUNT_UNREALISED_MTM"] = "0"
    os.environ.setdefault("V12_ALLOW_STALE_NPZ", "1")
    t0 = time.time()
    if not DB.exists():
        log(f"per_sym_store.db absent at {DB} — nothing to verify (exit 1)")
        return 1
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    claims = claims_active(con, a.since, a.cat_side, a.symside)
    if a.history:
        claims += claims_history(con, a.since, a.cat_side, a.symside)
    con.close()
    idx = index_artifacts()
    cache = Cache(CACHE, enabled=not a.no_cache)
    rows = []
    for c in claims:
        rows.append(judge(c, idx.get(c["sym_side"], {}), cache, a.tol, a.require_standalone_json, a.max_boards))
    cache.save()
    live = [r for r in rows if r["source"] == "per_sym_active"]
    hist = [r for r in rows if r["source"] == "per_sym_history"]
    vc = collections.Counter(r["verdict"] for r in live)
    rc = collections.Counter(r["reason"] for r in live)
    bad = [r for r in live if r["verdict"] in ("UNBACKED", "CONTRADICTED")]
    if not a.quiet:
        log(f"=== CLAIM BACKING GATE — {dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%d %H:%MZ')} ===")
        log(f"tol {a.tol} pp · standalone-json {'REQUIRED' if a.require_standalone_json else 'not required (chart-embedded JSON accepted)'}"
            f" · MTM marks {'excluded' if a.exclude_mtm else 'counted (project default)'}")
        log(f"claims judged: {len(live)} live (per_sym_active){f' + {len(hist)} publish rows' if hist else ''}"
            f" · artifact groups indexed: {sum(len(v) for v in idx.values())} over {len(idx)} sym_sides\n")
        log("  LIVE CLAIMS")
        for v in ("BACKED", "UNBACKED", "CONTRADICTED"):
            pct = (100.0 * vc[v] / len(live)) if live else 0.0
            log(f"    {v:14s} {vc[v]:5d}  ({pct:5.1f}%)")
        log("\n  reasons")
        for k, n in rc.most_common():
            log(f"    {k:22s} {n:5d}")
        mh = collections.Counter(m for r in live if r["reason"] == "TRIO_INCOMPLETE" for m in (r.get("missing") or []))
        if mh:
            log("\n  TRIO_INCOMPLETE — which leg of the evidence trio is missing")
            for k, n in mh.most_common():
                log(f"    {k:22s} {n:5d}")
        sf = [r for r in live if r.get("sign_flip")]
        log(f"\n  SIGN FLIPS (claimed positive, own trades negative): {len(sf)}")
        ta = [r for r in live if r["verdict"] == "BACKED" and r.get("trades_agree") is False]
        log(f"  BACKED but the trade COUNT disagrees with the claim:  {len(ta)}")
        if hist:
            hvc = collections.Counter(r["verdict"] for r in hist)
            log(f"\n  PUBLISH ROWS: " + "  ".join(f"{v} {hvc[v]}" for v in ("BACKED", "UNBACKED", "CONTRADICTED")))
        log(f"\n  WORKLIST — worst {a.top} live claims (largest |claim - own trades|):")
        wl = sorted([r for r in bad if r.get("d_gain") is not None], key=lambda r: -abs(r["d_gain"]))[: a.top]
        for r in wl:
            log(f"    {r['sym_side']:20s} {str(r['updated_at'])[:16]:17s} claim {float(r['gain_pct']):9.4f}%  "
                f"own trades {r['evidence_gain_pct']:9.4f}%  d {r['d_gain']:+9.4f}  {r['verdict']}/{r['reason']}")
        noev = [r for r in bad if r["reason"] == "NO_EVIDENCE"]
        log(f"\n  NO EVIDENCE AT ALL ({len(noev)}) — first {min(a.top, len(noev))}:")
        for r in noev[: a.top]:
            log(f"    {r['sym_side']:20s} {str(r['updated_at'])[:16]:17s} claim {float(r['gain_pct']):9.4f}%  "
                f"{r['n_boards']} artifact group(s)  {r.get('detail', '')}")
        if a.verbose:
            log("\n  PER-CLAIM EVIDENCE")
            for r in rows:
                log(f"\n  {r['sym_side']} [{r['source']}] {r['updated_at']} claim gain {r['gain_pct']} trades {r['trades']}"
                    f" -> {r['verdict']}/{r['reason']} {r.get('detail', '')}")
                for e in r.get("evidence", []):
                    log(f"      {e.get('html', '')[:66]:66s} gain {str(e.get('evidence_gain_pct')):>10s} tr {str(e.get('evidence_trades')):>5s}"
                        f" d {str(e.get('d_gain')):>10s} xlsx {'ok' if e.get('xlsx_ok') else 'MISSING'} json {e.get('json_source', '-')}"
                        f" missing {'+'.join(e.get('missing') or []) or '-'}")
    out = {"generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(), "tol": a.tol,
           "require_standalone_json": bool(a.require_standalone_json), "exclude_mtm": bool(a.exclude_mtm),
           "since": a.since, "cat_side": a.cat_side, "symside": a.symside,
           "n_live": len(live), "n_history": len(hist), "live_verdicts": dict(vc), "live_reasons": dict(rc),
           "n_sign_flips": sum(1 for r in live if r.get("sign_flip")),
           "n_artifact_groups": sum(len(v) for v in idx.values()), "elapsed_s": round(time.time() - t0, 1),
           "rows": rows}
    dest = Path(a.json) if a.json else OUT_DIR / f"claim_backing_gate_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%d%H%M')}.json"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(out, default=str, indent=1))
    if not a.quiet:
        log(f"\nwrote {dest}  ({round(time.time() - t0, 1)}s)")
        if bad:
            log(f"\nGATE FAILED: {len(bad)} of {len(live)} live claims are UNBACKED or CONTRADICTED (exit 1)")
        else:
            log(f"\nGATE PASSED: every one of {len(live)} live claims is backed trade-by-trade (exit 0)")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
