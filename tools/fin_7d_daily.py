"""The fin 7D driver: one pass per day over MEN's crypto book, published side by side as each board lands.

USER 2026-10-31 (verbatim): "fin should start its 7D compute run at 20:15 every day and start trading the sym_sides as
they come in, making sure no sym_side ever exceeds 24hr (whcih would block trading - actually we should make that ~30hrs
ot give it some more time and consistency)".

Why this exists: the crypto half of the 7D chain was driven by `v15_fleet_scheduler`, which the 2026-10-27 switch to
`fleet_dispatch.py` retired. The STOCK half was rescued by `tools/v15_trc_7d_run.py`; the crypto half never was, so
`v15_preopen_7d_activate` has been logging `waiting_for_collection` indefinitely and fin has had nothing to trade.
This is the crypto twin of the trc runner, deliberately built the same way: select, fan out, publish incrementally.

  select    tools/v15_preopen_7d_select.py over men's book (fin's admission source while the mirror is open)
  dispatch  tools/v15_preopen_7d_launch.py per sym_side on the crypto NPZ workers, V18_CATCHUP=1, nice 15, mem floor
  publish   as each board finishes -> data/vector_direct/sevenday_crypto.json, pushed to S1 at once, so the trader
            picks a side up minutes after its board lands instead of waiting for the whole pass

WHAT MAY BE PUBLISHED AS TRADEABLE. `v15_preopen_7d_stage.validate_one` is the bar. Measured 2026-10-31: boards pass
everything it checks -- 7D window, matching 30D source hash, overrides/defaults/full-set agreement, the essential-filter
gate, GAIN-TRUTH (vector == chart-ledger) -- and then fail on `live_verified` alone, because the pilot exits rc=0 after
"[DONE-STEP] published, entering live verify" without ever writing that field. Such a set is VECTOR-VERIFIED BUT NOT
LIVE-PARITY-VERIFIED, and this tool will not quietly call it tradeable: it is recorded with `tier: "vector_only"` and
is published as tradeable ONLY when the operator sets FIN7D_ALLOW_VECTOR_ONLY=1. Default is off, so fin stays flat and
the report says exactly how many sets are one check away. Nothing here is ever labelled more verified than it is.
"""
import argparse
import concurrent.futures as cf
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
SCOPE = "fin_men_crypto"
PY_REMOTE = "/home/niels/.conda/envs/binance_env_mac/bin/python"
# crypto NPZ workers. S1 is left out: it trades the venue and runs the NPZ/state chain (same reason trc gives it 0 slots).
WORKERS = {"s6": 3, "s5": 3, "s2": 1}
MEM_FLOOR_MB = int(os.environ.get("FIN7D_MEM_FLOOR_MB", "4000"))
BOARD_TIMEOUT_S = int(os.environ.get("FIN7D_BOARD_TIMEOUT_S", "3600"))
POLL_S = int(os.environ.get("FIN7D_POLL_S", "60"))
DEADLINE_S = int(os.environ.get("FIN7D_DEADLINE_S", str(4 * 3600)))  # hourly runs: never hold the lock all day
ALLOW_VECTOR_ONLY = os.environ.get("FIN7D_ALLOW_VECTOR_ONLY", "0") == "1"
# a board that declares itself NOT-FINISHED is relaunched this many times before the side is given up on
REDO_ATTEMPTS = int(os.environ.get("FIN7D_REDO_ATTEMPTS", "2"))
MAX_AGE_S = float(os.environ.get("FIN7D_MAX_AGE_S", str(30 * 3600)))
# USER 2026-10-31: "any crypto sym_side that has not had an update for >20hrs has to get updated and traded on 7D fin
# account simple". Each run recomputes every side whose last STAGED 7D set is older than this (or missing), so nothing
# reaches the 30 h entry limit: 10 h of margin for a slow or failed board to be retried.
REFRESH_AFTER_S = float(os.environ.get("FIN7D_REFRESH_AFTER_S", str(20 * 3600)))
S1_BASE_DB = "/home/niels/binance/data/hourly_reconfig/per_sym_store.db"          # men's 30D store (S1 crypto producer)
S1_STORE_DB = "/home/niels/binance-sandbox/data/hourly_reconfig/per_sym_store_7d.db"  # fin's 7D store
S1_SIDES_FILE = "/home/niels/fwd_fin7d_syms.txt"                                   # sides the fin 7D state producer builds
SSH = ["ssh", "-S", "none", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "StrictHostKeyChecking=accept-new"]
S1 = ("s1-int", "s1-pub", "niels@10.0.0.3")
PUBLISHED = ROOT / "data" / "vector_direct" / "sevenday_crypto.json"


def stamp(epoch=None):
    return dt.datetime.fromtimestamp(epoch if epoch is not None else time.time(), dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(msg):
    print(f"[{stamp()}] {msg}", flush=True)


def sh(host, command, timeout=40):
    try:
        r = subprocess.run(SSH + [host, command], capture_output=True, text=True, timeout=timeout)
        return r.returncode, r.stdout.strip(), r.stderr.strip()
    except (subprocess.TimeoutExpired, OSError) as exc:
        return 255, "", f"{type(exc).__name__}: {exc}"


def select_queue(day):
    out = ROOT / "data" / "preopen_7d" / f"queue_{day}.json"
    rc = subprocess.run([sys.executable, str(ROOT / "tools" / "v15_preopen_7d_select.py"), "--out", str(out)],
                        capture_output=True, text=True, cwd=str(ROOT), timeout=900)
    if rc.returncode:
        raise RuntimeError(f"select failed: {rc.stderr[-300:]}")
    doc = json.loads(out.read_text())
    sides = list((doc.get("accounts") or {}).get("fin") or [])
    log(f"selected {len(sides)} fin sym_sides; {len((doc.get('_meta') or {}).get('missing_sql_baseline') or [])} skipped for no 30D baseline")
    return out, doc, sides


def ship_queue(host, local_queue, day):
    remote = f"binance-sandbox/data/preopen_7d/queue_{day}.json"
    sh(host, f"mkdir -p ~/binance-sandbox/data/preopen_7d", 30)
    r = subprocess.run(["rsync", "-az", "--timeout=40", "-e", " ".join(SSH), str(local_queue), f"{host}:{remote}"],
                       capture_output=True, text=True, timeout=120)
    return r.returncode == 0


def launch(host, ss, day):
    # relative to ~/binance-sandbox, which the command cd's into; prefixing it with binance-sandbox/ again made mkdir
    # create a doubled path and the redirect then had no directory to write into (measured 2026-10-31)
    rdir = f"data/preopen_7d/fin_run/{day}/results"
    env = ("V18_CATCHUP=1 V15_7D_PILOT_FILE=v15_pilot.py V15_7D_ALREADY_PAUSED=1 "
           f"V15_7D_MIN_MEM_MB=3000 V15_7D_ANYTIME=1 V15_7D_OVERLAY=0 ")
    cmd = (f"avail=$(awk '/MemAvailable/{{print int($2/1024)}}' /proc/meminfo); "
           f"if [ \"$avail\" -lt {MEM_FLOOR_MB} ]; then echo HOLD_MEM_$avail; exit 0; fi; "
           f"cd ~/binance-sandbox && mkdir -p {rdir}/{ss} && "
           f"test ! -e {rdir}/{ss}/launcher.json && "
           f"{env} setsid -f nice -n 15 {PY_REMOTE} -u tools/v15_preopen_7d_launch.py "
           f"--queue ~/binance-sandbox/data/preopen_7d/queue_{day}.json --sym-side {ss} "
           f"--result-dir ~/binance-sandbox/{rdir} --workers 7 --timeout-sec {BOARD_TIMEOUT_S} --python {PY_REMOTE} "
           f"> ~/binance-sandbox/{rdir}/{ss}/launcher.json 2> ~/binance-sandbox/{rdir}/{ss}/launcher.stderr < /dev/null && echo LAUNCHED")
    rc, out, err = sh(host, cmd, 60)
    return out or f"rc={rc} {err[-120:]}"


def fetch_result(host, ss, day):
    """The finished board's launcher verdict and its progress fields, read straight off the worker."""
    rdir = f"binance-sandbox/data/preopen_7d/fin_run/{day}/results/{ss}"  # fetch_result reads absolute-from-home
    rc, out, _ = sh(host, f"cat ~/{rdir}/launcher.json 2>/dev/null", 30)
    if rc or not out.strip():
        return None
    try:
        verdict = json.loads(out)
    except ValueError:
        return None
    # the progress file reaches 8 MB (its `done` map holds every cell), so only the scalars are pulled out of it
    rc2, out2, _ = sh(host, f"python3 -c \"import json;d=json.load(open('/home/niels/{rdir}/progress/{ss}_7d_progress.json'));"
                            f"print(json.dumps({{k:d.get(k) for k in ('window_days','result_done_utc','final_path','publish_class',"
                            f"'not_compliant','unprotected','source_30d_sha256','live_verified','baseline_gain','cumulative_gain')}}))\" 2>/dev/null", 60)
    progress = {}
    if not rc2 and out2.strip():
        try:
            progress = json.loads(out2)
        except ValueError:
            progress = {}
    # A board can declare ITSELF unfinished and ask to be relaunched: "[NOT-FINISHED] <ss> REDO scheduled — no publish
    # this run, herd relaunches into a fresh fill". That is not a result, however complete the sheet looks.
    _, redo, _ = sh(host, f"grep -c -e 'NOT-FINISHED' -e 'REDO scheduled' ~/{rdir}/pilot.log 2>/dev/null", 30)
    try:
        redo_n = int(redo.strip() or 0)
    except ValueError:
        redo_n = 0
    return {"verdict": verdict, "progress": progress, "host": host, "redo": redo_n > 0}


def classify(result):
    """(tier, why). 'validated' = validate_one passed. 'vector_only' = everything but the live-parity record."""
    if result is None:
        return None, "no result yet"
    verdict, progress = result["verdict"], result["progress"]
    if verdict.get("valid"):
        return "validated", "validate_one passed"
    why = str(verdict.get("why") or "")
    rc = verdict.get("rc")
    if rc is None or int(rc) != 0:   # `or 1` would turn rc=0 (success) into a failure
        return None, f"board failed rc={verdict.get('rc')}: {why[:120]}"
    if progress.get("window_days") != 7:
        return None, f"not a 7D board: {why[:120]}"
    if progress.get("not_compliant") or progress.get("unprotected") or progress.get("publish_class") == "BEST_EFFORT":
        return None, f"failed the pilot qualification gate: {why[:120]}"
    if result.get("redo"):
        return "redo", "the board declared itself NOT-FINISHED and scheduled a REDO — it must be relaunched, not published"
    gain = progress.get("cumulative_gain")
    if gain is None or float(gain) == 0.0:
        return None, f"no result of its own (cumulative_gain={gain}, baseline_gain={progress.get('baseline_gain')})"
    live = progress.get("live_verified") or {}
    if not progress.get("result_done_utc") or not progress.get("final_path") or not live:
        return "vector_only", "computed and vector-verified; the pilot never recorded live/vector parity"
    return None, f"refused: {why[:160]}"


def load_prior():
    """Every side the last publication recorded, with its staged_epoch. A run must ADD to these, never wipe them."""
    try:
        doc = json.loads(PUBLISHED.read_text())
    except (OSError, ValueError):
        return {}
    return {ss: row for ss, row in (doc.get("detail") or {}).items() if isinstance(row, dict) and row.get("staged_epoch") is not None}


def needs_refresh(ss, prior, now):
    row = prior.get(ss)
    return not row or not row.get("staged") or now - float(row["staged_epoch"]) > REFRESH_AFTER_S


def stage_on_s1(worker, ss, run_id, day):
    """Lay this board's 7D overrides on men's 30D base in fin's 7D store on S1. (ok, why)."""
    rdir = f"binance-sandbox/data/preopen_7d/fin_run/{run_id}/results/{ss}"
    rc, out, err = sh(worker, f"python3 -c \"import json;d=json.load(open('/home/niels/{rdir}/progress/{ss}_7d_progress.json'));"
                              f"print(json.dumps({{'overrides':d.get('cumulative_overrides'),'metrics':{{k:d.get(k) for k in "
                              f"('cumulative_gain','baseline_gain','window_days','result_done_utc')}}}}))\"", 120)
    if rc or not out.strip():
        return False, f"could not read the board's overrides on {worker}: {err[-120:]}"
    try:
        doc = json.loads(out.strip().splitlines()[-1])
    except ValueError:
        return False, "board overrides unparsable"
    overrides = doc.get("overrides")
    if not isinstance(overrides, dict) or not overrides:
        return False, "board has no cumulative_overrides"
    with tempfile.TemporaryDirectory() as tmp:
        ov, mt = Path(tmp) / "ov.json", Path(tmp) / "mt.json"
        ov.write_text(json.dumps(overrides))
        mt.write_text(json.dumps(doc.get("metrics") or {}))
        errors = []
        for host in S1:
            try:
                r = subprocess.run(["scp", "-q", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", str(ov), str(mt), f"{host}:/tmp/"],
                                   capture_output=True, text=True, timeout=90)
            except (subprocess.TimeoutExpired, OSError) as exc:
                errors.append(f"{host}: {type(exc).__name__}")
                continue
            if r.returncode:
                errors.append(f"{host}: scp rc={r.returncode}")
                continue
            rc2, out2, err2 = sh(host, f"cd ~/binance-sandbox && python3 -I tools/fin_7d_stage_one.py --sym-side {ss} "
                                       f"--overrides /tmp/ov.json --metrics /tmp/mt.json --base-db {S1_BASE_DB} --store-db {S1_STORE_DB} "
                                       f"--day {day} && (grep -qx {ss} {S1_SIDES_FILE} 2>/dev/null || echo {ss} >> {S1_SIDES_FILE})", 120)
            if rc2 == 0:
                return True, f"staged into fin's 7D store via {host} ({len(overrides)} overrides)"
            errors.append(f"{host}: {(err2 or out2)[-160:]}")
        return False, "staging failed on every S1 path: " + " | ".join(errors)


def publish(sides, day, meta):
    """Rewrite sevenday_crypto.json with every side that may be traded, then push it to S1."""
    now = time.time()
    combined = {**load_prior(), **sides}   # earlier runs' sides stay published until they age out
    fresh = {ss: row for ss, row in combined.items() if now - float(row["staged_epoch"]) <= MAX_AGE_S}
    # tradeable needs the set STAGED as well as verified: fin trades from per_sym_store_7d.db, so an authorised side
    # with no staged 7D set would have nothing to be traded from
    tradeable = {ss: row["staged_epoch"] for ss, row in fresh.items() if row.get("staged")
                 and (row["tier"] == "validated" or (row["tier"] == "vector_only" and ALLOW_VECTOR_ONLY))}
    # "redo" is recorded for the report and is NEVER tradeable under any flag
    doc = {"source": "fin_7d_daily", "venue": "crypto", "scope": SCOPE, "account": "fin", "book": "men",
           "day": day, "epoch": now, "written_at": stamp(now), "max_age_s": MAX_AGE_S,
           "allow_vector_only": ALLOW_VECTOR_ONLY, "sides": tradeable,
           "detail": {ss: {k: row.get(k) for k in ("tier", "why", "host", "staged_utc", "staged_epoch", "staged", "stage_why", "run_id")}
                      for ss, row in fresh.items()},
           "counts": {"tradeable": len(tradeable), "vector_only": sum(1 for r in fresh.values() if r["tier"] == "vector_only"),
                      "validated": sum(1 for r in fresh.values() if r["tier"] == "validated"),
                      "redo": sum(1 for r in fresh.values() if r["tier"] == "redo")},
           "meta": meta,
           "contract": "sides{} are the ONLY sym_sides fin may enter; a side absent or older than max_age_s is exit-only"}
    PUBLISHED.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(doc, indent=1, sort_keys=True)
    with tempfile.NamedTemporaryFile("w", dir=str(PUBLISHED.parent), prefix=f".{PUBLISHED.name}.", delete=False) as fh:
        fh.write(body)
        tmp = Path(fh.name)
    os.replace(tmp, PUBLISHED)
    delivered = []
    with cf.ThreadPoolExecutor(max_workers=len(S1)) as pool:
        for host, ok in zip(S1, pool.map(lambda h: _push_s1(h), S1)):
            if ok:
                delivered.append(host)
    log(f"published {len(tradeable)} tradeable / {len(fresh)} fresh sides -> S1 via {delivered or 'NOTHING (push failed on every path)'}")
    return doc, delivered


def _push_s1(host):
    target = "binance/data/vector_direct/sevenday_crypto.json"
    sh(host, "mkdir -p ~/binance/data/vector_direct", 25)
    r = subprocess.run(["rsync", "-az", "--timeout=30", "-e", " ".join(SSH), str(PUBLISHED), f"{host}:{target}"],
                       capture_output=True, text=True, timeout=90)
    return r.returncode == 0


def run(day=None, dry_run=False):
    day = day or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%d")
    # A directory PER RUN, never per day: hourly refreshes would otherwise find the previous run's launcher.json, skip the
    # relaunch (`test ! -e`) and re-classify an OLD result under a NEW timestamp — an old test wearing a new date.
    run_id = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%MZ")
    started = time.time()
    queue_path, queue, candidates = select_queue(run_id)
    prior = load_prior()
    sides = [ss for ss in candidates if needs_refresh(ss, prior, started)]
    log(f"run {run_id}: {len(candidates)} fin sides with a 30D base; {len(sides)} older than {REFRESH_AFTER_S / 3600:.0f} h or never "
        f"staged -> recompute; {len(candidates) - len(sides)} still fresh")
    if not sides:
        publish({}, day, {"run_id": run_id, "started_utc": stamp(started), "boards": 0, "note": "nothing stale"})
        return 0
    hosts = [h for h in WORKERS if sh(h, "true", 20)[0] == 0]
    if not hosts:
        raise RuntimeError("no crypto NPZ worker is reachable")
    for h in list(hosts):
        if not ship_queue(h, queue_path, run_id):
            log(f"{h}: queue could not be shipped — excluded from this pass")
            hosts = [x for x in hosts if x != h]
    log(f"workers {', '.join(f'{h}x{WORKERS[h]}' for h in hosts)} | boards {len(sides)} | deadline {DEADLINE_S / 3600:.1f}h"
        + (" | DRY RUN" if dry_run else ""))
    if dry_run:
        return 0
    pending, inflight, done, attempts = list(sides), {}, {}, {}
    meta = {"run_id": run_id, "started_utc": stamp(started), "boards": len(sides)}
    while (pending or inflight) and time.time() - started < DEADLINE_S:
        for host in hosts:
            free = WORKERS[host] - sum(1 for h in inflight.values() if h == host)
            while free > 0 and pending:
                ss = pending.pop(0)
                answer = launch(host, ss, run_id)
                if answer == "LAUNCHED":
                    inflight[ss] = host
                    free -= 1
                    log(f"launched {ss} on {host}")
                elif answer.startswith("HOLD_MEM"):
                    pending.append(ss)   # the host is short of memory right now; try it again next poll
                    free = 0
                else:
                    pending.append(ss)
                    log(f"{ss} on {host}: {answer}")
                    free = 0
        for ss, host in list(inflight.items()):
            result = fetch_result(host, ss, run_id)
            if result is None:
                continue
            inflight.pop(ss, None)
            tier, why = classify(result)
            if tier == "redo":
                # the board asked for a fresh fill; give it one, bounded, instead of losing the side for the day
                n = attempts.get(ss, 0) + 1
                attempts[ss] = n
                if n <= REDO_ATTEMPTS:
                    sh(host, f"rm -f ~/binance-sandbox/data/preopen_7d/fin_run/{run_id}/results/{ss}/launcher.json", 30)
                    pending.append(ss)
                    log(f"REDO {ss} on {host}: relaunching (attempt {n + 1} of {REDO_ATTEMPTS + 1})")
                else:
                    done[ss] = {"tier": "redo", "why": f"{why} (gave up after {n} relaunches)", "host": host, "run_id": run_id,
                                "staged_epoch": time.time(), "staged_utc": stamp(), "staged": False}
                    log(f"GAVE UP {ss} on {host}: still asking for a REDO after {n} relaunches")
                continue
            if not tier:
                log(f"REFUSED {ss} on {host}: {why}")
                continue
            row = {"tier": tier, "why": why, "host": host, "run_id": run_id, "staged_epoch": time.time(), "staged_utc": stamp()}
            ok, stage_why = stage_on_s1(host, ss, run_id, day)
            row.update(staged=ok, stage_why=stage_why)
            done[ss] = row
            log(f"FINISHED {ss} on {host}: {tier} — {why} | {'STAGED' if ok else 'NOT STAGED'}: {stage_why}")
            publish(done, day, meta)
        if pending or inflight:
            time.sleep(POLL_S)
    doc, _ = publish(done, day, {**meta, "finished_utc": stamp(), "unfinished": sorted(pending + list(inflight))})
    log(f"run {run_id} done in {(time.time() - started) / 60:.0f} min: {doc['counts']} | unfinished {len(pending) + len(inflight)}")
    if doc["counts"]["tradeable"] == 0 and doc["counts"]["vector_only"] and not ALLOW_VECTOR_ONLY:
        log(f"NOTE {doc['counts']['vector_only']} sets are vector-verified and ONE check short of tradeable "
            f"(the pilot never records live/vector parity). fin stays flat. Set FIN7D_ALLOW_VECTOR_ONLY=1 to trade them.")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--day", default=None, help="UTC day stamp, default today")
    ap.add_argument("--dry-run", action="store_true", help="select and show the plan, launch nothing")
    a = ap.parse_args(argv)
    return run(a.day, a.dry_run)


if __name__ == "__main__":
    sys.exit(main())
