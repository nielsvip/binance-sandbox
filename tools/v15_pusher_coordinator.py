#!/usr/bin/env python3
"""v15_pusher_coordinator — S1 master for 24/7/365 distributed gain-pusher rounds.

Owns: rounds state, per-sym best anchors, merged PRIORITY registry, unit queue.
Every tick (S1 cron */5, flock):
  1. transport: push outbox/{host}/ -> host inbox, pull host done/ + heartbeat,
     push anchors/ + registry/ to hosts (S1 has passwordless ssh to all workers).
  2. ingest: validate reports, archive reports/r{round}/{ss}.json, advance state,
     adopt improved sets into anchors/ (chained: next round anchors from latest).
  3. learn: tally promoted (switch=value) fleet-wide; >=5 syms -> P1_MUST_TEST
     test_value with evidence (additive only, capped). Bump registry _version.
  4. generate: next unit per sym (no global barrier — a sym advances the tick
     its previous round lands, so no host ever waits). Requeue assigned-but-
     undone units after 45 min. Target 500 complete rounds, then keep going.
  5. status: PUSHER_STATUS.md/json (rounds, units/h, per-host cpu, gain curve).

Round r is COMPLETE when all tradeable sym_sides carry a round-r report.
All gains are fresh-eval (workers re-anchor on their own engine); every report
stamps host/engine_md5/npz_id/registry_version for the audit trail.

One-time: --build-seed (Mac) assembles seed/anchors + seed/round1 from the FLEET
run; --seed (S1) imports them as round 1 and opens round 2.
"""
import datetime as dt
import json
import os
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

HOSTS_FALLBACK = {"s1": "127.0.0.1", "s2": "10.0.0.4", "s5": "10.0.0.5", "s6": "10.0.0.6", "s7": "10.0.0.7"}
TARGET_ROUNDS = 500
REQUEUE_MIN = 45
P1_MIN_SYMS = 5
P1_MAX_SWITCHES = 250
P1_MAX_VALUES = 12
DOWN_AFTER_ERRS = 2
OUTBOX_CAP_MULT = 2


def discover_hosts():
    """Hosts are dynamic (workers get deleted/added): read the fleet file every
    tick. USER 2026-10-09: use ANY compute found; s6/s7 deletion tonight must
    be seamless (s1/s2/s5 carry on). Returns {name: ssh}."""
    try:
        fh = json.loads((ROOT / "tools" / "fleet_hosts_final.json").read_text())
        out = {}
        for h in fh.get("hosts", []):
            name = h.get("name")
            sshs = h.get("ssh") or []
            if name and sshs:
                out[name] = sshs[0]
        if out:
            return out
    except Exception:
        pass
    return dict(HOSTS_FALLBACK)


def host_weight(st, host):
    """Cached nproc probe (hourly); s1 is scheduler home -> weight 1 (its
    supervisor still fills it past 90% only when truly idle)."""
    if host == "s1":
        return 1
    info = (st.get("hostinfo") or {}).get(host) or {}
    if time.time() - float(info.get("ts", 0)) < 3600 and info.get("w"):
        return int(info["w"])
    return int(info.get("w") or 4)


def probe_host(st, hosts, host):
    try:
        ssh = hosts[host]
        if host == "s1" and ssh == "127.0.0.1":
            n = os.cpu_count() or 8
        else:
            rc, out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", ssh, "nproc"], timeout=30)
            n = int(out.strip().split()[0]) if rc == 0 else 0
        if n > 0:
            st.setdefault("hostinfo", {})[host] = {"w": max(1, n - 2), "nproc": n, "ts": time.time()}
            return True
    except Exception:
        pass
    return False


def log(msg):
    print(f"[pushcoord] {msg}", flush=True)


def utcnow():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def tradeable_symsides(root=ROOT):
    import v15_universe as U
    return sorted(U.tradeable_sym_sides(root))


def run(cmd, timeout=300):
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def _is_local(host, ssh):
    return host == "s1" and ssh == "127.0.0.1"


def _local(p):
    return os.path.expanduser(p)


def rsync_push(src, host, ssh, dest):
    if _is_local(host, ssh):
        os.makedirs(_local(dest), exist_ok=True)
        cmd = ["rsync", "-az", "--remove-source-files", "--prune-empty-dirs", f"{src}/", _local(dest)]
    else:
        cmd = ["rsync", "-az", "--remove-source-files", "--prune-empty-dirs", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", f"{src}/", f"{ssh}:{dest}"]
    return run(cmd)


def rsync_pull(host, ssh, src, dest):
    if _is_local(host, ssh):
        os.makedirs(_local(src), exist_ok=True)
        cmd = ["rsync", "-az", "--remove-source-files", "--prune-empty-dirs", f"{_local(src)}/", f"{dest}/"]
    else:
        cmd = ["rsync", "-az", "--remove-source-files", "--prune-empty-dirs", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", f"{ssh}:{src}/", f"{dest}/"]
    return run(cmd)


def rsync_sync(src, host, ssh, dest):
    if _is_local(host, ssh):
        os.makedirs(_local(dest), exist_ok=True)
        cmd = ["rsync", "-az", f"{src}/", _local(dest)]
    else:
        cmd = ["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", f"{src}/", f"{ssh}:{dest}"]
    return run(cmd)


def pull_file(host, ssh, remote, local):
    if _is_local(host, ssh):
        try:
            import shutil
            shutil.copy2(os.path.expanduser(remote.replace("~", "~")), local)
            return 0, ""
        except Exception as e:
            return 1, str(e)
    return run(["rsync", "-az", "-e", "ssh -o BatchMode=yes -o ConnectTimeout=10", f"{ssh}:{remote}", local])


def load_state(base):
    p = base / "state.json"
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {"rounds_complete": 0, "target": TARGET_ROUNDS, "syms": {}, "assigned": {}, "registry_version": 0,
            "units_done": 0, "units_err": 0, "started": utcnow(), "milestones": []}


def save_state(base, st):
    tmp = base / "state.json.tmp"
    tmp.write_text(json.dumps(st, indent=1, default=str))
    tmp.replace(base / "state.json")


def ingest_report(st, base, rep, improved):
    """Pure-ish ingest: returns (accepted, improved_adopted). Mutates st + anchors on disk."""
    ss = str(rep.get("symside", ""))
    rnd = int(rep.get("round", 0) or 0)
    if not ss or rnd <= 0:
        return False, False
    if rep.get("skip") in ("npz missing/corrupt", "prepare failed", "s1 pull failed"):
        cur = st["syms"].setdefault(ss, {"done_round": 0})
        cur["terminal"] = {"reason": rep["skip"], "ts": time.time(), "round": rnd}
        st["assigned"].pop(f"{rnd:04d}_{ss}", None)
        st["units_err"] = st.get("units_err", 0) + 1
        return False, False
    if rep.get("error") or rep.get("skip"):
        st["units_err"] = st.get("units_err", 0) + 1
        return False, False
    fin = rep.get("final") or {}
    if not isinstance(fin.get("gain"), (int, float)):
        st["units_err"] = st.get("units_err", 0) + 1
        return False, False
    cur = st["syms"].get(ss, {"done_round": 0})
    if rnd <= cur.get("done_round", 0):
        return False, False
    arch = base / "reports" / f"r{rnd:04d}"
    arch.mkdir(parents=True, exist_ok=True)
    (arch / f"{ss}.json").write_text(json.dumps(rep, indent=1, default=str))
    cur["done_round"] = rnd
    cur["last_gain"] = fin["gain"]
    cur["last_trades"] = fin.get("trades")
    cur["last_valid"] = fin.get("valid")
    cur["last_engine"] = rep.get("engine_md5")
    cur["last_host"] = rep.get("host")
    best = cur.get("best") or {}
    adopted = False
    if improved is not None and (not best or fin["gain"] > best.get("gain", -1e18)):
        (base / "anchors" / f"{ss}.json").write_text(json.dumps(improved, indent=1, default=str))
        meta = {}
        mp = base / "anchors_meta.json"
        if mp.exists():
            try:
                meta = json.loads(mp.read_text())
            except Exception:
                meta = {}
        meta[ss] = {"round": rnd, "gain": fin["gain"], "engine": rep.get("engine_md5"), "host": rep.get("host"), "ts": utcnow()}
        mp.write_text(json.dumps(meta, indent=1, default=str))
        cur["best"] = {"gain": fin["gain"], "round": rnd}
        adopted = True
    elif improved is not None and not (base / "anchors" / f"{ss}.json").exists():
        (base / "anchors" / f"{ss}.json").write_text(json.dumps(improved, indent=1, default=str))
        adopted = True
    st["syms"][ss] = cur
    st["units_done"] = st.get("units_done", 0) + 1
    st["assigned"].pop(f"{rnd:04d}_{ss}", None)
    return True, adopted


def merge_registry(base, st, fresh_reports):
    """Tally promoted (switch=value) across fresh reports; frequent winners -> P1."""
    tally = {}
    for rep in fresh_reports:
        ss = str(rep.get("symside", ""))
        for m in rep.get("moves") or []:
            flip = m.get("flip") or {}
            if not flip or (m.get("delta") or 0) <= 0:
                continue
            for sw, val in flip.items():
                t = tally.setdefault(sw, {}).setdefault(json.dumps(val, sort_keys=True), {"val": val, "syms": {}, "d": 0.0})
                t["syms"][ss] = t["syms"].get(ss, 0.0) + float(m["delta"])
                t["d"] += float(m["delta"])
    if not tally:
        return 0
    rp = base / "registry" / "PRIORITY_SWITCHES.json"
    try:
        reg = json.loads(rp.read_text())
    except Exception:
        reg = {}
    p1 = reg.setdefault("P1_MUST_TEST", {})
    added = 0
    for sw, vals in sorted(tally.items(), key=lambda kv: -sum(v["d"] for v in kv[1].values())):
        for vkey, t in vals.items():
            if len(t["syms"]) < P1_MIN_SYMS:
                continue
            ent = p1.setdefault(sw, {"priority": "P1", "reason": "fleet auto-learn: promoted on >=5 symsides in one round",
                                     "evidence": [], "test_values": [], "applies_to": ["CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT"]})
            if len(p1) > P1_MAX_SWITCHES:
                continue
            cur_vals = {json.dumps(v, sort_keys=True) for v in ent.get("test_values", [])}
            if vkey in cur_vals or len(ent.get("test_values", [])) >= P1_MAX_VALUES:
                continue
            ent["test_values"].append(t["val"])
            ex = sorted(t["syms"], key=lambda s: -t["syms"][s])[:6]
            ent["evidence"].append(f"AUTO r{st.get('rounds_complete', 0)}+1: +{t['d']:.2f} on {len(t['syms'])} syms ({','.join(ex)})")
            added += 1
    if added:
        reg["_version"] = int(reg.get("_version", 0)) + 1
        rp.write_text(json.dumps(reg, indent=1, default=str))
        st["registry_version"] = reg["_version"]
    return added


TERMINAL_TTL = 24 * 3600


def sym_counts(st, ss, r, now):
    s = st["syms"].get(ss, {})
    if s.get("done_round", 0) >= r:
        return True
    if s.get("covered", 0) >= r:
        return True
    t = s.get("terminal") or {}
    if t and now - float(t.get("ts", 0)) < TERMINAL_TTL:
        return True
    e = (st.get("errs") or {}).get(f"{r:04d}_{ss}") or {}
    if e.get("n", 0) >= 3 and now - float(e.get("ts", 0)) < 6 * 3600:
        return True
    return False


def eff_done(st, ss):
    s = st["syms"].get(ss, {})
    return max(s.get("done_round", 0), s.get("covered", 0))


def rounds_complete(st, universe, now=None):
    now = now or time.time()
    r = 0
    while universe and all(sym_counts(st, ss, r + 1, now) for ss in universe):
        r += 1
    return r


def pick_host(st, hosts, down):
    out = {h: 0 for h in hosts}
    for u, info in (st.get("assigned") or {}).items():
        h = (info or {}).get("host")
        if h in out:
            out[h] += 1
    elig = [h for h in hosts if h not in down and out[h] < OUTBOX_CAP_MULT * host_weight(st, h)]
    if not elig:
        elig = [h for h in hosts if h not in down]
    if not elig:
        return None
    return min(elig, key=lambda h: (out[h] / max(1, host_weight(st, h)), h))


def host_down(st, base, host):
    """Host lost (deleted/stalled): drop its assigned units + outbox files so
    they regenerate onto healthy hosts next tick. Idempotent ingest dedupes
    any straggler results if the host comes back."""
    for u, info in list((st.get("assigned") or {}).items()):
        if (info or {}).get("host") == host:
            st["assigned"].pop(u, None)
    for f in (base / "outbox" / host).glob("*.json"):
        f.unlink(missing_ok=True)
    log(f"host {host} DOWN: assigned+outbox requeued")


def find_progress_anchor(ss):
    """New syms (universe churns with live rankings): anchor from the newest
    progress cumulative anywhere on S1, else empty set (pusher repairs from
    scratch — honest fresh evals, slower first round). Returns (overrides, src)."""
    import glob
    cands = []
    for pat in (os.path.expanduser("~/v15_run*/progress/%s_v14_progress.json" % ss),
                os.path.expanduser("~/binance-sandbox/data/reports/lifecycle_pilot/%s_v14_progress.json" % ss)):
        cands.extend(glob.glob(pat))
    best, best_mtime = None, 0
    for c in cands:
        try:
            m = os.path.getmtime(c)
            if m > best_mtime:
                d = json.loads(open(c).read())
                ov = d.get("cumulative_overrides") or {}
                if ov:
                    best, best_mtime = (ov, f"progress:{os.path.basename(os.path.dirname(c))}"), m
        except Exception:
            continue
    if best:
        return best
    return {}, "empty"


def pending_units(base, st, hosts):
    pend = set()
    for h in list(hosts) + ["_retired"]:
        for f in (base / "outbox" / h).glob("*.json"):
            pend.add(f.stem)
    for u in (st.get("assigned") or {}):
        pend.add(u)
    return pend


def main():
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.expanduser("~/v15_pusher"))
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--seed-dir", default=None)
    ap.add_argument("--build-seed", action="store_true")
    ap.add_argument("--mac-root", default=str(ROOT))
    a = ap.parse_args()
    base = pathlib.Path(a.root)
    if a.build_seed:
        build_seed(pathlib.Path(a.mac_root), pathlib.Path(a.seed_dir or (pathlib.Path(a.mac_root) / "data" / "pusher_seed")))
        return
    hosts = discover_hosts()
    for d in ("outbox", "done", "anchors", "registry", "reports", "logs"):
        (base / d).mkdir(parents=True, exist_ok=True)
        if d in ("outbox", "done"):
            for h in hosts:
                (base / d / h).mkdir(parents=True, exist_ok=True)
    st = load_state(base)
    if a.seed:
        seed_import(base, st, pathlib.Path(a.seed_dir or (base / "seed")))
        save_state(base, st)
    try:
        manual_down = {l.strip() for l in (base / "down_hosts.txt").read_text().splitlines() if l.strip()}
    except Exception:
        manual_down = set()
    for h in hosts:
        probe_host(st, hosts, h)
    down = set(manual_down) & set(hosts)
    for h in down:
        host_down(st, base, h)
    for h in hosts:
        n = (st.get("host_err") or {}).get(h, {}).get("n", 0)
        if n >= DOWN_AFTER_ERRS:
            rc, _ = pull_file(h, hosts[h], "~/v15_pusher/heartbeat.json", str(base / f"heartbeat_{h}.json"))
            if rc == 0:
                st.setdefault("host_err", {}).setdefault(h, {})["n"] = 0
                log(f"host {h} recovered (heartbeat ok)")
            else:
                down.add(h)
                host_down(st, base, h)
    universe = tradeable_symsides()
    log(f"tick: hosts={sorted(hosts)} down={sorted(down)} universe={len(universe)} rounds_complete={st.get('rounds_complete', 0)} units_done={st.get('units_done', 0)}")
    for h in hosts:
        if h in down:
            continue
        ssh = hosts[h]
        fails = 0
        rc, out = rsync_sync(str(base / "anchors"), h, ssh, "~/v15_pusher/anchors")
        if rc != 0:
            log(f"anchors {h} rc={rc} {out.strip()[-200:]}")
            fails += 1
        rc, out = rsync_sync(str(base / "registry"), h, ssh, "~/v15_pusher/registry")
        if rc != 0:
            log(f"registry {h} rc={rc} {out.strip()[-200:]}")
            fails += 1
        rc, out = rsync_push(str(base / "outbox" / h), h, ssh, "~/v15_pusher/inbox/")
        if rc != 0:
            log(f"push {h} rc={rc} {out.strip()[-200:]}")
            fails += 1
        rc, out = rsync_pull(h, ssh, "~/v15_pusher/done/", str(base / "done" / h))
        if rc != 0:
            log(f"pull-done {h} rc={rc} {out.strip()[-200:]}")
            fails += 1
        rc, out = pull_file(h, ssh, "~/v15_pusher/heartbeat.json", str(base / f"heartbeat_{h}.json"))
        if rc != 0:
            log(f"heartbeat {h} rc={rc} {out.strip()[-200:]}")
        he = st.setdefault("host_err", {}).setdefault(h, {"n": 0})
        if fails:
            he["n"] += 1
            if he["n"] >= DOWN_AFTER_ERRS:
                down.add(h)
                host_down(st, base, h)
        else:
            he["n"] = 0
    fresh = []
    for h in hosts:
        for rf in sorted((base / "done" / h).glob("*_report.json")):
            try:
                rep = json.loads(rf.read_text())
            except Exception:
                rf.unlink(missing_ok=True)
                continue
            imp = None
            ipf = rf.parent / (rf.name[:-len("_report.json")] + "_improved.json")
            if ipf.exists():
                try:
                    imp = json.loads(ipf.read_text())
                except Exception:
                    imp = None
            ok, _ = ingest_report(st, base, rep, imp)
            if ok:
                fresh.append(rep)
            rf.unlink(missing_ok=True)
            ipf.unlink(missing_ok=True)
        for ef in (base / "done" / h).glob("*_ERROR.json"):
            try:
                rep = json.loads(ef.read_text())
                ekey = f"{int(rep.get('round', 0)):04d}_{rep.get('symside', '')}"
                st["assigned"].pop(ekey, None)
                errs = st.setdefault("errs", {})
                e = errs.get(ekey, {"n": 0, "ts": 0})
                e["n"] += 1
                e["ts"] = time.time()
                errs[ekey] = e
                el = base / "logs" / "errors.jsonl"
                with open(el, "a") as f:
                    f.write(json.dumps({"ts": utcnow(), "host": h, **rep}, default=str) + "\n")
            except Exception:
                pass
            ef.unlink(missing_ok=True)
            st["units_err"] = st.get("units_err", 0) + 1
    if fresh:
        n = merge_registry(base, st, fresh)
        log(f"ingested {len(fresh)} reports, registry +{n} test_values (v{st.get('registry_version', 0)})")
    now = time.time()
    for u, info in list((st.get("assigned") or {}).items()):
        if now - float((info or {}).get("ts", 0)) > REQUEUE_MIN * 60:
            log(f"requeue {u} (assigned {REQUEUE_MIN}m+, no result)")
            st["assigned"].pop(u, None)
    pend = pending_units(base, st, hosts)
    made = 0
    for ss in universe:
        nxt = eff_done(st, ss) + 1
        key = f"{nxt:04d}_{ss}"
        if key in pend:
            continue
        err = (st.get("errs") or {}).get(key)
        if err and err.get("n", 0) >= 3 and now - float(err.get("ts", 0)) < 6 * 3600:
            st["syms"].setdefault(ss, {}).update({"covered": nxt, "covered_why": "err_backoff"})
            continue
        term = (st["syms"].get(ss, {}).get("terminal") or {})
        if term and now - float(term.get("ts", 0)) < TERMINAL_TTL:
            st["syms"].setdefault(ss, {}).update({"covered": nxt, "covered_why": term.get("reason", "terminal")})
            continue
        if not (base / "anchors" / f"{ss}.json").exists():
            ov, src = find_progress_anchor(ss)
            (base / "anchors" / f"{ss}.json").write_text(json.dumps(ov, indent=1, default=str))
            log(f"new sym {ss}: anchor from {src} ({len(ov)} keys)")
        h = pick_host(st, hosts, down)
        if h is None:
            log("no healthy host with capacity — units wait for next tick")
            break
        (base / "outbox" / h / f"{key}.json").write_text(json.dumps({"round": nxt, "symside": ss, "anchor_rev": nxt - 1, "ts": utcnow()}))
        st["assigned"][key] = {"host": h, "ts": now}
        made += 1
    # NOTE: assigned entries are written at enqueue; transport failure just delays pickup.
    rc_done = rounds_complete(st, universe)
    if rc_done > st.get("rounds_complete", 0):
        st["rounds_complete"] = rc_done
        log(f"ROUND {rc_done} COMPLETE ({len(universe)}/{len(universe)})")
        if rc_done >= TARGET_ROUNDS and TARGET_ROUNDS not in st.get("milestones", []):
            st["milestones"].append(TARGET_ROUNDS)
            log(f"MILESTONE: {TARGET_ROUNDS} complete rounds — continuing 24/7 (no cap)")
    save_state(base, st)
    write_status(base, st, universe, hosts, down)
    log(f"tick done: +{made} units outbox rounds_complete={st.get('rounds_complete', 0)}")


def write_status(base, st, universe, hosts, down):
    tab = {}
    for h in hosts:
        hb = {}
        try:
            hb = json.loads((base / f"heartbeat_{h}.json").read_text())
        except Exception:
            pass
        out_n = len(list((base / "outbox" / h).glob("*.json")))
        tab[h] = {"cpu": hb.get("cpu"), "workers": hb.get("workers"), "avail_mb": hb.get("avail_mb"), "outbox": out_n,
                  "down": h in (down or set()),
                  "age_s": round(time.time() - float(hb.get("ts", 0))) if hb.get("ts") else None}
    gains = [s.get("last_gain") for s in st["syms"].values() if isinstance(s.get("last_gain"), (int, float))]
    best = [s.get("best", {}).get("gain") for s in st["syms"].values() if isinstance(s.get("best", {}).get("gain"), (int, float))]
    status = {"at": utcnow(), "rounds_complete": st.get("rounds_complete", 0), "target": st.get("target", TARGET_ROUNDS),
              "universe": len(universe), "units_done": st.get("units_done", 0), "units_err": st.get("units_err", 0),
              "registry_version": st.get("registry_version", 0),
              "mean_last_gain": round(sum(gains) / len(gains), 3) if gains else None,
              "mean_best_gain": round(sum(best) / len(best), 3) if best else None, "hosts": tab}
    (base / "PUSHER_STATUS.json").write_text(json.dumps(status, indent=1, default=str))
    lines = [f"# Pusher fleet — {status['at']}", "",
             f"rounds complete **{status['rounds_complete']}** / target {status['target']} (no cap, 24/7) · universe {len(universe)} · units done {status['units_done']} (err {status['units_err']}) · registry v{status['registry_version']}",
             f"mean last gain {status['mean_last_gain']} · mean best gain {status['mean_best_gain']}", "",
             "| host | cpu% | workers | outbox | down | hb age |", "|---|---|---|---|---|---|"]
    for h, i in tab.items():
        lines.append(f"| {h} | {i['cpu']} | {i['workers']} | {i['outbox']} | {i['down']} | {i['age_s']}s |")
    (base / "PUSHER_STATUS.md").write_text("\n".join(lines) + "\n")


def build_seed(mac_root, out_dir):
    """Mac-side: assemble seed/anchors for all 270 tradeable + seed/round1 reports from FLEET."""
    sys.path.insert(0, str(mac_root / "tools"))
    import v15_universe as U
    tradeable = sorted(U.tradeable_sym_sides(mac_root))
    fleet = {r["symside"]: r for r in json.loads((mac_root / "data/reports/gain_pusher/runs/FLEET/SUMMARY.json").read_text())}
    (out_dir / "anchors").mkdir(parents=True, exist_ok=True)
    r1, from_progress = [], []
    for ss in tradeable:
        dst = out_dir / "anchors" / f"{ss}.json"
        if ss in fleet:
            src = mac_root / "data/reports/gain_pusher/runs/FLEET" / f"{ss}_improved.json"
            if src.exists():
                dst.write_text(src.read_text())
                r1.append(fleet[ss])
                continue
        pj = mac_root / "data/reports/lifecycle_pilot" / f"{ss}_v14_progress.json"
        if pj.exists():
            try:
                d = json.loads(pj.read_text())
                dst.write_text(json.dumps(d.get("cumulative_overrides") or {}, indent=1))
                from_progress.append(ss)
                continue
            except Exception:
                pass
        dst.write_text(json.dumps({}, indent=1))
        from_progress.append(ss)
    (out_dir / "round1.json").write_text(json.dumps(r1, indent=1, default=str))
    (out_dir / "meta.json").write_text(json.dumps({"tradeable": len(tradeable), "fleet_round1": len(r1), "from_progress_or_empty": from_progress}, indent=1))
    print(f"[seed] anchors={len(tradeable)} fleet_r1={len(r1)} progress_fallback={len(from_progress)} -> {out_dir}")


def seed_import(base, st, seed_dir):
    """S1-side one-time: anchors + round-1 results in, round-2 opens."""
    n = 0
    for f in (seed_dir / "anchors").glob("*.json"):
        shutil_copy(f, base / "anchors" / f.name)
        n += 1
    r1f = seed_dir / "round1.json"
    m = 0
    if r1f.exists():
        for rep in json.loads(r1f.read_text()):
            if str(rep.get("symside", "")).upper() not in {s.upper() for s in tradeable_symsides()}:
                continue
            rep = dict(rep)
            rep["round"] = 1
            imp = None
            ipf = seed_dir / "anchors" / f"{rep['symside']}.json"
            if ipf.exists():
                try:
                    imp = json.loads(ipf.read_text())
                except Exception:
                    imp = None
            ok, _ = ingest_report(st, base, rep, imp)
            m += 1 if ok else 0
    log(f"seed: anchors={n} round1_reports={m}")
    try:
        reg_src = seed_dir / "PRIORITY_SWITCHES.json"
        if reg_src.exists():
            reg = json.loads(reg_src.read_text())
            reg["_version"] = 1
            (base / "registry" / "PRIORITY_SWITCHES.json").write_text(json.dumps(reg, indent=1))
            st["registry_version"] = 1
    except Exception as e:
        log(f"seed registry: {e}")


def shutil_copy(a, b):
    import shutil
    shutil.copy2(a, b)


if __name__ == "__main__":
    main()
