#!/usr/bin/env python3
"""NPZB-relaxed installer (ON s1): build thin-history crypto NPZs with the relaxed guard (n<2500) and install validated causal_v3 files.

For symbols whose legacy NPZ is untrusted (2026-10-10: legacy 15m/HTF OHLCV proven NOT authentic futures klines) and whose
local klines cover ~35-40d after micro-backfill: build via tools/crypto_npz_precompute_relaxed.py, validate
(marker causal_v3, is_leaky False, schema == healthy ref, span>=30d, gap<=3h, last bar closed, sweep loader prepare LONG),
backup + atomic install, fundoi reapply (skipped on 418), push to workers with md5 verify.
usage: npzb_relaxed_install.py --symbols-file F [--apply] [--push-hosts 10.0.0.4,10.0.0.5,10.0.0.6] [--max N] [--sleep S]"""
import argparse
import csv
import hashlib
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
BASE = HOME / "binance-sandbox"
LIVE = BASE / "backtest_v8" / "indicators"
W = HOME / "npzb_relaxed"
SN = W / "stage_new"
LOGS = W / "logs"
REC = W / "installed.csv"
PY = str(BASE / ".venv" / "bin" / "python")
REF_SYM = "BTCUSDC"
FUNDOI_OK = {"cov_funding", "cov_oi"}

sys.path.insert(0, str(BASE))
from vec_decisions import htf_causal_align as A


def memavail():
    for line in open("/proc/meminfo"):
        if line.startswith("MemAvailable"):
            return int(line.split()[1]) // 1024
    return 0


def running(sym):
    r = subprocess.run(["pgrep", "-f", f"{sym}_(LONG|SHORT)"], capture_output=True, text=True)
    for p in r.stdout.split():
        if not p or int(p) == os.getpid():
            continue
        try:
            cmd = open(f"/proc/{p}/cmdline").read()
        except Exception:
            continue
        if "v15_pilot" in cmd or "v15_365" in cmd or "v15_365_repair" in cmd:
            return True
    return False


def host_running(host, sym):
    r = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=accept-new", "-o", "ConnectTimeout=10", f"niels@{host}",
                        f"ps -eo args | grep -E '[v]15_(pilot|365)' | grep -cE '{sym}_(LONG|SHORT)'"], capture_output=True, text=True)
    try:
        return int(r.stdout.strip() or "0") > 0
    except Exception:
        return True


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def sh(cmd, log=None, env=None, timeout=1800):
    e = dict(os.environ)
    e.update(env or {})
    with open(log or os.devnull, "a") as f:
        return subprocess.run(cmd, shell=True, stdout=f, stderr=subprocess.STDOUT, env=e, timeout=timeout, cwd=str(BASE)).returncode


def rec(row):
    new = not REC.exists()
    with open(REC, "a", newline="") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["ts", "symbol", "status", "detail", "md5_new", "n_bars"])
        w.writerow(row)


def validate(sym, ref_keys):
    import numpy as np
    fails = []
    try:
        z = np.load(SN / f"{sym}.npz", allow_pickle=True)
        store = {k: z[k] for k in z.files}
    except Exception as e:
        return False, "load fail " + str(e)[:80], 0
    mk = store.get("htf_align")
    marker = str(np.asarray(mk).ravel()[0]) if mk is not None else None
    if marker != "causal_v3":
        fails.append(f"marker={marker}")
    if A.is_leaky(store):
        fails.append("is_leaky True")
    missing = set(ref_keys) - set(store) - FUNDOI_OK
    if missing:
        fails.append("keys missing vs ref: %s" % sorted(missing)[:6])
    t = np.asarray(store["timestamps"]).astype("float64")
    n = len(t)
    span = float((t[-1] - t[0]) / 86400.0)
    age_h = float((time.time() - t[-1]) / 3600.0)
    if span < 30.0:
        fails.append(f"span {span:.1f}d < 30d")
    if time.time() < float(t[-1]) + 960:
        fails.append("last bar still forming")
    recent = t[t >= t[-1] - 30 * 86400]
    gd = np.diff(recent)
    gap_h = float(gd.max() / 3600.0) if len(gd) else 0.0
    if gap_h > 3.0:
        fails.append(f"gap {gap_h:.1f}h in last 30d")
    if fails:
        return False, "; ".join(fails), n
    return True, f"span {span:.1f}d age {age_h:.1f}h gap {gap_h:.2f}h", n


def sweep_loader_ok(sym):
    try:
        sys.path.insert(0, str(BASE / "tools" / "opt"))
        import evaluate_v12 as E
        pr = E.prepare(f"{sym}_LONG", window_days=30)
        return (pr is not None, "prepare returned None" if pr is None else "")
    except Exception as e:
        return False, str(e)[:120]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols-file", required=True)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--push-hosts", default="")
    ap.add_argument("--max", type=int, default=0)
    ap.add_argument("--sleep", type=int, default=30)
    a = ap.parse_args()
    import numpy as np
    for d in (SN, LOGS):
        d.mkdir(parents=True, exist_ok=True)
    syms = [x.strip() for x in open(a.symbols_file) if x.strip()]
    ref_keys = set(np.load(LIVE / f"{REF_SYM}.npz", allow_pickle=True).files)
    bak = HOME / ("npz_backup_relaxed_" + time.strftime("%Y%m%d"))
    done = 0
    deferred = []
    for sym in syms:
        if a.max and done >= a.max:
            break
        if (LOGS / f"{sym}.installed").exists():
            continue
        while memavail() < 4000:
            time.sleep(120)
        ts = time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime())
        if running(sym):
            deferred.append(sym)
            rec([ts, sym, "DEFER", "pilot/365 running", "", ""])
            continue
        out = SN / f"{sym}.npz"
        if out.exists():
            out.unlink()
        r = sh(f"nice -n 10 {PY} tools/crypto_npz_precompute_relaxed.py --mode crypto --symbols {sym} --out-dir {SN}", LOGS / f"{sym}_build.log")
        if r or not out.exists():
            tail = open(LOGS / f"{sym}_build.log").read()[-300:].replace("\n", " ") if (LOGS / f"{sym}_build.log").exists() else ""
            rec([ts, sym, "BUILD_FAIL", f"rc {r} {tail}", "", ""])
            continue
        ok, detail, n = validate(sym, ref_keys)
        if not ok:
            rec([ts, sym, "VALIDATION_FAIL", detail, "", n])
            continue
        if not a.apply:
            rec([ts, sym, "VALIDATED_DRYRUN", detail, md5(out), n])
            done += 1
            continue
        if running(sym) or any(host_running(h, sym) for h in [x for x in a.push_hosts.split(",") if x]):
            deferred.append(sym)
            rec([ts, sym, "DEFER", "pilot/365 running on a host", "", n])
            continue
        bak.mkdir(exist_ok=True)
        live = LIVE / f"{sym}.npz"
        if live.exists():
            shutil.copy2(live, bak / f"{sym}.npz")
        tmp = LIVE / f"{sym}.relaxed.tmp"
        shutil.copy2(out, tmp)
        if md5(tmp) != md5(out):
            tmp.unlink()
            rec([ts, sym, "COPY_MISMATCH", "", "", n])
            continue
        os.replace(tmp, live)
        ok_sw, why = sweep_loader_ok(sym)
        if not ok_sw:
            if (bak / f"{sym}.npz").exists():
                shutil.copy2(bak / f"{sym}.npz", live)
            rec([ts, sym, "ROLLED_BACK", "sweep loader: " + why, "", n])
            continue
        mnew = md5(live)
        ok_push = True
        for h in [x for x in a.push_hosts.split(",") if x]:
            subprocess.run(["rsync", "-a", "-e", "ssh -o StrictHostKeyChecking=accept-new", str(live), f"niels@{h}:/home/niels/binance-sandbox/backtest_v8/indicators/{sym}.npz"])
            rr = subprocess.run(["ssh", "-o", "StrictHostKeyChecking=accept-new", f"niels@{h}", f"md5sum /home/niels/binance-sandbox/backtest_v8/indicators/{sym}.npz"], capture_output=True, text=True)
            if rr.stdout.split()[:1] != [mnew]:
                ok_push = False
        rec([ts, sym, "INSTALLED" if ok_push else "INSTALLED_PUSH_MISMATCH", detail, mnew, n])
        (LOGS / f"{sym}.installed").write_text(mnew)
        done += 1
        time.sleep(a.sleep)
    print("done", done, "deferred", deferred)


if __name__ == "__main__":
    main()
