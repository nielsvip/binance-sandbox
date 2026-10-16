#!/usr/bin/env python3
"""V15 DRIFT WATCH — task 07.1 (USER 2026-10-15): boards claim +20% median, re-run gives +9.5% on
their own window (OOS audit 10-14). This tool re-evaluates the last N published sets on their own
window and flags drift > 1pp with the responsible component (engine / NPZ / template / set).

READ-ONLY: prints JSON lines to stdout, never writes state, installs no cron. Stdlib only.
Exit 0 = no drift over the threshold, 1 = drift flagged, 2 = usage error.
"""

import argparse
import datetime
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _md5_file(path, chunk=1 << 20):
    try:
        h = hashlib.md5()
        with open(str(path), "rb") as f:
            for b in iter(lambda: f.read(chunk), b""):
                h.update(b)
        return h.hexdigest()
    except Exception:
        return None


def _utcnow():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _load_manifests(out_dir, n):
    mans = []
    try:
        files = sorted(Path(out_dir).glob("*_manifest.json"), key=lambda q: q.stat().st_mtime, reverse=True)
    except Exception:
        return []
    for p in files:
        try:
            m = json.loads(p.read_text())
        except Exception:
            continue
        if not isinstance(m, dict) or not m.get("symside"):
            continue
        mans.append((p, m))
        if len(mans) >= n:
            break
    def _key(pm):
        try:
            return str((pm[1] or {}).get("published_utc") or "")
        except Exception:
            return ""
    mans.sort(key=_key, reverse=True)
    return mans[:n]


def _progress_overrides(symside, progress_dir):
    try:
        pj = json.loads((Path(progress_dir) / f"{symside}_v14_progress.json").read_text())
    except Exception:
        return None, "no-progress-file"
    ov = {k: v for k, v in ((pj or {}).get("cumulative_overrides") or {}).items() if not (isinstance(v, str) and " + " in v)}
    if not ov:
        return None, "empty-overrides"
    return ov, ""


def _current_ids(symside):
    ids = {"engine_md5": _md5_file(ROOT / "v12_quick_engine.py"), "npz_md5": None, "template_md5": None}
    try:
        from tools.v15_row_guards import npz_identity_for_symside as _nid
        _ni = _nid(symside) or {}
        ids["npz_md5"] = _ni.get("md5")
        ids["npz_ts_last"] = _ni.get("ts_last")
    except Exception:
        pass
    return ids


def check(ns):
    from tools.opt.v12_pilot import prepare_batch as _pb, evaluate_prepared_sanitized as _eps
    import v15_pilot as _P
    out_dir = ns.out_dir or str(ROOT / "SPREADSHEETS" / "V15_V16_CELL_BY_CELL")
    prog_dir = ns.progress_dir or str(ROOT / "data" / "reports" / "lifecycle_pilot")
    mans = _load_manifests(out_dir, ns.n)
    if not mans:
        print(json.dumps({"at": _utcnow(), "status": "NO_MANIFESTS", "out_dir": out_dir}))
        return 2
    flagged = 0
    for p, m in mans:
        symside = m.get("symside")
        rec = {"symside": symside, "manifest": p.name, "published_utc": m.get("published_utc")}
        try:
            recorded = (m.get("metrics") or {}).get("gain_pct")
            rec["recorded_gain"] = None if recorded is None else float(recorded)
        except Exception:
            rec["recorded_gain"] = None
        prov = m.get("provenance") or {}
        ov, why = _progress_overrides(symside, prog_dir)
        if ov is None:
            rec["status"] = "UNVERIFIABLE"
            rec["reason"] = why
            print(json.dumps(rec))
            continue
        try:
            san, _ = _P.sanitize_overrides(dict(ov), {})
        except Exception:
            san = dict(ov)
        try:
            prep = _pb(symside, int(ns.window_days))
        except Exception as e:
            rec["status"] = "UNVERIFIABLE"
            rec["reason"] = f"prepare-error:{e}"[:120]
            print(json.dumps(rec))
            continue
        if not isinstance(prep, dict) or prep.get("_stale"):
            rec["status"] = "UNVERIFIABLE"
            rec["reason"] = str((prep or {}).get("_stale") or "prepare-None")[:160]
            print(json.dumps(rec))
            continue
        try:
            fresh = _eps(prep, san, int(ns.window_days)) or {}
        except Exception as e:
            rec["status"] = "UNVERIFIABLE"
            rec["reason"] = f"eval-error:{e}"[:120]
            print(json.dumps(rec))
            continue
        fg = fresh.get("gain_pct")
        try:
            fg = None if fg is None else float(fg)
        except Exception:
            fg = None
        rec["fresh_gain"] = fg
        rec["fresh_trades"] = fresh.get("trades")
        rec["fresh_valid"] = fresh.get("valid")
        if rec["recorded_gain"] is None or fg is None:
            rec["status"] = "UNVERIFIABLE"
            rec["reason"] = "missing-gain"
            print(json.dumps(rec))
            continue
        drift = round(fg - rec["recorded_gain"], 4)
        rec["drift_pp"] = drift
        cur = _current_ids(symside)
        comp = {}
        try:
            if prov.get("engine_md5") and cur.get("engine_md5") and prov["engine_md5"] != cur["engine_md5"]:
                comp["engine"] = {"was": str(prov["engine_md5"])[:12], "now": str(cur["engine_md5"])[:12]}
            if prov.get("npz_sha") and cur.get("npz_md5") and prov["npz_sha"] != cur["npz_md5"]:
                comp["npz"] = {"was": str(prov["npz_sha"])[:12], "now": str(cur["npz_md5"])[:12]}
            tname = m.get("template")
            if prov.get("template_md5") and tname:
                for cand in (ROOT / "SPREADSHEETS" / "TEMPLATE_FINAL_NORM" / str(tname), ROOT / "SPREADSHEETS" / str(tname)):
                    if cand.exists():
                        tm = _md5_file(cand)
                        if tm and tm != prov["template_md5"]:
                            comp["template"] = {"was": str(prov["template_md5"])[:12], "now": str(tm)[:12]}
                        break
            try:
                import hashlib as _hl
                sh = _hl.md5(json.dumps(ov, sort_keys=True, default=str).encode()).hexdigest()
                if prov.get("published_set_hash") and sh != prov["published_set_hash"]:
                    comp["set"] = {"note": "progress set differs from published hash (board moved on)"}
            except Exception:
                pass
        except Exception:
            pass
        rec["components_changed"] = comp
        if abs(drift) > float(ns.drift_pp):
            rec["status"] = "DRIFT"
            flagged += 1
        else:
            rec["status"] = "OK"
        print(json.dumps(rec))
    print(json.dumps({"at": _utcnow(), "status": "DRIFT" if flagged else "OK", "checked": len(mans), "flagged": flagged, "threshold_pp": float(ns.drift_pp)}))
    return 1 if flagged else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="re-eval last N published sets on their own window, flag drift>1pp (read-only)")
    ap.add_argument("--n", type=int, default=10, help="last N published sets (default 10)")
    ap.add_argument("--out-dir", default="", help="published xlsx dir (default SPREADSHEETS/V15_V16_CELL_BY_CELL)")
    ap.add_argument("--progress-dir", default="", help="progress JSON dir (default data/reports/lifecycle_pilot)")
    ap.add_argument("--window-days", type=int, default=30, help="re-eval window = the board's own window (default 30)")
    ap.add_argument("--drift-pp", type=float, default=1.0, help="flag |fresh-recorded| above this (default 1.0)")
    ns = ap.parse_args(argv)
    if not ns.check:
        ap.print_help()
        return 2
    if (ns.n or 0) <= 0 or (ns.window_days or 0) <= 0:
        print("error: --n and --window-days must be positive", file=sys.stderr)
        return 2
    return check(ns)


if __name__ == "__main__":
    sys.exit(main())
