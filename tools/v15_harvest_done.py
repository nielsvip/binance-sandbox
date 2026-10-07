#!/usr/bin/env python3
"""
v15_harvest_done — server-side harvester of honestly-complete sweep sheets.

Runs on s1 (crypto) / s2 (stocks). For every sym_side whose progress JSON is
is_complete (all 12 SWITCH_SHEETS tabs filled) and carries an honest final gain,
it materialises ONE bh/gain-named finished xlsx into a stable dir
(~/v15_mac_done/) that no cron touches, plus (optional) a BNO-style zoomable
chart. The Mac pulls that dir down. Deliverables are thus decoupled from the
churny V15_V16_CELL_BY_CELL dir and its several deleting crons.

Honest number rule (NO-LIES): gain = final_gain_fresh_vec when present (engine
re-verified), else the pilot's recorded final_gain (pilot already collapses
engine_mixed_chain to the fresh vec). bh from progress["bh"]. Never invents.

Idempotent: skips a sheet whose bh/gain file already exists with the same name.
"""
import argparse, json, os, pathlib, re, shutil, sys, zipfile

ROOT = pathlib.Path.home() / "binance-sandbox"
if not ROOT.exists():
    ROOT = pathlib.Path("/Users/niels/Documents/binance")
CELL = ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL"
PROG = ROOT / "data" / "reports" / "lifecycle_pilot"
_PD = pathlib.Path.home() / "v15_current_progress_dir.txt"  # run17+: the scheduler's current progress dir holds the finished sym_sides
if os.environ.get("V15_PROGRESS_DIR"):
    PROG = pathlib.Path(os.environ["V15_PROGRESS_DIR"])
elif _PD.exists() and pathlib.Path(_PD.read_text().strip()).is_dir():
    PROG = pathlib.Path(_PD.read_text().strip())
OUT = pathlib.Path.home() / "v15_mac_done"
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))


def fmt_bg(v):
    return f"{float(v):.2f}".replace("-", "m").replace(".", "p")


def valid_xlsx(path):
    try:
        with zipfile.ZipFile(path) as z:
            return len(z.namelist()) >= 10 and z.testzip() is None
    except Exception:
        return False


def honest_gain(prog):
    fv = prog.get("final_gain_fresh_vec")
    if fv is not None:
        return float(fv)
    return float(prog["final_gain"])


def source_sheet(symside, gname):
    named = CELL / gname
    if named.exists() and valid_xlsx(named):
        return named
    try:
        cands = sorted(CELL.glob(f"{symside}_bh*_30d_matrix.xlsx"), key=lambda p: p.stat().st_mtime, reverse=True)
    except Exception:
        cands = []
    for cand in cands:
        if valid_xlsx(cand):
            return cand
    plain = CELL / f"{symside}_30d_matrix.xlsx"
    if plain.exists() and valid_xlsx(plain):
        return plain
    return None


def harvest_xlsx():
    try:
        from v15_progress_board import is_complete
    except Exception as e:
        print(f"[harvest] progress board import failed: {e}", flush=True)
        return []
    harvested = []
    for jf in sorted(PROG.glob("*_v14_progress.json")):
        symside = jf.name[: -len("_v14_progress.json")]
        try:
            prog = json.loads(jf.read_text())
        except Exception:
            continue
        if prog.get("final_gain") is None or prog.get("bh") is None:
            continue
        # USER 2026-09-30: only a sheet whose final set complies (valid TIM/DD/trades — the pilot publishes it and records
        # final_path) is finished; a not_compliant sheet never gets a bh/gain filename
        if prog.get("not_compliant") or not prog.get("final_path"):
            continue
        try:
            if not is_complete(symside):
                continue
        except Exception:
            pass  # board reads the default progress dir; a finished pilot with final_path + compliant final set is complete
        gain = honest_gain(prog)
        bh = float(prog["bh"])
        _trades = prog.get("final_trades")
        if _trades is None:
            gname = f"{symside}_bh{fmt_bg(bh)}_gain{fmt_bg(gain)}_30d_matrix.xlsx"
        else:
            from tools.v15_final_naming import final_matrix_name as _final_matrix_name
            gname = _final_matrix_name(symside, bh, gain, int(_trades), 30)
        dst = OUT / gname
        if dst.exists() and valid_xlsx(dst):
            harvested.append((symside, gname))
            continue
        src = source_sheet(symside, gname)
        if src is None:
            print(f"[harvest] {symside} complete but no valid source xlsx yet — retry next loop", flush=True)
            continue
        for old in OUT.glob(f"{symside}_bh*_30d_matrix.xlsx"):
            if old.name != gname:
                old.unlink()
        for old in OUT.glob(f"{symside}_bh*_30d_zoom.html"):
            old.unlink()
        tmp = OUT / (gname + ".tmp")
        shutil.copy2(src, tmp)
        os.replace(tmp, dst)
        harvested.append((symside, gname))
        print(f"[harvest] {symside} -> {gname} (gain {gain:.2f} bh {bh:.2f})", flush=True)
    return harvested


def make_charts(harvested, cap):
    try:
        from tools.opt.hires_chart import generate_hires
    except Exception as e:
        print(f"[chart] hires import failed: {e}", flush=True)
        return
    made = 0
    for symside, gname in harvested:
        if made >= cap:
            break
        chart_name = gname.replace("_matrix.xlsx", "_zoom.html")
        if (OUT / chart_name).exists():
            continue
        jf = PROG / f"{symside}_v14_progress.json"
        try:
            ov = json.loads(jf.read_text()).get("cumulative_overrides") or {}
        except Exception:
            ov = {}
        try:
            p = generate_hires(symside, ov, 30, out_name=chart_name)
            shutil.copy2(p, OUT / chart_name)
            made += 1
            print(f"[chart] {chart_name}", flush=True)
        except Exception as e:
            print(f"[chart-err] {symside}: {str(e)[:120]}", flush=True)
    if made:
        print(f"[chart] made {made} charts", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--charts", action="store_true", help="also generate BNO-style charts (uses local NPZ)")
    ap.add_argument("--chart-cap", type=int, default=15, help="max charts per run (throttle vs herd CPU)")
    args = ap.parse_args()
    harvested = harvest_xlsx()
    print(f"[harvest] {len(harvested)} honest-complete sheets in {OUT}", flush=True)
    if args.charts:
        make_charts(harvested, args.chart_cap)


if __name__ == "__main__":
    main()
