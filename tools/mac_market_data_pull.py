#!/usr/bin/env python3
"""mac_market_data_pull.py - the RETURN half of the market-data contract, for every file the Mac's live engines read.

USER 2026-10-22: "no market_data file ... so it never has a >90s market data file NO MATTER WHAT THE FUCK HAPPENS."
Measured 2026-10-22: the Mac's ez_indicators sat in a 100-minute warm-up, data/market_data_20261022_004023.json (00:40Z)
was the newest snapshot until 04:48Z and every live engine traded on it for 4 h.

WHAT THE LIVE ENGINES ACTUALLY OPEN (read from the code, 2026-10-22 - not guessed):
  * ez_manage._get_latest_market_data_file(): the NEWEST-BY-MTIME data/market_data_*.json (non-"worker"; `_s1.json` files
    count). latest_market_data.json only when NO timestamped file exists. This feeds ii() - the indicator lifeline.
  * ez_manage cold loaders (_cold_data_loop, _loader_loop) and ez_positions_service._cold_data_loop /
    _refresh_from_source: the HIGHEST-NAME data/market_data_*.json.
  * ez_positions_service._enforce_market_data_sync / _aggressive_refresh: config.LATEST_MARKET_DATA_FILE =
    data/latest_market_data.json (rejects it when older than MAX_FILE_AGE_SECONDS).
So BOTH families matter. The old puller fetched S1's ~/binance/data/latest_market_data.json, which is NOT what S1's
producer writes: the S1 stack (ez_s1_stack) writes ~/binance/ez_s1_stack/data/latest_market_data.json, and
~/binance/data/latest_market_data.json on S1 was 00:49Z (4 h old) while the stack file was 17 s old. It pulled nothing.

WHAT THIS DOES, every 5 s for ~55 s per cron minute:
  1. Mac producer health = newest mtime of the Mac's OWN files (latest_market_data.json + non-_s1 market_data_*.json).
  2. If the Mac's own producer wrote within MAX_AGE_S (90): nothing to do (content lag is only reported).
  3. Otherwise probe every fallback source IN PARALLEL (S1 stack, S2 stack; each over its direct then gateway route) and
     take the strictly freshest. A candidate is installed ONLY when it is strictly newer than the newest snapshot the
     engines can see, parses as a non-empty JSON dict, and its CONTENT (median 3m/15m bar age, share of 15m bars
     older than 30 min) is not worse than the Mac's newest by more than the tolerance (the same relative gate as
     scripts/s1_snapshot_fresh_gate.py - a fresh mtime proves nothing about fresh bars).
  4. Install = data/market_data_<UTC write time>_s1.json (the `_s1.json` suffix is what live_failover_common and
     mac_market_data_push_s1 already EXCLUDE from "the Mac produces", so a fallback can never make a dead Mac look
     healthy to the failover agent) AND data/latest_market_data.json (the positions-service reader). The installed
     file's mtime is the SOURCE's real write time (skew-corrected), never "now": the age stays honest.
  5. Never installs an older file over a newer one: every compare is explicit, the mtime test is repeated immediately
     before the rename, and nothing is ever rsynced straight into place (data_never_miss 10-21: a sync without -u once
     served 26-day-old prices).
  6. ALERT: if after all that no snapshot the engines can see is <= MAX_AGE_S old -> a line in
     data/alerts/market_data_stale.jsonl, the flag file data/alerts/MARKET_DATA_STALE.flag, the log, and a macOS
     notification (rate-limited). data/market_data_freshness.json is rewritten every round with every source's age.
Env: MD_BASE, MAC_MD_MAX_AGE_S, MAC_MD_SOURCES ("name=host1,host2=remote/path;..."), MAC_MD_ROUNDS, MAC_MD_SLEEP_S,
MAC_MD_DRY=1 (probe + decide, install nothing).
"""
import concurrent.futures as cf
import datetime as dt
import json
import os
import subprocess
import sys
import time
from pathlib import Path

try:
    import orjson
except ImportError:
    orjson = None

BASE = Path(os.environ.get("MD_BASE", "/Users/niels/Documents/binance"))
DATA = BASE / "data"
ALERT_DIR = DATA / "alerts"
ALERT = ALERT_DIR / "market_data_stale.jsonl"
FLAG = ALERT_DIR / "MARKET_DATA_STALE.flag"
STATUS = DATA / "market_data_freshness.json"
LOG = Path(os.environ.get("MD_LOG", "/Users/niels/logs/mac_market_data_pull_s1.log"))
MAX_AGE = float(os.environ.get("MAC_MD_MAX_AGE_S", "90"))
ROUNDS = int(os.environ.get("MAC_MD_ROUNDS", "11"))
SLEEP_S = float(os.environ.get("MAC_MD_SLEEP_S", "5"))
DEADLINE_S = float(os.environ.get("MAC_MD_DEADLINE_S", "58"))   # never run into the next cron minute even if ssh hangs
DRY = os.environ.get("MAC_MD_DRY") == "1"
MARGIN_S = 3.0           # a candidate must beat the engines' newest file by this much (jitter between probes)
TOL_S = float(os.environ.get("MAC_MD_TOL_S", "120"))           # content tolerance vs the Mac's newest snapshot (median bar age, seconds)
TOL_FRAC = 0.10          # tolerance on the share of 15m bars older than 30 min
LAG_S = float(os.environ.get("MAC_MD_LAG_S", "600"))          # Mac snapshot whose median 3m bar is older than this is "mtime-fresh, content-stale"
BETTER_S = float(os.environ.get("MAC_MD_BETTER_S", "300"))    # a fallback must beat the Mac's median 3m bar age by this much to override a fresh-mtime file
WRITE_LATEST = os.environ.get("MAC_MD_WRITE_LATEST", "1") == "1"
LATEST_MINE = {"mtime": None}   # mtime of the latest_market_data.json THIS script installed (so it never counts as the Mac producer writing)
OVERRIDE = os.environ.get("MAC_MD_CONTENT_OVERRIDE", "1") == "1"
ALERT_EVERY_S = 30.0
NOTIFY_EVERY_S = 600.0
SSH_OPTS = ["-o", "BatchMode=yes", "-o", "ConnectTimeout=5", "-o", "StrictHostKeyChecking=accept-new",
            "-o", "ControlMaster=no", "-o", "ControlPath=none", "-o", "ServerAliveInterval=5", "-o", "ServerAliveCountMax=2"]
DEFAULT_SOURCES = ("s1=s1-pub,s1-int=binance/ez_s1_stack/data/latest_market_data.json;"
                   "s2=s2,s2-pub=binance/ez_s2_stack/data/latest_market_data.json")


def parse_sources(spec):
    out = []
    for item in spec.split(";"):
        item = item.strip()
        if not item:
            continue
        name, hosts, path = item.split("=", 2)
        out.append({"name": name, "hosts": [h for h in hosts.split(",") if h], "path": path})
    return out


SOURCES = parse_sources(os.environ.get("MAC_MD_SOURCES", DEFAULT_SOURCES))


def log(msg):
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a") as handle:
            handle.write(f"[{dt.datetime.now(dt.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')}] {msg}\n")
    except OSError:
        pass


def jloads(raw):
    return orjson.loads(raw) if orjson else json.loads(raw)


def atomic_json(path, obj):
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(tmp, "w") as handle:
        json.dump(obj, handle, default=str)
    os.replace(tmp, path)


def engine_visible_files(mine_latest=None):
    """(own, every, stamped): [(mtime, path)].
    own     = what the Mac's producer wrote (timestamped non-_s1 files + latest_market_data.json unless WE wrote that one),
    every   = everything the engines may open,
    stamped = the timestamped files only (ez_manage picks the newest mtime among these)."""
    own, every, stamped = [], [], []
    try:
        with os.scandir(DATA) as entries:
            for entry in entries:
                name = entry.name
                if not name.endswith(".json") or "worker" in name:
                    continue
                is_latest = name == "latest_market_data.json"
                if not is_latest and not name.startswith("market_data_2"):
                    continue
                try:
                    mtime = entry.stat().st_mtime
                except OSError:
                    continue
                every.append((mtime, entry.path))
                if not is_latest:
                    stamped.append((mtime, entry.path))
                if name.endswith("_s1.json"):
                    continue
                if is_latest and mine_latest is not None and abs(mtime - mine_latest) < 0.05:
                    continue
                own.append((mtime, entry.path))
    except OSError:
        pass
    return own, every, stamped


def iso_age(value, now):
    try:
        return now - dt.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


def content_stats(data, now, symbols=None):
    """Median 3m / 15m bar age + share of 15m bars older than 30 min (same quantities as s1_snapshot_fresh_gate).
    `symbols`: restrict to this set - S1/S2 carry ~330 symbols (dead ones included) against the Mac's ~200 tradeable ones, and
    comparing across different universes made a healthy S1 look 35 % stale."""
    ages = {"3m": [], "15m": []}
    for symbol, values in data.items():
        if symbols is not None and symbol not in symbols:
            continue
        if isinstance(values, dict):
            for tf in ages:
                age = iso_age(values.get("timestamp_" + tf), now)
                if age is not None:
                    ages[tf].append(age)
    def med(xs):
        xs = sorted(xs)
        return xs[len(xs) // 2] if xs else None
    n15 = len(ages["15m"])
    return {"m3": med(ages["3m"]), "m15": med(ages["15m"]),
            "stale15_frac": (sum(1 for a in ages["15m"] if a > 1800) / n15) if n15 else None, "n": (len(ages["3m"]) if symbols is not None else len(data))}


_content_cache = {}


def mac_content(path, mtime, now):
    key = (path, mtime, int(now // 15))
    if key in _content_cache:
        return _content_cache[key]
    try:
        with open(path, "rb") as handle:
            data = jloads(handle.read())
        stats = content_stats(data, now)
        stats["symbols"] = {k for k, v in data.items() if isinstance(v, dict)}
    except (OSError, ValueError):
        stats = None
    _content_cache.clear()
    _content_cache[key] = stats
    return stats


def probe(src):
    """Remote write age of the source file, measured on the REMOTE clock (immune to Mac/remote skew)."""
    cmd = f'f="$HOME/{src["path"]}"; [ -f "$f" ] && echo "$(stat -c %Y "$f") $(stat -c %s "$f") $(date +%s)" || echo MISSING'
    errors = []
    for host in src["hosts"]:
        t0 = time.time()
        try:
            res = subprocess.run(["ssh", *SSH_OPTS, host, cmd], capture_output=True, text=True, timeout=14)
        except subprocess.TimeoutExpired:
            errors.append(f"{host}:ssh_timeout")
            continue
        out = res.stdout.strip().splitlines()[-1:] or [""]
        if res.returncode != 0 or not out[0]:
            errors.append(f"{host}:rc{res.returncode}")
            continue
        if out[0] == "MISSING":
            errors.append(f"{host}:file_missing")
            continue
        try:
            mt, size, rnow = (int(x) for x in out[0].split())
        except ValueError:
            errors.append(f"{host}:bad_reply")
            continue
        local_now = (t0 + time.time()) / 2.0
        return {"name": src["name"], "host": host, "path": src["path"], "age": float(rnow - mt), "size": size,
                "local_mtime": local_now - float(rnow - mt), "errors": errors}
    return {"name": src["name"], "host": None, "path": src["path"], "age": None, "size": None, "local_mtime": None, "errors": errors}


def pull(cand):
    tmp = DATA / f".md_pull.{os.getpid()}.{cand['name']}.tmp"
    cmd = ["rsync", "-a", "--timeout=20", "-e", "ssh " + " ".join(SSH_OPTS), f"{cand['host']}:{cand['path'].replace('binance/', '~/binance/', 1)}", str(tmp)]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=50)
    except subprocess.TimeoutExpired:
        tmp.unlink(missing_ok=True)
        return None, "rsync_timeout"
    if res.returncode != 0 or not tmp.exists() or tmp.stat().st_size == 0:
        tmp.unlink(missing_ok=True)
        return None, f"rsync_rc{res.returncode}:{res.stderr.strip()[:80]}"
    return tmp, None


def gate(cand_stats, mac_stats):
    if cand_stats["n"] < 100 or cand_stats["m3"] is None or cand_stats["m15"] is None:
        return False, f"too_few_symbols_or_no_bar_timestamps n={cand_stats['n']}"
    if mac_stats is None or mac_stats["m3"] is None or mac_stats["m15"] is None:
        ok = cand_stats["m3"] <= 900.0
        return ok, f"abs m3={int(cand_stats['m3'])}s (no usable Mac reference)"
    checks = {
        "m3": cand_stats["m3"] <= mac_stats["m3"] + TOL_S,
        "m15": cand_stats["m15"] <= mac_stats["m15"] + TOL_S,
        "stale15": (cand_stats["stale15_frac"] or 0.0) <= (mac_stats["stale15_frac"] or 0.0) + TOL_FRAC,
    }
    txt = (f"cand(m3={int(cand_stats['m3'])}s m15={int(cand_stats['m15'])}s stale15={cand_stats['stale15_frac']:.2f}) "
           f"mac(m3={int(mac_stats['m3'])}s m15={int(mac_stats['m15'])}s stale15={(mac_stats['stale15_frac'] or 0):.2f})")
    failed = [k for k, v in checks.items() if not v]
    return (not failed), f"{txt} failed={','.join(failed) or 'none'}"


def install(tmp, cand, override_floor=None):
    """Atomically publish tmp as market_data_<write time>_s1.json + latest_market_data.json, mtime = the real write time.
    override_floor (content override only): the newest mtime of the Mac's own stale-content file; the installed mtime/label is
    max(real write time, floor + 1 s) capped at now, because ez_manage picks the newest MTIME and the cold loaders the highest NAME."""
    mt = min(cand["local_mtime"], time.time())
    if override_floor is not None:
        mt = min(max(mt, override_floor + 1.0), time.time())
    label = time.strftime("%Y%m%d_%H%M%S", time.gmtime(mt))
    target = DATA / f"market_data_{label}_s1.json"
    latest = DATA / "latest_market_data.json"
    # last look: the Mac's own producer may have written while we transferred - never replace newer with older.
    _, every, _st = engine_visible_files()
    newest = max((m for m, _ in every), default=0.0)
    if override_floor is None and newest >= mt - 1.0 and target.exists():
        tmp.unlink(missing_ok=True)
        return False, "already_installed_or_newer_now"
    if override_floor is None and newest > mt + MARGIN_S:
        tmp.unlink(missing_ok=True)
        return False, f"a newer file appeared during transfer ({int(newest - mt)}s newer)"
    os.utime(tmp, (mt, mt))
    twin = DATA / f".md_pull.{os.getpid()}.latest.tmp"
    with open(tmp, "rb") as src, open(twin, "wb") as dst:
        while True:
            chunk = src.read(1 << 20)
            if not chunk:
                break
            dst.write(chunk)
        dst.flush()
        os.fsync(dst.fileno())
    os.utime(twin, (mt, mt))
    os.replace(tmp, target)
    try:
        cur = latest.stat().st_mtime
    except OSError:
        cur = 0.0
    wrote_latest = WRITE_LATEST and cur <= mt
    if wrote_latest:
        os.replace(twin, latest)
        LATEST_MINE["mtime"] = mt
    else:
        twin.unlink(missing_ok=True)
    mine = sorted(DATA.glob("market_data_2*_s1.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    for old in mine[8:]:
        old.unlink(missing_ok=True)
    return True, target.name


def notify(text):
    try:
        subprocess.run(["osascript", "-e", f'display notification "{text}" with title "MARKET DATA STALE"'],
                       capture_output=True, timeout=5)
    except (OSError, subprocess.TimeoutExpired):
        pass


class Run:
    def __init__(self):
        try:
            self.state = json.load(open(STATUS))
        except (OSError, ValueError):
            self.state = {}
        if self.state.get("latest_mine_mtime"):
            LATEST_MINE["mtime"] = float(self.state["latest_mine_mtime"])
        self.last_alert = float(self.state.get("last_alert_ts", 0))
        self.last_notify = float(self.state.get("last_notify_ts", 0))
        self.last_hb = 0.0
        self.last_content_check = 0.0
        self.last_mac_stats = None
        self.last_lag_log = 0.0
        self.last_override = None
        self.last_override_reject = 0.0
        self.last_override_log = 0.0

    def snapshot_view(self):
        own, every, stamped = engine_visible_files(LATEST_MINE["mtime"])
        own_m = max((m for m, _ in own), default=0.0)
        best_m, best_path = max(every, default=(0.0, ""))
        st_m, st_path = max(stamped, default=(0.0, ""))
        return {"own_m": own_m, "best_m": best_m, "best_path": best_path, "stamped_m": st_m, "stamped_path": st_path}

    def visible_age(self, now):
        return now - self.snapshot_view()["best_m"]

    def one_round(self):
        now = time.time()
        view = self.snapshot_view()
        own_m, best_m, best_path = view["own_m"], view["best_m"], view["best_path"]
        own_age, best_age = now - own_m, now - best_m
        with cf.ThreadPoolExecutor(max_workers=max(1, len(SOURCES))) as pool:
            probes = list(pool.map(probe, SOURCES))
        status = {"ts": now, "max_age_s": MAX_AGE, "mac_own_age_s": round(own_age, 1), "mac_visible_age_s": round(best_age, 1),
                  "mac_visible_file": os.path.basename(best_path), "sources": {}, "action": "none"}
        for p in probes:
            status["sources"][p["name"]] = {"host": p["host"], "age_s": p["age"], "size": p["size"], "errors": p["errors"]}
        if own_age <= MAX_AGE:
            status["action"] = "mac_producer_fresh"
            picked = view["stamped_path"]
            engines_on_own = bool(picked) and not picked.endswith("_s1.json")
            stats = mac_content(picked, view["stamped_m"], now) if engines_on_own else None
            status["mac_content"] = {k: v for k, v in stats.items() if k != "symbols"} if stats else None
            lagging = bool(stats) and stats["m3"] is not None and stats["m3"] > LAG_S
            if lagging and now - self.last_lag_log > 60:
                self.last_lag_log = now
                log(f"CONTENT_LAG: Mac snapshot {os.path.basename(picked)} written {int(now - view['stamped_m'])}s ago but its median 3m bar is {int(stats['m3'])}s old, 15m {int(stats['m15'] or 0)}s (producer warming up or starved)")
                self.alert("market_data_content_lag", own_age, best_age, status, f"Mac snapshot written {int(now - view['stamped_m'])}s ago but median 3m bar is {int(stats['m3'])}s old")
            if lagging and OVERRIDE and not DRY:
                self.content_override(probes, stats, picked, now, status)
            self.finish(status, self.visible_age(now), now)
            return
        # ---- Mac producer silent > MAX_AGE: fallback mode ----
        cands = [p for p in probes if p["age"] is not None and p["local_mtime"] > best_m + MARGIN_S]
        cands.sort(key=lambda p: p["age"])
        status["action"] = "fallback_no_fresher_source" if not cands else "fallback_try"
        mac_stats = None
        for cand in cands:
            if DRY:
                status["action"] = f"DRY would pull {cand['name']}@{cand['host']} age={cand['age']:.0f}s"
                break
            tmp, err = pull(cand)
            if tmp is None:
                status["sources"][cand["name"]]["pull_error"] = err
                log(f"PULL_FAIL {cand['name']}@{cand['host']}: {err}")
                continue
            if mac_stats is None:
                mac_stats = mac_content(view["stamped_path"] or best_path, view["stamped_m"] or best_m, now)
            cstats, why = self.validate(tmp, cand, now, mac_stats["symbols"] if mac_stats else None)
            if cstats is None:
                status["sources"][cand["name"]]["pull_error"] = why
                continue
            ok, why = gate(cstats, mac_stats)
            status["sources"][cand["name"]]["gate"] = why
            if not ok:
                tmp.unlink(missing_ok=True)
                log(f"GATE_REJECT {cand['name']}@{cand['host']} (file {int(cand['age'])}s old): {why}")
                continue
            done, what = install(tmp, cand)
            if done:
                status["action"] = f"installed {what} from {cand['name']}@{cand['host']} (source age {int(cand['age'])}s, mac visible was {int(best_age)}s) {why}"
                log(f"INSTALLED {what} from {cand['name']}@{cand['host']}: source write age {int(cand['age'])}s, Mac visible age was {int(best_age)}s, Mac own {int(own_age)}s | {why}")
                break
            log(f"SKIP install {cand['name']}: {what}")
        self.finish(status, self.visible_age(now), now)

    def validate(self, tmp, cand, now, symbols=None):
        try:
            with open(tmp, "rb") as handle:
                data = jloads(handle.read())
            if not isinstance(data, dict) or not data:
                raise ValueError("not a non-empty dict")
            return content_stats(data, now, symbols), ""
        except (OSError, ValueError) as exc:
            tmp.unlink(missing_ok=True)
            log(f"REJECTED {cand['name']}@{cand['host']}: not valid non-empty JSON ({exc})")
            return None, f"invalid_json:{exc}"

    def content_override(self, probes, mac_stats, picked, now, status):
        """The Mac wrote a snapshot just now (mtime says fresh) but its bars are old. Make the engines see a fallback snapshot whose
        bars are materially newer. ez_manage takes the newest MTIME and the cold loaders the highest NAME, so the installed file
        takes max(real write time, the Mac file's mtime + 1 s), capped at now - never in the future, never older than its source."""
        fresh = sorted((p for p in probes if p["age"] is not None and p["age"] <= MAX_AGE), key=lambda p: p["age"])
        for cand in fresh:
            reuse = self.last_override
            if reuse and reuse["name"] == cand["name"] and abs(reuse["remote_mtime"] - cand["local_mtime"]) <= 3.0 and Path(reuse["path"]).exists():
                tmp = DATA / f".md_pull.{os.getpid()}.reuse.tmp"
                with open(reuse["path"], "rb") as src, open(tmp, "wb") as dst:
                    while True:
                        chunk = src.read(1 << 20)
                        if not chunk:
                            break
                        dst.write(chunk)
                cstats, why = reuse["stats"], "re-bump of the already-gated file"
            else:
                tmp, err = pull(cand)
                if tmp is None:
                    status["sources"][cand["name"]]["pull_error"] = err
                    log(f"OVERRIDE pull failed {cand['name']}@{cand['host']}: {err}")
                    continue
                cstats, why = self.validate(tmp, cand, now, mac_stats["symbols"])
                if cstats is None:
                    continue
                ok, why = gate(cstats, mac_stats)
                better = cstats["m3"] is not None and cstats["m3"] + BETTER_S < mac_stats["m3"]
                status["sources"][cand["name"]]["gate"] = why + f" better_by={int(mac_stats['m3'] - (cstats['m3'] or 0))}s"
                if not (ok and better):
                    tmp.unlink(missing_ok=True)
                    if now - self.last_override_reject > 60:
                        self.last_override_reject = now
                        log(f"OVERRIDE_REJECT {cand['name']}@{cand['host']}: {why} (needs gate ok AND 3m median better by >{int(BETTER_S)}s) ok={ok} better={better}")
                    continue
            own, _every, _st = engine_visible_files(LATEST_MINE["mtime"])
            floor = max((m for m, _ in own), default=now)
            done, what = install(tmp, cand, override_floor=floor)
            if done:
                self.last_override = {"name": cand["name"], "remote_mtime": cand["local_mtime"], "path": str(DATA / what), "stats": cstats}
                status["action"] = f"content_override installed {what} from {cand['name']}@{cand['host']}"
                if now - self.last_override_log > 30:
                    self.last_override_log = now
                    log(f"CONTENT_OVERRIDE {what} from {cand['name']}@{cand['host']} (source write age {int(cand['age'])}s): {why} | Mac file {os.path.basename(picked)} had median 3m bar {int(mac_stats['m3'])}s old")
                return

    def alert(self, kind, own_age, best_age, status, msg):
        now = time.time()
        if now - self.last_alert < ALERT_EVERY_S and kind == "market_data_stale":
            return
        if kind == "market_data_content_lag" and now - float(self.state.get("last_lag_alert_ts", 0)) < 300:
            return
        ALERT_DIR.mkdir(parents=True, exist_ok=True)
        row = {"ts": int(now), "kind": kind, "mac_own_age_s": int(own_age), "mac_visible_age_s": int(best_age), "max_age_s": int(MAX_AGE),
               "sources": status.get("sources"), "msg": msg}
        with open(ALERT, "a") as handle:
            handle.write(json.dumps(row, default=str) + "\n")
        if kind == "market_data_stale":
            self.last_alert = now
        else:
            self.state["last_lag_alert_ts"] = now

    def finish(self, status, visible_age, now):
        visible_age = max(0.0, visible_age)
        status["mac_visible_age_after_s"] = round(visible_age, 1)
        stale = visible_age > MAX_AGE
        status["stale"] = stale
        if stale:
            srcs = {k: (v["age_s"] if v["age_s"] is not None else "unreachable:" + ",".join(v["errors"])) for k, v in status["sources"].items()}
            msg = (f"NO snapshot the engines can read is <= {int(MAX_AGE)}s old: newest {int(visible_age)}s, Mac producer silent {int(status['mac_own_age_s'])}s, "
                   f"fallback source write ages {srcs}")
            self.alert("market_data_stale", status["mac_own_age_s"], visible_age, status, msg)
            try:
                ALERT_DIR.mkdir(parents=True, exist_ok=True)
                atomic_json(FLAG, {"ts": now, "msg": msg})
            except OSError:
                pass
            if now - self.last_notify > NOTIFY_EVERY_S:
                self.last_notify = now
                notify(f"No crypto snapshot younger than {int(MAX_AGE)}s (newest {int(visible_age)}s)")
            if now - self.last_hb > 30:
                self.last_hb = now
                log("ALERT: " + msg)
        else:
            try:
                FLAG.unlink()
            except OSError:
                pass
            if now - self.last_hb > 60:
                self.last_hb = now
                log(f"ok: engines see a {int(visible_age)}s old snapshot (Mac own {int(status['mac_own_age_s'])}s) action={status['action'][:120]}")
        self.state.update(status)
        self.state["latest_mine_mtime"] = LATEST_MINE["mtime"]
        self.state["last_alert_ts"] = self.last_alert
        self.state["last_notify_ts"] = self.last_notify
        try:
            atomic_json(STATUS, self.state)
        except OSError:
            pass


def main():
    run = Run()
    t_start = time.time()
    for index in range(ROUNDS):
        if index and time.time() - t_start > DEADLINE_S:
            break
        started = time.time()
        try:
            run.one_round()
        except Exception as exc:  # a puller that dies silently is the failure this exists to prevent
            log(f"ROUND_ERROR {type(exc).__name__}: {exc}")
        if index < ROUNDS - 1:
            time.sleep(max(0.0, SLEEP_S - (time.time() - started)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
