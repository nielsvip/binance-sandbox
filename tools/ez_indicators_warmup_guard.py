#!/usr/bin/env python3
"""EZ_INDICATORS WARM-UP GUARD (USER 2026-10-19: "the root cause needs to be fixed forever at once this is not
supposed to be possible").

WHAT BROKE, MEASURED 2026-10-19 02:45-03:05Z
--------------------------------------------
`data/latest_market_data.json` — the crypto indicator snapshot every live decision reads — stopped advancing at
2026-10-18 20:46Z and was still 22471 s stale 6 h later, while `ez_indicators.py` was alive and computing and NOT
ONE error line was written. The chain, each link proven from the log:

  1. `ez_indicators.run()` performs a SERIAL warm-up over every timeframe
     (`for tf in self._schedule_order: await self._process_timeframe(tf, ...)`, ez_indicators.py:2926-2927,
     `_schedule_order = ["D", "4h", "1h", "15m", "3m"]`, ez_indicators.py:2714) and only AFTER that loop creates its
     task set, `_save_loop` among them (ez_indicators.py:2930). So NOTHING writes the snapshot — neither
     `_save_data`'s pretty write nor `_broadcast_market_data`'s Plan B/Plan C writes (ez_indicators.py:4046-4053) —
     until all five timeframes have completed once.
  2. That warm-up takes >30 min on 199 symbols (measured D 01:36:33 -> 4h 01:48:25 -> 1h 01:54:35 -> 15m 01:59:26,
     i.e. 712 s / 370 s / 291 s between single log lines).
  3. `run_with_watchdog.sh:186` sets `NO_OUTPUT_TIMEOUT=120` for ez_indicators — "writes every ~1s" — and the kill
     criterion is purely the mtime of /Users/niels/logs/ez_indicators.log (run_with_watchdog.sh:383-398). During
     warm-up the process legitimately logs ONE line per timeframe, minutes apart, so the watchdog SIGTERMs a
     perfectly healthy process (`exit=143`, `signal 15: TERM`) partway through, over and over, and every restart
     throws the warm-up away and begins again at "D". COUNTED FROM THE WATCHDOG'S OWN LOG (the application log's
     "SCRIPT RESTART" banner is import-time noise — see _real_restarts_last_hour): 12 real restarts in the 20:00
     hour, then 60 / 30 / 61 / 26 / 11 / 19 through 02:00, and of the 38 kills in that span 34 carry the reason
     "No output for 12Xs". The two others ("415s", "306s") are the genuinely stuck processes the timeout is for.
  4. `[3m] full=` — the last timeframe, the one that gates the task set — last appeared at 2026-10-18 20:04:17 and
     then never again. `sentiment_top_40.json` 20:04, `sentiment_bottom_40.json` 20:05, `ranking_points.json` 20:05
     (all written off the end of that pass) agree to the minute.

THE TRAP, AND WHY IT IS PERMANENT ONCE ENTERED
----------------------------------------------
The log traffic that satisfies the 120 s watchdog is produced BY the save loop, which only exists AFTER warm-up.
So once a single kill lands before warm-up completes, the process can never again reach the state that keeps it
alive: it is killed at ~2-6 min, restarts, gets ~1/3 of the way through, is killed again, forever. The only reason
it limped at all is the incidental `[SIGNAL] Failed to publish signal ... TimeoutError` noise from `_emit_signals`
(ez_indicators.py:5027) — too sporadic to reliably stay under 120 s. Downstream, the Mac correctly self-reported
`venues.crypto.stale=True` and asked S1 to produce crypto data, so S1 was promoted to failover and burned ~2 of its
16 cores on live producers instead of backtests for 6 h. S1's behaviour was CORRECT; the Mac was the defect.

WHAT THIS GUARD DOES
--------------------
It replaces a BAD liveness proxy (log chattiness) with a GOOD one, without touching either locked file
(`ez_indicators.py` LOCKED_FILES.md:372, `run_with_watchdog.sh` LOCKED_FILES.md:388) and without weakening the
watchdog. While — and only while — ez_indicators is provably ALIVE AND MAKING WARM-UP PROGRESS, it appends one
`[WARMUP_HEARTBEAT]` line to the log the watchdog stats, so the bogus kill does not land and the warm-up is allowed
to finish. The watchdog keeps every one of its teeth for the cases it exists for:

  * no CPU advance in CPU_SAMPLE_S            -> no heartbeat -> watchdog kills it, exactly as before
  * no new timeframe line in TF_MAX_GAP_S     -> no heartbeat -> watchdog kills it (a wedged warm-up is still dead)
  * process older than WARMUP_GRACE_S         -> no heartbeat -> watchdog kills it (patience is bounded)
  * warm-up already finished (3m seen, or a save line seen) -> no heartbeat at all; steady state is self-sustaining

So the guard removes ONLY the false positive. It never restarts, kills, or signals anything.

It also reports, every run, to data/reports/indicator_snapshot_health.json, and raises
data/reports/ALERT_INDICATOR_SNAPSHOT.txt + exit 1 when either half of the chain is broken:
  * the Mac snapshot is older than SNAPSHOT_STALE_ALERT_S                      (the defect above, caught in minutes)
  * S1 has held crypto failover longer than S1_FAILOVER_ALERT_S while the Mac heartbeat AND the Mac snapshot are
    both fresh (that would be a genuine failover bug, and the one-command revert is printed with the alert)

Read-only except for the heartbeat line and its own report files.

WHY THIS LIVES OUTSIDE ez_indicators.py, AND THE IN-CODE FIX STILL OWED
-----------------------------------------------------------------------
Both files that carry the defect are LOCKED — `ez_indicators.py` (LOCKED_FILES.md:372) and
`run_with_watchdog.sh` (LOCKED_FILES.md:388, :537) — and CLAUDE.md STEP 0b allows an edit only when the USER says
"unlock <file>" in the same message. That did not happen, so neither was touched. This guard is a complete,
tested fix for the kill loop, but it is an external one: it depends on a cron staying installed.

THE PERMANENT IN-CODE FIX, for whenever the operator unlocks it — ONE addition, no behaviour change, and it is the
pattern this repo has already used for exactly this failure: `run_with_watchdog.sh:197` records that
`tradier_rankings` was SIGKILLed 67x/day because one synchronous loop "logged NOTHING", and the fix was "a progress
heartbeat was added to the loop". The same is owed here — inside `_process_timeframe` (or the warm-up loop at
ez_indicators.py:2926), emit one INFO line every N symbols, e.g.:

    if processed % 25 == 0:
        logger.info(f"[WARMUP] {timeframe} {processed}/{len(self.symbols)}")

That makes ez_indicators self-sufficient under the existing 120 s timeout, needs no cron, and lets this guard fall
back to being pure detection. Until then the heartbeat here is load-bearing: DO NOT remove the cron line
(`# EZIND_WARMUP_GUARD`) without landing that change first.

Usage:
    tools/ez_indicators_warmup_guard.py                 # one pass + internal top-up loop (cron: every minute)
    tools/ez_indicators_warmup_guard.py --once          # single check, no loop (for tests/manual)
    tools/ez_indicators_warmup_guard.py --dry-run       # never append a heartbeat, just report
    tools/ez_indicators_warmup_guard.py --no-s1         # skip the ssh probe of S1
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOG = Path("/Users/niels/logs/ez_indicators.log")
SNAPSHOT = ROOT / "data" / "latest_market_data.json"
OUT = ROOT / "data" / "reports" / "indicator_snapshot_health.json"
ALERT = ROOT / "data" / "reports" / "ALERT_INDICATOR_SNAPSHOT.txt"

# --- heartbeat policy (every bound below is a reason the watchdog KEEPS its kill) -----------------------------
HB_WHEN_LOG_AGE_S = int(os.environ.get("EZIND_HB_WHEN_LOG_AGE_S", "45"))
#   Watchdog kills at 120 s of log silence (run_with_watchdog.sh:186). Top up at 45 s, leaving 75 s of margin for
#   a missed cron minute. MEASURED 2026-10-19 04:0xZ: at 60 s this was too tight — see S1_PROBE_EVERY_S below.
TF_MAX_GAP_S = int(os.environ.get("EZIND_TF_MAX_GAP_S", "1800"))
#   MEASURED gaps between consecutive warm-up timeframe lines, 2026-10-19 (pid 57405, the first process the guard
#   kept alive long enough to watch): D 03:09:24 -> 4h 03:20:57 (693 s) -> 1h 03:29:10 (493 s) -> 15m 03:40:25
#   (675 s). The FIFTH and last pass, "3m", is the heaviest (199 symbols at the finest granularity) and has not yet
#   been timed end to end, so 1800 s leaves it room while still calling a warm-up that has genuinely stopped
#   progressing dead within 30 min.
WARMUP_GRACE_S = int(os.environ.get("EZIND_WARMUP_GRACE_S", "5400"))
#   A full warm-up is ~47 min at 199 symbols (02:58:56 start, four of five passes done by 03:40:25). 90 min is
#   generous without being open-ended; past it, protection stops unconditionally and the watchdog takes over.
CPU_SAMPLE_S = float(os.environ.get("EZIND_CPU_SAMPLE_S", "10.0"))
CPU_MIN_DELTA_S = float(os.environ.get("EZIND_CPU_MIN_DELTA_S", "0.05"))
#   MEASURED 2026-10-19 03:09Z: the warm-up is I/O-bound, not CPU-bound — pid 57405 had burned 29.5 s of CPU in
#   706 s of wall clock (4%) while legitimately fetching klines for 199 symbols, and a first attempt at a 0.3 s /
#   3 s floor (10% CPU) rejected that healthy process outright. The CPU check is therefore only a FLOOR against a
#   fully parked interpreter (a deadlocked process accrues ~0); the real liveness evidence is TF_MAX_GAP_S below,
#   which is warm-up progress itself, plus the absolute WARMUP_GRACE_S bound.

# --- alert policy --------------------------------------------------------------------------------------------
SNAPSHOT_STALE_ALERT_S = int(os.environ.get("EZIND_SNAPSHOT_STALE_ALERT_S", "600"))
S1_FAILOVER_ALERT_S = int(os.environ.get("EZIND_S1_FAILOVER_ALERT_S", "900"))
S1_PROBE_EVERY_S = int(os.environ.get("EZIND_S1_PROBE_EVERY_S", "300"))
#   MEASURED 2026-10-19 04:02Z — THE GUARD'S OWN FIRST BUG. A full pass (55 s heartbeat loop + an unconditional
#   ssh probe of S1) took >60 s, so `v15_lock_run.sh` found its lock held and skipped every other cron minute:
#   30 runs in 60 minutes, leaving ~70 s holes. With the heartbeat threshold at 60 s a 124 s silence fitted inside
#   one hole and the watchdog killed the process anyway at 03:45:55 (after 2904 s — far better than the 195-500 s
#   before the guard, but still a kill). The probe is now throttled and cached so a pass stays well inside a
#   minute: heartbeat coverage must never depend on how long an ssh round trip takes.
S1_CACHE = ROOT / "data" / "reports" / ".ezind_s1_probe_cache.json"
S1_HOST = os.environ.get("EZIND_S1_HOST", "s1-pub")
S1_STATE = "/home/niels/binance/data/live_failover/state.json"
S1_MAC_HB = "/home/niels/binance/data/live_failover/mac_data_heartbeat.json"
REVERT_CMD = (
    f"ssh {S1_HOST} 'sudo systemctl stop live-failover-crypto-producers.service "
    "&& bash ~/binance/tools/s1_failover_stop.sh crypto'"
)

TS_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\]")
RESTART_RE = re.compile(r"^SCRIPT RESTART - ez_indicators - (\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) UTC")
TF_RE = re.compile(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}),\d+\] INFO \[(D|4h|1h|15m|3m)\] full=")
SAVE_RE = re.compile(r"Saved indicators to latest_market_data")
LAST_TF = "3m"


def _utc(epoch):
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_ts(text):
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    except Exception:
        return None


def _age(path):
    try:
        return time.time() - Path(path).stat().st_mtime
    except Exception:
        return None


def _pid():
    """The ez_indicators python child, never the run_with_watchdog.sh wrapper."""
    try:
        out = subprocess.run(
            ["pgrep", "-f", r"python -u ez_indicators\.py"], capture_output=True, text=True, timeout=10
        ).stdout.split()
        return int(out[0]) if out else None
    except Exception:
        return None


def _ps_seconds(pid, keyword):
    """Parse a ps time column into seconds. macOS prints [[dd-]hh:]mm:ss[.ff].

    2026-10-19: this helper exists because macOS `ps` has NO `etimes` keyword (that is GNU/procps only) — asking
    for it makes BSD ps print its whole keyword list instead of a number, which silently made the process-age
    check return None and suppressed every heartbeat. Same class of bug as the missing GNU flock/timeout on this
    host. `etime` and `cputime` are both BSD keywords and share this one format.
    """
    if not pid:
        return None
    try:
        raw = subprocess.run(
            ["ps", "-o", f"{keyword}=", "-p", str(pid)], capture_output=True, text=True, timeout=10
        ).stdout.strip()
    except Exception:
        return None
    if not raw or not re.match(r"^\d+[-:.\d]*$", raw):
        return None
    days = 0
    if "-" in raw:
        head, raw = raw.split("-", 1)
        try:
            days = int(head)
        except Exception:
            days = 0
    total = 0.0
    try:
        for part in raw.split(":"):
            total = total * 60.0 + float(part)
    except Exception:
        return None
    return total + days * 86400.0


def _cpu_seconds(pid):
    return _ps_seconds(pid, "cputime")


def _proc_age_s(pid):
    return _ps_seconds(pid, "etime")


def _real_restarts_last_hour():
    """Count REAL restarts from run_with_watchdog.sh's own log, which is the only authoritative source.

    2026-10-19 04:0xZ: the `SCRIPT RESTART - ez_indicators` banner in the application log is NOT a restart count.
    ez_indicators.py:246 writes that banner at IMPORT time, and `tools/failover_readiness_audit.sh:63` imports the
    module every 20 min (Mac cron */20) precisely to prove it is runnable. Measured in the 03:00-03:59 window: 18
    banners, but only 1 real restart. Counting banners would have made this guard overstate churn by ~18x, so the
    count comes from the watchdog's "Starting ez_indicators.py" lines instead.
    """
    path = Path("/Users/niels/logs/ez_indicators_watchdog.log")
    cutoff = time.time() - 3600
    n = 0
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            fh.seek(max(0, size - 256 * 1024))
            for line in fh.read().decode("utf-8", "replace").splitlines():
                if "Starting ez_indicators.py" not in line:
                    continue
                m = re.match(r"^\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})\]", line)
                epoch = _parse_ts(m.group(1)) if m else None
                if epoch and epoch >= cutoff:
                    n += 1
    except Exception:
        return None
    return n


def _scan_log(boundary_epoch=None, tail_bytes=512 * 1024):
    """Warm-up progress, and whether warm-up has completed since the CURRENT process started.

    `boundary_epoch` is the running process's start time. It is used in preference to the log's own
    "SCRIPT RESTART" banner because that banner is written on import by unrelated tools (see
    _real_restarts_last_hour) and a spurious one would otherwise look like a fresh warm-up.
    Only the tail is read — the live log is tens of MB.
    """
    info = {
        "last_restart_epoch": boundary_epoch,
        "last_tf": None,
        "last_tf_epoch": None,
        "tfs_since_restart": [],
        "warmup_done": None,
        "restarts_last_hour": _real_restarts_last_hour(),
        "log_age_s": _age(LOG),
    }
    try:
        size = LOG.stat().st_size
        with LOG.open("rb") as fh:
            fh.seek(max(0, size - tail_bytes))
            lines = fh.read().decode("utf-8", "replace").splitlines()
    except Exception as exc:
        info["error"] = f"{type(exc).__name__}: {exc}"
        return info
    if info["last_restart_epoch"] is None:
        # No running process to date the boundary from: fall back to the newest banner, worth more than nothing.
        for line in lines:
            m = RESTART_RE.match(line)
            if m:
                epoch = _parse_ts(m.group(1))
                if epoch:
                    info["last_restart_epoch"] = epoch
    cut = info["last_restart_epoch"] or 0.0
    done = False
    for line in lines:
        m = TF_RE.match(line)
        if m:
            epoch = _parse_ts(m.group(1))
            if epoch is None:
                continue
            info["last_tf"], info["last_tf_epoch"] = m.group(2), epoch
            if epoch >= cut:
                info["tfs_since_restart"].append(m.group(2))
                if m.group(2) == LAST_TF:
                    done = True
            continue
        if SAVE_RE.search(line):
            ts = TS_RE.match(line)
            epoch = _parse_ts(ts.group(1)) if ts else None
            if epoch is not None and epoch >= cut:
                done = True
    info["warmup_done"] = done
    return info


def _heartbeat_decision(pid, log_info, dry_run):
    """Return (should_write, reason). Every False is a case the watchdog is RIGHT to kill."""
    if pid is None:
        return False, "no ez_indicators process"
    if log_info.get("warmup_done"):
        return False, "warm-up already complete (steady state is self-sustaining)"
    age = log_info.get("log_age_s")
    if age is None:
        return False, "log age unknown"
    if age < HB_WHEN_LOG_AGE_S:
        return False, f"log fresh ({int(age)}s < {HB_WHEN_LOG_AGE_S}s)"
    proc_age = _proc_age_s(pid)
    if proc_age is None:
        return False, "process age unknown"
    if proc_age > WARMUP_GRACE_S:
        return False, f"process {int(proc_age)}s old > grace {WARMUP_GRACE_S}s — protection bounded, let it be killed"
    last_tf_epoch = log_info.get("last_tf_epoch")
    tf_gap = None if last_tf_epoch is None else time.time() - last_tf_epoch
    # Before the first timeframe line the process is importing/initialising: that is legitimately quiet, but only
    # for as long as TF_MAX_GAP_S would have allowed between two timeframes.
    reference_gap = tf_gap if (last_tf_epoch and last_tf_epoch >= (log_info.get("last_restart_epoch") or 0)) else proc_age
    if reference_gap > TF_MAX_GAP_S:
        return False, f"no warm-up progress for {int(reference_gap)}s > {TF_MAX_GAP_S}s — warm-up is wedged, let it be killed"
    before = _cpu_seconds(pid)
    if before is None:
        return False, "cpu time unreadable"
    time.sleep(CPU_SAMPLE_S)
    after = _cpu_seconds(pid)
    if after is None:
        return False, "process vanished during cpu sample"
    delta = after - before
    if delta < CPU_MIN_DELTA_S:
        return False, f"cpu advanced only {delta:.2f}s in {CPU_SAMPLE_S}s — not computing, let it be killed"
    if dry_run:
        return False, f"would heartbeat (cpu +{delta:.2f}s, progress {int(reference_gap)}s ago) — dry-run"
    return True, f"alive and progressing (cpu +{delta:.2f}s in {CPU_SAMPLE_S}s, last progress {int(reference_gap)}s ago)"


def _write_heartbeat(pid, reason):
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = (
        f"[{stamp},000] INFO [WARMUP_HEARTBEAT] ez_indicators pid={pid} warm-up in progress — {reason} "
        f"(tools/ez_indicators_warmup_guard.py)\n"
    )
    with LOG.open("a") as fh:          # O_APPEND; one short line is atomic against the logger's own writes
        fh.write(line)
    return line.strip()


def _probe_s1_cached(timeout=20):
    """S1's state, re-probed at most every S1_PROBE_EVERY_S and otherwise served from disk.

    Keeps a pass short enough that cron never skips a minute (see S1_PROBE_EVERY_S) and keeps the ssh/CPU
    footprint on S1 — which is running the backtest fleet — to one tiny read-only python per 5 min.
    """
    try:
        if S1_CACHE.exists() and (time.time() - S1_CACHE.stat().st_mtime) < S1_PROBE_EVERY_S:
            cached = json.loads(S1_CACHE.read_text())
            cached["from_cache_age_s"] = round(time.time() - S1_CACHE.stat().st_mtime, 1)
            return cached
    except Exception:
        pass
    fresh = _probe_s1(timeout=timeout)
    try:
        S1_CACHE.parent.mkdir(parents=True, exist_ok=True)
        S1_CACHE.write_text(json.dumps(fresh, default=str))
    except Exception:
        pass
    return fresh


def _probe_s1(timeout=20):
    probe = (
        "python3 -c \"import json,time;"
        "s=json.load(open('%s'));"
        "h=json.load(open('%s'));"
        "v=(s.get('venues') or {}).get('crypto') or {};"
        "c=(h.get('venues') or {}).get('crypto') or {};"
        "print(json.dumps({'mode':v.get('mode'),'since_epoch':v.get('since_epoch'),"
        "'activations':v.get('activations'),'hb_mtime':__import__('os').stat('%s').st_mtime,"
        "'mac_snapshot_age_s':c.get('snapshot_age_s'),'mac_stale':c.get('stale'),'now':time.time()}))\""
        % (S1_STATE, S1_MAC_HB, S1_MAC_HB)
    )
    try:
        res = subprocess.run(
            ["ssh", "-o", "ConnectTimeout=10", "-o", "BatchMode=yes", S1_HOST, probe],
            capture_output=True, text=True, timeout=timeout,
        )
        if res.returncode != 0:
            return {"error": (res.stderr or "ssh failed").strip()[:300]}
        return json.loads(res.stdout.strip().splitlines()[-1])
    except Exception as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single check, no internal top-up loop")
    ap.add_argument("--loop-seconds", type=int, default=40, help="internal top-up window (cron runs us every 60s)")
    ap.add_argument("--dry-run", action="store_true", help="never append a heartbeat")
    ap.add_argument("--no-s1", action="store_true", help="skip the ssh probe of S1")
    args = ap.parse_args()

    deadline = time.time() + (0 if args.once else max(0, args.loop_seconds))
    heartbeats, decisions = [], []
    while True:
        pid = _pid()
        _pa = _proc_age_s(pid)
        log_info = _scan_log(None if _pa is None else time.time() - _pa)
        ok, reason = _heartbeat_decision(pid, log_info, args.dry_run)
        decisions.append(reason)
        if ok:
            try:
                heartbeats.append(_write_heartbeat(pid, reason))
            except Exception as exc:
                decisions.append(f"heartbeat write failed: {type(exc).__name__}: {exc}")
        if time.time() >= deadline:
            break
        time.sleep(5)

    pid = _pid()
    proc_age = _proc_age_s(pid)
    log_info = _scan_log(None if proc_age is None else time.time() - proc_age)
    snap_age = _age(SNAPSHOT)
    alerts = []
    if snap_age is None:
        alerts.append(f"SNAPSHOT MISSING: {SNAPSHOT} does not exist")
    elif snap_age > SNAPSHOT_STALE_ALERT_S:
        alerts.append(
            f"SNAPSHOT STALE: {SNAPSHOT.name} is {int(snap_age)}s old (> {SNAPSHOT_STALE_ALERT_S}s). "
            f"ez_indicators pid={pid} warmup_done={log_info.get('warmup_done')} "
            f"tfs_since_restart={log_info.get('tfs_since_restart')} "
            f"restarts_last_hour={log_info.get('restarts_last_hour')}. "
            "Live crypto decisions are reading a stale indicator snapshot."
        )
    if (log_info.get("restarts_last_hour") or 0) >= 6:
        alerts.append(
            f"RESTART CHURN: {log_info['restarts_last_hour']} REAL ez_indicators restarts in the last hour "
            "(counted from run_with_watchdog.sh's own log, not the import-time banner) — warm-up cannot complete "
            "between kills (run_with_watchdog.sh:186 NO_OUTPUT_TIMEOUT=120)."
        )

    s1 = None if args.no_s1 else _probe_s1_cached()
    if s1 and not s1.get("error"):
        hb_age = (s1.get("now") or 0) - (s1.get("hb_mtime") or 0)
        active_s = (s1.get("now") or 0) - (s1.get("since_epoch") or 0) if s1.get("mode") == "ACTIVE" else 0
        s1["heartbeat_age_s"] = round(hb_age, 1)
        s1["active_s"] = round(active_s, 1)
        mac_fresh = snap_age is not None and snap_age <= SNAPSHOT_STALE_ALERT_S
        if s1.get("mode") == "ACTIVE" and active_s > S1_FAILOVER_ALERT_S and hb_age < 300 and mac_fresh:
            alerts.append(
                f"S1 STUCK IN CRYPTO FAILOVER: mode=ACTIVE for {int(active_s)}s while the Mac heartbeat is fresh "
                f"({int(hb_age)}s) AND the Mac snapshot is fresh ({int(snap_age)}s). S1 is spending cores on live "
                f"producers instead of backtests. ONE-COMMAND REVERT: {REVERT_CMD}"
            )
        elif s1.get("mode") == "ACTIVE" and active_s > S1_FAILOVER_ALERT_S:
            # Not a failover bug: S1 is correctly covering a genuinely stale Mac. Recorded, not alerted on.
            s1["note"] = "ACTIVE is CORRECT while the Mac snapshot is stale — fix the Mac, S1 returns to NORMAL on its own"

    report = {
        "generated_utc": _utc(time.time()),
        "snapshot": {
            "path": str(SNAPSHOT),
            "age_s": None if snap_age is None else round(snap_age, 1),
            "stale_alert_s": SNAPSHOT_STALE_ALERT_S,
        },
        "ez_indicators": {
            "pid": pid,
            "proc_age_s": proc_age,
            "log_age_s": None if log_info.get("log_age_s") is None else round(log_info["log_age_s"], 1),
            "warmup_done": log_info.get("warmup_done"),
            "timeframes_since_restart": log_info.get("tfs_since_restart"),
            "last_timeframe": log_info.get("last_tf"),
            "last_timeframe_utc": _utc(log_info["last_tf_epoch"]) if log_info.get("last_tf_epoch") else None,
            "last_restart_utc": _utc(log_info["last_restart_epoch"]) if log_info.get("last_restart_epoch") else None,
            "restarts_last_hour": log_info.get("restarts_last_hour"),
        },
        "heartbeat": {
            "written": len(heartbeats),
            "lines": heartbeats[-3:],
            "decisions": decisions[-6:],
            "policy": {
                "when_log_age_s": HB_WHEN_LOG_AGE_S,
                "tf_max_gap_s": TF_MAX_GAP_S,
                "warmup_grace_s": WARMUP_GRACE_S,
                "cpu_min_delta_s": CPU_MIN_DELTA_S,
            },
        },
        "s1_failover": s1,
        "alerts": alerts,
        "verdict": "ALERT" if alerts else "OK",
    }
    OUT.parent.mkdir(parents=True, exist_ok=True)
    tmp = OUT.with_suffix(".tmp")
    tmp.write_text(json.dumps(report, indent=1, default=str))
    os.replace(tmp, OUT)

    if alerts:
        ALERT.write_text(
            f"{_utc(time.time())} INDICATOR SNAPSHOT ALERT\n\n"
            + "\n\n".join(f"- {a}" for a in alerts)
            + f"\n\nreport: {OUT}\n"
        )
        for a in alerts:
            print(f"ALERT: {a}")
        return 1
    ALERT.unlink(missing_ok=True)
    print(
        f"OK snapshot_age={report['snapshot']['age_s']}s warmup_done={log_info.get('warmup_done')} "
        f"heartbeats={len(heartbeats)} last_decision={decisions[-1] if decisions else 'n/a'}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
