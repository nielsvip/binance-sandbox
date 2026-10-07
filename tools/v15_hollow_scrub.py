"""v15_hollow_scrub — one-time repair for policy-hollow progress boards (USER 2026-10-03).

The 3-day sampling/tab-level run marked rows done that had 0 evals (SKIPPED_SAMPLING)
or uncalculated yellow columns (tab-level exclusions, unrecorded). Published sheets are
PROHIBITed from retouch, so the fixed pilot's load-time scan never runs for them —
this tool schedules their rebuild directly in the progress files where they live:

  --apply : archive (.hollowprev_TS.json), drop hollow rows from done, unpublish
            (pop final_gain/final_path), set needs_redo -> next herd launch does a
            full REDO-RESET + clean re-fill. Live sheets (pilot running / fresh mtime)
            are SKIPPED here — the fixed pilot's load scan (HOLLOW-REDO) catches them
            at their next launch.

Default (no --apply) is report-only. JSON-only, no workbook needed.

Run on each writer host (S1/S4/S5) after deploying the fixed v15_pilot.py:
  python3 tools/v15_hollow_scrub.py                       # report
  python3 tools/v15_hollow_scrub.py --apply               # schedule rebuilds
"""
import argparse
import json
import os
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from tools.v15_row_guards import is_gate_casualty, scan_board_for_hollow  # noqa: E402


def map_key_for_symside(symside: str) -> str:
    s = symside.upper()
    base = s[:-5] if s.endswith("_LONG") else s[:-6] if s.endswith("_SHORT") else s
    is_crypto = base.endswith(("USDT", "USDC", "USD1", "BUSD", "FDUSD", "TUSD", "DAI"))
    return f"{'CRYPTO' if is_crypto else 'STOCKS'}_{'LONG' if s.endswith('_LONG') else 'SHORT'}"


def symside_of_progress_name(name: str):
    if name.endswith("_v14_progress.json"):
        return name[: -len("_v14_progress.json")]
    return None


def live_pilots() -> set:
    try:
        ps = subprocess.run(["ps", "-eo", "args"], capture_output=True, text=True, timeout=10)
        out = ps.stdout or ""
    except Exception:
        return set()
    found = set()
    for line in out.splitlines():
        if "v15_pilot" in line and "--sym-side" in line:
            parts = line.split("--sym-side")
            if len(parts) > 1:
                found.add(parts[1].strip().split()[0].strip())
    return found


def atomic_write_json(path: str, data: dict):
    tmp = path + f".scrubtmp_{os.getpid()}"
    with open(tmp, "w") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)


def main() -> int:
    ap = argparse.ArgumentParser(description="Report/schedule rebuilds for hollow v15 boards.")
    ap.add_argument("--dir", default=os.path.join(ROOT, "data", "reports", "lifecycle_pilot"))
    ap.add_argument("--apply", action="store_true", help="schedule rebuilds (default: report only)")
    ap.add_argument("--rescue-gate", action="store_true", help="also rescue RULE#3-gate quarantine casualties (strategy verdicts still stand)")
    ap.add_argument("--live-minutes", type=float, default=10.0, help="mtime freshness treated as live")
    args = ap.parse_args()
    spec_path = os.path.join(ROOT, "data", "wiring", "tab_filters", "tab_level_filters.json")
    try:
        spec = json.loads(open(spec_path).read())
        spec_mtime = os.path.getmtime(spec_path)
    except Exception as e:
        print(f"[scrub] no tab-level spec ({e}) — era check off", flush=True)
        spec, spec_mtime = {}, None
    live = live_pilots()
    files = sorted(f for f in os.listdir(args.dir) if f.endswith("_v14_progress.json"))
    print(f"[scrub] dir={args.dir} files={len(files)} live_pilots={len(live)} apply={args.apply}", flush=True)
    n_hollow_files = n_hollow_rows = n_done_rows = n_skipped_live = n_impossible = 0
    fleet_tally: dict = {}
    for fn in files:
        p = os.path.join(args.dir, fn)
        symside = symside_of_progress_name(fn)
        try:
            st = os.stat(p)
            prog = json.loads(open(p).read())
        except Exception as e:
            print(f"[scrub] {fn}: unreadable ({e})", flush=True)
            continue
        done = prog.get("done", {}) or {}
        n_done_rows += len(done)
        if prog.get("verdict") == "IMPOSSIBLE":
            n_impossible += 1
            if args.rescue_gate and is_gate_casualty(prog) and symside not in live:
                print(f"[scrub] {fn}: GATE-CASUALTY {str(prog.get('impossible_reasons'))[:100]}", flush=True)
                if args.apply:
                    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
                    try:
                        with open(p + f".gatecasualty_{ts}.json", "w") as fh:
                            json.dump(prog, fh)
                    except Exception as e:
                        print(f"[scrub] {fn}: archive failed ({e}) — NOT touching", flush=True)
                        continue
                    for rk in ("verdict", "impossible_reasons", "impossible_path", "impossible_metrics", "final_gain", "final_path", "not_compliant"):
                        prog.pop(rk, None)
                    prog["redo_depth"] = 0
                    prog["needs_redo"] = {"overrides": dict(prog.get("cumulative_overrides") or {}), "result": {"gain_pct": prog.get("cumulative_gain")}, "depth": 1, "reason": "gate-rescue: quarantined by the RULE#3 completeness gate over settled-verdict rows — re-fill every row"}
                    try:
                        atomic_write_json(p, prog)
                    except Exception as e:
                        print(f"[scrub] {fn}: WRITE FAILED ({e}) — archive kept", flush=True)
                        continue
                    print(f"[scrub] {fn}: rescued (tombstone cleared, rebuild scheduled)", flush=True)
            continue
        if symside in live or (time.time() - st.st_mtime) < args.live_minutes * 60:
            n_skipped_live += 1
            print(f"[scrub] {fn}: SKIPPED-LIVE (pilot running or mtime fresh) — pilot load-scan handles it", flush=True)
            continue
        assume_tl = True if spec_mtime is None else (st.st_mtime >= spec_mtime)
        hol = scan_board_for_hollow(done, map_key_for_symside(symside or ""), spec, assume_tablevel_on=assume_tl)
        drop = hol.get("drop", [])
        if not drop:
            continue
        n_hollow_files += 1
        n_hollow_rows += len(drop)
        for k, v in (hol.get("tally", {}) or {}).items():
            fleet_tally[k] = fleet_tally.get(k, 0) + v
        print(f"[scrub] {fn}: HOLLOW {len(drop)}/{len(done)} {dict(hol.get('tally', {}))} final={prog.get('final_gain')}", flush=True)
        if not args.apply:
            continue
        ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
        try:
            with open(p + f".hollowprev_{ts}.json", "w") as fh:
                json.dump(prog, fh)
        except Exception as e:
            print(f"[scrub] {fn}: archive failed ({e}) — NOT touching", flush=True)
            continue
        for k in drop:
            done.pop(k, None)
        for rk in ("final_gain", "final_path", "not_compliant"):
            prog.pop(rk, None)
        depth = int(prog.get("redo_depth", 0)) + 1
        prog["needs_redo"] = {"overrides": dict(prog.get("cumulative_overrides") or {}), "result": {"gain_pct": prog.get("cumulative_gain")}, "depth": depth, "reason": f"hollow-scrub: {len(drop)} rows with uncalculated yellows {dict(hol.get('tally', {}))} — re-fill every row"}
        prog["done"] = done
        try:
            atomic_write_json(p, prog)
        except Exception as e:
            print(f"[scrub] {fn}: WRITE FAILED ({e}) — archive kept at .hollowprev_{ts}.json", flush=True)
            continue
        print(f"[scrub] {fn}: scheduled rebuild depth={depth} kept={len(done)}", flush=True)
    print(f"[scrub] FLEET files={len(files)} hollow_files={n_hollow_files} hollow_rows={n_hollow_rows}/{n_done_rows} skipped_live={n_skipped_live} impossible={n_impossible} tally={fleet_tally}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
