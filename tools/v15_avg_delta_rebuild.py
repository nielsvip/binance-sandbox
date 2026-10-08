#!/usr/bin/env python3
"""v15_avg_delta_rebuild — CANONICAL cross-sym_side average builder (USER 2026-09-30: every sym_side counts ONCE, LATEST deltas).

Supersedes the old pooled version (it counted every cell of every progress JSON: n=3002 for ~354 sym_sides). Backup of the old
file: backups/before_avgfix_*_v15_avg_delta_rebuild.py. The aggregation engine is tools/v15_vector_delta_rebuild.py.

Pipeline (run on the Mac; hosts from tools/fleet_hosts.json):
  1. per host: list every *_v14_progress.json (~/v15_*/progress, lifecycle_pilot) and --scan it remotely (stdlib only, script is
     copied to /tmp on the host) -> TSV symside/real/is56/final_gain/mtime/path
  2. select_latest(): per sym_side the NEWEST §56.0-era file written after the sign-fix cutoff (2026-09-28 08:00Z) whose real-cell
     count >= 50% of that sym_side's best (incomplete/garbage newest -> next newest). ONE file per sym_side, fleet-wide.
  3. per host --emit-partial on ONLY its selected files, Mac merges -> SPREADSHEETS/v15_avg_delta_latest.xlsx (+ dated archive,
     + compat copy v15_vector_delta_latest.xlsx). Columns: tab,name,kind,pos_sym,avg_delta,median_delta,n. Per (tab,kind,name) each
     sym_side contributes its single best value, so n <= #sym_sides of the cat_side (asserted).
"""
import argparse, concurrent.futures as cf, datetime, json, pathlib, shutil, subprocess, sys, tempfile

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_vector_delta_rebuild as R  # noqa: E402

import os  # noqa: E402
HOSTS_FILE = os.environ.get("V15_FLEET_HOSTS") or str(ROOT / "tools" / "fleet_hosts.json")  # S1 daily chain: V15_FLEET_HOSTS=tools/fleet_hosts_final.json (127.0.0.1/10.0.0.4/10.0.0.5)
HOSTS = json.load(open(HOSTS_FILE if os.path.isabs(HOSTS_FILE) else ROOT / HOSTS_FILE))["hosts"]
LIST_CMD = "ls ~/v15_*/progress/*_v14_progress.json ~/binance-sandbox/data/reports/lifecycle_pilot/*_v14_progress.json 2>/dev/null"


def ssh(h, cmd, timeout=1800):
    last = None
    for t in h["ssh"]:
        try:
            r = subprocess.run(["ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", t, cmd], capture_output=True, text=True, timeout=timeout)
        except Exception as e:
            last = str(e)
            continue
        if r.returncode in (0, 2):  # ls returns 2 when one glob is empty
            h["_via"] = t
            return r.stdout
        last = r.stderr[-200:]
    print(f"[avg] host {h['name']} unreachable: {last}", file=sys.stderr)
    return None


def scp_to(h, local, remote_path):
    """scp with the SAME alias fallback as ssh() — a stale s1-int mux must not kill the run (all aliases of a
    host are the same machine, so any reachable one works). Prefers h['_via'] if set, then the rest in order."""
    order = ([h["_via"]] if h.get("_via") in h["ssh"] else []) + [t for t in h["ssh"] if t != h.get("_via")]
    last = None
    for t in order:
        r = subprocess.run(["scp", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-q", str(local), f"{t}:{remote_path}"], capture_output=True, text=True)
        if r.returncode == 0:
            h["_via"] = t
            return t
        last = r.stderr[-200:]
    raise RuntimeError(f"scp to {h['name']} failed on all aliases {order}: {last}")


def scp_from(h, remote_path, local):
    order = ([h["_via"]] if h.get("_via") in h["ssh"] else []) + [t for t in h["ssh"] if t != h.get("_via")]
    last = None
    for t in order:
        r = subprocess.run(["scp", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes", "-q", f"{t}:{remote_path}", str(local)], capture_output=True, text=True)
        if r.returncode == 0:
            h["_via"] = t
            return t
        last = r.stderr[-200:]
    raise RuntimeError(f"scp from {h['name']} failed on all aliases {order}: {last}")


def scan_host(h):
    if ssh(h, "true", 30) is None:
        return h["name"], []
    via = h["_via"]
    scp_to(h, ROOT / "tools" / "v15_vector_delta_rebuild.py", "/tmp/v15_vdr.py")
    out = ssh(h, f"{LIST_CMD} > /tmp/v15_avg_files.txt; python3 /tmp/v15_vdr.py --files /tmp/v15_avg_files.txt --scan /tmp/v15_avg_scan.tsv >/dev/null && cat /tmp/v15_avg_scan.tsv")
    rows = []
    for l in (out or "").splitlines():
        p = l.split("\t")
        if len(p) == 6:
            rows.append((p[0], int(p[1]), int(p[2]), int(p[3]), float(p[4]), p[5], h["name"]))
    print(f"[avg] {h['name']}: {len(rows)} progress files scanned via {via}", flush=True)
    return h["name"], rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.datetime.utcnow().strftime("%Y%m%d"))
    ap.add_argument("--out", default=None)
    a = ap.parse_args()
    print(f"[avg] hosts file {HOSTS_FILE}: {[(h['name'], h['ssh']) for h in HOSTS]}", flush=True)
    with cf.ThreadPoolExecutor(len(HOSTS)) as ex:
        scans = list(ex.map(scan_host, HOSTS))
    allrows = [r for _, rows in scans for r in rows]
    sel = R.select_latest(allrows)
    per_host = {}
    for ss, r in sel.items():
        per_host.setdefault(r[6], []).append(r[5])
    print("[avg] selected latest file per sym_side:", {h: len(v) for h, v in per_host.items()}, "sym_sides:", len(sel), "of", len({r[0] for r in allrows}), "seen", flush=True)
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="avgd_"))
    parts = []
    for h in HOSTS:
        files = per_host.get(h["name"])
        if not files:
            continue
        lf = tmp / f"{h['name']}.txt"
        lf.write_text("\n".join(files) + "\n")
        scp_to(h, lf, "/tmp/v15_avg_sel.txt")
        ssh(h, "python3 /tmp/v15_vdr.py --files /tmp/v15_avg_sel.txt --emit-partial /tmp/v15_avg_part.json")
        dst = tmp / f"{h['name']}.json"
        scp_from(h, "/tmp/v15_avg_part.json", dst)
        parts.append(str(dst))
    dry = os.environ.get("V15_AVG_DRYRUN") == "1"  # daily chain DRYRUN: write only --out (+ selection json beside it), never the shared latest copies
    dated = a.out or str(R.ARCHIVE / f"v15_avg_delta_{a.date}.xlsx")
    selection = pathlib.Path(dated).with_suffix(".selection.json") if dry else ROOT / "data" / "avg_delta_selection.json"
    selection.parent.mkdir(parents=True, exist_ok=True)
    selection.write_text(json.dumps({s: {"host": r[6], "path": r[5], "real": r[1], "mtime": r[4]} for s, r in sel.items()}, indent=1))
    # USER 2026-10-08 (#4 approved): cat_side PRIORS from the autopsy-first bases — engine-verified combo deltas for sym_sides
    # that have NO board in this selection (never double-counted), same partial shape. V15_AUTOPSY_PRIORS=0 disables.
    if os.environ.get("V15_AUTOPSY_PRIORS", "1") != "0":
        _pp = tmp / "autopsy_priors.json"
        _pr = subprocess.run([sys.executable, str(ROOT / "tools" / "v15_autopsy_priors.py"), "--bases", os.environ.get("V15_AUTOPSY_BASES", os.path.expanduser("~/v15_autopsy_first")),
                              "--out", str(_pp), "--exclude-selection", str(selection)], capture_output=True, text=True, timeout=600)
        print((_pr.stdout or "").strip()[-400:] or f"[autopsy-priors] rc={_pr.returncode} {(_pr.stderr or '')[-300:]}", flush=True)
        if _pr.returncode == 0 and _pp.exists():
            parts.append(str(_pp))
    agg, seen = R.merge_partials(parts)
    for cs in R.CAT_SIDES:
        print(f"[avg] {cs}: sym_sides={len(seen[cs])}")
    R.write_workbook(agg, seen, dated)
    print(f"[dated] {dated}")
    if dry:
        print(f"[avg] V15_AVG_DRYRUN=1: latest copies NOT written (selection -> {selection})")
        return
    shutil.copyfile(dated, str(R.LATEST))
    shutil.copyfile(dated, str(R.COMPAT_LATEST))
    print(f"[latest] {R.LATEST} (+ compat {R.COMPAT_LATEST.name})")


if __name__ == "__main__":
    main()
