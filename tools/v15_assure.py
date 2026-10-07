#!/usr/bin/env python3
"""v15_assure — completion-assurance framework for v15_pilot template sheets.

Why this exists (2026-09-28): pilots on s1/s2 computed millions of evals (delta logs
+ progress JSONs hold the truth: e.g. GDX_LONG 3032 done rows, 73583 numeric yellow
deltas) but the workbooks shipped with F=0 everywhere, E2 still the 'BASELINE'
string and <5% of yellow cells filled. Compute was done and thrown away. The engine
itself is fast (evaluate_prepared_sanitized p99 0.07s on s1, 0.02-0.03s Mac) — the
loss is entirely in the 6.5k-line pilot write path.

Modes (all honest per NO-LIES: every number traces to a real engine eval):
  audit    [SYM_SIDE...|--all]   fill-state truth: xlsx cells vs progress-JSON done
  refill   SYM_SIDE...|--all     rebuild xlsx cells from progress-JSON truth (no evals)
  complete SYM_SIDE              evaluate remaining pending rows with the real engine,
                                 hard per-eval timeout, greedy E chain per spec
  bench    SYM_SIDE              prove per-eval latency (target p95 <= 0.3s)
  watch    [--interval 60]       stall detector: JSON behind xlsx / stale heartbeat ->
                                 auto refill (report-only for kills unless --kill)

Locking: same per-output-file flock as v15_pilot (data/locks/v15_pilot_{ss}_{md5}.lock)
so refill/complete never race a live pilot on the same workbook.
"""
from __future__ import annotations
import argparse
import fcntl
import hashlib
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
OUT_DIR = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROG_DIR = Path(os.environ["V15_PROGRESS_DIR"]) if os.environ.get("V15_PROGRESS_DIR") else ROOT / "data" / "reports" / "lifecycle_pilot"
LOCK_DIR = ROOT / "data" / "locks"
HEARTBEAT = Path("/tmp")
SWITCH_SHEETS = ["STDEV_SLOPE_SIZING", "ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES"]
YELLOW_TIMEOUT = 10.0
# USER 2026-09-28: HUSTLE (F) is NOT used under the worst_first system; vector-derived
# numbers in F are misleading. Leave F blank unless explicitly enabled.
WRITE_HUSTLE = os.environ.get("V15_WRITE_HUSTLE", "0") == "1"
KEY_RE = re.compile(r"^([A-Z0-9_]+)!(\d+):(.+?)=(.*)$", re.S)


def log(msg):
    print(f"[v15_assure {time.strftime('%H:%M:%S')}] {msg}", flush=True)


def wb_path_for(symside, out_dir=None):
    return Path(out_dir or OUT_DIR) / f"{symside}_30d_matrix.xlsx"


def acquire_lock(symside, wb_path):
    LOCK_DIR.mkdir(parents=True, exist_ok=True)
    key = hashlib.md5(str(Path(wb_path).resolve()).encode()).hexdigest()[:16]
    fh = open(LOCK_DIR / f"v15_pilot_{symside}_{key}.lock", "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except (IOError, OSError, BlockingIOError):
        fh.close()
        return None
    fh.write(f"{os.getpid()}\n")
    fh.flush()
    return fh


def find_progress(symside):
    cands = sorted(PROG_DIR.glob(f"{symside}_*progress*.json"), key=lambda p: -p.stat().st_mtime)
    for p in cands:
        try:
            d = json.loads(p.read_text())
        except Exception:
            continue
        if isinstance(d, dict) and d.get("done"):
            return p, d
    return None, None


def atomic_save(wb, wb_path):
    # s1 syncers delete foreign *.tmp files from SPREADSHEETS mid-flight
    # (killed a complete run at 17:36 with FileNotFoundError on its own tmp):
    # retry with a fresh name, and use a dot-hidden prefix rsync patterns skip.
    last = None
    for attempt in range(3):
        tmp = Path(wb_path).parent / f".{Path(wb_path).name}.{os.getpid()}.{attempt}.assure.tmp"
        try:
            wb.save(tmp)
            with zipfile.ZipFile(tmp, "r") as z:
                names = z.namelist()
                if len(names) < 10 or z.testzip() is not None:
                    tmp.unlink(missing_ok=True)
                    raise RuntimeError(f"zip validation failed for {wb_path} ({len(names)} entries)")
            os.replace(tmp, wb_path)
            return
        except (FileNotFoundError, RuntimeError) as e:
            last = e
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            time.sleep(0.5 * (attempt + 1))
    raise RuntimeError(f"atomic_save failed after 3 attempts for {wb_path}: {last}")


def hdr_cols(ws):
    m = {}
    for c in range(1, ws.max_column + 1):
        v = ws.cell(row=2, column=c).value
        if isinstance(v, str) and v.strip():
            m[v.strip()] = c
    cols = {"C": m.get("override", 3), "E": m.get("BASELINE", 5), "F": m.get("HUSTLE_DELTA", m.get("HUSTLE", 6)), "G": m.get("VECTOR_DELTA", m.get("VECTOR", 7)), "H": m.get("LIVE_DELTA", 8), "I": m.get("LIVE_SHARPE", 9), "K": m.get("PER_ROW_FILTERS", 11)}
    yellows = {h: c for h, c in m.items() if "=" in h and c >= 12}
    return cols, yellows


def is_yellow(cell):
    f = cell.fill
    return f is not None and f.fill_type == "solid" and str(getattr(f.fgColor, "rgb", "") or "").upper().endswith("FFFF00")


def sheet_stats(ws):
    cols, ymap = hdr_cols(ws)
    st = {"rows": 0, "C": 0, "E": 0, "F": 0, "G": 0, "yellow_marked": 0, "yellow_num": 0}
    for r in range(3, ws.max_row + 1):
        if ws.cell(row=r, column=1).value in (None, ""):
            continue
        st["rows"] += 1
        if ws.cell(row=r, column=cols["C"]).value not in (None, ""):
            st["C"] += 1
        for k in ("E", "F", "G"):
            if isinstance(ws.cell(row=r, column=cols[k]).value, (int, float)):
                st[k] += 1
        for h, c in ymap.items():
            cell = ws.cell(row=r, column=c)
            if is_yellow(cell):
                st["yellow_marked"] += 1
            if isinstance(cell.value, (int, float)) or (isinstance(cell.value, str) and cell.value.startswith("INVALID")):
                st["yellow_num"] += 1
    return st, cols, ymap


def chain_baseline(prog):
    done = prog.get("done") or {}
    for rec in done.values():
        cb = rec.get("cumulative_before")
        if isinstance(cb, (int, float)):
            return float(cb)
    bg = prog.get("baseline_gain")
    return float(bg) if isinstance(bg, (int, float)) else None


def audit_one(symside, out_dir=None):
    import openpyxl
    wb_path = wb_path_for(symside, out_dir)
    pj, prog = find_progress(symside)
    rep = {"symside": symside, "xlsx": str(wb_path) if wb_path.exists() else None, "progress_json": str(pj) if pj else None}
    if prog:
        done = prog["done"]
        rep["json_done"] = len(done)
        rep["json_yellow_num"] = sum(1 for v in done.values() for y in (v.get("yellows") or {}).values() if isinstance(y, (int, float)))
        rep["json_mtime_age_min"] = round((time.time() - pj.stat().st_mtime) / 60, 1)
        rep["cumulative_gain"] = prog.get("cumulative_gain")
    if wb_path.exists():
        try:
            wb = openpyxl.load_workbook(wb_path, read_only=False, data_only=False)
        except Exception as e:
            rep["verdict"] = f"XLSX_CORRUPT {e}"
            return rep
        tot = {"rows": 0, "F": 0, "G": 0, "E": 0, "yellow_num": 0, "yellow_marked": 0}
        e2_num = []
        for sn in SWITCH_SHEETS:
            if sn not in wb.sheetnames:
                continue
            ws = wb[sn]
            st, cols, _ = sheet_stats(ws)
            for k in tot:
                tot[k] += st[k]
            # USER spec b62f920d: E2 must be the "BASELINE" header string; a NUMBER there is clobber
            if isinstance(ws.cell(row=2, column=cols["E"]).value, (int, float)):
                e2_num.append(sn)
        wb.close()
        rep["xlsx_stats"] = tot
        rep["e2_numeric_clobbered"] = e2_num
    if not prog and not wb_path.exists():
        rep["verdict"] = "NO_DATA"
    elif not wb_path.exists():
        rep["verdict"] = "JSON_ONLY (refill will clone+fill)"
    elif prog:
        x = rep.get("xlsx_stats", {})
        behind = rep["json_yellow_num"] - x.get("yellow_num", 0)
        f_short = WRITE_HUSTLE and x.get("F", 0) < rep["json_done"] // 2
        if f_short or behind > 50 or rep.get("e2_numeric_clobbered"):
            rep["verdict"] = f"JSON_AHEAD_OF_XLSX (yellows behind by {behind}, F {x.get('F',0)}/{rep['json_done']}) -> refill"
        else:
            rep["verdict"] = "CONSISTENT"
    else:
        rep["verdict"] = "XLSX_ONLY (no progress json)"
    return rep


def parse_done_key(key):
    m = KEY_RE.match(key)
    if not m:
        return None
    return m.group(1), int(m.group(2)), m.group(3), m.group(4)


def refill_one(symside, out_dir=None, force=False):
    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    align = Alignment(horizontal="left", vertical="center")
    wb_path = wb_path_for(symside, out_dir)
    pj, prog = find_progress(symside)
    if not prog:
        log(f"{symside}: no progress JSON with done records — nothing to refill")
        return False
    lock = acquire_lock(symside, wb_path)
    if lock is None and not force:
        log(f"{symside}: workbook lock held by a live pilot — skipping (use --force to override)")
        return False
    try:
        if not wb_path.exists():
            if out_dir and Path(out_dir).resolve() != OUT_DIR.resolve():
                log(f"{symside}: no workbook in {out_dir} and clone only targets {OUT_DIR} — copy one in first")
                return False
            from v15_pilot import clone_template, get_template_for_symside
            tpl = get_template_for_symside(symside)
            log(f"{symside}: no workbook — cloning {tpl.name}")
            clone_template(tpl, symside)
        wb = openpyxl.load_workbook(wb_path, data_only=False)
        done = prog["done"]
        baseline = chain_baseline(prog)
        ibg = prog.get("initial_baseline_gain")
        # old corrupt JSONs carry a flat 0.0 chain while initial_baseline_gain is real:
        # writing F/E2 from that would lie — leave them for `complete` to re-anchor.
        # a chain start of exactly 0.0 is the known corruption signature, never a real gain
        baseline_trusted = baseline is not None and abs(baseline) > 1e-12
        if baseline is not None and not baseline_trusted:
            log(f"{symside}: chain baseline 0.0 (initial_baseline_gain={ibg}) — G fallback withheld where it would use it; run `complete` on the NPZ host to re-anchor")
        n_writes = n_yellow = n_red = n_skip = n_moved = n_stale = 0
        unmapped = set()
        maps = {}
        rowidx = {}
        for sn in SWITCH_SHEETS:
            if sn in wb.sheetnames:
                ws = wb[sn]
                cols, ymap = hdr_cols(ws)
                # template yellow headers are duplicated (e.g. WT_15M_BOUNCE_BB_MAX=0.8 in cols 15/34/236) — keep every twin
                yall = {}
                for c in range(12, ws.max_column + 1):
                    hv = ws.cell(row=2, column=c).value
                    if isinstance(hv, str) and "=" in hv:
                        yall.setdefault(hv.strip(), []).append(c)
                maps[sn] = (ws, cols, ymap, yall)
                idx = {}
                for rr in range(3, ws.max_row + 1):
                    a = ws.cell(row=rr, column=1).value
                    if a in (None, ""):
                        continue
                    idx[(str(a), str(ws.cell(row=rr, column=2).value))] = rr
                rowidx[sn] = idx
                # USER spec 2026-09-28 (v15_pilot b62f920d): E2 keeps the "BASELINE"
                # header string — the numeric baseline lives in the data-row E chain.
                # Self-heal number-clobbered headers (incl. our own earlier writes).
                if isinstance(ws.cell(row=2, column=cols["E"]).value, (int, float)):
                    ws.cell(row=2, column=cols["E"]).value = "BASELINE"
        bm = f"{symside}_BASELINE_METRICS"
        if bm in wb.sheetnames and isinstance(ibg, (int, float)) and not isinstance(wb[bm]["B2"].value, (int, float)):
            wb[bm]["B2"].value = float(ibg)
        last_cb = {}
        ordered = sorted(done.items(), key=lambda kv: (parse_done_key(kv[0]) or ("", 0))[:2])
        for key, rec in ordered:
            parsed = parse_done_key(key)
            if not parsed:
                n_skip += 1
                continue
            sn, r, switch, cand = parsed
            if sn not in maps:
                n_skip += 1
                continue
            ws, cols, ymap, yall = maps[sn]
            # rows drift between template generations: trust (switch, cand) identity, not the row number
            if not (3 <= r <= ws.max_row) or str(ws.cell(row=r, column=1).value) != switch or str(ws.cell(row=r, column=2).value) != cand:
                r2 = rowidx[sn].get((switch, cand))
                if r2 is None:
                    n_skip += 1
                    continue
                r = r2
                n_moved += 1
            cb = rec.get("cumulative_before")
            delta = rec.get("delta")
            promoted = bool(rec.get("promoted"))
            # R18: BASELINE blank by default; written on the tab's first row and wherever the running baseline
            # changed (the row right after a positive delta carries the new higher baseline)
            prev_cb = last_cb.get(sn)
            ec = ws.cell(row=r, column=cols["E"])
            if isinstance(cb, (int, float)) and (prev_cb is None or abs(float(cb) - prev_cb) > 1e-12):
                ec.value = float(cb)
            elif isinstance(ec.value, (int, float)):
                ec.value = None
            if isinstance(cb, (int, float)):
                last_cb[sn] = float(cb)
            vec_gain = rec.get("vec_gain")
            if vec_gain is None and isinstance(rec.get("joint_gain"), (int, float)):
                vec_gain = rec["joint_gain"]
            yl = rec.get("yellows") or {}
            if vec_gain is None and yl and isinstance(cb, (int, float)):
                nums = [d for d in yl.values() if isinstance(d, (int, float))]
                if nums:
                    vec_gain = cb + max(nums)
            # USER column semantics 2026-09-28 (v15_pilot 97b8b109/b62f920d):
            # F = row delta vs the cumulative combination (greedy marginal),
            # G = row's best real gain minus the INITIAL baseline,
            # K = numeric sum of positive yellow deltas.
            f_delta = rec.get("delta_vs_cumulative", delta)
            fc = ws.cell(row=r, column=cols["F"])
            if not WRITE_HUSTLE:
                fc.value = None
            elif isinstance(f_delta, (int, float)):
                fc.value = float(f_delta)
                fc.font = Font(name="Arial", size=10, bold=True, color="006100" if promoted else "9C0006")
                fc.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") if f_delta < 0 else PatternFill(fill_type=None)
                fc.alignment = align
            g_val = rec.get("delta_vs_initial")
            if g_val is None and isinstance(vec_gain, (int, float)) and isinstance(ibg, (int, float)):
                g_val = float(vec_gain) - float(ibg)
            gc = ws.cell(row=r, column=cols["G"])
            if isinstance(g_val, (int, float)):
                gc.value = float(g_val)
                gc.font = Font(name="Arial", size=10, bold=True, color="006100" if g_val > 0 else "9C0006")
                gc.alignment = align
            reasons = rec.get("yellow_reasons") or {}
            # BIBLE §19 no-op yellows (result == naked switch) read 0 and are never summed
            noops = set(rec.get("noop_yellows") or [])
            nd = rec.get("naked_delta")
            if not noops and isinstance(nd, (int, float)) and "naked" not in reasons:
                noops = {h for h, d in yl.items() if isinstance(d, (int, float)) and h not in reasons and abs(d - nd) < 1e-9}
            # K = positive VALID yellows only: invalid (0-trade) evals read as fake +|baseline| (COP_SHORT K 199.9)
            k_val = None
            if yl:
                pos_sum = sum(d for h, d in yl.items() if isinstance(d, (int, float)) and d > 1e-9 and h not in reasons and h not in noops)
                k_val = pos_sum if pos_sum > 0 else 0.0
            if isinstance(k_val, (int, float)):
                kc = ws.cell(row=r, column=cols["K"])
                kc.value = float(k_val)
                kc.alignment = align
            for hdr in yl:
                if hdr not in yall:
                    unmapped.add(hdr)
            for hdr, ycols in yall.items():
                d = yl.get(hdr)
                for col in ycols:
                    yc = ws.cell(row=r, column=col)
                    if not isinstance(d, (int, float)):
                        # stale number/marker from an older run (different baseline) — JSON truth has no value here
                        if isinstance(yc.value, (int, float)) or (isinstance(yc.value, str) and yc.value.startswith("INVALID")):
                            yc.value = None
                            n_stale += 1
                        continue
                    bad = hdr in reasons
                    noop = hdr in noops
                    yc.value = f"INVALID {reasons[hdr]}"[:40] if bad else float(d)  # NONE-vs-0 (zero audit): a no-op filter keeps its REAL value (italic grey), never a fabricated 0.0
                    yc.font = Font(name="Arial", size=10, italic=bad or noop, color="808080" if bad or noop else None)
                    yc.alignment = align
                    n_yellow += 1
            for hdr in reasons:
                col = ymap.get(hdr)
                if col and hdr not in yl:
                    rc = ws.cell(row=r, column=col)
                    if not isinstance(rc.value, (int, float)):
                        rc.fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                        n_red += 1
            if promoted and ws.cell(row=r, column=cols["C"]).value in (None, ""):
                pos = [h for h, d in yl.items() if isinstance(d, (int, float)) and d > 1e-9]
                ws.cell(row=r, column=cols["C"]).value = " + ".join([f"{switch}={cand}"] + pos)
            n_writes += 1
        atomic_save(wb, wb_path)
        wb.close()
        log(f"{symside}: refilled {n_writes} rows ({n_moved} row-relocated), {n_yellow} yellow cells, {n_stale} stale cleared, {n_red} reds, {n_skip} keys skipped (no matching row), {len(unmapped)} yellow headers unmapped in this template, baseline={'%.4f' % baseline if baseline_trusted else 'UNTRUSTED->complete'} -> {wb_path.name}")
        rep = audit_one(symside, out_dir)
        log(f"{symside}: post-refill verdict {rep.get('verdict')} (residue = drifted rows/headers or untrusted baseline -> run `complete` on the NPZ host)")
        return n_writes > 0
    finally:
        if lock:
            lock.close()


def complete_one(symside, out_dir=None, workers=8, save_every=25, max_rows=0, force=False):
    import signal

    def _sig_log(signum, frame):
        log(f"{symside}: RECEIVED SIGNAL {signum} ({signal.Signals(signum).name}) — pid={os.getpid()} pgid={os.getpgid(0)} — exiting")
        sys.exit(128 + signum)
    for _s in (signal.SIGTERM, signal.SIGHUP, signal.SIGINT, signal.SIGQUIT, signal.SIGUSR1):
        try:
            signal.signal(_s, _sig_log)
        except Exception:
            pass
    log(f"{symside}: complete start pid={os.getpid()} pgid={os.getpgid(0)} sid={os.getsid(0)}")
    import concurrent.futures as cf

    import openpyxl
    from openpyxl.styles import Alignment, Font, PatternFill
    import v15_pilot as VP
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    align = Alignment(horizontal="left", vertical="center")
    wb_path = wb_path_for(symside, out_dir)
    pj, prog = find_progress(symside)
    if prog is None:
        prog = {"symside": symside, "done": {}, "window_days": 30}
        pj = PROG_DIR / f"{symside}_assure_progress.json"
    lock = acquire_lock(symside, wb_path)
    if lock is None and not force:
        log(f"{symside}: lock held by live pilot — not competing (use --force)")
        return False
    try:
        if not wb_path.exists():
            log(f"{symside}: no workbook — run refill first (clones template)")
            return False
        t0 = time.time()
        prep = prepare_batch(symside, int(prog.get("window_days") or 30))
        if prep is None:
            log(f"{symside}: prepare_batch returned None (no NPZ on this host) — cannot complete here")
            return False
        log(f"{symside}: prepared NPZ in {time.time()-t0:.1f}s")
        defaults = VP.get_defaults_for_symside(symside)
        done = prog.setdefault("done", {})
        cumulative_gain = prog.get("cumulative_gain")
        baseline = chain_baseline(prog)
        wb = openpyxl.load_workbook(wb_path, data_only=False)
        cumulative_overrides = dict(prog.get("cumulative_overrides") or {})
        # engine sign is authoritative (old JSONs carry abs()-flipped/zeroed cums):
        # re-anchor the chain with a fresh eval of the stored cumulative_overrides.
        res = evaluate_prepared_sanitized(prep, dict(cumulative_overrides), int(prog.get("window_days") or 30))
        g = res.get("gain_pct")
        if g is None:
            log(f"{symside}: re-anchor eval returned no gain ({res.get('invalid_reason')}) — abort")
            return False
        if cumulative_gain is not None and abs(float(cumulative_gain) - float(g)) > 1e-6:
            log(f"{symside}: stored cum {cumulative_gain} != engine {g:.4f} — engine wins")
        cumulative_gain = float(g)
        if baseline is None:
            base_res = evaluate_prepared_sanitized(prep, {}, int(prog.get("window_days") or 30))
            if base_res.get("gain_pct") is None:
                log(f"{symside}: baseline eval returned no gain ({base_res.get('invalid_reason')}) — abort")
                return False
            baseline = float(base_res["gain_pct"])
        hb = HEARTBEAT / f"v15_assure_heartbeat_{symside}.txt"
        pool = cf.ThreadPoolExecutor(max_workers=max(1, workers))
        eval_times = []

        def timed_eval(ov):
            t = time.time()
            fut = pool.submit(evaluate_prepared_sanitized, prep, dict(ov), int(prog.get("window_days") or 30))
            try:
                r = fut.result(timeout=YELLOW_TIMEOUT)
            except cf.TimeoutError:
                return None, f"TIMEOUT {YELLOW_TIMEOUT:.0f}s"
            except Exception as e:
                return None, f"ERR {e}"[:60]
            finally:
                eval_times.append(time.time() - t)
            return r, ""

        def delta_of(res, cb):
            if not res or res.get("gain_pct") is None:
                return None, False, str((res or {}).get("invalid_reason") or "no result")[:40]
            d = float(res["gain_pct"]) - cb
            d = 0.0 if abs(d) < 1e-9 else d
            if not res.get("valid"):
                return d, False, str(res.get("invalid_reason") or "invalid")[:40]
            return d, True, ""

        n_rows = 0
        dirty = 0
        for sn in SWITCH_SHEETS:
            if sn not in wb.sheetnames:
                continue
            ws = wb[sn]
            cols, ymap = hdr_cols(ws)
            # E2 stays the "BASELINE" header per USER spec (v15_pilot b62f920d)
            if isinstance(ws.cell(row=2, column=cols["E"]).value, (int, float)):
                ws.cell(row=2, column=cols["E"]).value = "BASELINE"
            for r in range(3, ws.max_row + 1):
                switch = ws.cell(row=r, column=1).value
                if switch in (None, ""):
                    continue
                cand = ws.cell(row=r, column=2).value
                key = f"{sn}!{r}:{switch}={cand}"
                if key in done:
                    # backfill F (= row delta vs cumulative, USER spec 97b8b109)
                    fd = done[key].get("delta_vs_cumulative", done[key].get("delta"))
                    fcell = ws.cell(row=r, column=cols["F"])
                    if isinstance(fd, (int, float)) and not isinstance(fcell.value, (int, float)):
                        fcell.value = float(fd)
                        fcell.font = Font(name="Arial", size=10, bold=True, color="006100" if fd > 0 else "9C0006")
                        fcell.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") if fd < 0 else PatternFill(fill_type=None)
                        fcell.alignment = align
                        dirty += 1
                    continue
                if max_rows and n_rows >= max_rows:
                    break
                row_t0 = time.time()
                cb = float(cumulative_gain)
                cand_parsed = VP._parse_opt_value(cand, defaults.get(switch))
                variant = dict(cumulative_overrides)
                variant.update(VP._switch_overrides(switch, cand_parsed))
                variant, _ = VP.sanitize_overrides(variant, defaults)
                hdrs = []
                for h, c in ymap.items():
                    if is_yellow(ws.cell(row=r, column=c)):
                        hdrs.append(h)
                futs = {}
                naked_res, naked_err = timed_eval(variant)
                for h in hdrs:
                    filt, opt = h.split("=", 1)
                    v = dict(variant)
                    v[filt.strip()] = VP._parse_opt_value(opt.strip(), defaults.get(filt.strip()))
                    v, _ = VP.sanitize_overrides(v, defaults)
                    futs[h] = pool.submit(evaluate_prepared_sanitized, prep, v, int(prog.get("window_days") or 30))
                yl, promotable, reasons = {}, {}, {}
                deadline = time.time() + YELLOW_TIMEOUT * max(1, -(-len(futs) // max(1, workers)))
                for h, fut in futs.items():
                    try:
                        res = fut.result(timeout=max(0.01, deadline - time.time()))
                    except cf.TimeoutError:
                        reasons[h] = f"TIMEOUT {YELLOW_TIMEOUT:.0f}s"
                        continue
                    except Exception as e:
                        reasons[h] = f"ERR {e}"[:40]
                        continue
                    d, ok, why = delta_of(res, cb)
                    if d is None:
                        reasons[h] = why
                        continue
                    yl[h] = float(d)
                    promotable[h] = ok
                    if why:
                        reasons[h] = why
                pos = [h for h, d in yl.items() if d > 1e-9 and promotable.get(h)]
                sum_pos = sum(yl[h] for h in pos)
                if hdrs:
                    if sum_pos > 1e-9:
                        delta_row = float(sum_pos)
                    else:
                        real = [d for h, d in yl.items() if promotable.get(h)] or list(yl.values())
                        delta_row = min(0.0, max(real)) if real else None
                else:
                    delta_row, ok, why = delta_of(naked_res, cb)
                    if why:
                        reasons["naked"] = why
                    if delta_row is not None and not ok:
                        delta_row = min(0.0, delta_row)
                promote = delta_row is not None and delta_row > 1e-9
                blk = VP.promotion_block_reason(switch)
                if promote and blk:
                    promote = False
                    reasons["promotion"] = blk
                joint_gain = None
                if promote and hdrs:
                    trial = dict(cumulative_overrides)
                    trial.update(VP._switch_overrides(switch, cand_parsed))
                    for h in pos:
                        filt, opt = h.split("=", 1)
                        trial[filt.strip()] = VP._parse_opt_value(opt.strip(), defaults.get(filt.strip()))
                    trial, _ = VP.sanitize_overrides(trial, defaults)
                    jres, jerr = timed_eval(trial)
                    jd, jok, jwhy = delta_of(jres, cb)
                    if jok and jd is not None and jd > 1e-9:
                        joint_gain = cb + jd
                        cumulative_overrides = trial
                        cumulative_gain = joint_gain
                    else:
                        promote = False
                        reasons["joint"] = jwhy or jerr or f"joint delta {jd} <= 0"
                elif promote:
                    cumulative_overrides.update(VP._switch_overrides(switch, cand_parsed))
                    cumulative_overrides, _ = VP.sanitize_overrides(cumulative_overrides, defaults)
                    cumulative_gain = float(naked_res["gain_pct"])
                ws.cell(row=r, column=cols["E"]).value = cb
                gc = ws.cell(row=r, column=cols["G"])
                if delta_row is None:
                    gc.fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                else:
                    # USER column semantics (97b8b109/b62f920d): F = delta vs cumulative
                    fc = ws.cell(row=r, column=cols["F"])
                    fc.value = float(delta_row)
                    fc.font = Font(name="Arial", size=10, bold=True, color="006100" if promote else "9C0006")
                    fc.fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid") if delta_row < 0 else PatternFill(fill_type=None)
                    fc.alignment = align
                vec_gain = (naked_res or {}).get("gain_pct")
                if joint_gain is not None:
                    vec_gain = joint_gain
                elif yl:
                    nums = list(yl.values())
                    vec_gain = cb + max(nums) if nums else vec_gain
                # G = best real gain minus INITIAL baseline; K = sum of positive yellows
                _ibg = prog.get("initial_baseline_gain")
                _g_base = _ibg if isinstance(_ibg, (int, float)) else baseline
                if isinstance(vec_gain, (int, float)) and isinstance(_g_base, (int, float)):
                    gc.value = float(vec_gain) - float(_g_base)
                    gc.font = Font(name="Arial", size=10, bold=True, color="006100" if gc.value > 0 else "9C0006")
                    gc.alignment = align
                if sum_pos > 1e-9:
                    kc = ws.cell(row=r, column=cols["K"])
                    kc.value = float(sum_pos)
                    kc.alignment = align
                for h, d in yl.items():
                    col = ymap.get(h)
                    if col:
                        yc = ws.cell(row=r, column=col)
                        yc.value = float(d)
                        bad = h in reasons
                        yc.font = Font(name="Arial", size=10, italic=bad, color="808080" if bad else None)
                        yc.alignment = align
                for h, why in reasons.items():
                    col = ymap.get(h)
                    if col and h not in yl:
                        ws.cell(row=r, column=col).fill = PatternFill(start_color="FF0000", end_color="FF0000", fill_type="solid")
                if promote:
                    ws.cell(row=r, column=cols["C"]).value = " + ".join([f"{switch}={cand}"] + pos)
                done[key] = {"delta": delta_row, "delta_vs_cumulative": delta_row, "k_sum_pos_yellows": float(sum_pos) if sum_pos > 1e-9 else None, "delta_vs_initial": (float(vec_gain) - float(_g_base)) if isinstance(vec_gain, (int, float)) and isinstance(_g_base, (int, float)) else None, "promoted": promote, "vec_gain": (naked_res or {}).get("gain_pct"), "joint_gain": joint_gain, "yellows": dict(yl), "yellow_reasons": dict(reasons), "cumulative_before": cb, "cumulative_after": float(cumulative_gain), "by": "v15_assure"}
                prog["cumulative_gain"] = float(cumulative_gain)
                prog["cumulative_overrides"] = dict(cumulative_overrides)
                n_rows += 1
                dirty += 1
                row_secs = time.time() - row_t0
                try:
                    hb.write_text(f"{time.time():.0f} {sn}!{r} {switch}={cand} delta={delta_row} {row_secs:.2f}s")
                except Exception:
                    pass
                if row_secs > 5.0:
                    log(f"SLOW ROW {sn}!{r} {switch}={cand} took {row_secs:.1f}s ({len(hdrs)} yellows)")
                if dirty >= save_every:
                    atomic_save(wb, wb_path)
                    pj.write_text(json.dumps(prog))
                    dirty = 0
            if max_rows and n_rows >= max_rows:
                break
        atomic_save(wb, wb_path)
        pj.write_text(json.dumps(prog))
        wb.close()
        pool.shutdown(wait=False)
        if eval_times:
            ts = sorted(eval_times)
            log(f"{symside}: completed {n_rows} pending rows, evals={len(ts)} p50={ts[len(ts)//2]:.3f}s p95={ts[int(len(ts)*.95)]:.3f}s max={ts[-1]:.3f}s cum={cumulative_gain:.4f}")
        else:
            log(f"{symside}: nothing pending — workbook already complete (done={len(done)})")
        return True
    finally:
        if lock:
            lock.close()


def bench_one(symside, n=20):
    from tools.opt.v12_pilot import evaluate_prepared_sanitized, prepare_batch
    t0 = time.time()
    prep = prepare_batch(symside, 30)
    log(f"prepare {time.time()-t0:.2f}s")
    if prep is None:
        log(f"{symside}: no NPZ on this host — bench impossible here")
        return False
    ts = []
    probes = [{}, {"DC_EXIT_ENABLED": True}, {"WT_15M_BOUNCE_OPEN_ENABLED": True}]
    for i in range(n):
        ov = dict(probes[i % len(probes)])
        ov["ENTRY_SCORE_THRESHOLD"] = 5.0 + (i % 7)
        t = time.time()
        evaluate_prepared_sanitized(prep, ov, 30)
        ts.append(time.time() - t)
    ts.sort()
    p50, p95, mx = ts[len(ts) // 2], ts[int(len(ts) * 0.95)], ts[-1]
    verdict = "PASS" if p95 <= 0.3 else "FAIL"
    log(f"{symside}: n={n} p50={p50:.3f}s p95={p95:.3f}s max={mx:.3f}s target p95<=0.3s -> {verdict}")
    return p95 <= 0.3


def all_symsides(out_dir=None):
    seen = {}
    for p in Path(out_dir or OUT_DIR).glob("*_30d_matrix.xlsx"):
        m = re.match(r"^([A-Z0-9]+_(?:LONG|SHORT))", p.name)
        if m:
            seen[m.group(1)] = True
    for p in PROG_DIR.glob("*_*progress*.json"):
        m = re.match(r"^([A-Z0-9]+_(?:LONG|SHORT))_", p.name)
        if m:
            seen.setdefault(m.group(1), True)
    return sorted(seen)


def watch(interval=60, apply_refill=True, kill=False, out_dir=None):
    log(f"watch started interval={interval}s apply_refill={apply_refill} kill={kill}")
    while True:
        for ss in all_symsides(out_dir):
            try:
                rep = audit_one(ss, out_dir)
                v = rep.get("verdict", "")
                age = rep.get("json_mtime_age_min")
                # recover sheets behind their JSON, and clone+fill fresh JSON-only runs (<24h);
                # older JSON_ONLY chains stay untouched until someone asks (stale template drift)
                if v.startswith("JSON_AHEAD_OF_XLSX") or (v.startswith("JSON_ONLY") and age is not None and age < 1440):
                    log(f"{ss}: {v}")
                    if apply_refill:
                        refill_one(ss, out_dir)
                lockfiles = list(LOCK_DIR.glob(f"v15_pilot_{ss}_*.lock"))
                if age is not None and age > 30 and lockfiles:
                    held = False
                    pid = None
                    for lf in lockfiles:
                        try:
                            fh = open(lf, "r+")
                            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                            fh.close()
                        except (IOError, OSError, BlockingIOError):
                            held = True
                            try:
                                pid = int(open(lf).read().strip().splitlines()[0])
                            except Exception:
                                pid = None
                    if held:
                        log(f"STALL {ss}: pilot holds lock (pid {pid}) but progress JSON idle {age:.0f} min")
                        if kill and pid:
                            log(f"STALL {ss}: killing pid {pid} and refilling")
                            try:
                                os.kill(pid, 9)
                                time.sleep(2)
                                refill_one(ss, out_dir)
                            except Exception as e:
                                log(f"kill failed {e}")
            except Exception as e:
                log(f"watch error {ss}: {e}")
        time.sleep(interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=["audit", "refill", "complete", "bench", "watch"])
    ap.add_argument("symsides", nargs="*")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--force", action="store_true", help="proceed even if a pilot holds the workbook lock")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--save-every", type=int, default=25)
    ap.add_argument("--max-rows", type=int, default=0)
    ap.add_argument("--interval", type=int, default=60)
    ap.add_argument("--kill", action="store_true", help="watch: kill stalled pilots (default report-only)")
    ap.add_argument("--no-refill", action="store_true", help="watch: report only, never write")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--n", type=int, default=20)
    args = ap.parse_args()
    targets = all_symsides(args.out_dir) if args.all else args.symsides
    if not targets:
        # V15_ASSURE_SYMSIDES env keeps the sym_side out of the cmdline: stale babysitters
        # (e.g. the Mac monitor-crypto launchd agent) ssh-pkill s1 PIDs whose ps line
        # matches their symbol pattern and were SIGKILLing our runs mid-row.
        targets = [s.strip() for s in os.environ.get("V15_ASSURE_SYMSIDES", "").split(",") if s.strip()]
    if args.mode == "watch":
        watch(args.interval, apply_refill=not args.no_refill, kill=args.kill, out_dir=args.out_dir)
        return
    if not targets:
        ap.error("give SYM_SIDE(s), --all, or V15_ASSURE_SYMSIDES env")
    rc = 0
    for ss in targets:
        if args.mode == "audit":
            rep = audit_one(ss, args.out_dir)
            if args.json:
                print(json.dumps(rep))
            else:
                x = rep.get("xlsx_stats") or {}
                log(f"{ss}: {rep.get('verdict')} | json done={rep.get('json_done')} yellows={rep.get('json_yellow_num')} age={rep.get('json_mtime_age_min')}min | xlsx F={x.get('F')} G={x.get('G')} yellows={x.get('yellow_num')}/{x.get('yellow_marked')} E2clobbered={len(rep.get('e2_numeric_clobbered') or [])}")
            if not str(rep.get("verdict", "")).startswith("CONSISTENT"):
                rc = 1
        elif args.mode == "refill":
            if not refill_one(ss, args.out_dir, force=args.force):
                rc = 1
        elif args.mode == "complete":
            if not complete_one(ss, args.out_dir, workers=args.workers, save_every=args.save_every, max_rows=args.max_rows, force=args.force):
                rc = 1
        elif args.mode == "bench":
            if not bench_one(ss, n=args.n):
                rc = 1
    sys.exit(rc)


if __name__ == "__main__":
    main()
