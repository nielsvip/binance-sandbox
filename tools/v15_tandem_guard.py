#!/usr/bin/env python3
"""v15_tandem_guard — pre-market proof that EVERY default surface moved together (USER 2026-10-08:
"make sure whoever does the v15_avg_delta daily recount and the new templates, sql, json and configs
does NOT forget QuickConfig so everything is in tandem before market open every day").

Checks, for the newest S1 chain stamp (today, else yesterday):
  1. the S1 stamp is DONE and the Mac follow-up stamp for the SAME date is DONE
  2. every promoted key of that stamp agrees on all four default surfaces per cat_side:
     TEMPLATE bold == data/per_sym_settings.json == venue config (config.py / config_tradier.py) == QuickConfig
     (switch_parity.verify_default_surfaces, the chain's own audit)
  3. switch_parity.startup_gate hard == [] for the four cat_sides on the Mac
  4. config.py / config_tradier.py / v12_quick_engine.py are byte-identical (md5) on the Mac and on every fleet
     dir (sandbox + live) — the files live trading and the sweeps import
Writes data/daily_chain/TANDEM_<date>.json; on any failure also data/daily_chain/ALERT_TANDEM_<date>.txt, a macOS
notification, and exit 1 (cron-friendly, loud). Read-only: never edits a surface.

  python3 tools/v15_tandem_guard.py [--date YYYYMMDD] [--no-fleet] [--notify]
"""
import argparse
import datetime
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)
CODE = ["config.py", "config_tradier.py", "v12_quick_engine.py"]
CAT_SIDES = ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]
S1 = os.environ.get("V15_CHAIN_S1", "s1-pub")
FLEET = os.environ.get("V15_TANDEM_HOSTS", "s1-pub s2 s5 s6").split()
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10"]


def _ssh(host, cmd, timeout=60):
    r = subprocess.run(SSH + [host, cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip(), r.stderr.strip()


def _md5(path):
    return hashlib.md5(Path(path).read_bytes()).hexdigest() if Path(path).exists() else "MISSING"


def _mac_stamp(date):
    p = ROOT / "data" / "daily_chain" / f"{date}_mac_apply.json"
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _s1_stamp(date):
    rc, out, _ = _ssh(S1, f"cat ~/binance-sandbox/data/daily_chain/{date}.json 2>/dev/null")
    try:
        return json.loads(out) if rc == 0 and out else {}
    except Exception:
        return {}


def pick_date(explicit):
    if explicit:
        return explicit, _s1_stamp(explicit)
    today = datetime.datetime.now(datetime.timezone.utc)
    for d in (today, today - datetime.timedelta(days=1)):
        ds = d.strftime("%Y%m%d")
        st = _s1_stamp(ds)
        if st.get("status") == "DONE":
            return ds, st
    return today.strftime("%Y%m%d"), {}


HARD_CLASSES = ("bold-vs-quick", "bold-vs-cat", "bold-vs-global", "not-in-configs")  # a surface lags the template bold
INFO_CLASSES = ("side-split-cat-truth", "fallback-split")  # §67: one global cannot hold two side bolds; cat_side carries the side truth
DEAD_ROWS_KEPT = {"REENTRY_TIER2_MAX_MINUTES", "REENTRY_TIER2_MAX_MINUTES_TRADIER"}  # USER 2026-10-08: code removed, template rows left alone


def surfaces_ok(keys):
    """ALL keys, every cat_side (not only the day's promoted keys): a hard class means a surface forgot the bold."""
    import switch_parity as SP
    fossil = getattr(SP, "FOSSIL_HELD", frozenset())  # builder P0-FOSSIL pins: cat_side deliberately != bold (template lane owns the bold)
    bad, info = [], []
    for cs in CAT_SIDES:
        try:
            audit = SP.verify_default_surfaces(cs)
        except Exception as e:
            bad.append({"cat_side": cs, "error": repr(e)[:200]})
            continue
        for m in audit.get("mismatches") or []:
            k = m.get("key")
            row = {"cat_side": cs, **{kk: (str(vv)[:80] if vv is not None else None) for kk, vv in m.items() if kk in ("key", "class", "bold", "cat", "global", "quick", "other_bold")}, "promoted_today": k in keys}
            informational = m.get("class") in INFO_CLASSES or (cs, k) in fossil or k in DEAD_ROWS_KEPT
            if (cs, k) in fossil:
                row["note"] = "builder P0-FOSSIL pin"
            elif k in DEAD_ROWS_KEPT:
                row["note"] = "dead switch, rows kept by USER 2026-10-08"
            (info if informational else bad).append(row)
    return bad, info


def gates():
    import switch_parity as SP
    out = {}
    for cs in CAT_SIDES:
        try:
            g = SP.startup_gate(cs)
            out[cs] = {"ok": bool(g.get("ok")), "hard": [str(h)[:160] for h in (g.get("hard") or [])]}
        except Exception as e:
            out[cs] = {"ok": False, "hard": [f"gate error {e!r}"[:160]]}
    return out


def fleet_md5():
    mac = {f: _md5(f) for f in CODE}
    rows = []
    for h in FLEET:
        for d in ("binance-sandbox", "binance"):
            rc, out, err = _ssh(h, f"cd ~/{d} 2>/dev/null && md5sum {' '.join(CODE)}")
            got = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2} if rc == 0 else {}
            for f in CODE:
                rows.append({"host": h, "dir": d, "file": f, "mac": mac[f][:8], "fleet": (got.get(f) or ("UNREACHABLE" if rc else "MISSING"))[:12], "same": got.get(f) == mac[f]})
    return mac, rows


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--date")
    ap.add_argument("--no-fleet", action="store_true")
    ap.add_argument("--notify", action="store_true")
    a = ap.parse_args(argv)
    date, s1 = pick_date(a.date)
    mac = _mac_stamp(date)
    keys = set(k.split(":")[-1] for k in (s1.get("promoted_keys") or []))
    rep = {"date": date, "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(), "s1_stamp": s1.get("status") or "MISSING", "s1_ended": s1.get("ended"),
           "mac_stamp": mac.get("status") or "MISSING", "mac_failed_step": mac.get("failed_step"), "promoted_keys": len(keys), "failures": []}
    if s1.get("status") != "DONE":
        rep["failures"].append(f"S1 chain stamp {date} is {rep['s1_stamp']} — no defaults landed")
    if mac.get("status") != "DONE":
        rep["failures"].append(f"Mac follow-up stamp {date} is {rep['mac_stamp']} (step {mac.get('failed_step')}: {str(mac.get('why'))[:120]}) — config/QuickConfig NOT synced")
    rep["surface_mismatches"], rep["side_split_info"] = surfaces_ok(keys)
    if rep["surface_mismatches"]:
        by = {}
        for m in rep["surface_mismatches"]:
            by.setdefault(m.get("class") or "error", []).append(f"{m['cat_side']}:{m.get('key')}")
        rep["failures"].append("surfaces lag the TEMPLATE bold: " + "; ".join(f"{c} {len(v)} ({', '.join(v[:6])}{'…' if len(v) > 6 else ''})" for c, v in by.items()))
    rep["gates"] = gates()
    hard = {cs: g["hard"] for cs, g in rep["gates"].items() if g["hard"]}
    if hard:
        rep["failures"].append(f"startup gate HARD on {sorted(hard)}: {json.dumps(hard)[:300]}")
    if not a.no_fleet:
        rep["mac_md5"], rep["fleet"] = fleet_md5()
        diff = [r for r in rep["fleet"] if not r["same"]]
        if diff:
            rep["failures"].append(f"{len(diff)} fleet code files differ from the Mac: " + ", ".join(f"{r['host']}:{r['dir']}/{r['file']}={r['fleet']}" for r in diff[:8]))
    rep["ok"] = not rep["failures"]
    out = ROOT / "data" / "daily_chain" / f"TANDEM_{date}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1))
    line = f"[tandem-guard] {date} {'OK' if rep['ok'] else 'FAIL'} s1={rep['s1_stamp']} mac={rep['mac_stamp']} keys={len(keys)} failures={len(rep['failures'])}"
    print(line)
    for f in rep["failures"]:
        print("  -", f)
    if not rep["ok"]:
        (ROOT / "data" / "daily_chain" / f"ALERT_TANDEM_{date}.txt").write_text(line + "\n" + "\n".join(rep["failures"]) + "\n")
        if a.notify:
            subprocess.run(["osascript", "-e", f'display notification "{len(rep["failures"])} tandem failures — see data/daily_chain/ALERT_TANDEM_{date}.txt" with title "v15 TANDEM GUARD FAIL {date}"'], capture_output=True)
    return 0 if rep["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
