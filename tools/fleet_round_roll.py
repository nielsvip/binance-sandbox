#!/usr/bin/env python3
"""fleet_round_roll — roll the fleet_dispatch round (2026-10-28, run37 -> run38). Run from the Mac. DRY-RUN unless --apply.

The dispatcher (tools/fleet_dispatch.py) does not read data/v15_round_env.json: a round IS the per-host pair
~/v15_current_progress_dir.txt + ~/v15_defaults_round.txt, read by fleet_probe (pending/done) and fleet_launch.sh (pilot env).
Running pilots carry V15_PROGRESS_DIR in their env and finish into the old dir; nothing is killed.

  archive  --hosts s5,s6 [--to s2]   tar.zst of every progress JSON (*_v14_progress.json, not the delta-log tree) + every published
                                     final (*_bh*_30d_matrix.xlsx + manifest/html) of the CURRENT round, pulled by --to over the
                                     private network, listing-verified, then pulled to the Mac (~/v15_round_archives/).
  closeout --round run37             per tradeable sym_side the newest finished board across hosts -> data/reports/round_closeout_<run>.json
  flip     --round run38 --tag YYYYMMDD   fingerprint computed on S1 (same formula as tools/v15_autopilot.stage_restart), template +
                                     per_sym_settings md5 parity checked on every host (abort on mismatch), mkdir the new progress dir,
                                     wait for a finished dispatcher tick, write both pointer files on all hosts in parallel (tmp + mv),
                                     read back; any failure restores the old pointers on every host.
"""
import argparse
import concurrent.futures as cf
import datetime
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
HOSTS = {"s1": ("s1-pub", "10.0.0.3"), "s2": ("s2", "10.0.0.4"), "s5": ("s5", "10.0.0.5"), "s6": ("s6", "10.0.0.6")}
SSH = ["ssh", "-o", "ControlPath=none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=10"]
TEMPLATES = [f"SPREADSHEETS/TEMPLATE_FINAL_NORM/TEMPLATE_{v}_{s}.xlsx" for v in ("CRYPTO", "STOCKS") for s in ("LONG", "SHORT")]
SWEEP_DEF = "data/sweep_defaults/per_sym_settings.json"


def sh(name, script, timeout=120, inp=None):
    for attempt in range(3):
        r = subprocess.run(SSH + [HOSTS[name][0], script], capture_output=True, text=True, timeout=timeout, input=inp)
        if r.returncode != 255:
            return r.returncode, r.stdout, r.stderr
        time.sleep(3)
    return r.returncode, r.stdout, r.stderr


def pointers(name):
    rc, out, _ = sh(name, "cat ~/v15_current_progress_dir.txt; echo '|||'; cat ~/v15_defaults_round.txt")
    pdir, _, rdef = out.partition("|||")
    return rc, pdir.strip(), rdef.strip()


def fingerprint(round_id):
    script = (f"cd ~/binance-sandbox && python3 -c \"import hashlib,json;m=lambda p:hashlib.md5(open(p,'rb').read()).hexdigest();"
              f"e=json.load(open('data/engine_deploy/CURRENT.json')).get('engine_md5','unknown')[:8];"
              f"print('{round_id}-json'+m('{SWEEP_DEF}')[:8]+'-tpl'+m('{TEMPLATES[0]}')[:12]+'-eng'+e)\"")
    rc, out, err = sh("s1", script)
    return out.strip() if rc == 0 else None


def parity():
    files = TEMPLATES + [SWEEP_DEF]
    res = {}
    with cf.ThreadPoolExecutor(4) as pool:
        futs = {n: pool.submit(sh, n, "cd ~/binance-sandbox && md5sum " + " ".join(files)) for n in HOSTS}
        for n, f in futs.items():
            rc, out, _ = f.result()
            res[n] = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2}
    bad = {f: {n: res[n].get(f, "MISSING")[:12] for n in HOSTS} for f in files if len({res[n].get(f) for n in HOSTS}) != 1}
    return res, bad


def tick_quiet(max_wait=90):
    """Wait until the acting dispatcher's heartbeat says 'tick done' and we are mid-minute (cron fires each :00)."""
    t0 = time.time()
    while time.time() - t0 < max_wait:
        rc, out, _ = sh("s1", "cat ~/fleet_dispatch/heartbeat.json 2>/dev/null; echo; date +%S")
        lines = out.strip().splitlines()
        sec = int(lines[-1]) if lines and lines[-1].isdigit() else 0
        if "tick done" in out and 15 <= sec <= 40:
            return True
        time.sleep(3)
    return False


def cmd_flip(a):
    old = {n: pointers(n) for n in HOSTS}
    print("OLD pointers:")
    for n, (rc, p, r) in old.items():
        print(f"  {n}: rc={rc} pdir={p} round={r}")
    rdef = fingerprint(a.round)
    pdir = f"/home/niels/v15_{a.round}_{a.tag}/progress"
    res, bad = parity()
    for f in TEMPLATES + [SWEEP_DEF]:
        print(f"  md5 {pathlib.Path(f).name:28s} " + " ".join(f"{n}={res[n].get(f, 'MISSING')[:8]}" for n in HOSTS))
    print(f"NEW pdir={pdir}\nNEW defaults_round={rdef}")
    print("template/per_sym_settings parity:", "OK (identical on all hosts)" if not bad else json.dumps(bad, indent=1))
    expect = [x.strip() for x in (a.expect_tpl or "").split(",") if x.strip()]
    if expect:
        got = [res["s1"].get(f, "")[:len(e)] for f, e in zip(TEMPLATES, expect)]
        if got != expect or len(expect) != len(TEMPLATES):
            print(f"ABORT: templates on disk {got} != expected {expect} (order CL,CS,SL,SS)")
            return 2
        print(f"templates match expected {expect}")
    if not rdef or bad or any(o[0] != 0 for o in old.values()):
        print("ABORT: fingerprint unavailable, parity mismatch, or a host unreadable")
        return 2
    if not a.apply:
        print("DRY-RUN: would mkdir -p", pdir, "on", ",".join(HOSTS), "then write both pointer files in parallel inside one quiet tick window")
        return 0
    for n in HOSTS:
        rc, _, err = sh(n, f"mkdir -p {pdir} && test -d {pdir}")
        if rc != 0:
            print(f"ABORT: mkdir on {n} failed {err[-200:]}")
            return 2
    if not tick_quiet():
        print("ABORT: no quiet dispatcher window within 90 s")
        return 2
    write = (f"printf '%s\\n' {pdir} > ~/v15_current_progress_dir.txt.roll && printf '%s\\n' {rdef} > ~/v15_defaults_round.txt.roll && "
             f"mv -f ~/v15_current_progress_dir.txt.roll ~/v15_current_progress_dir.txt && mv -f ~/v15_defaults_round.txt.roll ~/v15_defaults_round.txt")
    with cf.ThreadPoolExecutor(4) as pool:
        rcs = dict(zip(HOSTS, pool.map(lambda n: sh(n, write)[0], HOSTS)))
    new = {n: pointers(n) for n in HOSTS}
    ok = all(rcs[n] == 0 and new[n][1] == pdir and new[n][2] == rdef for n in HOSTS)
    for n in HOSTS:
        print(f"  {n}: write rc={rcs[n]} now pdir={new[n][1]} round={new[n][2]}")
    if not ok:
        print("FAILED: restoring old pointers on every host")
        for n, (_, p, r) in old.items():
            sh(n, f"printf '%s\\n' {p} > ~/v15_current_progress_dir.txt && printf '%s\\n' {r} > ~/v15_defaults_round.txt")
        return 2
    print(f"ROLLED to {a.round} at {datetime.datetime.utcnow().strftime('%Y-%m-%dT%H:%M:%SZ')}")
    return 0


def cmd_archive(a):
    dest = a.to
    stamp = datetime.datetime.utcnow().strftime("%Y%m%dT%H%MZ")
    mac_dir = pathlib.Path.home() / "v15_round_archives"
    for n in a.hosts.split(","):
        rc, pdir, _ = pointers(n)
        ip = HOSTS[n][1]
        name = f"{pathlib.Path(pdir).parent.name}_{n}_{stamp}.tar.zst"
        listing = (f"cd {pdir} && ls *_v14_progress.json; cd ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL && "
                   f"ls *_bh*_30d_matrix.xlsx *_bh*_30d_matrix_manifest.json *_bh*_30d_matrix.html 2>/dev/null")
        rc, out, _ = sh(n, listing)
        files = [l for l in out.splitlines() if l.strip()]
        n_prog = sum(1 for f in files if f.endswith("_v14_progress.json"))
        n_fin = sum(1 for f in files if f.endswith("_30d_matrix.xlsx"))
        print(f"{n}: pdir={pdir} progress_json={n_prog} finals={n_fin} files={len(files)} -> {dest}:~/v15_round_archives/{name}")
        if not a.apply:
            continue
        pack = (f"tar -C / -cf - $(cd {pdir} && ls *_v14_progress.json | sed 's#^#{pdir.lstrip('/')}/#') "
                f"$(cd ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL && ls *_bh*_30d_matrix.xlsx *_bh*_30d_matrix_manifest.json *_bh*_30d_matrix.html 2>/dev/null "
                f"| sed 's#^#home/niels/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/#') | zstd -q -T2 -10")
        pull = (f"mkdir -p ~/v15_round_archives && ssh -o BatchMode=yes niels@{ip} {json.dumps(pack)} > ~/v15_round_archives/{name}.tmp && "
                f"n=$(zstd -dc ~/v15_round_archives/{name}.tmp | tar -tf - | wc -l) && [ $n -eq {len(files)} ] && "
                f"mv ~/v15_round_archives/{name}.tmp ~/v15_round_archives/{name} && echo OK $n $(du -h ~/v15_round_archives/{name} | cut -f1)")
        rc, out, err = sh(dest, pull, timeout=1800)
        print(f"  {dest} pull: rc={rc} {out.strip()} {err.strip()[-200:]}")
        if rc != 0:
            continue
        mac_dir.mkdir(parents=True, exist_ok=True)
        r = subprocess.run(["rsync", "-a", "-e", " ".join(SSH), f"{HOSTS[dest][0]}:v15_round_archives/{name}", str(mac_dir) + "/"], capture_output=True, text=True, timeout=3600)
        print(f"  Mac pull: rc={r.returncode} {mac_dir / name} {r.stderr.strip()[-200:]}")
    return 0


CENSUS = r'''
import json,glob,os
pdir=open(os.path.expanduser("~/v15_current_progress_dir.txt")).readline().strip()
out={}
for p in glob.glob(pdir+"/*_v14_progress.json"):
    try: d=json.load(open(p))
    except Exception: continue
    fp=str(d.get("final_path") or "")
    out[os.path.basename(p)[:-18]]={"done_utc":d.get("result_done_utc"),"final_gain":d.get("final_gain"),"final_path":os.path.basename(fp),
        "published":bool(fp) and os.path.exists(fp) and not d.get("not_compliant"),"not_compliant":d.get("not_compliant"),"verdict":d.get("verdict"),
        "rows":len(d.get("done") or {}),"holdout_confirmed":bool(d.get("holdout_confirm"))}
print(json.dumps(out))
'''


def cmd_closeout(a):
    rc, out, _ = sh("s1", "cat ~/binance-sandbox/data/v15_best_first.json")
    try:
        universe = sorted({str(s).upper() for s in json.loads(out).get("allowed") or [] if str(s).upper().endswith(("_LONG", "_SHORT"))})
    except ValueError:
        universe = []
    per_host = {}
    for n in HOSTS:
        rc, out, _ = sh(n, "python3 -", inp=CENSUS, timeout=300)
        try:
            per_host[n] = json.loads(out)
        except ValueError:
            per_host[n] = {}
    rec = {"round": a.round, "generated_at": datetime.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ"),
           "criterion": "per tradeable sym_side (S1 data/v15_best_first.json allowed): the newest finished board of this round across s1/s2/s5/s6; "
                        "published = compliant final_path present on disk. Written by tools/fleet_round_roll.py closeout (the scheduler-built "
                        "full_course_round_<run>.json stopped updating when fleet_dispatch replaced the scheduler on 2026-10-27).",
           "universe_n": len(universe), "sym_sides": {}}
    for ss in universe:
        best = None
        for n, boards in per_host.items():
            b = boards.get(ss)
            if b and (best is None or str(b.get("done_utc") or "") > str(best[1].get("done_utc") or "")):
                best = (n, b)
        rec["sym_sides"][ss] = dict(best[1], host=best[0]) if best else {"verdict": "NEVER_RUN"}
    vals = list(rec["sym_sides"].values())
    rec["n_published"] = sum(1 for v in vals if v.get("published"))
    rec["n_finished_unpublished"] = sum(1 for v in vals if v.get("done_utc") and not v.get("published"))
    rec["n_unfinished"] = sum(1 for v in vals if not v.get("done_utc"))
    print(json.dumps({k: v for k, v in rec.items() if k != "sym_sides"}, indent=1))
    if a.apply:
        path = ROOT / "data" / "reports" / f"round_closeout_{a.round}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(rec, indent=1, default=str))
        os.replace(tmp, path)
        print("written", path)
    return 0


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    f = sub.add_parser("flip")
    f.add_argument("--round", required=True)
    f.add_argument("--tag", required=True)
    f.add_argument("--apply", action="store_true")
    f.add_argument("--expect-tpl", default="", help="comma list of md5 prefixes in order CRYPTO_LONG,CRYPTO_SHORT,STOCKS_LONG,STOCKS_SHORT")
    r = sub.add_parser("archive")
    r.add_argument("--hosts", default="s5,s6")
    r.add_argument("--to", default="s2")
    r.add_argument("--apply", action="store_true")
    c = sub.add_parser("closeout")
    c.add_argument("--round", required=True)
    c.add_argument("--apply", action="store_true")
    a = ap.parse_args()
    return {"flip": cmd_flip, "archive": cmd_archive, "closeout": cmd_closeout}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
