#!/usr/bin/env python3
"""Detached dispatch job (USER 2026-10-13: "the npz goes WITH THE PUSH of the sym_side, calculated on s1 before dispatching").
The fleet scheduler tick has a hard 110 s cron timeout; a synchronous NPZ rebuild (minutes per stock symbol once the US
market is open) killed every tick from 13:44Z and the fleet launched nothing. The tick now enqueues this job instead:
  1. NPZ rebuild + md5-verified push on S1 (per-symbol single flight + V15_NPZ_PREP_SLOTS concurrent builds, default 8),
  2. ssh launch of the pilot(s) on the target host — ONLY after step 1 (JIT law: NPZ fresh right before load).
ok=False from the prep tool = sym_side must not launch (stale tail it could not heal) -> skip, the scheduler retries later.
Prep tool crash / lock wait timeout = launch with what the host has (USER 2026-10-13: "work with what you have").
Every outcome is one JSON line in /tmp/v15_dispatch.log."""
import argparse, fcntl, json, os, pathlib, re, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
LOG = pathlib.Path(os.environ.get("V15_DISPATCH_LOG", "/tmp/v15_dispatch.log"))


def log(rec):
    rec["ts"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    try:
        with LOG.open("a") as f:
            f.write(json.dumps(rec) + "\n")
    except OSError:
        pass


def _acquire_slot(n_slots, wait_s):
    """Counting semaphore over flock files: at most n_slots NPZ builds at once (a stock build is ~20 single-core
    minutes; the old single global lock queued every dispatch of the fleet behind one build -> idle fleet)."""
    deadline = time.time() + wait_s
    while True:
        for i in range(max(1, n_slots)):
            f = open(f"/tmp/v15_npz_prep.slot{i}.lock", "w")
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return f
            except OSError:
                f.close()
        if time.time() >= deadline:
            return None
        time.sleep(5)


def prep(symbol, push):
    """Per-symbol single flight (same symbol never built twice at once) + fleet-wide build slots."""
    t0 = time.time()
    sym_lock = open(f"/tmp/v15_npz_prep.{symbol.upper()}.lock", "w")
    wait_s = float(os.environ.get("V15_NPZ_LOCK_WAIT_S", "1500"))
    try:
        fcntl.flock(sym_lock, fcntl.LOCK_EX)
    except OSError:
        pass
    slot = _acquire_slot(int(os.environ.get("V15_NPZ_PREP_SLOTS", "8")), wait_s)
    if slot is None:
        return {"ok": None, "reason": "slot-wait-timeout", "prep_s": round(time.time() - t0, 1)}
    cmd = [sys.executable, str(ROOT / "tools" / "v15_npz_prep.py"), "--symbol", symbol]
    if push:
        cmd += ["--push", push]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=float(os.environ.get("V15_NPZ_PREP_TIMEOUT_S", "2400")), cwd=str(ROOT))
        lines = [l for l in (r.stdout or "").strip().splitlines() if l.strip()]
        res = json.loads(lines[-1]) if lines else {"ok": None, "reason": f"no-output rc={r.returncode} {(r.stderr or '')[-160:]}"}
    except subprocess.TimeoutExpired:
        res = {"ok": None, "reason": "prep-timeout"}
    except (OSError, ValueError) as e:
        res = {"ok": None, "reason": f"prep-error {e}"[:120]}
    finally:
        slot.close()
        sym_lock.close()
    res["prep_s"] = round(time.time() - t0, 1)
    return res


def _dedup_lock(tag):
    """One live job per dispatch tag (a dead tick that never saved dispatch_q re-dispatched the same tag every 2 min)."""
    f = open("/tmp/v15_dispatch_job." + "".join(c if c.isalnum() else "_" for c in tag) + ".lock", "w")
    try:
        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return f
    except OSError:
        f.close()
        return None


def _all_30d(tag):
    legs = tag.split(":", 2)[-1].split("+") if tag.count(":") >= 2 else []
    return bool(legs) and all(l[1:].startswith("30D") for l in legs)


def tail_refresh(symbol):
    """USER 2026-10-13: "the new test ALWAYS runs on the latest npz, never on old info". Append only the missing closed
    15m bars to S1's NPZ with tools/npz_bar_refresh.py (builder's own indicator code, guard + atomic replace; ~9 s per
    stock symbol) instead of a full-history rebuild. A frozen symbol (unfinished board / running pilot) is never touched."""
    try:
        sys.path.insert(0, str(ROOT))
        from tools.v15_npz_freeze import is_frozen
        if is_frozen(symbol):
            return {"tail": "frozen"}
    except ImportError:
        pass
    mode = "crypto" if symbol.endswith(("USDT", "USDC")) else "stocks"
    t0 = time.time()
    try:
        r = subprocess.run([str(ROOT / ".venv" / "bin" / "python"), str(ROOT / "tools" / "npz_bar_refresh.py"), "--symbols", symbol, "--mode", mode, "--w15", "2500", "--workers", "2", "--no-sentiment", "--no-fng"], capture_output=True, text=True, timeout=float(os.environ.get("V15_TAIL_REFRESH_TIMEOUT_S", "300")), cwd=str(ROOT))
        st = "INSTALLED" if '"status": "INSTALLED"' in (r.stdout or "") else ("current" if r.returncode == 0 else f"rc={r.returncode}")
    except subprocess.TimeoutExpired:
        st = "timeout"
    return {"tail": st, "tail_s": round(time.time() - t0, 1)}


def fast_push(symbol, push):
    """S1 NPZ exists -> tail-refresh it to the latest closed bar, push it (rsync, md5 verify) and launch; None -> fall back to the builder (no NPZ on S1)."""
    import hashlib
    live = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
    if not live.exists():
        return None
    tr = tail_refresh(symbol) if os.environ.get("V15_DISPATCH_TAIL_REFRESH", "1") == "1" else {"tail": "off"}
    if tr.get("tail") == "frozen":
        # a board on this symbol is unfinished/running: its host keeps the NPZ it started on (never push a different one)
        return {"ok": True, "action": "frozen-keep", "reason": "frozen symbol: target keeps its board's NPZ", "prep_s": 0.0}
    t0 = time.time()
    if not push:
        return {"ok": True, "action": "fast30d-local", "reason": f"S1 NPZ tail {tr.get('tail')}", "prep_s": tr.get("tail_s", 0.0)}
    h = hashlib.md5(live.read_bytes()).hexdigest()
    dst = f"{push}:binance-sandbox/backtest_v8/indicators/{symbol}.npz"
    try:
        subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15 -o StrictHostKeyChecking=accept-new", str(live), dst], timeout=300, check=True)
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", push, f"md5sum binance-sandbox/backtest_v8/indicators/{symbol}.npz"], capture_output=True, text=True, timeout=60)
        ok = r.stdout.split()[0] == h if r.stdout else False
    except (subprocess.SubprocessError, OSError, IndexError):
        ok = False
    return {"ok": True if ok else None, "action": "fast30d-push", "reason": f"tail {tr.get('tail')} ({tr.get('tail_s')}s); pushed S1 NPZ md5 {h[:8]} verified={ok}", "prep_s": round(time.time() - t0, 1)}


def ship_prev_best(symbol, tag, push):
    """USER 2026-10-13: the first baseline is ALWAYS the best of last round's best settings and the cat_side defaults.
    Write the top distinct published sets of every 30D side in this dispatch from S1's per_sym_history (orchestrator
    publish history) to ~/v15_prev_best/{ss}.json and push them with the job; the pilot re-measures them at baseline."""
    import sqlite3
    db = ROOT / "data" / "hourly_reconfig" / "per_sym_store.db"
    out_dir = pathlib.Path(os.path.expanduser("~/v15_prev_best"))
    out_dir.mkdir(parents=True, exist_ok=True)
    sides = [("LONG" if l[0] == "L" else "SHORT") for l in tag.split(":", 2)[-1].split("+") if l[1:].startswith("30D")]
    files = []
    try:
        con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=10)
        for side in sides:
            ss = f"{symbol}_{side}"
            rows = con.execute("select gain_pct, overrides_json, updated_at from per_sym_history where sym_side=? and overrides_json is not null and gain_pct is not null order by gain_pct desc limit 40", (ss,)).fetchall()
            sets, seen = [], set()
            for g, oj, ts in rows:
                try:
                    ov = json.loads(oj)
                except ValueError:
                    continue
                if not isinstance(ov, dict) or len(ov) < 5:
                    continue
                key = json.dumps(ov, sort_keys=True, default=str)
                if key in seen:
                    continue
                seen.add(key)
                sets.append({"gain_recorded": g, "updated_at": ts, "overrides": ov})
                if len(sets) >= 5:
                    break
            if sets:
                f = out_dir / f"{ss}.json"
                f.write_text(json.dumps({"sym_side": ss, "sets": sets, "source": "per_sym_history", "written_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}))
                files.append(str(f))
        con.close()
    except Exception as e:
        log({"event": "prev_best_warn", "symbol": symbol, "err": str(e)[:160]})
        return 0
    if push and files:
        try:
            subprocess.run(["ssh", "-o", "BatchMode=yes", push, "mkdir -p ~/v15_prev_best"], timeout=30)
            subprocess.run(["rsync", "-a", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=15"] + files + [f"{push}:v15_prev_best/"], timeout=120)
        except subprocess.SubprocessError:
            pass
    return len(files)


AUTOTUNE_KNOB_RE = re.compile(r"^V15_V17_(COMBO_SHARE(_WINNER)?|SHARE_(SURGEON|SURGEON_WINNER|FOCUS|ABLATION|RETEST)|P3_MAX_S_(WINNER|MEDIOCRE|LOSER|LOST_CAUSE)|P_MIN)$")
AUTOTUNE_BOUNDS = {"SHARE": (0.05, 0.95), "P3_MAX_S": (30.0, 3600.0), "P_MIN": (0.001, 0.05)}


def _autotune_exports(tag, path=None):
    """V17 KPI auto-tuner reader hook (BIBLE "V17 KPI + AUTO-TUNE"): `export K=V; ` prefix for the remote launch command from
    data/kpi/v17_autotune.json (tools/v17_kpi.py --autotune). Only whitelisted pinpoint knobs, finite numbers inside hard
    bounds, apply=true, file <= V17_AUTOTUNE_MAX_AGE_D (3) days old, a 30D leg in the tag; V17_AUTOTUNE_APPLY=0 = off. Never
    the 0.3 rule (V15_V17_MIN_LIFT_PM) or the absolute cap (V15_V17_P3_MAX_S). An inline VAR=… in the scheduler's own env
    string still wins (it is assigned after the export). Any problem -> '' (pilot defaults)."""
    if os.environ.get("V17_AUTOTUNE_APPLY", "1") == "0" or (tag and ":" in tag and "30D" not in tag):
        return "", None
    p = pathlib.Path(path or (ROOT / "data" / "kpi" / "v17_autotune.json"))
    try:
        doc = json.loads(p.read_text())
        if not doc.get("apply") or time.time() - p.stat().st_mtime > float(os.environ.get("V17_AUTOTUNE_MAX_AGE_D", "3")) * 86400:
            return "", None
        out = {}
        for k, v in (doc.get("env") or {}).items():
            if not AUTOTUNE_KNOB_RE.match(str(k)) or isinstance(v, bool):
                continue
            x = float(v)
            lo, hi = next(b for key, b in AUTOTUNE_BOUNDS.items() if key in k)
            if x == x and lo <= x <= hi:
                out[k] = round(x, 6)
    except (OSError, ValueError, TypeError, StopIteration):
        return "", None
    if not out:
        return "", None
    return "".join(f"export {k}={v}; " for k, v in sorted(out.items())), {"generated_utc": doc.get("generated_utc"), "n": len(out)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", required=True)
    ap.add_argument("--push", default="", help="target ssh host for the md5-verified NPZ push ('' = local host)")
    ap.add_argument("--via", required=True, help="ssh target that runs the launch command")
    ap.add_argument("--cmd-file", required=True, help="file holding the remote launch shell command (deleted after use)")
    ap.add_argument("--tag", default="")
    a = ap.parse_args()
    cmd_path = pathlib.Path(a.cmd_file)
    remote = cmd_path.read_text()
    _guard = _dedup_lock(a.tag or a.symbol)
    if _guard is None:
        log({"event": "dup-skip", "symbol": a.symbol, "tag": a.tag})
        cmd_path.unlink(missing_ok=True)
        return 0
    res = None
    if os.environ.get("V15_DISPATCH_30D_FAST", "1") == "1":  # every window: tail-refresh + push (365D/REPAIR included)
        # USER 2026-10-13: "YOU DONT NEED A 365D NPZ FOR A 30D BACKTEST" — a full-history rebuild took 10+ min per stock
        # symbol and stalled every launch. 30D boards launch at once on S1's current NPZ (md5-verified push to the target);
        # the builder only runs when S1 has no NPZ for the symbol at all.
        res = fast_push(a.symbol, a.push)
    if res is None:
        res = prep(a.symbol, a.push)
    if res.get("ok") is False:
        log({"event": "skip", "symbol": a.symbol, "tag": a.tag, "prep": res})
        cmd_path.unlink(missing_ok=True)
        return 3
    if os.environ.get("V15_PREV_BEST_SHIP", "1") == "1":
        res["prev_best_files"] = ship_prev_best(a.symbol, a.tag, a.push)
    at_pre, at_info = _autotune_exports(a.tag)
    _wm = re.search(r"--workers (\d+)", remote)
    try:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", a.via, at_pre + remote], timeout=60)
        rc = r.returncode
    except subprocess.TimeoutExpired:
        rc = "ssh-timeout (job may still have started; pgrep dedups)"
    log({"event": "launched", "symbol": a.symbol, "tag": a.tag, "via": a.via, "ssh_rc": rc, "prep": res, "workers": int(_wm.group(1)) if _wm else None, "autotune": at_info})
    cmd_path.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
