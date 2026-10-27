#!/usr/bin/env python3
"""FLEET DISK KEEPER — a backtest server must NEVER stop backtesting for lack of disk (USER 2026-10-27: "make sure a server
can NEVER stop backtesting even if it means it needs to clear legacy information if it can not send it anywhere else - any
idle server will be destroyed and not recovered").

Runs ON every backtest host (S1/S2/S5/S6) from its own cron (*/3, flock), so it needs neither the Mac nor any other host to be
up. When free disk is below TARGET (= the scheduler's admission floor + HEADROOM), it walks a fixed ladder of legacy classes,
oldest first, until the target is met:

  T0 tmp_scratch     /tmp regular files >= 5 MB, older than 12 h, not open                         -> delete (BIBLE: /tmp scratch
                     is "stored in vain")
  T1 tmp_xlsx        SPREADSHEETS/**/*.tmp.xlsx older than 1 h                                        -> delete (stored in vain)
  T2 received        ~/fleet_offload/* (legacy other hosts parked here), oldest first                 -> delete (it is the last copy
                     of legacy data whose owner could not keep it; a working host outranks it)
  T3 log_compress    ~/logs, ~/binance-sandbox/logs: *.log >= 50 MB, older than 2 d                   -> zstd in place (reversible)
  T4 bench_runs      ~/v15_bench/runs/* older than 2 d, not named by the champion or a pending/running candidate
  T5 old_backups     ~/binance-sandbox/backups/* and ~/binance/backups/* older than 7 d (the Mac keeps the canonical backups)
  T6 dated_legacy    home-level dirs/files whose name carries a date stamp older than 7 d, or start with scratch_/tmp_
  T7 old_rounds      ~/v15_run<N>_* older than the previous round (by number) and untouched for 14 d (outside the brain window)
  T8 old_rounds_win  the same, untouched 3..14 d (still inside the brain's delta-log window) — last resort
T4..T7 are OFFLOAD-FIRST: streamed as tar|zstd to the fleet host with the most free disk (that host must keep >= 25 GB after
receiving), sha256 of the exact bytes verified on both ends, then the source is removed. Only when no host can take it is the
source deleted in place — the user's rule: a working server outranks legacy data. Every action is a JSONL line.

NEVER touched (hard guard, checked on the resolved path of every candidate): the live NPZ stores (backtest_v8/indicators*),
klines stores, data/, SPREADSHEETS/TEMPLATE_*, code, ~/binance (the standby live tree) except its backups/, ~/v20_spills,
~/v20_union, the gate champion/candidates, conda envs, dot-dirs, the current and previous round (by number), and any path with an open handle.
Default is a dry run; --apply acts. Exit 0 = at/above target, 1 = still below target after the ladder (alert line written).
"""
import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
LOG = HOME / "logs" / "fleet_disk_keeper.jsonl"
FLOOR_MB = {"niels": 4500, "s1": 4500, "s5": 4500}  # scheduler disk_min_mb; others default to DEFAULT_FLOOR_MB
DEFAULT_FLOOR_MB = 3000
HEADROOM_MB = int(os.environ.get("FDK_HEADROOM_MB", "8000"))
DEST_RESERVE_MB = int(os.environ.get("FDK_DEST_RESERVE_MB", "25000"))
FLEET = {"niels": "10.0.0.3", "s2": "10.0.0.4", "s5": "10.0.0.5", "s6": "10.0.0.6"}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "-o", "ControlPath=none", "-o", "StrictHostKeyChecking=no", "-o", "ServerAliveInterval=15"]
OFFLOAD_DIR = "fleet_offload"
DATE_RE = re.compile(r"(20\d{2})[-_]?(\d{2})[-_]?(\d{2})")
ROUND_RE = re.compile(r"^v15_run(\d+)_")
PROTECT_PARTS = ("v20_spills", "v20_union", "v15_bench/gate", "miniforge3", "anaconda")  # the live trees (sandbox, ~/binance) are protected whole above


def now():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def log(event, **kw):
    rec = {"ts": now(), "host": HOST, "event": event, **kw}
    print(json.dumps(rec), flush=True)
    try:
        LOG.parent.mkdir(parents=True, exist_ok=True)
        with LOG.open("a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:
        pass


HOST = socket.gethostname()


def free_mb(path=HOME):
    return shutil.disk_usage(path).free // (1024 * 1024)


def size_of(p: Path) -> int:
    try:
        if p.is_symlink():
            return 0
        if p.is_file():
            return p.stat().st_size
        total = 0
        for root, _dirs, files in os.walk(p, followlinks=False):
            for f in files:
                try:
                    total += os.lstat(os.path.join(root, f)).st_size
                except OSError:
                    pass
        return total
    except OSError:
        return 0


def newest_mtime(p: Path) -> float:
    try:
        if p.is_file() or p.is_symlink():
            return p.lstat().st_mtime
        m = p.lstat().st_mtime
        for root, dirs, files in os.walk(p, followlinks=False):
            for f in files + dirs:
                try:
                    m = max(m, os.lstat(os.path.join(root, f)).st_mtime)
                except OSError:
                    pass
        return m
    except OSError:
        return time.time()


def open_paths() -> list:
    try:
        out = subprocess.run(["lsof", "-nP", "-u", str(os.getuid()), "-Fn"], capture_output=True, text=True, timeout=120).stdout
    except Exception:
        return None  # unknown = treat everything as possibly open (fail-closed)
    return [ln[1:] for ln in out.splitlines() if ln.startswith("n/")]


def is_open(p: Path, opened) -> bool:
    if opened is None:
        return True
    s = str(p)
    return any(o == s or o.startswith(s + "/") for o in opened)


def protected(p: Path, newest_rounds: set) -> str:
    try:
        rp = p.resolve()
    except Exception:
        return "unresolvable"
    s = str(rp)
    if not s.startswith(str(HOME) + "/") and not s.startswith("/tmp/"):
        return "outside_home_and_tmp"
    rel = s[len(str(HOME)) + 1:] if s.startswith(str(HOME) + "/") else s
    if s.startswith("/tmp/"):
        return ""
    top = rel.split("/", 1)[0]
    if top.startswith("."):
        return "dot_dir"
    if top == "binance" and not rel.startswith("binance/backups/"):
        return "standby_live_tree"
    if top == "binance-sandbox" and not (rel.startswith("binance-sandbox/backups/") or rel.startswith("binance-sandbox/logs/") or (rel.startswith("binance-sandbox/SPREADSHEETS/") and rel.endswith(".tmp.xlsx"))):
        return "sandbox_live_tree"
    if top in newest_rounds:
        return "current_or_previous_round"
    for part in PROTECT_PARTS:
        if part in rel and not rel.startswith(("binance-sandbox/backups/", "binance/backups/", "logs/", OFFLOAD_DIR + "/")):
            return f"protected:{part}"
    return ""


def age_days(p: Path) -> float:
    return (time.time() - newest_mtime(p)) / 86400.0


def stamp_age_days(name: str):
    m = DATE_RE.search(name)
    if not m:
        return None
    try:
        d = dt.datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=dt.timezone.utc)
    except ValueError:
        return None
    return (dt.datetime.now(dt.timezone.utc) - d).total_seconds() / 86400.0


def round_dirs():
    out = []
    for p in HOME.iterdir():
        m = ROUND_RE.match(p.name)
        if m and p.is_dir():
            out.append((int(m.group(1)), p))
    return sorted(out)


def gate_keep_names() -> set:
    keep = set()
    gate = HOME / "v15_bench" / "gate"
    try:
        champ = json.loads((gate / "champion" / "meta.json").read_text()) if (gate / "champion" / "meta.json").exists() else {}
        keep.add(str(champ.get("from") or ""))
    except Exception:
        pass
    for meta in (gate / "candidates").glob("*/meta.json") if (gate / "candidates").exists() else []:
        try:
            if json.loads(meta.read_text()).get("state") in ("pending", "running"):
                keep.add(meta.parent.name)
        except Exception:
            keep.add(meta.parent.name)
    return {k for k in keep if k}


# ------------------------------------------------------------------ candidate classes (oldest first inside each)
def c_tmp_scratch():
    out = []
    for p in Path(os.environ.get("FDK_TMP_DIR", "/tmp")).iterdir():
        try:
            st = p.lstat()
        except OSError:
            continue
        if p.is_file() and not p.is_symlink() and st.st_uid == os.getuid() and st.st_size >= 5 * 1024 * 1024 and time.time() - st.st_mtime > 12 * 3600:
            out.append(p)
    return out


def c_tmp_xlsx():
    base = HOME / "binance-sandbox" / "SPREADSHEETS"
    return [p for p in base.rglob("*.tmp.xlsx") if time.time() - p.lstat().st_mtime > 3600] if base.exists() else []


def c_received():
    d = HOME / OFFLOAD_DIR
    return sorted((p for p in d.rglob("*") if p.is_file()), key=lambda p: p.lstat().st_mtime) if d.exists() else []


def c_logs():
    out = []
    for d in (HOME / "logs", HOME / "binance-sandbox" / "logs"):
        if d.exists():
            out += [p for p in d.glob("*.log") if p.is_file() and p.stat().st_size >= 50 * 1024 * 1024 and time.time() - p.stat().st_mtime > 2 * 86400]
    return out


def c_bench_runs():
    d = HOME / "v15_bench" / "runs"
    if not d.exists():
        return []
    keep = gate_keep_names()
    return [p for p in d.iterdir() if p.is_dir() and not any(k in p.name for k in keep) and age_days(p) > 2]


def c_old_backups():
    out = []
    for d in (HOME / "binance-sandbox" / "backups", HOME / "binance" / "backups"):
        if d.exists():
            out += [p for p in d.iterdir() if not p.is_symlink() and time.time() - p.lstat().st_mtime > 7 * 86400 and age_days(p) > 7]
    return out


def c_dated_legacy():
    out = []
    for p in HOME.iterdir():
        if p.name.startswith(".") or ROUND_RE.match(p.name) or p.name in ("binance", "binance-sandbox", "logs", OFFLOAD_DIR, "v15_bench", "v20_spills", "v20_union") or p.is_symlink():
            continue
        sa = stamp_age_days(p.name)
        if (sa is not None and sa > 7 and age_days(p) > 7) or (p.name.startswith(("scratch_", "tmp_")) and age_days(p) > 7):
            out.append(p)
    return out


def protected_rounds() -> set:
    """The current and previous round (by NUMBER: max round on this host and the one before it) are never touched."""
    rds = round_dirs()
    if not rds:
        return set()
    top = rds[-1][0]
    return {p.name for n, p in rds if n >= top - 1}


def c_old_rounds():
    keep = protected_rounds()
    return [p for n, p in round_dirs() if p.name not in keep and age_days(p) > 14]  # outside the brain's 14-day delta-log window


def c_old_rounds_recent():
    keep = protected_rounds()
    return [p for n, p in round_dirs() if p.name not in keep and 3 < age_days(p) <= 14]


LADDER = [("T0_tmp_scratch", c_tmp_scratch, "delete"), ("T1_tmp_xlsx", c_tmp_xlsx, "delete"), ("T2_received_offload", c_received, "delete"),
          ("T3_log_compress", c_logs, "compress"), ("T4_bench_runs", c_bench_runs, "offload"), ("T5_old_backups", c_old_backups, "offload"),
          ("T6_dated_legacy", c_dated_legacy, "offload"), ("T7_old_rounds", c_old_rounds, "offload"),
          ("T8_old_rounds_in_brain_window", c_old_rounds_recent, "offload")]


# ------------------------------------------------------------------ actions
def remove(p: Path):
    if p.is_dir() and not p.is_symlink():
        shutil.rmtree(p)
    else:
        p.unlink()


def compress(p: Path):
    z = p.with_name(p.name + ".zst")
    if z.exists():
        raise RuntimeError(f"{z} exists")
    if subprocess.run(["nice", "-n", "19", "zstd", "-q", "-10", "-T1", str(p), "-o", str(z)], capture_output=True, timeout=3600).returncode:
        raise RuntimeError("zstd failed")
    h1 = hashlib.sha256()
    with p.open("rb") as fh:
        for b in iter(lambda: fh.read(1 << 20), b""):
            h1.update(b)
    dec = subprocess.run(["zstd", "-dc", str(z)], capture_output=True, timeout=3600)
    if dec.returncode or hashlib.sha256(dec.stdout).hexdigest() != h1.hexdigest():
        z.unlink(missing_ok=True)
        raise RuntimeError("zstd verify failed")
    p.unlink()


def remote_free(ip: str):
    try:
        r = subprocess.run(SSH + [f"niels@{ip}", "df -Pm ~ | tail -1"], capture_output=True, text=True, timeout=30)
        return int(r.stdout.split()[3]) if r.returncode == 0 else None
    except Exception:
        return None


def pick_dest(nbytes: int):
    best = None
    for name, ip in FLEET.items():
        if name == HOST or ip == FLEET.get(HOST):
            continue
        f = remote_free(ip)
        if f is None:
            continue
        if f - nbytes // (1024 * 1024) >= DEST_RESERVE_MB and (best is None or f > best[2]):
            best = (name, ip, f)
    return best


def offload(paths: list, dest) -> dict:
    """One tar|zstd stream of `paths` (relative to HOME) to dest:~/fleet_offload/<host>/<first>_<n>_<stamp>.tar.zst; the sha256 of
    the exact bytes sent must equal the receiver's sha256 and `zstd -t` must pass, else the part file is removed and we raise."""
    name, ip, _f = dest
    rels = [str(p.resolve()).replace(str(HOME) + "/", "", 1) for p in paths]
    label = rels[0].replace("/", "__")[:120]
    obj = f"{OFFLOAD_DIR}/{HOST}/{label}_{len(rels)}items_{dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ')}.tar.zst"
    part = obj + ".part"
    subprocess.run(SSH + [f"niels@{ip}", f"mkdir -p ~/{OFFLOAD_DIR}/{HOST}"], check=True, timeout=30)
    tar = subprocess.Popen(["nice", "-n", "19", "tar", "-C", str(HOME), "-cf", "-", "--"] + rels, stdout=subprocess.PIPE)
    zst = subprocess.Popen(["nice", "-n", "19", "zstd", "-q", "-3", "-T2", "-c"], stdin=tar.stdout, stdout=subprocess.PIPE)
    tar.stdout.close()
    rcv = subprocess.Popen(SSH + [f"niels@{ip}", f"cat > ~/{part}"], stdin=subprocess.PIPE)
    h = hashlib.sha256()
    for b in iter(lambda: zst.stdout.read(1 << 20), b""):
        h.update(b)
        rcv.stdin.write(b)
    rcv.stdin.close()
    if rcv.wait(timeout=7200) or zst.wait(timeout=60) or tar.wait(timeout=60):
        subprocess.run(SSH + [f"niels@{ip}", f"rm -f ~/{part}"], timeout=60)
        raise RuntimeError("stream failed")
    chk = subprocess.run(SSH + [f"niels@{ip}", f"zstd -tq ~/{part} && sha256sum ~/{part}"], capture_output=True, text=True, timeout=3600)
    if chk.returncode or not chk.stdout or chk.stdout.split()[0] != h.hexdigest():
        subprocess.run(SSH + [f"niels@{ip}", f"rm -f ~/{part}"], timeout=60)
        raise RuntimeError("remote sha256 mismatch")
    subprocess.run(SSH + [f"niels@{ip}", f"mv ~/{part} ~/{obj}"], check=True, timeout=60)
    return {"dest": f"{name}:~/{obj}", "sha256": h.hexdigest(), "items": len(rels)}


BATCH_BYTES = int(os.environ.get("FDK_BATCH_MB", "3000")) * 1024 * 1024
MIN_OFFLOAD_BYTES = 1024 * 1024  # smaller legacy files free nothing worth the risk (crontab backups, scripts) and stay


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--target-mb", type=int, default=None)
    a = ap.parse_args(argv)
    floor = FLOOR_MB.get(HOST, DEFAULT_FLOOR_MB)
    target = a.target_mb or floor + HEADROOM_MB
    start = free_mb()
    if start >= target:
        return 0
    log("below_target", free_mb=start, floor_mb=floor, target_mb=target, apply=a.apply)
    newest = protected_rounds()
    opened = open_paths()
    freed_plan = 0
    for tier, fn, action in LADDER:
        try:
            cands = fn()
        except Exception as e:
            log("tier_error", tier=tier, error=f"{type(e).__name__}: {e}"[:300])
            continue
        cands.sort(key=lambda p: newest_mtime(p))
        batch, batch_bytes = [], 0
        for i, p in enumerate(cands + [None]):
            cur = free_mb() if a.apply else start + freed_plan // (1024 * 1024)
            if p is not None:
                if cur + batch_bytes // (1024 * 1024) >= target and not batch:
                    log("target_met", free_mb=cur, target_mb=target)
                    return 0
                why = protected(p, newest)
                if why:
                    log("skip_protected", tier=tier, path=str(p), why=why)
                    continue
                if is_open(p, opened):
                    log("skip_open", tier=tier, path=str(p))
                    continue
                nbytes = size_of(p)
                if nbytes <= 0 or (action == "offload" and nbytes < MIN_OFFLOAD_BYTES):
                    continue
                if action != "offload":
                    if not a.apply:
                        log("plan", tier=tier, action=action, path=str(p), mb=round(nbytes / 1048576, 1))
                        freed_plan += nbytes
                        continue
                    try:
                        if action == "delete":
                            remove(p)
                            log("deleted", tier=tier, path=str(p), mb=round(nbytes / 1048576, 1), reason="stored in vain / received legacy")
                        else:
                            compress(p)
                            log("compressed", tier=tier, path=str(p), mb=round(nbytes / 1048576, 1))
                    except Exception as e:
                        log("action_failed", tier=tier, path=str(p), error=f"{type(e).__name__}: {e}"[:300])
                    continue
                batch.append(p)
                batch_bytes += nbytes
                if batch_bytes < BATCH_BYTES and cur + batch_bytes // (1024 * 1024) < target:
                    continue
            if not batch:
                continue
            mb = round(batch_bytes / 1048576, 1)
            if not a.apply:
                log("plan", tier=tier, action="offload", items=len(batch), mb=mb, first=str(batch[0]), last=str(batch[-1]))
                freed_plan += batch_bytes
            else:
                dest = pick_dest(batch_bytes)
                done = False
                if dest is not None:
                    try:
                        rec = offload(batch, dest)
                        for q in batch:
                            remove(q)
                        log("offloaded", tier=tier, mb=mb, first=str(batch[0]), **rec)
                        done = True
                    except Exception as e:
                        log("offload_failed", tier=tier, items=len(batch), error=f"{type(e).__name__}: {e}"[:300])
                        if free_mb() >= floor:  # transient failure above the hard floor: keep it, the next cycle (3 min) retries
                            log("retained_retry", tier=tier, items=len(batch), mb=mb, free_mb=free_mb(), floor_mb=floor)
                            batch, batch_bytes = [], 0
                            continue
                else:
                    log("no_destination", tier=tier, items=len(batch), mb=mb, reserve_mb=DEST_RESERVE_MB)
                if not done:
                    for q in batch:
                        try:
                            remove(q)
                        except Exception as e:
                            log("action_failed", tier=tier, path=str(q), error=f"{type(e).__name__}: {e}"[:300])
                    log("deleted_no_destination", tier=tier, items=len(batch), mb=mb, first=str(batch[0]), reason="no fleet host could take it; a working server outranks legacy data (USER 2026-10-27)")
            batch, batch_bytes = [], 0
    end = free_mb() if a.apply else start + freed_plan // (1024 * 1024)
    if end >= target:
        log("target_met", free_mb=end, target_mb=target)
        return 0
    log("ALERT_STILL_BELOW_TARGET", free_mb=end, floor_mb=floor, target_mb=target, below_floor=end < floor, note="ladder exhausted; next legacy class needs an operator decision")
    return 1


if __name__ == "__main__":
    sys.exit(main())
