#!/usr/bin/env python3
"""v15_gap_install_watcher — install verified NPZ rebuilds from ~/npzprep_gap (S1, 2026-10-14).

Scope: the 33-symbol tradier rebuild (PID 4101813, out ~/npzprep_gap/out) repairing
the 2026-08-26->2026-09-24 Tradier 15m hole + 1-row stubs, plus full-history crypto
rebuilds for TRBUSDT/WIFUSDC/ZROUSDT (deep klines, shallow 45d NPZs).

Rules (from tools/stock_npz_refresh.py + BACKTEST_BIBLE): gate-check every staged
file; NEVER replace a good file with a worse one (new tail must not be older than
installed, new span must not be shorter); backup installed before overwrite;
atomic install (tmp + os.replace); sweep-loader check after install with rollback
on failure; skip symbols with a running pilot (deferred, never forced).

Usage (S1): setsid -f .venv/bin/python -u tools/v15_gap_install_watcher.py
Log: ~/logs/v15_gap_install_watcher.log. Report: data/npz_gap_repair_report_<ts>.json
"""
import datetime
import fcntl
import glob
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
sys.path.insert(0, os.path.join(ROOT, "tools", "opt"))

STOCK_SYMBOLS = ["ADP", "AEM", "ALMU", "AXTI", "CALM", "CDW", "COP", "DHR", "ETH", "FANG", "FCX", "GILD", "GLD", "GM", "GOOGL", "LITE", "MRVL", "MSTR", "NUE", "NVDA", "NXE", "PLTR", "PR", "PWR", "PYPL", "RBLX", "RRC", "SCCO", "SLV", "TDG", "TECK", "TRGP", "TTD"]
CRYPTO_SYMBOLS = ["TRBUSDT", "WIFUSDC", "ZROUSDT"]
STOCK_OUT = os.path.expanduser("~/npzprep_gap/out")
CRYPTO_OUT = os.path.expanduser("~/npzprep_gap/out_crypto")
IND = os.path.join(ROOT, "backtest_v8", "indicators")
PRECOMP_PID = 4101813
WAIT_TIMEOUT_S = 20 * 3600
POLL_S = 300


def log(msg):
    print("[gap_watcher %sZ] %s" % (time.strftime("%H:%M:%S", time.gmtime()), msg), flush=True)


def md5(p):
    h = hashlib.md5()
    with open(p, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def gate_stock(p):
    from stock_npz_refresh import gate as _gate
    return _gate(p)


def gap_ok(p):
    import numpy as np
    from stock_npz_gap_guard import check_timestamps
    try:
        with np.load(str(p), allow_pickle=True) as z:
            return check_timestamps(z["timestamps"])
    except Exception as exc:
        return {"ok": False, "reason": "unreadable: %s" % str(exc)[:100]}


def last_bar(p):
    import numpy as np
    with np.load(str(p), allow_pickle=True) as z:
        t = z["timestamps"].astype("float64")
    k = 1000.0 if t[-1] > 1e11 else 1.0
    return float(t[-1] / k), float((t[-1] - t[0]) / k / 86400.0), int(len(t))


def pilot_running(sym):
    out = subprocess.run(["pgrep", "-f", "v15_pilot.py --sym-side %s_" % sym], capture_output=True, text=True).stdout.strip()
    return bool(out)


def sweep_loader_ok(sym):
    try:
        import evaluate_v12 as E
        return E.prepare("%s_LONG" % sym, window_days=30) is not None, ""
    except Exception as exc:
        return False, str(exc)[:120]


def install_stock(sym, out_dir, backup_dir, results, min_span_d=0):
    rec = {"symbol": sym, "action": "skip", "detail": ""}
    new = os.path.join(out_dir, "%s.npz" % sym)
    cur = os.path.join(IND, "%s.npz" % sym)
    if not os.path.exists(new):
        rec["detail"] = "no staged output"
        results.append(rec)
        return rec
    if pilot_running(sym):
        rec["action"] = "deferred"
        rec["detail"] = "pilot running, never forced"
        log("%s DEFERRED pilot running" % sym)
        results.append(rec)
        return rec
    ng = gate_stock(new)
    if not ng.get("ok"):
        rec["detail"] = "staged fails gate: %s" % ng.get("reason")
        log("%s SKIP staged gate fail %s" % (sym, ng.get("reason")))
        results.append(rec)
        return rec
    ngap = gap_ok(new)
    if not ngap.get("ok"):
        rec["detail"] = "staged fails gap guard: %s" % ngap.get("reason")
        log("%s SKIP staged gap fail %s" % (sym, ngap.get("reason")))
        results.append(rec)
        return rec
    if min_span_d and float(ng.get("span_d", 0)) < min_span_d:
        rec["detail"] = "staged span %.0fd < required %.0fd" % (float(ng.get("span_d", 0)), min_span_d)
        log("%s SKIP %s" % (sym, rec["detail"]))
        results.append(rec)
        return rec
    if os.path.exists(cur):
        cg = gate_stock(cur)
        try:
            nlast, nspan, _n = last_bar(new)
            clast, cspan, _n2 = last_bar(cur)
        except Exception as exc:
            rec["detail"] = "bar compare failed: %s" % str(exc)[:80]
            results.append(rec)
            return rec
        if cg.get("ok") and clast >= nlast:
            rec["detail"] = "installed tail newer/equal (inst %d vs staged %d), never replace good with worse" % (clast, nlast)
            log("%s SKIP %s" % (sym, rec["detail"]))
            results.append(rec)
            return rec
        if cspan > nspan + 1.0:
            rec["detail"] = "installed span %.0fd > staged %.0fd, refusing shrink" % (cspan, nspan)
            log("%s SKIP %s" % (sym, rec["detail"]))
            results.append(rec)
            return rec
        os.makedirs(backup_dir, exist_ok=True)
        bak = os.path.join(backup_dir, "%s.npz" % sym)
        shutil.copy2(cur, bak)
        rec["backup"] = bak
        rec["md5_before"] = md5(cur)
    tmp = cur + ".gap_install_tmp"
    shutil.copy2(new, tmp)
    os.replace(tmp, cur)
    rec["md5_after"] = md5(cur)
    ok, err = sweep_loader_ok(sym)
    if not ok:
        shutil.copy2(rec["backup"], tmp)
        os.replace(tmp, cur)
        rec["action"] = "rolledback"
        rec["detail"] = "sweep loader failed after install, restored backup: %s" % err
        log("%s ROLLEDBACK %s" % (sym, err))
        results.append(rec)
        return rec
    cgap = gap_ok(cur)
    rec["action"] = "installed"
    rec["detail"] = "gate ok span %.1fd age %.1fh gap_sessions %s" % (ng.get("span_d", 0), ng.get("age_h", 0), cgap.get("sessions"))
    log("%s INSTALLED %s" % (sym, rec["detail"]))
    results.append(rec)
    return rec


def pid_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def crypto_freshness(syms):
    import numpy as np
    out = {}
    now = time.time()
    for sym in syms:
        p = os.path.join(IND, "%s.npz" % sym)
        try:
            with np.load(str(p), allow_pickle=True) as z:
                t = z["timestamps"].astype("float64")
            k = 1000.0 if t[-1] > 1e11 else 1.0
            out[sym] = {"age_h": round((now - t[-1] / k) / 3600.0, 1), "span_d": round(float((t[-1] - t[0]) / k / 86400.0), 0), "n": int(len(t))}
        except Exception as exc:
            out[sym] = {"error": str(exc)[:80]}
    return out


def main():
    lock = open("/tmp/v15_gap_install_watcher.lock", "w")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("another watcher holds the lock, exiting", flush=True)
        return 2
    ts = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d%H%M")
    backup_dir = os.path.join(ROOT, "backups", "before_gap_install_%s" % ts)
    results = []
    log("start: waiting for precompute pid %d (timeout %dh)" % (PRECOMP_PID, WAIT_TIMEOUT_S // 3600))
    waited = 0
    while pid_alive(PRECOMP_PID) and waited < WAIT_TIMEOUT_S:
        staged = len(glob.glob(os.path.join(STOCK_OUT, "*.npz")))
        if staged >= len(STOCK_SYMBOLS):
            break
        time.sleep(POLL_S)
        waited += POLL_S
        if waited % 3600 < POLL_S:
            log("waiting %dh staged %d/%d" % (waited // 3600, staged, len(STOCK_SYMBOLS)))
    log("wait over (alive=%s) staged %d/%d, installing stock set" % (pid_alive(PRECOMP_PID), len(glob.glob(os.path.join(STOCK_OUT, "*.npz"))), len(STOCK_SYMBOLS)))
    for sym in STOCK_SYMBOLS:
        try:
            install_stock(sym, STOCK_OUT, backup_dir, results)
        except Exception as exc:
            log("%s INSTALL ERROR %s: %s" % (sym, type(exc).__name__, str(exc)[:120]))
            results.append({"symbol": sym, "action": "error", "detail": str(exc)[:120]})
    log("stock installs done, rebuilding short-history crypto %s" % ",".join(CRYPTO_SYMBOLS))
    os.makedirs(CRYPTO_OUT, exist_ok=True)
    cmd = [sys.executable, os.path.join(ROOT, "backtest_v8_precompute.py"), "--symbols", ",".join(CRYPTO_SYMBOLS), "--mode", "crypto", "--workers", "1", "--out-dir", CRYPTO_OUT]
    try:
        subprocess.run(["nice", "-n", "19"] + cmd, cwd=ROOT, timeout=10 * 3600)
    except Exception as exc:
        log("crypto rebuild failed: %s" % str(exc)[:120])
    for sym in CRYPTO_SYMBOLS:
        try:
            install_stock(sym, CRYPTO_OUT, backup_dir, results, min_span_d=365)
        except Exception as exc:
            results.append({"symbol": sym, "action": "error", "detail": str(exc)[:120]})
    crypto_state = crypto_freshness(["ZECUSDC", "AIAUSDT", "GRAMUSDT"] + CRYPTO_SYMBOLS)
    stock_bad = []
    for sym in STOCK_SYMBOLS:
        info = gap_ok(os.path.join(IND, "%s.npz" % sym))
        if not info.get("ok"):
            stock_bad.append({sym: info.get("reason")})
    report = {"utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "installs": results, "stock_still_bad": stock_bad, "crypto_state": crypto_state}
    rp = os.path.join(ROOT, "data", "npz_gap_repair_report_%s.json" % ts)
    tmp = rp + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=1)
    os.replace(tmp, rp)
    log("DONE installed=%d deferred=%d skipped=%d rolledback=%d stock_still_bad=%d report=%s" % (sum(1 for r in results if r["action"] == "installed"), sum(1 for r in results if r["action"] == "deferred"), sum(1 for r in results if r["action"] == "skip"), sum(1 for r in results if r["action"] == "rolledback"), len(stock_bad), rp))
    return 0


if __name__ == "__main__":
    sys.exit(main())
