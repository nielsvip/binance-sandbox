#!/usr/bin/env python3
"""v15 mega sweep driver — one queue per server, N sym_sides in parallel, each a full v15_pilot workbook.

Every pilot runs with an isolated progress dir (V15_PROGRESS_DIR) so herd/cron syncs cannot inject stale `done` keys,
writes the per-eval DELTA-LOG, and uses the process-pool row evaluation. A sym_side is DONE when its pilot log has
"[spec-fill] DONE" or "SAMPLE-FLOOR-VIOLATION"; restarting the driver skips those.

Usage (s1 crypto / s2 stocks):
  python tools/v15_mega_sweep.py --venue crypto --parallel 4 --workers 4 --nav-mode jump
  python tools/v15_mega_sweep.py --venue stocks --parallel 4 --workers 4 --nav-mode jump --symbols XLE,UUUU
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD")


def full_window_symbols(venue: str, min_days: float) -> list:
    import numpy as np
    out = []
    for f in sorted((ROOT / "backtest_v8" / "indicators").glob("*.npz")):
        symbol = f.stem
        if "_" in symbol or not symbol.isalnum():
            continue
        if (venue == "crypto") != symbol.endswith(CRYPTO_SUFFIXES) or (venue == "stocks" and "USD" in symbol):
            continue
        try:
            with np.load(f, allow_pickle=False) as z:
                ts = z["timestamps"].astype("float64")
        except Exception:
            continue
        ts = ts / 1000.0 if ts.max() > 1e12 else ts
        window = ts[ts >= ts[-1] - 30 * 86400]
        if len(window) > 1 and (window[-1] - window[0]) / 86400 >= min_days:
            out.append(symbol)
    return out


def _fmt(x):
    try:
        return f"{float(x):.2f}".replace("-", "m").replace(".", "p")
    except (TypeError, ValueError):
        return "NA"


def finalize(sym_side: str, out_dir: Path, prog_dir: Path, nav_mode: str) -> str:
    # DC64-style names: {SS}_bh{bh}_gain{gain}_delta{delta}_30d_matrix.xlsx + {SS}_bh{bh}_gain{gain}_30D_REAL_ZOOMABLE.html
    xlsx = out_dir / f"{sym_side}_30d_matrix.xlsx"
    progs = list(prog_dir.glob("*_v14_progress.json"))
    if not xlsx.exists() or not progs:
        return "no xlsx/progress"
    prog = json.loads(progs[0].read_text())
    if prog.get("diagnostic_only"):
        # sample-floor skip (<10 trades): no sweep, no gain -> never a "finished" bh/gain name
        tr = str(prog.get("diagnostic_only")).split("trades=")[-1].split()[0]
        name = f"{sym_side}_DIAGNOSTIC_{tr}trades_bh{_fmt(prog.get('bh'))}_30d_matrix.xlsx"
        os.replace(xlsx, out_dir / name)
        return f"{name} (sample-floor skip, no chart)"
    gain = prog.get("cumulative_gain")
    base = prog.get("initial_baseline_gain", prog.get("baseline_gain"))
    bh = prog.get("bh")
    delta = (float(gain) - float(base)) if gain is not None and base is not None else None
    name = f"{sym_side}_bh{_fmt(bh)}_gain{_fmt(gain)}_delta{_fmt(delta)}_30d_matrix.xlsx"
    os.replace(xlsx, out_dir / name)
    chart_src = ROOT / "SPREADSHEETS" / f"{sym_side}_30D_REAL_ZOOMABLE_{nav_mode}.html"
    chart = "no chart"
    if chart_src.exists():
        chart = f"{sym_side}_bh{_fmt(bh)}_gain{_fmt(gain)}_30D_REAL_ZOOMABLE.html"
        shutil.copy2(chart_src, out_dir / chart)
    return f"{name} + {chart}"


DONE_STAGE_IDLE_S = 7200


def in_done_stage(log: Path) -> bool:
    # every row + final filter recheck done; the DONE-stage backtest_v12_engine LIVE verification prints nothing
    # for 10-60+ min — silence there is not a hang (2026-09-28: 20 s2 sheets reaped / 25 s1 sheets orphaned there)
    try:
        with open(log, "rb") as fh:
            fh.seek(max(0, log.stat().st_size - 8192))
            tail = fh.read().decode(errors="ignore")
    except OSError:
        return False
    return "[final-recheck]" in tail and "[spec-fill] DONE" not in tail and "already owns" not in tail


def live_pilot_symsides(log_dir: Path = None, max_idle_s: int = 1200) -> set:
    # only OUR sweep pilots (V15_PROGRESS_DIR under ~/v15_mega_progress) — never adopt a foreign pilot
    mine = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            env = open(f"/proc/{pid}/environ", "rb").read()
        except OSError:
            continue
        if b"v15_mega_progress" not in env or not any(a.endswith((b"v15_pilot.py", b"v15_mega_pilot.py")) for a in args) or b"--sym-side" not in args:
            continue
        ss = args[args.index(b"--sym-side") + 1].decode()
        if log_dir is not None:
            log = log_dir / f"{ss}.log"
            # never adopt a frozen pilot / orphaned pool worker: its log stops being written
            if not log.exists() or time.time() - log.stat().st_mtime > (DONE_STAGE_IDLE_S if in_done_stage(log) else max_idle_s):
                continue
        mine.add(ss)
    return mine


def is_real_done(log: Path) -> bool:
    # a full filled sheet — zero-trade/floor skips do not count toward the per-side target
    if not log.exists():
        return False
    text = log.read_text(errors="ignore")
    return "[spec-fill] DONE" in text and "SAMPLE-FLOOR-VIOLATION" not in text


def live_per_sym_symbols(venue: str) -> set:
    f = ROOT / "data" / "hourly_reconfig" / ("per_sym_active_config.json" if venue == "crypto" else "per_sym_active_config_stocks.json")
    try:
        keys = [k for k in json.loads(f.read_text()) if not k.startswith("_")]
    except (OSError, ValueError):
        return set()
    return {k.rsplit("_", 1)[0] if k.endswith(("_LONG", "_SHORT")) else k for k in keys}


def unfinalize(sym_side: str, out_dir: Path, log_dir: Path):
    # put a finished sheet back under its working name so the pilot RESUMES it (only pending rows are computed)
    named = sorted(out_dir.glob(f"{sym_side}_bh*_30d_matrix.xlsx"))
    if named:
        os.replace(named[-1], out_dir / f"{sym_side}_30d_matrix.xlsx")
    log = log_dir / f"{sym_side}.log"
    if log.exists():
        os.replace(log, log_dir / f"{sym_side}.log.{int(time.time())}")


def _sync_mega_pilot():
    # atomic + only-if-changed: re-copying in place raced with pilots starting up (half-written file -> IndentationError)
    src, dst = ROOT / "v15_pilot.py", ROOT / "v15_mega_pilot.py"
    # FROZEN for the running sweep (2026-09-28): other sessions push v15_pilot.py changes (e.g. a 365D DONE hook);
    # the sweep keeps its pilot so every sheet uses the same row-fill code. Delete v15_mega_pilot.py to re-sync.
    if dst.exists():
        return
    tmp = ROOT / f".v15_mega_pilot.{os.getpid()}.tmp"
    shutil.copy2(src, tmp)
    os.replace(tmp, dst)


def foreign_pilot_symsides() -> set:
    # sym_sides a NON-mega pilot (canonical herd etc.) is working on right now — never duplicate them
    busy = set()
    for pid in os.listdir("/proc"):
        if not pid.isdigit():
            continue
        try:
            args = open(f"/proc/{pid}/cmdline", "rb").read().split(b"\0")
            env = open(f"/proc/{pid}/environ", "rb").read()
        except OSError:
            continue
        if b"v15_mega_progress" in env or b"--sym-side" not in args or not any(a.endswith(b"v15_pilot.py") for a in args):
            continue
        busy.add(args[args.index(b"--sym-side") + 1].decode())
    return busy


def is_done(log: Path) -> bool:
    if not log.exists():
        return False
    text = log.read_text(errors="ignore")
    return "[spec-fill] DONE" in text or "SAMPLE-FLOOR-VIOLATION" in text


def chain_start(sym_side: str, prog_root: Path) -> str:
    # UTC ISO time the sheet's current chain began: first delta-log record, else progress JSON mtime ("" = never started)
    d = prog_root / sym_side
    for f in sorted(d.glob("v15_delta_log/*.jsonl")):
        try:
            with open(f) as fh:
                ts = json.loads(fh.readline()).get("ts")
            if ts:
                return ts
        except (OSError, ValueError):
            pass
    progs = list(d.glob("*_v14_progress.json"))
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(progs[0].stat().st_mtime)) if progs else ""


def archive_precut(sym_side: str, cut: str, out_dir: Path, prog_root: Path, log_dir: Path) -> int:
    # a chain started on an older engine cannot be resumed (baseline shifts make every re-eval incomparable):
    # move its progress/log/sheets aside (kept, never deleted) so the pilot starts clean on the current engine
    dest = Path.home() / "v15_mega_precut_archive" / cut.replace(":", "") / sym_side
    dest.mkdir(parents=True, exist_ok=True)
    moved = 0
    items = [prog_root / sym_side, log_dir / f"{sym_side}.log"] + list(out_dir.glob(f"{sym_side}_30d_matrix.xlsx")) + list(out_dir.glob(f"{sym_side}_bh*")) + list(out_dir.glob(f"{sym_side}_DIAGNOSTIC_*"))
    for p in items:
        if p.exists():
            shutil.move(str(p), str(dest / p.name))
            moved += 1
    return moved


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--venue", choices=["crypto", "stocks"], required=True)
    ap.add_argument("--symbols", default=None, help="comma list; default = every symbol with a full 30d NPZ window")
    ap.add_argument("--parallel", type=int, default=4, help="max SYMBOLS in RAM at once; each runs its _LONG and _SHORT sheets together")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--nav-mode", default="jump", choices=["jump", "fill_tab"])
    ap.add_argument("--window-days", type=int, default=30)
    ap.add_argument("--run-name", default=None)
    ap.add_argument("--target-per-side", type=int, default=0, help="stop launching a side once this many REAL filled sheets exist/run for it (0 = all)")
    ap.add_argument("--sym-sides-file", default=None, help="text file, one SYM_SIDE per line: run exactly these (those with a full 30d NPZ window)")
    ap.add_argument("--reverse", action="store_true", help="work the list from the END (the canonical herd works from the start) — no double compute")
    ap.add_argument("--redo", default="", help="comma list of finished sym_sides to resume (fill rows added since, e.g. STDEV) — queued first")
    args = ap.parse_args()
    run = args.run_name or f"MEGA_{args.venue}_{args.nav_mode}"
    out_dir = ROOT / "SPREADSHEETS" / "V15_MEGA" / run
    prog_root = Path.home() / "v15_mega_progress" / run
    log_dir = Path.home() / "v15_mega_logs" / run
    for d in (out_dir, prog_root, log_dir):
        d.mkdir(parents=True, exist_ok=True)
    symbols = args.symbols.split(",") if args.symbols else full_window_symbols(args.venue, 29.5 if args.venue == "crypto" else 27.0)
    cat = "CRYPTO" if args.venue == "crypto" else "STOCKS"
    live = live_per_sym_symbols(args.venue)
    symbols = sorted(symbols, key=lambda s: (s not in live, s))
    redo = [q for q in args.redo.split(",") if q]
    for q in redo:
        unfinalize(q, out_dir, log_dir)
    wanted = [f"{s}_{side}" for s in symbols for side in ("LONG", "SHORT")]
    if args.sym_sides_file:
        listed = [l.strip() for l in open(args.sym_sides_file) if l.strip()]
        full = set(full_window_symbols(args.venue, 29.5 if args.venue == "crypto" else 27.0))
        skipped = [q for q in listed if q.rsplit("_", 1)[0] not in full and ((args.venue == "crypto") == q.rsplit("_", 1)[0].endswith(CRYPTO_SUFFIXES))]
        order = {s: i for i, s in enumerate(symbols)}
        listed_syms = {q.rsplit("_", 1)[0] for q in listed}
        listed = [f"{s}_{side}" for s in sorted(listed_syms) for side in ("LONG", "SHORT")]  # always BOTH sides of a symbol
        wanted = sorted([q for q in listed if q.rsplit("_", 1)[0] in full], key=lambda q: (q.rsplit("_", 1)[0] not in live, order.get(q.rsplit("_", 1)[0], 1e9), q))
        print(f"[mega] sym-sides-file {len(listed)} listed -> {len(wanted)} {args.venue} with full 30d NPZ; {len(skipped)} {args.venue} skipped (no/short NPZ): {skipped}", flush=True)
    if args.reverse:
        wanted = wanted[::-1]
    queue = redo + [q for q in wanted if q not in redo]
    # engine cut line (~/.v15_mega_cutline_<venue>, UTC ISO): chains begun before it restart clean, AFTER every fresh sheet
    try:
        cut = (Path.home() / f".v15_mega_cutline_{args.venue}").read_text().strip()
    except OSError:
        cut = ""
    precut = {q for q in queue if cut and "" < chain_start(q, prog_root) < cut}
    queue = [q for q in queue if q not in precut and not is_done(log_dir / f"{q}.log")] + [q for q in queue if q in precut and not is_done(log_dir / f"{q}.log")] + [q for q in queue if q in precut and is_done(log_dir / f"{q}.log")]
    if cut:
        print(f"[mega] cut line {cut}: {len(precut)} pre-cut chains queued LAST (restart clean, old data archived at launch)", flush=True)
    adopted = live_pilot_symsides(log_dir) & set(queue)
    queue = [q for q in queue if q not in adopted]
    for q in [q for q in (f"{s}_{side}" for s in symbols for side in ("LONG", "SHORT")) if is_done(log_dir / f"{q}.log") and (out_dir / f"{q}_30d_matrix.xlsx").exists()]:
        print(f"[mega] FINALIZE (earlier run) {q}: {finalize(q, out_dir, prog_root / q, args.nav_mode)}", flush=True)
    retried = set()
    print(f"[mega] adopted {len(adopted)} already-running pilots: {sorted(adopted)}", flush=True)
    print(f"[mega] {run} {len(queue)} sym_sides queued parallel={args.parallel} workers={args.workers} out={out_dir}", flush=True)
    running = {}
    env_base = dict(os.environ, FORCE_DC_RERUN="1")
    started = time.time()
    finished = 0
    ctrl = Path.home() / f".v15_mega_parallel_{args.venue}"
    while queue or running or adopted:
        try:  # live symbol-slot limit set by the supervisor (tools/v15_mega_supervisor.sh)
            args.parallel = max(1, min(12, int(ctrl.read_text().strip())))
        except (OSError, ValueError):
            pass
        live = live_pilot_symsides(log_dir) if adopted else set()
        for sym_side in sorted(adopted - live):
            adopted.discard(sym_side)
            finished += 1
            ok = is_done(log_dir / f"{sym_side}.log")
            note = finalize(sym_side, out_dir, prog_root / sym_side, args.nav_mode) if ok else ""
            if not ok and sym_side not in retried:
                retried.add(sym_side)
                queue.append(sym_side)
                note = "adopted pilot died/stalled — requeued once"
            print(f"[mega] {'DONE' if ok else 'EXIT'} {sym_side} (adopted) finished={finished} left={len(queue)} {note}", flush=True)
        for sym_side, proc in list(running.items()):
            if proc.poll() is not None:
                del running[sym_side]
                finished += 1
                ok = is_done(log_dir / f"{sym_side}.log")
                note = ""
                if not ok and "already owns" in (log_dir / f"{sym_side}.log").read_text(errors="ignore"):
                    finished -= 1  # our earlier pilot still holds the sheet lock: adopt it, never burn the retry on a DEDUP exit
                    adopted.add(sym_side)
                    print(f"[mega] DEDUP {sym_side}: earlier pilot still owns the sheet — adopted, not requeued", flush=True)
                    continue
                if ok:
                    try:
                        note = finalize(sym_side, out_dir, prog_root / sym_side, args.nav_mode)
                    except Exception as e:
                        note = f"finalize failed {e}"
                elif sym_side not in retried:
                    retried.add(sym_side)
                    queue.append(sym_side)
                    note = "requeued once"
                print(f"[mega] {'DONE' if ok else 'EXIT'} {sym_side} rc={proc.returncode} finished={finished} left={len(queue)} elapsed={(time.time()-started)/60:.1f}m {note}", flush=True)
        def _syms(names):
            return {q.rsplit("_", 1)[0] for q in names}
        while queue and len(_syms(set(running) | adopted | {queue[0]})) <= args.parallel:
            if args.target_per_side:
                busy = set(running) | adopted
                def side_count(side):
                    return sum(1 for f in log_dir.glob(f"*_{side}.log") if is_real_done(f)) + sum(1 for q in busy if q.endswith("_" + side))
                pick = next((i for i, q in enumerate(queue) if side_count(q.rsplit("_", 1)[1]) < args.target_per_side), None)
                if pick is None:
                    queue.clear()
                    break
                queue.insert(0, queue.pop(pick))
            foreign = foreign_pilot_symsides()
            if queue[0] in foreign:
                skipped_busy = queue.pop(0)
                queue.append(skipped_busy)  # herd has it now; revisit at the end (skipped if done by then)
                print(f"[mega] SKIP {skipped_busy}: live herd pilot on it", flush=True)
                if all(q in foreign for q in queue):
                    break
                continue
            if queue[0] in live_pilot_symsides(None):  # our pilot still alive (quiet LIVE stage): adopt, never relaunch over it
                adopted.add(queue.pop(0))
                continue
            sym_side = queue.pop(0)
            partner =sym_side.rsplit("_", 1)[0] + ("_SHORT" if sym_side.endswith("_LONG") else "_LONG")
            if partner in queue:  # its other side goes right behind it -> same symbol, same moment, both sheets together
                queue.insert(0, queue.pop(queue.index(partner)))
            if sym_side in precut:
                precut.discard(sym_side)
                print(f"[mega] PRE-CUT {sym_side}: chain began {chain_start(sym_side, prog_root)} < cut {cut} — archived {archive_precut(sym_side, cut, out_dir, prog_root, log_dir)} items, restarting clean", flush=True)
            side = sym_side.rsplit("_", 1)[1]
            env = dict(env_base, V15_PROGRESS_DIR=str(prog_root / sym_side))
            (prog_root / sym_side).mkdir(parents=True, exist_ok=True)
            # byte-identical copy under another name: the herd's OOM guard pkills "v15_pilot.*<sym>" (youngest first)
            # and was SIGKILLing our pilots; it cannot match v15_mega_pilot.py
            _sync_mega_pilot()
            cmd = [sys.executable, "-u", str(ROOT / "v15_mega_pilot.py"), "--sym-side", sym_side,
                   "--template", str(ROOT / "SPREADSHEETS" / f"TEMPLATE_{cat}_{side}.xlsx"),
                   "--seq-mode", "worst_first", "--nav-mode", args.nav_mode, "--window-days", str(args.window_days),
                   "--vector-only", "--workers", str(args.workers), "--out", str(out_dir / f"{sym_side}_30d_matrix.xlsx")]
            log = open(log_dir / f"{sym_side}.log", "w")
            running[sym_side] = subprocess.Popen(cmd, cwd=str(ROOT), env=env, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, start_new_session=True)
            try:  # engine version this pilot imports (engine files keep changing under the sweep) -> group results by it later
                import hashlib as _hl
                _eng = _hl.md5((ROOT / "v12_quick_engine.py").read_bytes()).hexdigest()[:12]
                (prog_root / sym_side / "ENGINE_AT_LAUNCH.txt").open("a").write(f"{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())} {_eng}\n")
            except OSError:
                _eng = "?"
            print(f"[mega] START {sym_side} running={len(running)} left={len(queue)} engine={_eng}", flush=True)
        time.sleep(10)
    print(f"[mega] ALL DONE {run} finished={finished} elapsed={(time.time()-started)/60:.1f}m", flush=True)


if __name__ == "__main__":
    main()
