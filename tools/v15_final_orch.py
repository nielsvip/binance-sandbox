#!/usr/bin/env python3
"""v15_final_orch — s1-side orchestration of the Monday go-live qualification (see tools/v15_final_phase.py for the rules). Called by tools/v15_autopilot.py.

Timeline (UTC, overridable by env for rehearsals): T0 = AUTOPILOT_FINAL_T0 (2026-10-05T06:00) assemble the BEST finished 30D result per sym_side into a final
progress dir on each host, enable the scheduler's 365D verify + REPAIR chain (flag data/autopilot/chain_mode.flag); run: parity + (crypto) certification for chain-positive
sym_sides, export data/autopilot/final/qualifiers.json every tick; TQ = AUTOPILOT_FINAL_TQ (12:30) FINALIZE (frozen export, finalized=true); TEND = AUTOPILOT_FINAL_TEND (13:30) leave the
final phase, remove the flag, restore the pre-final progress-dir pointers and run the round-end (collect/template/defaults/sync/restart) so the cycle continues. The Mac go-live job (tools/golive_final.py, Mac cron 12:50/13:10) consumes qualifiers.json.
"""
import datetime as dt, json, os, shlex, subprocess, sys, time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import v15_final_phase as FP  # noqa: E402

ROOT = Path(os.path.expanduser("~/binance-sandbox"))
AP = ROOT / "data" / "autopilot"
FDIR = AP / "final"
FLAG = AP / "chain_mode.flag"


def parse_t(s, default):
    s = os.environ.get(s) or default
    return dt.datetime.fromisoformat(s.replace("Z", "+00:00"))


def times():
    return (parse_t("AUTOPILOT_FINAL_T0", "2026-10-05T06:00:00+00:00"), parse_t("AUTOPILOT_FINAL_TQ", "2026-10-05T12:30:00+00:00"), parse_t("AUTOPILOT_FINAL_TEND", "2026-10-05T13:30:00+00:00"))


def jload(p, d):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return d


def jsave(p, obj):
    p = Path(p); p.parent.mkdir(parents=True, exist_ok=True)
    t = p.with_suffix(p.suffix + ".tmp"); t.write_text(json.dumps(obj, indent=1, default=str)); t.replace(p)


def expected_sym_sides(snap):
    ns = set(snap.get("non_shortable", []))
    out = []
    for s in snap.get("stocks", []):
        out.append(f"{s}_LONG")
        if s not in ns:
            out.append(f"{s}_SHORT")
    for s in snap.get("crypto", []):
        out += [f"{s}_LONG", f"{s}_SHORT"]
    return out


def host_json(h, args, timeout=900):
    rc, out, err = FP.rhost(h, args, timeout=timeout)
    if rc != 0 or not out:
        return None, err
    try:
        return json.loads(out.splitlines()[-1]), ""
    except Exception as e:
        return None, f"bad json {e}"


def kill_pilots(h, final_pdir):
    """exact-PID kill of 30D/365D worker trees that do NOT belong to the final dir (their results are not used any more)."""
    code = (
        "import os,signal,json\n"
        "me={os.getpid(),os.getppid()};k=[]\n"
        "for d in os.listdir('/proc'):\n"
        "  if not d.isdigit() or int(d) in me: continue\n"
        "  try:\n"
        "    a=open('/proc/%s/cmdline'%d,'rb').read().split(b'\\0')\n"
        "    if not a or b'--sym-side' not in a or not any(x.endswith((b'v15_pilot.py',b'v15_365_cycle.py')) for x in a): continue\n"
        "    env=open('/proc/%s/environ'%d,'rb').read()\n"
        f"    if {final_pdir.encode()!r} in env: continue\n"
        "    os.kill(int(d),signal.SIGTERM);k.append(int(d))\n"
        "  except Exception: pass\n"
        "print(len(k))\n"
    )
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h["ssh"][0], "python3 -c " + __import__("shlex").quote(code)], capture_output=True, text=True, timeout=60)
    return r.stdout.strip()


def stop_herds(h):
    """SIGTERM the exact v15_local_herd PIDs (Monday T0: a live herd would relaunch 30D pilots into the old dir and starve the 365D chain). Saves each herd's exact cmdline+env+cwd+stdout for the TEND restart. Returns the spec list, or None when ssh itself failed."""
    code = (
        "import os,signal,json,time\n"
        "me={os.getpid(),os.getppid()};out=[]\n"
        "for d in os.listdir('/proc'):\n"
        "  if not d.isdigit() or int(d) in me: continue\n"
        "  try:\n"
        "    a=open('/proc/%s/cmdline'%d,'rb').read().split(b'\\0')\n"
        "    if not any(x.endswith(b'v15_local_herd.py') for x in a): continue\n"
        "    pid=int(d);env={}\n"
        "    for kv in open('/proc/%s/environ'%d,'rb').read().split(b'\\0'):\n"
        "      if b'=' in kv:\n"
        "        k,v=kv.split(b'=',1);k=k.decode()\n"
        "        if k.startswith('V15_') or k=='MAX_PARALLEL': env[k]=v.decode()\n"
        "    spec={'pid':pid,'cmd':[x.decode() for x in a if x],'cwd':os.readlink('/proc/%s/cwd'%d),'env':env}\n"
        "    try: spec['stdout']=os.readlink('/proc/%s/fd/1'%d)\n"
        "    except Exception: spec['stdout']=''\n"
        "    out.append(spec);os.kill(pid,signal.SIGTERM)\n"
        "  except Exception: pass\n"
        "time.sleep(8)\n"
        "for s in out:\n"
        "  try:\n"
        "    a=open('/proc/%d/cmdline'%s['pid'],'rb').read()\n"
        "    if b'v15_local_herd.py' in a: os.kill(s['pid'],signal.SIGKILL);s['killed']='SIGKILL'\n"
        "    else: s['killed']='SIGTERM'\n"
        "  except Exception: s['killed']='SIGTERM'\n"
        "print(json.dumps(out))\n"
    )
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h["ssh"][0], "python3 -c " + shlex.quote(code)], capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        return None
    try:
        return json.loads(r.stdout.strip().splitlines()[-1])
    except Exception:
        return None


def start_herds(h, specs, log):
    """TEND: replay each saved herd cmdline exactly. Skips hosts whose herd is already alive. Returns True when every host ends with a herd (running or relaunched)."""
    r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h["ssh"][0], "pgrep -f 'v15_local_herd[.]py'"], capture_output=True, text=True, timeout=30)  # [.] = the ssh wrapper embeds the pattern in its own cmdline; brackets keep it from matching itself
    if r.stdout.strip():
        log(f"FINAL herds: {h['name']} herd already alive ({' '.join(r.stdout.strip().split()[:3])}) — restart skipped"); return True
    if not specs:
        log(f"FINAL herds: {h['name']} NO saved herd spec — herd stays down, scheduler continues alone (manual: cd ~/binance-sandbox && nohup .venv/bin/python -u tools/v15_local_herd.py)"); return False
    ok = True
    for s in specs:
        out = s.get("stdout") or ""
        dest = out if (out.startswith("/") and "/dev/" not in out and "pipe" not in out and "socket" not in out) else "/tmp/herd_pdir_restarted.log"
        env = " ".join(f"{k}={shlex.quote(v)}" for k, v in (s.get("env") or {}).items())
        cmd = " ".join(shlex.quote(x) for x in s["cmd"])
        full = f"cd {shlex.quote(s.get('cwd') or '$HOME/binance-sandbox')} && setsid nohup env {env} {cmd} >> {shlex.quote(dest)} 2>&1 < /dev/null & echo RESTARTED-$!"
        r2 = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", h["ssh"][0], full], capture_output=True, text=True, timeout=60)
        log(f"FINAL herds: {h['name']} restart pid-was={s.get('pid')} -> {r2.stdout.strip()[:60]}")
        ok &= "RESTARTED-" in r2.stdout
    return ok


def assemble(st, snap, hosts, log, dry):
    fin = st.setdefault("final", {})
    cand = {}
    for h in hosts:
        res, err = host_json(h, "host-scan", timeout=1800)
        if res is None:
            log(f"FINAL assemble: scan failed on {h['name']}: {err}")
            return False
        for ss, info in res.items():
            info["host"] = h["name"]
            cur = cand.get(ss)
            if cur is None or (info["round"], info["mtime"]) > (cur["round"], cur["mtime"]):
                cand[ss] = info
    exp = expected_sym_sides(snap)
    have = [s for s in exp if s in cand]
    fin["expected"] = len(exp)
    fin["with_best_30d"] = len(have)
    fin["missing_30d"] = [s for s in exp if s not in cand][:200]
    log(f"FINAL assemble: {len(have)}/{len(exp)} sym_sides have a finished 30D result (newest round wins)")
    if len(have) < 0.3 * max(1, len(exp)):
        log("FINAL assemble REFUSED: < 30% coverage — keeping normal rounds; alert")
        st["alert"] = "FINAL assemble refused: coverage < 30%"
        return False
    if not fin.get("pin"):
        pc = collect(hosts, FP.FINAL_DIRNAME).get("current", {})
        engs = {h["name"]: (pc.get(h["name"]) or {}).get("engine_md5") for h in hosts if (pc.get(h["name"]) or {}).get("engine_md5")}
        if len(set(engs.values())) != 1 or not engs:
            log(f"FINAL assemble REFUSED: fleet engines not unanimous {engs} — retry next tick")
            return False
        fin["pin"] = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "engine_md5": list(engs.values())[0], "per_host": engs}
        log(f"FINAL assemble: T0 pin {fin['pin']['engine_md5'][:8]} on {sorted(engs)}")
    hosts = [h for h in hosts if h["name"] in (fin.get("pin") or {}).get("per_host", {})] or hosts
    # balanced symbol -> host assignment (LONG+SHORT of a symbol together, stocks and crypto balanced separately)
    load = {h["name"]: {"stocks": 0, "crypto": 0} for h in hosts}
    syms = sorted({s.rsplit("_", 1)[0] for s in have}, key=lambda x: (FP.is_crypto(x + "_LONG"), x))
    hmap = {}
    for sym in syms:
        v = "crypto" if FP.is_crypto(sym + "_LONG") else "stocks"
        hn = min(load, key=lambda n: (load[n][v], n))
        load[hn][v] += 1
        hmap[sym] = hn
    byname = {h["name"]: h for h in hosts}
    tag = FP.FINAL_DIRNAME
    for h in hosts:
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", h["ssh"][0], f"mkdir -p ~/{tag}/progress ~/{tag}/chain ~/{tag}/parity ~/{tag}/confirm"], capture_output=True, text=True, timeout=60)
    n_ok = 0
    for ss in have:
        info = cand[ss]
        src, dst = byname[info["host"]], byname[hmap[ss.rsplit("_", 1)[0]]]
        dest = f"/home/niels/{tag}/progress/{ss}_v14_progress.json"
        if dry:
            n_ok += 1; continue
        if src["name"] == dst["name"]:
            cmd = f"ssh -o BatchMode=yes {src['ssh'][0]} 'cp -p {info['path']} {dest}'"
        else:
            cmd = f"ssh -o BatchMode=yes {src['ssh'][0]} 'cat {info['path']}' | ssh -o BatchMode=yes {dst['ssh'][0]} 'cat > {dest}.tmp && mv {dest}.tmp {dest}'"
        r = subprocess.run(["bash", "-c", cmd], capture_output=True, text=True, timeout=300)
        n_ok += r.returncode == 0
    fin["copied"] = n_ok
    fin["host_map"] = hmap
    fin["chosen"] = {ss: {"host": cand[ss]["host"], "round": cand[ss]["round"], "final_gain": cand[ss]["final_gain"], "defaults_round": cand[ss].get("defaults_round")} for ss in have}
    if n_ok < 0.95 * len(have) and not dry:
        log(f"FINAL assemble: only {n_ok}/{len(have)} files copied — retry next tick")
        return False
    if dry:
        log("FINAL assemble (dry): no pointers changed")
        return True
    prev = fin.setdefault("prev", {})
    for h in hosts:
        if h["name"] not in prev:
            r = subprocess.run(["ssh", "-o", "BatchMode=yes", h["ssh"][0], "cat ~/v15_current_progress_dir.txt; echo ===; cat ~/v15_defaults_round.txt"], capture_output=True, text=True, timeout=60)
            pdv, _, rd = r.stdout.partition("===")
            prev[h["name"]] = {"pdir": pdv.strip(), "round": rd.strip()}
    specs = fin.setdefault("herd_cmd", {})
    for h in hosts:
        hs = stop_herds(h)
        specs[h["name"]] = hs or []
        log(f"FINAL assemble: {h['name']} herds stopped ({'SSH FAILED' if hs is None else [s['pid'] for s in hs]}) — specs saved for the TEND restart")
        pd = f"$HOME/{tag}/progress"
        subprocess.run(["ssh", "-o", "BatchMode=yes", h["ssh"][0], f"echo {pd} > ~/v15_current_progress_dir.txt && echo final-{FP.FINAL_TAG} > ~/v15_defaults_round.txt"], capture_output=True, text=True, timeout=60)
        k = kill_pilots(h, f"{tag}/progress")
        log(f"FINAL assemble: {h['name']} pointer -> final dir, killed {k} old pilot processes")
    AP.mkdir(parents=True, exist_ok=True)
    FLAG.write_text(dt.datetime.now(dt.timezone.utc).isoformat())
    return True


def collect(hosts, final_dirname):
    out = {"verdicts": {}, "parity": {}, "confirmed": {}, "confirm_started": {}, "run_par": {}, "run_conf": {}, "current": {}}
    for h in hosts:
        res, err = host_json(h, f"host-collect --final-dir /home/niels/{final_dirname}", timeout=900)
        if res is None:
            continue
        out["current"][h["name"]] = res.get("current") or {}
        for k in ("verdicts", "parity", "confirmed", "confirm_started"):
            out[k].update({kk: dict(vv, host=h["name"]) if isinstance(vv, dict) and k in ("verdicts", "parity", "confirmed") else vv for kk, vv in res.get(k, {}).items()})
        out["run_par"][h["name"]] = sum(1 for v in res.get("parity", {}).values() if v.get("status") == "RUNNING")
        out["run_conf"][h["name"]] = sum(1 for ss, age in res.get("confirm_started", {}).items() if ss not in res.get("confirmed", {}) and age < 900)
    return out


def drive(st, snap, hosts, log, dry):
    fin = st["final"]
    byname = {h["name"]: h for h in hosts}
    c = collect(hosts, FP.FINAL_DIRNAME)
    pin = (st.get("final", {}).get("pin") or {}).get("engine_md5")
    if pin:
        drifted = [hn for hn, cur in (c.get("current") or {}).items() if (cur or {}).get("engine_md5") != pin]
        if drifted:
            for k in ("verdicts", "parity", "confirmed"):
                c[k] = {ss: vv for ss, vv in c[k].items() if not (isinstance(vv, dict) and vv.get("host") in drifted)}
            fin["drift"] = {hn: (c.get("current") or {}).get(hn, {}) for hn in drifted}
            log(f"FINAL drive: drifted hosts excluded this tick (pin {pin[:8]}): {drifted}")
    v, par, conf = c["verdicts"], c["parity"], c["confirmed"]
    for ss, vd in v.items():
        if not vd.get("both_ok") or not vd.get("final_progress"):
            continue
        h = byname.get(vd["host"])
        if h is None or dry:
            continue
        if ss not in par and c["run_par"].get(h["name"], 0) < 2:
            FP.rhost(h, f"host-parity --ss {ss} --progress {vd['final_progress']} --final-dir /home/niels/{FP.FINAL_DIRNAME}", timeout=60)
            c["run_par"][h["name"]] = c["run_par"].get(h["name"], 0) + 1
        if FP.is_crypto(ss) and ss not in conf and ss not in c["confirm_started"] and c["run_conf"].get(h["name"], 0) < 2:
            FP.rhost(h, f"host-confirm --ss {ss} --progress {vd['final_progress']} --final-dir /home/niels/{FP.FINAL_DIRNAME}", timeout=60)
            c["run_conf"][h["name"]] = c["run_conf"].get(h["name"], 0) + 1
    return c


def export(st, snap, c, finalized):
    exp = expected_sym_sides(snap)
    ev, counts = {}, {}
    for ss in exp:
        vd = c["verdicts"].get(ss)
        status, why = FP.qualify(ss, vd, c["parity"], c["confirmed"])
        if finalized and status == "QUALIFIED" and (c["parity"].get(ss) or {}).get("status") == "RUNNING":
            why = why + ["parity still running at finalization (UNAVAILABLE -> UNVERIFIED, blocked)"]
        rec = {"status": status, "reasons": why}
        if status == "QUALIFIED":
            rec.update(final_progress=vd["final_progress"], overrides=vd.get("overrides"), host=vd["host"], w30=vd.get("w30"), w365=vd.get("w365"), span_365_days=vd.get("span_365_days"),
                       parity=c["parity"].get(ss), confirm=c["confirmed"].get(ss), chosen=(st.get("final", {}).get("chosen") or {}).get(ss))
        ev[ss] = rec
        counts[status] = counts.get(status, 0) + 1
    _pin = (st.get("final", {}).get("pin") or {})
    _eng = _pin.get("engine_md5") or jload(ROOT / "data/engine_deploy/CURRENT.json", {}).get("engine_md5") or ""
    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(), "finalized": finalized, "expected": len(exp), "counts": counts,
           "with_best_30d": st.get("final", {}).get("with_best_30d"), "engine_md5": _eng[:8], "pin": _pin or None, "evaluated": ev}
    return out


def final_tick(st, snap, hosts, log, dry):
    """returns True when the final phase is over (autopilot resumes normal rounds)."""
    T0, TQ, TEND = times()
    now = dt.datetime.now(dt.timezone.utc)
    fin = st.setdefault("final", {})
    if now >= TEND:
        if FLAG.exists() and not dry:
            FLAG.unlink()
        for h in hosts:  # resume where it left off: point every host back at the pre-final round dir + defaults round id
            p = (fin.get("prev") or {}).get(h["name"])
            if p and p.get("pdir") and not dry:
                subprocess.run(["ssh", "-o", "BatchMode=yes", h["ssh"][0], f"echo {p['pdir']} > ~/v15_current_progress_dir.txt && echo {p['round']} > ~/v15_defaults_round.txt"], capture_output=True, text=True, timeout=60)
            if not dry:
                start_herds(h, (fin.get("herd_cmd") or {}).get(h["name"]) or [], log)
        log("FINAL phase over (TEND): chain flag removed, pointers restored to the pre-final round dir; round-end (row order + defaults regeneration) runs next, then the cycle continues")
        fin["over"] = True
        return True
    if not fin.get("assembled"):
        if assemble(st, snap, hosts, log, dry):
            fin["assembled"] = now.isoformat()
        return False
    c = drive(st, snap, hosts, log, dry)
    finalized = bool(fin.get("finalized")) or now >= TQ
    out = export(st, snap, c, finalized)
    if finalized and not fin.get("finalized"):
        fin["finalized"] = now.isoformat()
        log(f"FINAL FINALIZED: {out['counts']}")
    if not dry:
        if not (fin.get("finalized") and (FDIR / "qualifiers.json").exists() and jload(FDIR / "qualifiers.json", {}).get("finalized")):
            jsave(FDIR / "qualifiers.json", out)
        jsave(FDIR / "progress_report.json", {"at": out["generated_at"], "counts": out["counts"], "finalized": finalized, "drift": fin.get("drift")})
    st["why"] = f"FINAL {out['counts']} finalized={finalized}"
    return False
