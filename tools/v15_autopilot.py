#!/usr/bin/env python3
"""v15_autopilot — unattended round machine. Runs ON s1 from cron (*/5, flock singleton, resumable: every stage is idempotent, state in data/autopilot/state.json).

USER 2026-10-02: "non stop run on all 3 servers of stocks and crypto that recalculates averages and defaults, resumed where it left off no matter what, coordinated by s1".
Nothing here touches LIVE: sweep defaults live in data/sweep_defaults/cat_side_defaults_4.json (pilots read it through CAT_SIDE_DEFAULTS_PATH, set by the scheduler);
the live data/cat_side_defaults_4.json is NEVER written. Read AUTOPILOT_RUNBOOK.md before changing anything.

Round = SWEEP (the fleet scheduler cron launches 30D sym_side pilots into the current progress dir) -> COLLECT (per-host partial aggregates -> merged workbook)
        -> TEMPLATE (v15_daily_template_update --apply: AVG_DELTA/POS_SYM, promotion, worst_first; NO --sync-defaults) -> NORMALISE (TEMPLATE_FINAL_NORM)
        -> DEFAULTS (build_cat_side_defaults_4 into the sweep-only path) -> SYNC (s2/s5, md5 verified) -> RESTART (new progress dir + defaults round id) -> next round.
Every tick also: heartbeat (data/autopilot/STATUS.json), scheduler-liveness check, venues self-heal, quiet/stall handling (attempt reset), disk guard.
usage: v15_autopilot.py [--once] [--dry-run] [--force-stage collect|template|normalise|defaults|sync|restart] [--status]
"""
import argparse, datetime as dt, fcntl, glob, hashlib, json, os, re, shlex, shutil, subprocess, sys, time
from pathlib import Path

ROOT = Path(os.path.expanduser("~/binance-sandbox"))
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
AP = ROOT / "data" / "autopilot"
STATE = AP / "state.json"
STATUS = AP / "STATUS.json"
LOGMD = AP / "LOG.md"
SNAP = AP / "sched_snapshot.json"
HOSTS = ROOT / "tools" / "fleet_hosts_final.json"
SWEEP_DEF = ROOT / "data" / "sweep_defaults" / "cat_side_defaults_4.json"
TEMPLATES = [f"SPREADSHEETS/TEMPLATE_{v}_{s}.xlsx" for v in ("CRYPTO", "STOCKS") for s in ("LONG", "SHORT")]
PY = str(ROOT / ".venv" / "bin" / "python")
if not os.path.exists(PY):
    PY = sys.executable
CATS = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")
MIN_COV, MIN_COV_LATE, LATE_H, MAX_RESETS = 0.85, 0.60, 48.0, 4
QUIET_TICKS = 3
MIN_POS_SYM = int(os.environ.get("AUTOPILOT_MIN_POS_SYM", "3"))


def now_utc():
    return dt.datetime.now(dt.timezone.utc)


def jload(p, d):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def jsave(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_suffix(p.suffix + ".tmp"); t.write_text(json.dumps(obj, indent=1, default=str)); t.replace(p)


def log(msg):
    AP.mkdir(parents=True, exist_ok=True)
    line = f"- {now_utc().strftime('%Y-%m-%d %H:%M:%SZ')} {msg}"
    print(line, flush=True)
    with open(LOGMD, "a") as fh:
        fh.write(line + "\n")


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def run(cmd, timeout=3600, env=None, cwd=None):
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, env=dict(os.environ, **(env or {})), cwd=str(cwd or ROOT))
    return r.returncode, (r.stdout + r.stderr)[-3000:]


def hosts():
    return json.load(open(HOSTS))["hosts"]


def ssh_t(h):
    return h["ssh"][0]


def rsh(h, cmd, timeout=120):
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh_t(h), cmd], capture_output=True, text=True, timeout=timeout)
    return r.returncode, r.stdout.strip()


# ----------------------------------------------------------------------------- health
def disk_guard(st, dry):
    """>90 % used on /: delete ONLY regenerable clutter: run dirs BELOW v15_run18, autopilot template backups older than the last 12, 365D sweep logs. Run18+ / current / final dirs are Monday evidence (host_scan MIN_RUN=18) and are NEVER deleted."""
    u = shutil.disk_usage("/")
    pct = 100.0 * u.used / u.total
    st["disk_pct"] = round(pct, 1)
    if pct < 90 or dry:
        return
    keep = {f"v15_{st.get('round')}_", f"v15_{st.get('prev_round')}_"}
    try:
        cur_pdir = os.path.realpath(open(os.path.expanduser("~/v15_current_progress_dir.txt")).read().strip().splitlines()[0])
    except Exception:
        cur_pdir = ""
    runs = sorted(glob.glob(os.path.expanduser("~/v15_run*_2026*")), key=os.path.getmtime)
    freed, kept = [], []
    for d in runs:
        base = os.path.basename(d)
        m = re.match(r"v15_run(\d+)_2026\d+$", base)
        if (m and int(m.group(1)) >= 18) or os.path.realpath(d) == cur_pdir or "v15_final_" in base or any(base.startswith(k) for k in keep):
            kept.append(base); continue
        shutil.rmtree(d, ignore_errors=True); freed.append(base)
        if 100.0 * shutil.disk_usage("/").used / u.total < 85:
            break
    for d in sorted(glob.glob(str(ROOT / "backups" / "autopilot_*")), key=os.path.getmtime)[:-12]:
        shutil.rmtree(d, ignore_errors=True); freed.append(os.path.basename(d))
    for f in glob.glob("/tmp/sweep_*_365D.log"):
        try:
            os.remove(f)
        except OSError:
            pass
    log(f"disk guard: {pct:.1f}% used; removed {freed}; protected {len(kept)} run dirs (run18+ evidence, current, final)")


def ensure_venues(dry):
    """both venues on every host (the scheduler orders them by the US market clock); self-heal if an agent paused one."""
    cfg = json.load(open(HOSTS))
    ch = False
    for h in cfg["hosts"]:
        if sorted(h.get("venues", [])) != ["crypto", "stocks"]:
            h["venues"] = ["stocks", "crypto"]; ch = True
    if ch and not dry:
        shutil.copy2(HOSTS, str(HOSTS) + f".bak_autopilot_{now_utc().strftime('%Y%m%d%H%M')}")
        jsave(HOSTS, cfg)
        log("self-heal: fleet_hosts_final.json venues restored to [stocks, crypto] on every host")


def scheduler_alive(st, dry):
    snap = jload(SNAP, {})
    try:
        age = time.time() - dt.datetime.fromisoformat(snap["at"]).timestamp()
    except Exception:
        age = 1e9
    st["snapshot_age_s"] = int(age)
    if age > 600 and not dry:
        log(f"scheduler snapshot {int(age)}s old: running one tick myself")
        lock = open("/tmp/v15_fleet_tick.lock", "w")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return snap
        rc, out = run([sys.executable, "tools/v15_fleet_scheduler.py", "--once", "--hosts", "tools/fleet_hosts_final.json", "--max-attempts", "2"], timeout=110, env={"V15_SCHED_NO_CHAIN": "1"})
        snap = jload(SNAP, {})
    return snap


def coverage(snap):
    cats = snap.get("cats", {})
    ns = set(snap.get("non_shortable", []))
    out = {}
    for c in CATS:
        x = cats.get(c) or {}
        exp = int(x.get("sym_sides", 0))
        if c == "STOCKS_SHORT":
            exp -= len(ns)
        done = int(x.get("done30", 0))
        out[c] = {"expected": max(exp, 0), "done": done, "running": int(x.get("running", 0)), "cov": round(done / exp, 3) if exp > 0 else 0.0}
    return out


def reset_attempts(dry):
    """clear the scheduler's per-key attempt counters (they exhaust when symbols were unready); serialised with the cron tick via its lock."""
    import v15_fleet_scheduler as FS
    lock = open("/tmp/v15_fleet_tick.lock", "w")
    t0 = time.time()
    while True:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB); break
        except OSError:
            if time.time() - t0 > 100:
                return False
            time.sleep(3)
    s = FS.load_state()
    n = len(s.get("attempts", {}))
    if not dry:
        s["attempts"] = {}; s["ready"] = {}
        FS.save_state(s)
    log(f"attempt counters + readiness cache reset ({n} keys)")
    return True


def sweep_tick(st, snap, dry):
    cov = coverage(snap)
    st["coverage"] = cov
    busy = sum(v["running"] for v in cov.values()) + len(snap.get("launched", []))
    st["quiet_ticks"] = 0 if busy else st.get("quiet_ticks", 0) + 1
    age_h = (time.time() - dt.datetime.fromisoformat(st["round_started"]).timestamp()) / 3600
    minc = min(v["cov"] for v in cov.values()) if cov else 0
    st["round_age_h"] = round(age_h, 1)
    full = all(v["done"] >= v["expected"] > 0 for v in cov.values())
    if full and st["quiet_ticks"] >= 1:
        return True, "all expected sym_sides done"
    if st["quiet_ticks"] >= QUIET_TICKS:
        unready = set((snap.get("skipped_unready") or {}).keys())
        launchable = [x for x in snap.get("pending_stocks", []) + snap.get("pending_crypto", []) if x not in unready]
        st["launchable_pending"] = len(launchable)
        if launchable and st.get("resets", 0) < MAX_RESETS:
            st["resets"] = st.get("resets", 0) + 1
            reset_attempts(dry); st["quiet_ticks"] = 0
            return False, f"quiet with {len(launchable)} launchable pending (attempts exhausted?): attempts reset #{st['resets']}"
        if minc >= MIN_COV:
            return True, f"quiet, coverage {minc} >= {MIN_COV}, launchable pending {len(launchable)}"
        if age_h >= LATE_H and minc >= MIN_COV_LATE:
            return True, f"quiet, late ({age_h:.0f}h), coverage {minc} >= {MIN_COV_LATE}"
        st["alert"] = f"STALLED: quiet, coverage {minc}, resets exhausted — see AUTOPILOT_RUNBOOK.md"
    return False, f"sweeping cov={ {c: v['cov'] for c, v in cov.items()} } quiet={st['quiet_ticks']}"


# ----------------------------------------------------------------------------- round-end stages
def _collect_select_winners(hpdirs):
    """Newest-per-sym_side file selection across hosts (USER 2026-10-03: synced-copy dupes killed run24 collect).
    Pilots fan progress JSONs out to peers (quarantine-push + minute-level sync), so one sym_side file exists on
    2-3 hosts; merge_partials would count it twice and trip the no-double-count assert. Rule per sym_side: biggest
    file wins (most rows = freshest compute; stale copies are smaller), ties -> newest mtime, then host order
    s1<s2<s5. Returns ({host: [paths]}, dropped [(sym, winner, loser)]) or (None, []) on ANY error (fail-open:
    caller falls back to legacy all-files behavior)."""
    try:
        per_host = {}
        for idx, (h, pd) in enumerate(hpdirs):
            rc, o = rsh(h, f"stat -c '%s %Y %n' {pd}/*_v14_progress.json 2>/dev/null || true", timeout=60)
            if rc != 0:
                return None, []
            rows = []
            for line in o.splitlines():
                p = line.split(" ", 2)
                if len(p) != 3:
                    continue
                try:
                    rows.append((p[2], int(p[0]), int(p[1])))
                except ValueError:
                    continue
            per_host[h["name"]] = (idx, rows)
        by_sym = {}
        for hn, (idx, rows) in per_host.items():
            for path, size, mt in rows:
                ss = os.path.basename(path)
                if not ss.endswith("_v14_progress.json"):
                    continue
                by_sym.setdefault(ss[: -len("_v14_progress.json")], []).append((size, mt, -idx, hn, path))
        winners = {h["name"]: [] for h, _ in hpdirs}
        dropped = []
        for ss, cands in by_sym.items():
            cands.sort(reverse=True)
            win = cands[0]
            winners[win[3]].append(win[4])
            for c in cands[1:]:
                dropped.append((ss, win[3], c[3]))
        return winners, dropped
    except Exception:
        return None, []


def stage_collect(st, dry):
    rid = st["round"]
    out = AP / "partials" / rid
    out.mkdir(parents=True, exist_ok=True)
    hpdirs = []
    for h in hosts():
        rc, pd = rsh(h, "cat ~/v15_current_progress_dir.txt")
        if rc != 0 or not pd:
            log(f"collect: {h['name']} has no progress dir"); continue
        hpdirs.append((h, pd.strip().splitlines()[0]))
    winners, dropped = _collect_select_winners(hpdirs)
    if winners is None:
        log("collect: winner selection failed — legacy all-files fallback (dupes may trip the merge assert)")
    else:
        nw = sum(len(v) for v in winners.values())
        log(f"collect: dedupe {nw + len(dropped)} files -> {nw} winners, {len(dropped)} synced-copy dupes skipped" + (f" e.g. {sorted(set(d[0] for d in dropped))[:8]}" if dropped else ""))
    parts = []
    for h, pd in hpdirs:
        pf = f"/tmp/v15_partial_{h['name']}.json"
        if winners is None:
            manifest = f"ls {pd}/*_v14_progress.json > /tmp/v15_files.txt"
        else:
            manifest = "cat > /tmp/v15_files.txt <<'V15EOF'\n" + "\n".join(winners[h["name"]]) + "\nV15EOF"
        cmd = f"cd {h['root']} && {manifest} && .venv/bin/python tools/v15_vector_delta_rebuild.py --files /tmp/v15_files.txt --emit-partial {pf}"
        rc, o = rsh(h, cmd, timeout=900)
        if rc != 0:
            log(f"collect: {h['name']} emit-partial failed {o[-200:]}"); return False
        dst = out / f"{h['name']}.json"
        if h["name"] == "s1":
            shutil.copy2(pf, dst)
        else:
            r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", f"{ssh_t(h)}:{pf}", str(dst)], capture_output=True, text=True, timeout=300)
            if r.returncode != 0:
                log(f"collect: scp {h['name']} failed"); return False
        parts.append(str(dst))
    if not parts:
        return False
    if dry:
        return True
    rc, o = run([PY, "tools/v15_vector_delta_rebuild.py", "--merge", ",".join(parts)], timeout=900)
    if rc != 0:
        log(f"collect: merge failed {o[-300:]}"); return False
    ws = ROOT / "SPREADSHEETS" / "v15_vector_delta_latest.xlsx"
    if not ws.exists() or time.time() - ws.stat().st_mtime > 3600:
        log("collect: merged workbook not fresh"); return False
    shutil.copy2(ws, out / "v15_vector_delta_latest.xlsx")
    return True


def stage_template(st, dry):
    rid = st["round"]
    bk = ROOT / "backups" / f"autopilot_{rid}_{now_utc().strftime('%Y%m%d%H%M')}"
    bk.mkdir(parents=True, exist_ok=True)
    for t in TEMPLATES:
        shutil.copy2(ROOT / t, bk / Path(t).name)
    st["template_backup"] = str(bk)
    if dry:
        return True
    rc, o = run([PY, "tools/v15_daily_template_update.py", "--apply", "--round-id", rid, "--min-pos-sym", str(MIN_POS_SYM)], timeout=7000, env={"TEMPLATES_UNFREEZE": "1"})
    (AP / f"template_update_{rid}.log").write_text(o)
    viol = [l for l in o.splitlines() if "violations=" in l and "violations=0" not in l]
    if viol:
        st["alert"] = "template update: a template had verification violations and was NOT saved: " + " | ".join(v[:160] for v in viol)
        log(st["alert"])
    if rc != 0:
        log(f"template update FAILED rc={rc} (templates unchanged: the tool saves only after its checks) {o[-300:]}")
        return False
    return True


def stage_normalise(st, dry):
    if dry:
        return True
    rc, o = run([PY, "tools/v15_template_normalize_defaults.py", "--src", "SPREADSHEETS", "--out", "SPREADSHEETS/TEMPLATE_FINAL_NORM"], timeout=1800)
    if rc != 0:
        log(f"normalise FAILED {o[-300:]}"); return False
    return True


def stage_defaults(st, dry):
    SWEEP_DEF.parent.mkdir(parents=True, exist_ok=True)
    if not SWEEP_DEF.exists() and not dry:
        shutil.copy2(ROOT / "data" / "cat_side_defaults_4.json", SWEEP_DEF)
    if dry:
        return True
    if SWEEP_DEF.exists():
        shutil.copy2(SWEEP_DEF, SWEEP_DEF.parent / f"cat_side_defaults_4.before_{st['round']}.json")
    env = {"CSD4_TEMPLATE_DIR": str(ROOT / "SPREADSHEETS"), "CSD4_OUT": str(SWEEP_DEF), "CAT_SIDE_DEFAULTS_PATH": str(SWEEP_DEF), "CSD4_PROMOTIONS": str(ROOT / "data" / "cat_side_promotions.json")}
    rc, o = run([PY, "tools/build_cat_side_defaults_4.py"], timeout=1800, env=env)
    (AP / f"defaults_build_{st['round']}.log").write_text(o)
    if rc != 0 or not SWEEP_DEF.exists():
        log(f"defaults build FAILED rc={rc} {o[-300:]}"); return False
    try:
        d = json.loads(SWEEP_DEF.read_text())
        assert all(c in d and len(d[c]) > 100 for c in CATS)
    except Exception as e:
        log(f"defaults sanity FAILED {e}"); return False
    return True


def stage_sync(st, dry):
    if dry:
        return True
    files = TEMPLATES + [f"SPREADSHEETS/TEMPLATE_FINAL_NORM/{Path(t).name}" for t in TEMPLATES] + ["data/sweep_defaults/cat_side_defaults_4.json", "data/cat_side_promotions.json"]
    ok = True
    for h in hosts():
        if h["name"] == "s1":
            continue
        for rel in files:
            if not (ROOT / rel).exists():
                continue
            r = subprocess.run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", "--mkpath", str(ROOT / rel), f"{ssh_t(h)}:{h['root']}/{rel}"], capture_output=True, text=True, timeout=300)
            ok &= r.returncode == 0
        rc, out = rsh(h, f"cd {h['root']} && md5sum " + " ".join(f for f in files if (ROOT / f).exists()))
        got = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2}
        bad = [f for f in files if (ROOT / f).exists() and got.get(f) != md5(ROOT / f)]
        if bad:
            log(f"sync md5 mismatch {h['name']}: {bad}"); ok = False
    return ok


def stage_restart(st, dry):
    """next round: new empty progress dir + defaults round id on every host. Running pilots are NOT killed (they finish into the old dir)."""
    n = int(st["round"].replace("run", "")) + 1
    nid = f"run{n}"
    tag = f"{nid}_{now_utc().strftime('%Y%m%d')}"
    eng = jload(ROOT / "data" / "engine_deploy" / "CURRENT.json", {}).get("engine_md5", "unknown")[:8]
    rdef = f"{nid}-json{md5(SWEEP_DEF)[:8] if SWEEP_DEF.exists() else 'none'}-tpl{md5(ROOT / TEMPLATES[0])[:12]}-eng{eng}"
    if dry:
        return True
    for h in hosts():
        pd = f"$HOME/v15_{tag}/progress"
        rc, o = rsh(h, f"mkdir -p {pd} && echo {pd} > ~/v15_current_progress_dir.txt && echo {rdef} > ~/v15_defaults_round.txt && cat ~/v15_current_progress_dir.txt")
        if rc != 0:
            log(f"restart: {h['name']} failed {o}"); return False
    if not reset_attempts(False):
        return False
    st.update(prev_round=st["round"], round=nid, round_started=now_utc().isoformat(), quiet_ticks=0, resets=0, defaults_round=rdef)
    log(f"ROUND {nid} STARTED defaults_round={rdef}")
    return True


STAGES = [("collect", stage_collect), ("template", stage_template), ("normalise", stage_normalise), ("defaults", stage_defaults), ("sync", stage_sync), ("restart", stage_restart)]


# ----------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--force-stage")
    a = ap.parse_args()
    if a.status:
        print(json.dumps({"state": jload(STATE, {}), "status": jload(STATUS, {})}, indent=1)); return
    AP.mkdir(parents=True, exist_ok=True)
    lk = open("/tmp/v15_autopilot.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[autopilot] locked"); return
    st = jload(STATE, None)
    if st is None:
        rd = ""
        try:
            rd = open(os.path.expanduser("~/v15_defaults_round.txt")).read().strip()
        except Exception:
            pass
        st = {"round": (rd.split("-")[0] if rd.startswith("run") else "run21"), "phase": "sweep", "round_started": now_utc().isoformat(), "quiet_ticks": 0, "resets": 0, "stage_ok": {}}
        log(f"autopilot INITIALISED round={st['round']} phase=sweep")
    st.setdefault("stage_ok", {})
    ensure_venues(a.dry_run)
    disk_guard(st, a.dry_run)
    snap = scheduler_alive(st, a.dry_run)
    st["alert"] = None
    # ---- FINAL PHASE (Mon 06:00Z .. 13:30Z): best settings -> 365D -> repair -> parity -> qualifiers.json (tools/v15_final_orch.py). No new rounds meanwhile.
    try:
        import v15_final_orch as FO
        T0, TQ, TEND = FO.times()
        _now = now_utc()
        fin = st.setdefault("final", {})
        if _now >= T0 and not fin.get("over") and not a.force_stage:
            if st.get("phase") != "final":
                log(f"FINAL PHASE entered (T0 {T0.isoformat()}), previous phase {st.get('phase')}")
                st["phase"] = "final"
            over = FO.final_tick(st, snap, hosts(), log, a.dry_run)
            if over:
                st["phase"] = "roundend"; st["stage_ok"] = {}; st["fail_count"] = 0
                st["roundend_started"] = now_utc().isoformat()
                log(f"post-final round-end of {st['round']} starts (regenerate row order + defaults, then the next round continues the cycle)")
            st["tick_at"] = now_utc().isoformat()
            if not a.dry_run:
                jsave(STATE, st)
                jsave(STATUS, {"tick_at": st["tick_at"], "round": st["round"], "phase": st["phase"], "why": st.get("why"), "alert": st.get("alert"), "final": {k: v for k, v in fin.items() if k not in ("chosen", "host_map", "missing_30d")}})
            print(json.dumps({"round": st["round"], "phase": st["phase"], "why": st.get("why"), "alert": st.get("alert")}))
            return
    except Exception as e:
        log(f"FINAL hook EXCEPTION {type(e).__name__}: {str(e)[:300]}")
        st["alert"] = f"final hook exception {str(e)[:120]}"
    if a.force_stage:
        st["phase"] = "roundend"; st["stage_ok"] = {k: False for k, _ in STAGES}
        for k, _ in STAGES:
            if k == a.force_stage:
                break
            st["stage_ok"][k] = True
    if st["phase"] == "sweep":
        done, why = sweep_tick(st, snap, a.dry_run)
        st["why"] = why
        if done:
            try:
                import v15_final_orch as _FO
                _t0 = _FO.times()[0]
                if not st.get("final", {}).get("over") and 0 < (_t0 - now_utc()).total_seconds() < 1.0 * 3600:
                    st["why"] = f"sweep complete ({why}) but round-end deferred: final phase starts at {_t0.isoformat()} (< 1 h)"
                    done = False
            except Exception:
                pass
        if done:
            log(f"round {st['round']} sweep complete: {why}; entering round-end")
            st["phase"] = "roundend"; st["stage_ok"] = {}
            st["roundend_started"] = now_utc().isoformat()
    if st["phase"] == "roundend":
        for name, fn in STAGES:
            if st["stage_ok"].get(name):
                continue
            t0 = time.time()
            try:
                ok = fn(st, a.dry_run)
            except Exception as e:
                ok = False; log(f"stage {name} EXCEPTION {type(e).__name__}: {str(e)[:200]}")
            st["stage_ok"][name] = bool(ok)
            log(f"stage {name}: {'ok' if ok else 'FAILED (retried next tick)'} {int(time.time() - t0)}s")
            if not ok:
                st["fail_count"] = st.get("fail_count", 0) + 1
                if st["fail_count"] >= 6:
                    st["alert"] = f"round-end stage {name} failed {st['fail_count']}x — see data/autopilot/LOG.md and AUTOPILOT_RUNBOOK.md; sweeps keep running on the previous defaults"
                    if name in ("collect", "template", "normalise", "defaults", "sync"):
                        log(f"GIVING UP this round-end at {name}: starting next round on the PREVIOUS defaults so servers stay busy")
                        st["stage_ok"] = {k: True for k, _ in STAGES[:-1]}; st["fail_count"] = 0
                break
            st["fail_count"] = 0
            if a.dry_run:
                continue
        if all(st["stage_ok"].get(n) for n, _ in STAGES):
            st["phase"] = "sweep"; st["stage_ok"] = {}
            st.setdefault("history", []).append({"round": st.get("prev_round"), "ended": now_utc().isoformat(), "coverage": st.get("coverage")})
            st["history"] = st["history"][-30:]
    st["tick_at"] = now_utc().isoformat()
    if not a.dry_run:
        jsave(STATE, st)
        jsave(STATUS, {"tick_at": st["tick_at"], "round": st["round"], "phase": st["phase"], "why": st.get("why"), "alert": st.get("alert"), "coverage": st.get("coverage"),
                       "round_age_h": st.get("round_age_h"), "snapshot_age_s": st.get("snapshot_age_s"), "defaults_round": st.get("defaults_round"), "hosts": snap.get("hosts")})
    print(json.dumps({"round": st["round"], "phase": st["phase"], "why": st.get("why"), "alert": st.get("alert")}))


if __name__ == "__main__":
    main()
