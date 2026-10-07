#!/usr/bin/env python3
"""v15_daily_pipeline — resumable daily stage machine (DAILY_OPTIMIZATION_PLAN stages 1-8), driven from the Mac.

Stages (state in data/daily_pipeline_state.json; each stage is idempotent and only advances on success):
  1 finish_check   all sym_sides of the venue have a finished 30D progress JSON (or --deadline hit -> partial, flagged)
  2 collect        per host: newest *_v14_progress.json per sym_side -> v15_vector_delta_rebuild.py --scan/--emit-partial -> Mac merge
  3 template       tools/v15_daily_template_update.py --apply   (parent-owned: AVG_DELTA/POS_SYM, promote, worst_first, verify)
  4 sync           rsync the 4 TEMPLATE_*.xlsx (+ data/cat_side_defaults_4.json, cat_side_promotions.json) to every host, md5-verify
  5 restart        new EMPTY progress dir on every host (~/v15_current_progress_dir.txt) so the pilot's ALREADY-FINISHED guard does
                   not fire; fleet scheduler then launches new-template 30D jobs. Running pilots are NOT killed.
  6 verify365      scheduler --windows 365D jobs (tools/v15_365_cycle.py) for finished winners
  7 per_sym_apply  STOCKS first, then crypto, through the EXISTING sanctioned paths only: tools/confirm_365d.py (crypto certification)
                   and tools/promote_365cycle_winners_20260929.py (per-sym books). LIVE WRITES REQUIRE --live-apply; default dry-run.
Usage: v15_daily_pipeline.py [--once] [--dry-run] [--stage N] [--reset] [--live-apply]
"""
import argparse, datetime, fcntl, hashlib, json, os, pathlib, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
import v15_fleet_scheduler as FS  # noqa: E402

STATE = ROOT / "data" / "daily_pipeline_state.json"
TEMPLATES = [f"SPREADSHEETS/TEMPLATE_{v}_{s}.xlsx" for v in ("CRYPTO", "STOCKS") for s in ("LONG", "SHORT")]
SYNC_EXTRA = ["data/cat_side_defaults_4.json", "data/cat_side_promotions.json"]  # MANDATORY (stage 4 fails without them)
STAGES = ["finish_check", "collect", "template", "sync", "restart", "verify365", "per_sym_apply"]
PY = sys.executable


def load():
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def save(st):
    STATE.parent.mkdir(exist_ok=True)
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(st, indent=1))
    tmp.replace(STATE)


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


def hosts():
    return json.load(open(FS.HOSTS))["hosts"]


def run(cmd, dry, **kw):
    print("  $", " ".join(map(str, cmd)), "(dry)" if dry else "", flush=True)
    return 0 if dry else subprocess.run(cmd, **kw).returncode


def stage_finish_check(st, a):
    cfg = json.load(open(FS.HOSTS))
    need = {v: {f"{s}_{d}" for s in FS.read_order(cfg, v) for d in ("LONG", "SHORT")} for v in ("stocks", "crypto")}
    done = set()
    for h in hosts():
        pd = (FS.probe(h) or {}).get("pdir") if not a.dry_run else None
        if pd:
            done |= FS.done_set(h, pd)
    st["finish"] = {v: {"need": len(n), "done": len(n & done)} for v, n in need.items()}
    complete = all(len(n - done) == 0 for n in need.values()) if done else False
    now = datetime.datetime.now(datetime.timezone.utc)
    deadline_hit = FS.minutes_to_open(now) < a.deadline_min and not FS.us_market_open(now) and len(need["stocks"] - done) == 0
    st["partial"] = bool(not complete)
    print("  finish:", st["finish"], "complete" if complete else "INCOMPLETE")
    return complete or a.allow_partial or deadline_hit or a.dry_run


def stage_collect(st, a):
    out = ROOT / "data" / "daily_partials" / datetime.date.today().isoformat()
    out.mkdir(parents=True, exist_ok=True)
    parts = []
    for h in hosts():
        pd = (FS.probe(h) or {}).get("pdir") if not a.dry_run else "~/v15_current"
        if not pd:
            print("  skip unreachable", h["name"]); continue
        r, pf = h["root"], f"/tmp/v15_partial_{h['name']}.json"
        cmd = (f"cd {r} && ls {pd}/*_v14_progress.json > /tmp/v15_files.txt && .venv/bin/python tools/v15_vector_delta_rebuild.py "
               f"--files /tmp/v15_files.txt --emit-partial {pf}")
        if run(["ssh", h.get("_via", h["ssh"][0]), cmd], a.dry_run) == 0:
            dst = out / f"{h['name']}.json"
            if run(["scp", "-q", f"{h.get('_via', h['ssh'][0])}:{pf}", str(dst)], a.dry_run) == 0:
                parts.append(str(dst))
    st["partials"] = parts
    if a.dry_run:
        return True
    if not parts:
        return False
    return run([PY, str(ROOT / "tools/v15_vector_delta_rebuild.py"), "--merge", ",".join(parts)], False) == 0


def stage_template(st, a):
    script = ROOT / "tools/v15_daily_template_update.py"
    if not script.exists():
        print("  MISSING tools/v15_daily_template_update.py (parent-owned) — cannot advance")
        return a.dry_run
    # USER 2026-09-30: bold defaults -> cat_side_defaults_4.json (live ez/tradier + sweep engine resolver) in the same step
    return run([PY, str(script)] + ([] if a.dry_run else ["--apply", "--sync-defaults"]), False) == 0


def stage_sync(st, a):
    ok = True
    for h in hosts():
        tgt = h.get("_via", h["ssh"][0])
        missing = [f for f in SYNC_EXTRA if not (ROOT / f).exists()]
        if missing:
            print("  MANDATORY sync file missing:", missing); return False
        for rel in TEMPLATES + SYNC_EXTRA:
            for root in (h["root"], "~/binance"):
                ok &= run(["rsync", "-az", "-e", "ssh -o ConnectTimeout=10", str(ROOT / rel), f"{tgt}:{root}/{rel}"], a.dry_run) == 0
        if not a.dry_run:  # md5 verify (sandbox copy)
            files = TEMPLATES + SYNC_EXTRA
            out = FS.sh_ssh(h, "cd " + h["root"] + " && md5sum " + " ".join(files)) or ""
            want = {rel: md5(ROOT / rel) for rel in files}
            got = {l.split()[1]: l.split()[0] for l in out.splitlines() if len(l.split()) == 2}
            bad = [r for r in files if got.get(r) != want[r]]
            if bad:
                print("  MD5 MISMATCH", h["name"], bad); ok = False
    return ok


def stage_restart(st, a):
    tag = datetime.datetime.utcnow().strftime("%Y%m%d_%H%M")
    st["progress_tag"] = tag
    ok = True
    for h in hosts():
        pd = f"~/v15_run_{tag}/progress"
        cmd = f"mkdir -p {pd} && echo $HOME/v15_run_{tag}/progress > ~/v15_current_progress_dir.txt"
        ok &= run(["ssh", h.get("_via", h["ssh"][0]), cmd], a.dry_run) == 0
    print("  (scheduler picks up the new dir next tick; old pilots keep running)")
    return ok


def chain_state():
    """latest scheduler snapshot (data/daily_reports/<date>/chain_state.json) — per-cat 30D/365D/REPAIR progress."""
    day = datetime.datetime.utcnow().strftime("%Y%m%d")
    p = ROOT / "data" / "daily_reports" / day / "chain_state.json"
    return json.loads(p.read_text()) if p.exists() else None


def cat_chain_done(c):
    """a cat_side's 365D/REPAIR chain is finished: 30D complete, every sym_side ended ok / unverifiable / failing (attempts exhausted)."""
    cs = (chain_state() or {}).get("cats", {}).get(c)
    if not cs or not cs.get("unlocked_365"):
        return False
    settled = cs["v365_ok"] + cs["v365_unverifiable"] + cs["repair_ok"] + len(cs["failing"])
    return settled >= cs["done30"]


def stage_verify365(st, a):
    # the fleet scheduler (cron) owns the 365D -> REPAIR chain; this stage only gates on it (per cat_side, stocks deadline-aware)
    now = datetime.datetime.now(datetime.timezone.utc)
    cats = ["STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT"]
    st["chain"] = {c: cat_chain_done(c) for c in cats}
    print("  chain done per cat:", st["chain"])
    stocks_ready = st["chain"]["STOCKS_LONG"] and st["chain"]["STOCKS_SHORT"]
    deadline = (not FS.us_market_open(now)) and FS.minutes_to_open(now) < a.deadline_min
    if deadline and not stocks_ready:
        st["partial_stocks"] = "deadline hit before stock chain finished — applying only verified sym_sides, flagged PARTIAL"
        print("  " + st["partial_stocks"])
    return stocks_ready or deadline or a.dry_run


def stage_apply(st, a):
    """Go-live gate stays pos30D AND pos365D; ONLY sanctioned paths (confirm_365d.py, promote_365cycle_winners_20260929.py).
    Stocks first (must be live before the open); crypto applies as soon as its chain is done. Dry-run unless --live-apply."""
    dry = a.dry_run or not a.live_apply
    ok = True
    done = (st.get("chain") or {})
    if done.get("STOCKS_LONG") or done.get("STOCKS_SHORT") or st.get("partial_stocks") or a.dry_run:
        ok &= run([PY, str(ROOT / "tools/promote_365cycle_winners_20260929.py")] + (["--dry-run"] if dry else []), False) == 0
    if done.get("CRYPTO_LONG") or done.get("CRYPTO_SHORT") or a.dry_run:
        ok &= run([PY, str(ROOT / "tools/confirm_365d.py"), "--all"] + (["--list"] if dry else []), False) == 0
    st["per_sym_apply"] = "DRY-RUN only — needs a --live-apply decision" if dry else "applied"
    st["NEEDS_DECISION"] = "live per_sym write pending --live-apply (evidence file for promote_365cycle_winners is still the 2026-09-29 one)" if dry else None
    return ok


FUNCS = [stage_finish_check, stage_collect, stage_template, stage_sync, stage_restart, stage_verify365, stage_apply]


def main():
    if (ROOT / "data" / "autopilot" / "state.json").exists() and not os.environ.get("ALLOW_LEGACY_PIPELINE"):
        sys.exit("REFUSED: superseded by tools/v15_autopilot.py (s1 cron) — its stages collect/template/normalise/defaults/sync/restart + the Monday final phase replace this machine. Set ALLOW_LEGACY_PIPELINE=1 only to debug.")
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stage", type=int)
    ap.add_argument("--reset", action="store_true")
    ap.add_argument("--live-apply", action="store_true")
    ap.add_argument("--allow-partial", action="store_true")
    ap.add_argument("--deadline-min", type=int, default=45, help="minutes before US open at which stocks are applied even if crypto is unfinished")
    a = ap.parse_args()
    lk = open("/tmp/v15_daily_pipeline.lock", "w")
    try:
        fcntl.flock(lk, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("[pipeline] locked"); return
    st = {} if a.reset else load()
    i = a.stage - 1 if a.stage else STAGES.index(st.get("next", "finish_check"))
    while i < len(STAGES):
        print(f"[pipeline] stage {i+1} {STAGES[i]}", flush=True)
        ok = FUNCS[i](st, a)
        st.setdefault("log", []).append({"stage": STAGES[i], "ok": bool(ok), "at": datetime.datetime.utcnow().isoformat() + "Z", "dry": a.dry_run})
        st["log"] = st["log"][-50:]
        if not ok:
            print(f"[pipeline] stage {STAGES[i]} not ready/failed — stays here"); st["next"] = STAGES[i]; break
        i += 1
        st["next"] = STAGES[i] if i < len(STAGES) else "finish_check"
        if a.stage or a.once:
            break
    if not a.dry_run:
        save(st)


if __name__ == "__main__":
    main()
