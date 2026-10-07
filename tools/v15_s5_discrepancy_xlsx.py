"""Join S5 live-verify results + manifest + sheet audit into one huge discrepancy xlsx.

Inputs (data/reports/s5_verify_20261003/): manifest.json, sheet_audit.json,
results/*.json (rsynced from S5 ~/v15_s5verify_20261003/results/).
Output: SPREADSHEETS/S5_LIVE_VERIFY_20261003_DISCREPANCIES.xlsx
Every number is a recorded measurement; nothing is sampled or extrapolated.
"""
import glob
import json
import os
import re
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
INDIR = os.path.join(ROOT, "data", "reports", "s5_verify_20261003")
OUT = os.path.join(ROOT, "SPREADSHEETS", "S5_LIVE_VERIFY_20261003_DISCREPANCIES.xlsx")
SYM_RE = re.compile(r"^(.+)_(LONG|SHORT)(?:_bh.*)?_30d_matrix\.xlsx$")
SEV_ORDER = {"P0": 0, "HIGH": 1, "MED": 2, "LOW": 3, "INFO": 4}
PLAYBOOK = [
 ("LIVE_BELOW_BH_WHILE_VEC_BEATS", "P0", "Vec beats B&H but live does not: promoting the vec number would be a promotion lie.", "BLOCK promotion for this symside. Require scalar parity (bible §43) + 365D both-positive (§44/§58) before any live book write. Re-run single with V8_FORCE_REAL=1 to test whether the AUTO_VECTOR union explains the live leg.", "§43 §44 §58 §62"),
 ("FILENAME_GAIN_MISMATCH", "P0", "Published bh/gain filename disagrees with the progress fresh-vec gain by >0.5pp (NO-LIES rider).", "Re-anchor: fresh full-set eval under the current engine is authoritative (§32). Republish with the fresh number or suffix [UNVERIFIED] and queue for v15_assure. Never hand-edit the filename.", "§32 §63-rider"),
 ("LIVE_INVALID", "HIGH", "Scalar run unusable (no NPZ / trade floor / exception).", "no npz -> rebuild NPZ (backtest_v8_precompute --symbol --mode) + check S5 has it; trades<30 -> §58 repair (entry-blocker audit §17.5 first); exception -> attach trace, fix forward in engine, never revert.", "§17 §18 §58"),
 ("VEC_INVALID", "HIGH", "Vector eval unusable on the final set.", "Same triage as LIVE_INVALID on the vec path. If vec invalid but live valid, suspect vec-only gate (sweep_cat_overrides / cat_side default) blocking entries.", "§17 §18"),
 ("LIVE_TIMEOUT_OR_CRASH", "HIGH", "Worker produced no result (timeout 900s / crash / OOM).", "Rerun the single entry by hand; check dmesg/OOM on S5; check NPZ size for the symbol; keep LIVE_TIMEOUT=900, never silently extend.", "§5.7 §22"),
 ("ZERO_TRADE_SPLIT", "HIGH", "One engine trades, the other has 0.", "Compare entry gates: live-only engines (AUTO_VECTOR union, MTF_ARMED, LIVE_ENTRY_ENGINE) vs vec entry-blockers (§17.5). Run the §17.2 parity audit for this symside; test the V8_FORCE_REAL=1 arm.", "§17 §43"),
 ("TRADE_RATIO_BREACH", "HIGH", "Both trade but vec/live trade-count ratio outside 0.80..1.25.", "Ledger-diff: which lifecycle (entry/exit/augment/reduce/reentry) diverges. Usual suspects: HLR sanction asymmetry (§63), recross bypass, MIN_HOLD bar-idiom (15m vec vs 3m live).", "§43 §63"),
 ("GAIN_MISMATCH", "HIGH", "Trade counts agree but gains differ (>0.5pp and >15%).", "Sizing/exit-price divergence: check fill-sum vs peak-concurrent deployed (§51), fee/slippage path, REDUCE semantics (crypto full-close). Ledger-diff closes by reason.", "§43 §51"),
 ("FILE_BADZIP", "HIGH", "Workbook unreadable (BadZip).", "Refill from progress JSON (tools/v15_refill_from_json.py); _atomic_save zip-validate (ZipFile>=10) before os.replace; never publish a truncated file.", "§35 §32"),
 ("FILE_TRUNCATED", "HIGH", "Zip has <10 entries (OOM/pkill truncation signature).", "Same as FILE_BADZIP. Check OOM kills on the filling host around the file mtime.", "§35"),
 ("E3_BASELINE_BAD", "HIGH", "First-tab E3 baseline missing/non-numeric while progress has done>0.", "Sheet fill bug: baseline must be the real v12 gain of defaults+prior-best written at clone (§56 Step 2). Re-clone + re-baseline; check H/I formula corruption of header detection.", "§56 §35"),
 ("FG_EMPTY", "HIGH", "No F/G fills anywhere while progress has done>0.", "Fill-path regression (0-trade early return, guard skipping clone, or yellow-timeout storm). Compare progress done records vs xlsx; refill from JSON, then fix the filler forward.", "§56"),
 ("RECORDED_VS_FRESH_DRIFT", "MED", "Manifest recorded gain != fresh vec on the same set (engine cut since fill).", "Re-anchor under the current engine (§32); stamp engine_mixed_chain; never publish chained totals. Deltas across default rounds must never be averaged (§60.6).", "§32 §60.6"),
 ("QUAL_FAIL", "MED", "Promotion-gate failure on one engine leg (TIM/DD/TRADES/SHARPE/GAIN/BH).", "§58 repair loop by failing axis: trades->open ENTRY/REENTRY paths; TIM/DD->exits+reentry filters; gain<BH->quarantine IMPOSSIBLE until repaired. Gates never loosened.", "§51 §58"),
 ("TAB_MISSING", "MED", "One or more of the 13 SWITCH_SHEETS tabs absent.", "Re-clone from TEMPLATE_{venue}_{side}.xlsx; do not hand-add tabs (ONE-script template rule §56).", "§56"),
 ("C_EMPTY", "MED", "No C overrides while progress has done>0.", "BEST-C-FILL regression (ingest guard, parser missing single-value C). Restore BEST->C then refill from JSON.", "§56"),
 ("YELLOW_ZERO", "MED", "First data row has 0 numeric yellows on most tabs.", "Yellow-map gap (token rule) or yellow-timeout storm. Check SKIP-ALARM/flags; yellows are per-switch evidence (§56 R10-R14).", "§56 §60.5"),
 ("FILE_SMALL", "MED", "Matrix file <500KB (suspect partial write).", "Open-verify; if truncated treat as FILE_TRUNCATED and refill from JSON.", "§35"),
 ("NO_PROGRESS", "MED", "Published xlsx has no matching progress JSON entry.", "Orphan final: rebuild or re-derive the C-set from the xlsx C column before trusting the filename number.", "§32"),
 ("NO_XLSX", "MED", "Progress has done>0 but no xlsx exists for the symside.", "0-trade early return before clone, or cleanup deleted it. Refill/clone from JSON (§32 assure path).", "§32"),
 ("H_EMPTY_ALL", "INFO", "No H (LIVE_DELTA) fills: sheet never live-verified.", "Expected before this run. After this run, backfill H/I from results (live gain - cumulative_before) per promoted row.", "§56 R23"),
 ("FILE_VANISHED", "INFO", "File listed then gone during audit (concurrent cleanup).", "No action; provenance note. Re-run audit after fleet quiesces for a stable count.", "§21"),
 ("NPZ_CHANGED_MID_RUN", "MED", "Symbol NPZ changed (mtime/size) between the vec and live legs: npz_keeper refreshed mid-run, so the two legs did not trade identical bars.", "Rerun the entry after NPZ quiesce (or pause npz_keeper pushes to S5 during verify windows). Do not trust this row's parity verdict.", "§2 §43"),
 ("TEMPLATE_DEFAULTS_GATE", "HIGH", "Template group lacks exactly-one YES=bold default: the pilot fail-closed REFUSES to run this template.", "Set the intended default row bold + is_default=YES (exactly one per group); re-run the exact-loader audit. ONE-script template rule (§56) — no hand edits mid-sweep.", "§56"),
]
PB = {p[0]: p for p in PLAYBOOK}


def r4(x):
    try:
        return round(float(x), 4)
    except (TypeError, ValueError):
        return x


def load_inputs():
    man = json.load(open(os.path.join(INDIR, "manifest.json")))
    audit = json.load(open(os.path.join(INDIR, "sheet_audit.json")))
    results = {}
    for p in glob.glob(os.path.join(INDIR, "results", "*.json")):
        try:
            r = json.load(open(p))
            results[r.get("symside", os.path.basename(p).replace(".json", ""))] = r
        except Exception:
            results[os.path.basename(p).replace(".json", "")] = {"symside": os.path.basename(p).replace(".json", ""), "result_unreadable": True}
    return man, audit, results


def sym_of_file(fn):
    m = SYM_RE.match(os.path.basename(fn))
    return f"{m.group(1)}_{m.group(2)}" if m else None


def build_rows(man, audit, results):
    allres = []
    disc = []
    by_sym_files = {}
    try:
        _b = json.load(open(os.path.join(INDIR, "bolds.json")))
        for _cat, _v in (_b.get("violations") or {}).items():
            for _x in _v:
                disc.append({"symside": f"(template {_cat})", "class": "TEMPLATE_DEFAULTS_GATE", "severity": "HIGH", "detail": str(_x)[:200], "vec": "", "live": "", "gap": "", "likely_cause": PB["TEMPLATE_DEFAULTS_GATE"][2], "suggested_fix": PB["TEMPLATE_DEFAULTS_GATE"][3], "bible": PB["TEMPLATE_DEFAULTS_GATE"][4]})
    except OSError:
        pass
    for f in audit["files"]:
        s = sym_of_file(f["file"])
        if s:
            by_sym_files.setdefault(s, []).append(f)

    def add(sym, cls, detail, vec_val="", live_val="", gap=""):
        sev, _cause, fix, ref = PB[cls][1], PB[cls][2], PB[cls][3], PB[cls][4]
        disc.append({"symside": sym, "class": cls, "severity": sev, "detail": detail, "vec": vec_val, "live": live_val, "gap": gap, "likely_cause": PB[cls][2], "suggested_fix": fix, "bible": ref})

    for e in man["entries"]:
        sym = e["symside"]
        r = results.get(sym, {"missing_result": True})
        if r.get("missing_result") or r.get("launcher_timeout_or_crash") or r.get("result_unreadable"):
            add(sym, "LIVE_TIMEOUT_OR_CRASH", f"no usable worker result (timeout/crash/unreadable) file={e['file']}")
            v = {}
            l = {}
            par_ok, par_reason = False, "no result"
        else:
            v = r.get("vec", {}) or {}
            l = r.get("live", {}) or {}
            par_ok = bool((r.get("parity") or {}).get("ok"))
            par_reason = (r.get("parity") or {}).get("reason", "")
            if not v.get("valid"):
                add(sym, "VEC_INVALID", f"vec invalid: {v.get('invalid_reason_full') or v.get('invalid_reason')}", "", "")
            if not l.get("valid"):
                reason = str(l.get("invalid_reason_full") or l.get("invalid_reason") or "")
                if "hard exit" in reason or "timeout" in reason or r.get("live_pending"):
                    add(sym, "LIVE_TIMEOUT_OR_CRASH", f"live {reason} child_rc={l.get('child_rc')}", "", "")
                else:
                    add(sym, "LIVE_INVALID", f"live invalid: {reason}", "", "")
            n0, n1, n2 = r.get("npz_pre_vec"), r.get("npz_pre_live"), r.get("npz_post_live")
            if n0 and n1 and n2 and (n0 != n1 or n1 != n2):
                add(sym, "NPZ_CHANGED_MID_RUN", f"npz stat moved pre_vec={n0} pre_live={n1} post_live={n2}", "", "")
            lt = int(l.get("trades") or 0)
            vt = int(v.get("trades") or 0)
            if (lt == 0) != (vt == 0):
                add(sym, "ZERO_TRADE_SPLIT", par_reason, vt, lt, f"vec {vt} vs live {lt}")
            elif lt > 0 and vt > 0 and not (0.80 <= vt / lt <= 1.25):
                add(sym, "TRADE_RATIO_BREACH", par_reason, vt, lt, round(vt / lt, 3))
            if v.get("valid") and l.get("valid") and lt > 0 and vt > 0:
                lg = float(l.get("gain_pct") or 0)
                vg = float(v.get("gain_pct") or 0)
                if abs(lg - vg) > 0.5 and abs(lg - vg) / max(1e-9, abs(lg)) > 0.15:
                    add(sym, "GAIN_MISMATCH", par_reason, r4(vg), r4(lg), r4(lg - vg))
            rec = e.get("final_gain_fresh_vec")
            if rec is None:
                rec = e.get("cumulative_gain")
            try:
                if rec is not None and v.get("gain_pct") is not None and abs(float(v.get("gain_pct")) - float(rec)) > 1e-6:
                    drift = float(v.get("gain_pct")) - float(rec)
                    flag = " ENGINE_MIXED_CHAIN" if (e.get("engine_mixed_chain") or abs(drift) > 0.5) else ""
                    add(sym, "RECORDED_VS_FRESH_DRIFT", f"recorded {r4(rec)} vs fresh vec {r4(v.get('gain_pct'))}{flag}", r4(v.get("gain_pct")), "", r4(drift))
            except (TypeError, ValueError):
                pass
            try:
                bh = float(e.get("bh")) if e.get("bh") is not None else None
                if bh is not None and v.get("gain_pct") is not None and l.get("gain_pct") is not None and float(v.get("gain_pct")) >= bh > float(l.get("gain_pct")):
                    add(sym, "LIVE_BELOW_BH_WHILE_VEC_BEATS", f"vec {r4(v.get('gain_pct'))}>=BH {r4(bh)} but live {r4(l.get('gain_pct'))}<BH", r4(v.get("gain_pct")), r4(l.get("gain_pct")), r4(float(l.get("gain_pct")) - bh))
            except (TypeError, ValueError):
                pass
            for leg, m in (("vec", v), ("live", l)):
                q = (r.get(f"qual_{leg}") or {})
                for g in ("tim_20_80", "dd_le_30", "trades_ge_30", "sharpe_gt_0p2", "gain_ge_0", "gain_ge_bh"):
                    if g in q and not q[g]:
                        key = {"tim_20_80": f"TIM={v.get('tim_pct') if leg=='vec' else l.get('tim_pct')}", "dd_le_30": f"DD={v.get('max_dd_pct') if leg=='vec' else l.get('max_dd_pct')}", "trades_ge_30": f"trades={vt if leg=='vec' else lt}", "sharpe_gt_0p2": f"sh={v.get('pool_sharpe') if leg=='vec' else l.get('pool_sharpe')}", "gain_ge_0": f"gain={v.get('gain_pct') if leg=='vec' else l.get('gain_pct')}", "gain_ge_bh": f"gain vs BH {e.get('bh')}"}[g]
                        add(sym, "QUAL_FAIL", f"{leg} gate {g} fail ({key})", "", "")
        venue = "CRYPTO" if sym.split("_")[0].upper().endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI")) else "STOCKS"
        side = "SHORT" if sym.endswith("_SHORT") else "LONG"
        vg = v.get("gain_pct")
        lg = l.get("gain_pct")
        allres.append({"symside": sym, "venue": venue, "side": side, "progress_file": e.get("file"), "done_n": e.get("done_n"), "ov_n": e.get("overrides_n"), "bh": r4(e.get("bh")), "recorded_cum": r4(e.get("cumulative_gain")), "recorded_fresh": r4(e.get("final_gain_fresh_vec")), "vec_gain": r4(vg), "vec_trades": v.get("trades"), "vec_tim": r4(v.get("tim_pct")), "vec_dd": r4(v.get("max_dd_pct")), "vec_sharpe": r4(v.get("pool_sharpe")), "vec_valid": v.get("valid"), "live_gain": r4(lg), "live_trades": l.get("trades"), "live_tim": r4(l.get("tim_pct")), "live_dd": r4(l.get("max_dd_pct")), "live_sharpe": r4(l.get("pool_sharpe")), "live_valid": l.get("valid"), "parity_ok": par_ok, "parity_reason": par_reason, "gain_gap": (r4(float(lg) - float(vg)) if isinstance(vg, (int, float)) and isinstance(lg, (int, float)) else ""), "trade_ratio": (round(float(v.get("trades") or 0) / float(l.get("trades") or 0), 3) if (l.get("trades") or 0) else ""), "vec_s": r.get("vec_s"), "live_s": r.get("live_s"), "force_real": r.get("force_real")})
    for f in audit["files"]:
        fn = f["file"]
        sym = sym_of_file(fn) or "(unparsed)"
        if f.get("audit_error"):
            add(sym, "FILE_VANISHED", f"{fn}: {f['audit_error']}")
            continue
        if f.get("zip_error") or f.get("open_error"):
            add(sym, "FILE_BADZIP", f"{fn}: {f.get('zip_error') or f.get('open_error')}")
            continue
        if (f.get("zip_entries") or 99) < 10:
            add(sym, "FILE_TRUNCATED", f"{fn}: zip_entries={f.get('zip_entries')}")
        if (f.get("size_kb") or 0) < 500:
            add(sym, "FILE_SMALL", f"{fn}: size_kb={f.get('size_kb')}")
        if f.get("tabs_missing"):
            add(sym, "TAB_MISSING", f"{fn}: missing {','.join(f['tabs_missing'])}")
        tabs = f.get("tabs", {})
        if tabs:
            first = next((t for t in ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES") if t in tabs), next(iter(tabs)))
            e3 = tabs[first].get("e3")
            tot_c = sum(t.get("c", 0) for t in tabs.values())
            tot_f = sum(t.get("f", 0) for t in tabs.values())
            tot_g = sum(t.get("g", 0) for t in tabs.values())
            tot_h = sum(t.get("h", 0) for t in tabs.values())
            tot_rows = sum(t.get("rows", 0) for t in tabs.values())
            me = next((x for x in man["entries"] if x["symside"] == sym), {})
            has_done = (me.get("done_n") or 0) > 0
            if not isinstance(e3, (int, float)) and has_done:
                add(sym, "E3_BASELINE_BAD", f"{fn} {first}!E3={str(e3)[:40]} (done={me.get('done_n')})")
            if tot_c == 0 and has_done:
                add(sym, "C_EMPTY", f"{fn}: C fills 0/{tot_rows} rows (done={me.get('done_n')})")
            if tot_f == 0 and tot_g == 0 and has_done:
                add(sym, "FG_EMPTY", f"{fn}: F+G fills 0/{tot_rows} rows (done={me.get('done_n')})")
            if tot_h == 0:
                add(sym, "H_EMPTY_ALL", f"{fn}: H fills 0/{tot_rows} rows (never live-verified)")
            y_tabs = sum(1 for t in tabs.values() if t.get("rows", 0) > 0 and (t.get("y_num") or 0) == 0)
            if tabs and y_tabs > len(tabs) / 2:
                add(sym, "YELLOW_ZERO", f"{fn}: {y_tabs}/{len(tabs)} tabs with 0 numeric yellows on first row")
        if f.get("published"):
            me = next((x for x in man["entries"] if x["symside"] == sym), None)
            if me is None:
                add(sym, "NO_PROGRESS", f"{fn}: published but no progress entry")
            elif f.get("fname_gain") is not None:
                ref = me.get("final_gain_fresh_vec")
                if ref is None:
                    ref = me.get("cumulative_gain")
                try:
                    if ref is not None and abs(float(f["fname_gain"]) - float(ref)) > 0.5:
                        add(sym, "FILENAME_GAIN_MISMATCH", f"{fn}: filename gain {f['fname_gain']} vs progress {r4(ref)}", r4(ref), "", round(float(f["fname_gain"]) - float(ref), 4))
                except (TypeError, ValueError):
                    pass
    for e in man["entries"]:
        if (e.get("done_n") or 0) > 0 and e["symside"] not in by_sym_files:
            add(e["symside"], "NO_XLSX", f"progress {e['file']} done={e['done_n']} but no xlsx for symside")
    disc.sort(key=lambda d: (SEV_ORDER.get(d["severity"], 9), d["symside"], d["class"]))
    return allres, disc, by_sym_files


def style_header(ws, ncols):
    from openpyxl.styles import Font, PatternFill
    for c in range(1, ncols + 1):
        cell = ws.cell(row=1, column=c)
        cell.font = Font(name="Arial", size=10, bold=True, color="FFFFFF")
        cell.fill = PatternFill(start_color="1F4E78", end_color="1F4E78", fill_type="solid")
    ws.freeze_panes = "A2"
    ws.auto_filter.ref = ws.dimensions


def sev_fill(sev):
    from openpyxl.styles import PatternFill
    return {"P0": PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid"), "HIGH": PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"), "MED": PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"), "INFO": PatternFill(start_color="D9D9D9", end_color="D9D9D9", fill_type="solid")}.get(sev)


def write_book(man, audit, results, allres, disc):
    import openpyxl
    from openpyxl.styles import Alignment
    from openpyxl.utils import get_column_letter
    wb = openpyxl.Workbook()
    wrap = Alignment(vertical="top", wrap_text=True)
    def widths(ws, w):
        for i, x in enumerate(w, 1):
            ws.column_dimensions[get_column_letter(i)].width = x
    ws = wb.active
    ws.title = "README"
    readme = ["S5 LIVE VERIFICATION vs FINAL RESULTS (run s5_verify_20261003)", "", "METHOD: every lifecycle final set (cumulative_overrides) evaluated on S5 through BOTH engines on the same frozen 30d window:", "  vec  = tools.opt.v12_pilot.prepare_batch + evaluate_prepared_sanitized (exactly the pilot final-fresh re-anchor)", "  live = backtest_v12_engine.run_one (real process_position path, _assert_live_path guarded; pilot-identical bare call incl. AUTO_VECTOR union)", "PARITY GATE (bible §43): trade-count ratio 0.80..1.25 AND gain within 0.5pp/15%. QUAL GATES (§51/§58): TIM 20-80, DD<=30, trades>=30, pool_sharpe>0.2, gain>=0, gain>=BH.", "DIAGNOSTIC ARM: a per-cat_side sample ALSO runs with V8_FORCE_REAL=1 (AUTO_VECTOR union skipped) to quantify the live-only base confound.", "SHEETS: SUMMARY counts; ALL_RESULTS one row per symside; DISCREPANCIES one row per instance (a symside can repeat); PARITY_FAILS/QUAL_GATES slices; SHEET_FILES per-xlsx audit; FIX_PLAYBOOK per class; PROVENANCE hashes + fence record.", "SEVERITY: P0 = NO-LIES violation risk (blocks everything); HIGH = blocks promotion; MED = §58 repair loop; INFO = expected/provenance.", "Every number is a recorded measurement. Nothing sampled, nothing extrapolated."]
    for i, line in enumerate(readme, 1):
        ws.cell(row=i, column=1).value = line
    ws.column_dimensions["A"].width = 150
    ws2 = wb.create_sheet("SUMMARY")
    from collections import Counter
    cc = Counter((d["severity"], d["class"]) for d in disc)
    par_ok = sum(1 for r in allres if r["parity_ok"])
    ws2.append(["metric", "value"])
    for k, v in [("symsides", len(allres)), ("results_received", sum(1 for r in allres if r["vec_s"] is not None)), ("parity_ok", par_ok), ("parity_fail", len(allres) - par_ok), ("discrepancy_rows", len(disc)), ("xlsx_audited", audit["n"]), ("run_utc", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))]:
        ws2.append([k, v])
    ws2.append([])
    ws2.append(["severity", "class", "count"])
    for (sev, cls), n in sorted(cc.items(), key=lambda x: (SEV_ORDER.get(x[0][0], 9), x[0][1])):
        ws2.append([sev, cls, n])
    widths(ws2, [28, 34, 12])
    cols = ["symside", "venue", "side", "progress_file", "done_n", "ov_n", "bh", "recorded_cum", "recorded_fresh", "vec_gain", "vec_trades", "vec_tim", "vec_dd", "vec_sharpe", "vec_valid", "live_gain", "live_trades", "live_tim", "live_dd", "live_sharpe", "live_valid", "parity_ok", "parity_reason", "gain_gap_live_minus_vec", "trade_ratio_vec_live", "vec_s", "live_s", "force_real"]
    ws3 = wb.create_sheet("ALL_RESULTS")
    ws3.append(cols)
    for r in allres:
        ws3.append([r.get(c, "") if r.get(c) is not None else "" for c in cols])
        if not r["parity_ok"]:
            f = sev_fill("HIGH")
            ws3.cell(row=ws3.max_row, column=cols.index("parity_ok") + 1).fill = f
    style_header(ws3, len(cols))
    widths(ws3, [22, 9, 7, 34, 8, 7, 10, 12, 12, 10, 10, 9, 9, 10, 9, 10, 10, 9, 9, 10, 9, 9, 44, 12, 12, 8, 8, 9])
    dcols = ["symside", "severity", "class", "detail", "vec", "live", "gap", "likely_cause", "suggested_fix", "bible"]
    ws4 = wb.create_sheet("DISCREPANCIES")
    ws4.append(dcols)
    for d in disc:
        ws4.append([d.get(c, "") if d.get(c) is not None else "" for c in dcols])
        f = sev_fill(d["severity"])
        if f:
            for x in (2, 3):
                ws4.cell(row=ws4.max_row, column=x).fill = f
        for x in (4, 8, 9):
            ws4.cell(row=ws4.max_row, column=x).alignment = wrap
    style_header(ws4, len(dcols))
    widths(ws4, [22, 9, 30, 60, 12, 12, 12, 50, 80, 14])
    ws5 = wb.create_sheet("PARITY_FAILS")
    ws5.append(cols)
    for r in allres:
        if not r["parity_ok"]:
            ws5.append([r.get(c, "") if r.get(c) is not None else "" for c in cols])
    style_header(ws5, len(cols))
    widths(ws5, [22, 9, 7, 34, 8, 7, 10, 12, 12, 10, 10, 9, 9, 10, 9, 10, 10, 9, 9, 10, 9, 9, 44, 12, 12, 8, 8, 9])
    ws6 = wb.create_sheet("QUAL_GATES")
    ws6.append(["symside", "leg", "valid", "tim_20_80", "dd_le_30", "trades_ge_30", "sharpe_gt_0p2", "gain_ge_0", "gain_ge_bh"])
    for sym in sorted(results):
        r = results[sym]
        for leg in ("vec", "live"):
            q = (r.get(f"qual_{leg}") or {})
            ws6.append([sym, leg, (r.get(leg) or {}).get("valid"), q.get("tim_20_80"), q.get("dd_le_30"), q.get("trades_ge_30"), q.get("sharpe_gt_0p2"), q.get("gain_ge_0"), q.get("gain_ge_bh")])
    style_header(ws6, 9)
    widths(ws6, [22, 7, 8, 11, 10, 13, 12, 10, 11])
    ws7 = wb.create_sheet("SHEET_FILES")
    ws7.append(["file", "symside", "published", "fname_bh", "fname_gain", "size_kb", "zip_entries", "tabs_present_n", "tabs_missing", "first_tab_E3", "rows", "C", "F", "G", "H", "I", "metrics_B2", "error"])
    for f in audit["files"]:
        tabs = f.get("tabs", {})
        tot = {k: sum(t.get(k, 0) for t in tabs.values()) for k in ("rows", "c", "f", "g", "h", "i")}
        first = next((t for t in ("STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES") if t in tabs), (next(iter(tabs)) if tabs else None))
        ws7.append([f["file"], sym_of_file(f["file"]), f.get("published"), f.get("fname_bh"), f.get("fname_gain"), f.get("size_kb"), f.get("zip_entries"), len(f.get("tabs_present", [])), ",".join(f.get("tabs_missing", [])), (tabs[first].get("e3") if first else ""), tot["rows"], tot["c"], tot["f"], tot["g"], tot["h"], tot["i"], f.get("metrics_b2"), (f.get("zip_error") or f.get("open_error") or f.get("audit_error") or "")[:80]])
    style_header(ws7, 18)
    widths(ws7, [52, 22, 9, 10, 10, 9, 10, 10, 24, 14, 9, 8, 8, 8, 8, 8, 12, 30])
    ws8 = wb.create_sheet("FIX_PLAYBOOK")
    ws8.append(["class", "severity", "likely_cause", "suggested_fix", "bible"])
    for cls, sev, cause, fix, ref in PLAYBOOK:
        ws8.append([cls, sev, cause, fix, ref])
        f = sev_fill(sev)
        if f:
            ws8.cell(row=ws8.max_row, column=2).fill = f
        for x in (3, 4):
            ws8.cell(row=ws8.max_row, column=x).alignment = wrap
    style_header(ws8, 5)
    widths(ws8, [30, 9, 60, 90, 12])
    ws9 = wb.create_sheet("PROVENANCE")
    ws9.append(["key", "value"])
    prov = [("manifest_entries", man["n_entries"]), ("manifest_dedup_dropped", man.get("n_dedup_dropped")), ("mac_git_head", man.get("mac_git_head")), ("s5_engine_note", "S5 runs the last locked deploy; md5 per result row in JSON"), ("fence_record", "S5: killed row365 driver.sh:91578 + herd + row365/fz workers; crons fenced (backup ~/v15_s5verify_20261003/fence/crontab.before); S1 scheduler s5 entry removed (backup ~/v15_s5verify_fence_fleet_hosts_final.before_20261003.json)"), ("sheet_audit_note", f"audited {audit['n']} xlsx at run time; dir listed 1267 at 06:08Z (concurrent Mac cleanup by peer session in flight)")]
    for k, v in (list((man.get("mac_md5") or {}).items()) + prov):
        ws9.append([k, v])
    widths(ws9, [30, 120])
    for wsx in (ws9, ws2):
        wsx.freeze_panes = "A2"
    wb.save(OUT)
    print(f"discrepancies={len(disc)} allres={len(allres)} -> {OUT}")


def main():
    man, audit, results = load_inputs()
    allres, disc, _ = build_rows(man, audit, results)
    write_book(man, audit, results, allres, disc)


if __name__ == "__main__":
    sys.exit(main())
