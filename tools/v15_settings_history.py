#!/usr/bin/env python3
"""v15_settings_history — USER 2026-10-13: "make sure the db written by the fleet orchestrator with each sym_side's
historical settings day by day is intact and updated when the json is written for any sym_side as soon as its new
settings have been calculated."
USER 2026-10-14: "make sure the db gets filled correctly from now on so we have the daily settings and metrics for each
sym_side after every calc and hold on to it infinitely (at least on ext drive) for stats and development and self learning".

Runs on the orchestrator (s1). Source = the PUBLISH MANIFESTS (SPREADSHEETS/V15_V16_CELL_BY_CELL*/{SS}_*_matrix_manifest.json
on every host): one manifest = one published board with its complete metrics (gain, trades, TIM, max DD, bh, gain_vs_bh,
valid, publish_class, engine/pilot md5, npz md5, overrides_md5). The board's exact SET comes from the round progress JSON
whose md5(cumulative_overrides) == the manifest overrides_md5 (same hash as v15_pilot). One row per board is appended to
`per_sym_history` in `data/hourly_reconfig/per_sym_store.db` (same table/columns as per_sym_store.upsert()).
(2026-10-13 version read the progress JSONs only: TIM/DD came from diagnose_repair.after and were mostly empty; a progress
file overwritten by the next round lost the board.)

Sources written to meta_json.source:
  fleet_orchestrator_publish      — set matched; tools/v15_published_selection.py (latest-board go-live) reads ONLY these.
  fleet_orchestrator_publish_noset — metrics recorded but the exact set could not be recovered (never a go-live source).
  manifest_backfill               — --backfill rows (older boards recorded after the fact; never a go-live source).

Deliberately HISTORY-ONLY: it never touches `per_sym_active`. Never deletes (per_sym_store pruning is off, USER 2026-10-14).
Idempotent per (sym_side, published_utc, overrides_md5). Integrity is checked before writing; the DB is untouched on failure.

Usage:
  python3 tools/v15_settings_history.py [--hosts 127.0.0.1,10.0.0.4] [--window-h 24] [--backfill] [--dry-run]
  --window-h 0 = every manifest on disk.
"""
import argparse
import datetime
import hashlib
import json
import os
import sqlite3
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.environ.get("PER_SYM_STORE_DB") or os.path.join(ROOT, "data", "hourly_reconfig", "per_sym_store.db")
CRYPTO_SUFFIX = ("USDT", "USDC", "BUSD", "FDUSD", "TUSD")
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "StrictHostKeyChecking=accept-new"]

COLLECT = r'''
import glob, hashlib, json, os, time
win = float(%(win)s)
now = time.time()
home = os.path.expanduser("~")
mans = []
for d in glob.glob(home + "/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL*") + glob.glob(home + "/binance-sandbox/data/manifest_archive*"):
    for f in glob.glob(d + "/*_matrix_manifest.json"):
        try:
            if win > 0 and now - os.path.getmtime(f) > win * 3600.0:
                continue
            m = json.load(open(f))
        except Exception:
            continue
        if m.get("symside") and m.get("published_utc") and m.get("overrides_md5"):
            m["_path"] = f
            mans.append(m)
want = {}
for m in mans:
    want.setdefault(m["symside"], set()).add(m["overrides_md5"])
sets = {}
pdirs = glob.glob(home + "/v15_run*/progress") + glob.glob(home + "/v15_*/progress") + [home + "/binance-sandbox/data/reports/lifecycle_pilot"]
for d in set(pdirs):
    for ss in want:
        for f in glob.glob(os.path.join(d, ss + "_v14_progress.json")) + glob.glob(os.path.join(d, ss + "_v14_progress.json.*")):
            try:
                j = json.load(open(f))
            except Exception:
                continue
            ov = j.get("cumulative_overrides")
            if not isinstance(ov, dict) or not ov:
                continue
            h = hashlib.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()
            if h in want[ss] and (ss, h) not in sets:
                npz = j.get("npz_id") or {}
                sets[(ss, h)] = {"overrides": ov, "progress": f, "baseline_gain": j.get("baseline_gain"), "verdict": j.get("verdict"),
                                 "defaults_round": j.get("defaults_round"), "result_done_utc": j.get("result_done_utc"),
                                 "npz": npz.get("md5") or npz.get("mtime_ns") if isinstance(npz, dict) else None}
out = []
for m in mans:
    s = sets.get((m["symside"], m["overrides_md5"])) or {}
    out.append({"manifest": m, "set": s})
print(json.dumps(out, default=str))
'''


def collect(host, win):
    code = COLLECT % {"win": win}
    if host in ("127.0.0.1", "localhost", "local"):
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=900)
    else:
        # piped on STDIN: shell-quoting a multi-line script through ssh mangles it
        r = subprocess.run(SSH + [f"niels@{host}", "python3 -"], input=code, capture_output=True, text=True, timeout=900)
    try:
        return json.loads((r.stdout or "").strip().splitlines()[-1])
    except Exception:
        return []


def cat_side_of(ss):
    sym, _, side = ss.rpartition("_")
    return f"{'CRYPTO' if sym.upper().endswith(CRYPTO_SUFFIX) else 'STOCKS'}_{side}"


def template_md5(cs):
    p = os.path.join(ROOT, "SPREADSHEETS", "TEMPLATE_FINAL_NORM", f"TEMPLATE_{cs}.xlsx")
    try:
        return hashlib.md5(open(p, "rb").read()).hexdigest()
    except OSError:
        return ""


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


def _i(x):
    try:
        return int(float(x)) if x is not None else None
    except (TypeError, ValueError):
        return None


def archive_board_npz(symside, npz_name, npz_md5, host, root=None, run=None):
    """USER 2026-10-15: "the copy has to happen at publish time in the orchestrator". Retain the exact NPZ a published
    board ran on, so tools/v15_scalar_parity_lane.py (backtest_v12_engine) verifies THAT result instead of a newly
    refreshed window. Archiving when the lane first sees the item was too late: the JIT rebuild at the symbol's next
    dispatch had already replaced the bytes.

    Keyed by md5, ONE copy retained per sym_side (older md5s pruned) -> bounded at ~one NPZ per sym_side. The copy runs
    on the board's OWN host (local cp, or ssh for a peer) because that is where the file is. Verifies the md5 before
    keeping it: a file that no longer matches the manifest is NOT the board's NPZ and is never substituted.
    Returns a short status string. Never raises. V15_NPZ_ARCHIVE=0 disables.
    """
    import subprocess
    if os.environ.get("V15_NPZ_ARCHIVE", "1") == "0":
        return "disabled"
    if not (symside and npz_name and npz_md5):
        return "no npz identity in the manifest"
    sym = str(npz_name)[:-4] if str(npz_name).endswith(".npz") else str(npz_name)
    root = root or "$HOME/binance-sandbox"  # $HOME, never ~: the paths below are double-quoted and ~ does not expand there
    keep = f"{root}/data/parity/npz_archive/{sym}__{npz_md5}.npz"
    src = f"{root}/backtest_v8/indicators/{npz_name}"
    sh = (
        f'set -e; mkdir -p {root}/data/parity/npz_archive; '
        f'if [ -f "{keep}" ]; then echo already; exit 0; fi; '
        f'if [ ! -f "{src}" ]; then echo no-src; exit 0; fi; '
        f'got=$(md5sum "{src}" | cut -d" " -f1); '
        f'if [ "$got" != "{npz_md5}" ]; then echo "md5-moved:$got"; exit 0; fi; '
        f'cp -p "{src}" "{keep}.tmp" && mv "{keep}.tmp" "{keep}"; '
        f'for f in {root}/data/parity/npz_archive/{sym}__*.npz; do [ "$f" = "{keep}" ] || rm -f "$f"; done; '
        f'echo archived'
    )
    local = str(host or "").strip() in ("", os.uname().nodename, "local", "localhost", "127.0.0.1")
    cmd = ["bash", "-lc", sh] if local else ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", str(host), sh]
    try:
        r = (run or subprocess.run)(cmd, capture_output=True, text=True, timeout=180)
        out = (r.stdout or "").strip().splitlines()
        return (out[-1] if out else "no-output")[:60]
    except Exception as e:
        return f"{type(e).__name__}"[:40]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hosts", default=os.environ.get("V15_HIST_HOSTS", "127.0.0.1,10.0.0.4"))
    ap.add_argument("--window-h", type=float, default=float(os.environ.get("V15_HIST_WINDOW_H", "24")))
    ap.add_argument("--backfill", action="store_true", help="tag rows manifest_backfill (never a go-live source)")
    ap.add_argument("--db", default=DB)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not os.path.exists(args.db):
        print(json.dumps({"error": f"db missing: {args.db}"}))
        return 2
    con = sqlite3.connect(args.db, timeout=60.0)
    ok = con.execute("PRAGMA quick_check").fetchone()[0]
    if ok != "ok":
        print(json.dumps({"error": f"quick_check={ok} — refusing to write", "db": args.db}))
        return 3
    if not {r[1] for r in con.execute("PRAGMA table_info(per_sym_history)").fetchall()}:
        print(json.dumps({"error": "per_sym_history table missing — run per_sym_store ensure first"}))
        return 3
    boards = {}
    per_host = {}
    for host in [h.strip() for h in args.hosts.split(",") if h.strip()]:
        rows = collect(host, args.window_h)
        per_host[host] = len(rows)
        for r in rows:
            m = r["manifest"]
            k = (m["symside"], m["published_utc"], m["overrides_md5"])
            if k not in boards or (r["set"] and not boards[k]["set"]):
                boards[k] = dict(r, host=host)
    tpl = {cs: template_md5(cs) for cs in ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")}
    wrote, dup, noset = 0, 0, 0
    for (ss, pub, ovm), r in sorted(boards.items(), key=lambda kv: kv[0][1]):
        hit = con.execute("SELECT 1 FROM per_sym_history WHERE sym_side=? AND updated_at=? AND json_extract(meta_json,'$.overrides_md5')=? LIMIT 1",
                          (ss, pub, ovm)).fetchone()
        mt0 = r["manifest"].get("metrics") or {}
        if not hit and mt0.get("gain_pct") is not None:
            # the 2026-10-13 progress-based writer already recorded this board (same sym_side, same day, same final gain)
            hit = con.execute("SELECT 1 FROM per_sym_history WHERE sym_side=? AND substr(updated_at,1,10)=? AND gain_pct IS NOT NULL AND abs(gain_pct-?)<1e-6 "
                              "AND json_extract(meta_json,'$.source')='fleet_orchestrator_publish' LIMIT 1", (ss, pub[:10], float(mt0["gain_pct"]))).fetchone()
        if hit:
            dup += 1
            continue
        m, s = r["manifest"], r["set"]
        mt = m.get("metrics") or {}
        ov = s.get("overrides")
        source = "manifest_backfill" if args.backfill else ("fleet_orchestrator_publish" if ov else "fleet_orchestrator_publish_noset")
        noset += 0 if ov else 1
        if args.dry_run:
            wrote += 1
            continue
        cs = cat_side_of(ss)
        try:
            epoch = datetime.datetime.fromisoformat(pub.replace("Z", "+00:00")).timestamp()
        except ValueError:
            epoch = None
        gain, base = _f(mt.get("gain_pct")), _f(s.get("baseline_gain"))
        meta = {"source": source, "host": r["host"], "manifest": m.get("_path"), "progress": s.get("progress"), "final_name": m.get("final_name"),
                "overrides_md5": ovm, "overrides_matched": bool(ov), "publish_class": m.get("publish_class"), "gain_vs_bh": _f(mt.get("gain_vs_bh")),
                "invalid_reason": mt.get("invalid_reason"), "counts": m.get("counts"), "pilot_md5": m.get("pilot_md5"), "file_md5": m.get("file_md5"),
                "npz": m.get("npz_md5") or s.get("npz"), "npz_name": m.get("npz_name"), "span_days": m.get("span_days"), "template": m.get("template"),
                "verdict": s.get("verdict"), "result_done_utc": s.get("result_done_utc"), "n_overrides": len(ov) if ov else None,
                "template_md5_at_record": tpl.get(cs), "recorded_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")}
        if source == "fleet_orchestrator_publish":
            meta["npz_archived"] = archive_board_npz(ss, m.get("npz_name"), meta.get("npz"), r.get("host"))
        con.execute(
            "INSERT INTO per_sym_history (sym_side, updated_at, epoch, cat_side, template_md5, defaults_round, "
            "winning_tag, gain_pct, baseline_gain, delta_pct, bh_pct, trades, pool_sharpe, max_dd_pct, tim_pct, "
            "valid, overrides_json, full_config_json, defaults_snapshot_json, meta_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (ss, pub, epoch, cs, tpl.get(cs, ""), str(s.get("defaults_round") or ""), f"pilot30d_{pub[:10]}", gain, base,
             (gain - base) if gain is not None and base is not None else None, _f(mt.get("bh")), _i(mt.get("trades")),
             None,  # no per-trade return list in the manifest -> no pool_sharpe (NO-LIES rule 6), never a placeholder
             _f(mt.get("max_dd_pct")), _f(mt.get("tim_pct")), 1 if mt.get("valid") else 0,
             json.dumps(ov, sort_keys=True, default=str) if ov else None, None, None, json.dumps(meta, default=str)))
        wrote += 1
    if not args.dry_run:
        con.commit()
    total = con.execute("SELECT COUNT(*) FROM per_sym_history").fetchone()[0]
    days = con.execute("SELECT COUNT(DISTINCT substr(updated_at,1,10)) FROM per_sym_history").fetchone()[0]
    syms = con.execute("SELECT COUNT(DISTINCT sym_side) FROM per_sym_history").fetchone()[0]
    newest = con.execute("SELECT MAX(updated_at) FROM per_sym_history").fetchone()[0]
    con.close()
    print(json.dumps({"db": args.db, "check": ok, "per_host_manifests": per_host, "boards": len(boards), "written": wrote, "written_without_set": noset,
                      "already_recorded": dup, "history_rows": total, "distinct_days": days, "distinct_sym_sides": syms, "newest_row": newest,
                      "backfill": bool(args.backfill), "dry_run": bool(args.dry_run)}, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())
