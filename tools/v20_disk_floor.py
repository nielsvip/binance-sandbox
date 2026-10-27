"""v20_disk_floor — hold a FREE-SPACE FLOOR on every fleet host, so compute can never be slowed by a full disk.

USER 2026-10-27: "you need a structural change for this ... we need >40gb always on each server".

The nightly absorb cycle compresses yesterday's corpus. That is not a floor: it frees what happens to be eligible,
not what the host needs. This tool starts from the requirement -- FLOOR_GB free on each host -- and walks an ordered
ladder of rungs until the host meets it, or reports exactly what is blocking and by how many GB.

THE LADDER (cheapest and most reversible first; a rung runs only if the host is still under the floor)
  R1 corpus   delegate to tools/v20_absorb_cycle.py --phase inventory,compress,report --apply.
              Since 2026-10-27 the brains read .zst (tools/zreader.py), so a finished round compresses the day it
              finishes instead of 21 days later, and the compressed log is still a training input.
  R2 shelve   per-file zstd of a classified-dead directory, IN PLACE, names kept (x.jsonl -> x.jsonl.zst).
              No network, needs only one file's worth of scratch, and every byte stays on the box. This is the rung
              that actually moves tens of GB on a host at 100%.
  R3 offload  tar.zst -> cold store (tools/gcs_cold_store.py, worker uploads direct, md5-verified), then unlink.
              The second copy and the full reclaim. Slow (measured 3-4 MB/s at nice 19), so it runs last.
  R4 alert    still under the floor: write the ranked shortfall with a reason per candidate. Never a silent failure,
              never a guess about what could have been deleted.

WHAT IT WILL NOT TOUCH, EVER
  * the live trees (~/binance, ~/binance-sandbox) except their backups/ shelf -- R2/R3 take top-level work dirs only
  * data/pure_board (live trading reads it: tradier_manage.py fresh_verdicts) and data/eval_cache_incoming (the
    merge STAGE whose master lives on TOSHIBA_EXT) -- both were nearly reclaimed on 2026-10-26 and are on NEVER
  * the current or previous round, fleet-wide (tools/v20_absorb_cycle.protected_rounds)
  * anything with an open file handle (lsof), anything touched inside --stale-days
  * a corpus, by deletion: R2 and R3 compress and copy. Deleting a corpus needs BACKTEST_BIBLE.md PURGE GATE
    tests 2 and 3, which are not built. The 29.7 GB eval_cache_v1.db was lost by treating a corpus as a cache.

Usage:
  python3 tools/v20_disk_floor.py --hosts s1,s2,s5,s6                 # measure + plan, change nothing
  python3 tools/v20_disk_floor.py --hosts s5 --apply                  # enforce the floor on S5
  python3 tools/v20_disk_floor.py --hosts s1,s2,s5,s6 --apply --rungs corpus,shelve
"""
import argparse
import json
import os
import shlex
import subprocess
import sys
from datetime import datetime, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

# Verified 2026-10-27 by `hostname -I` plus a marker directory on each box (s2 holds v15_bench, s5 holds
# rebuild_scratch_s5, s6 holds s2_archive). The first draft had s2 and s6 swapped, which made the plan attribute
# every candidate to the wrong host -- same map as tools/v20_absorb_cycle.py.
HOSTS = {"s1": "10.0.0.3", "s2": "10.0.0.4", "s5": "10.0.0.5", "s6": "10.0.0.6"}
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ControlPath=none", "-o", "ConnectTimeout=20", "-o", "ServerAliveInterval=15", "-o", "StrictHostKeyChecking=no"]
PYTHON = "/home/niels/.conda/envs/binance_env_mac/bin/python"
SANDBOX = "~/binance-sandbox"
LEDGER = os.path.join(ROOT, "data", "reports", "v20_disk_floor.jsonl")
FLOOR_GB = 40

# Top-level directories that are work product of a finished job: scratch, evacuation, import and archive staging.
# A name alone is never enough -- every candidate must also pass the stale, lsof and NEVER checks below.
DEAD_WORK = ("rebuild_scratch", "_scratch_archive", "_evac", "npz_restore_stage", "npzprep", "ext_staging",
             "stkt_npz_backup", "klines_bak_", "v15_import_", "v15_autopsy", "mirror_toshiba", "s2_archive",
             "npz_quarantine", "quarantine_scripts", "_held_", "progress_wiped")
NEVER = ("binance", "binance-sandbox", "v20_spills", "v20_union", "logs", "data", "pure_board",
         "eval_cache_incoming", ".ssh", ".conda", "backups", "klines_canonical", "klines_master", "bars_crypto",
         "bars_stocks", "v15_bench", "npz_inbox_q", "npz_inbox_s2")
# The one shelf inside a live tree: backups are the only reliable history (CLAUDE.md), so they are compressed in
# place and never deleted, never offloaded away from the box that owns them.
SHELVES = ("binance-sandbox/backups", "binance/backups")


def now():
    return datetime.now(timezone.utc).isoformat()


def log(event, **kw):
    line = json.dumps({"ts": now(), "event": event, **kw}, sort_keys=True, default=str)
    print(line, flush=True)
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    with open(LEDGER, "a") as fh:
        fh.write(line + "\n")


def sh(host, command, timeout=600):
    return subprocess.run(SSH + [f"niels@{HOSTS[host]}", command], capture_output=True, text=True, timeout=timeout)


def free_gb(host):
    result = sh(host, "df -P --block-size=1G / | tail -1 | awk '{print $4}'", timeout=120)
    try:
        return int((result.stdout or "0").strip())
    except ValueError:
        return -1


PROBE = r'''
import json, os, subprocess, time
home = os.path.expanduser("~")
out = []
for name in sorted(os.listdir(home)):
    path = os.path.join(home, name)
    if not os.path.isdir(path) or os.path.islink(path):
        continue
    total = newest = 0
    files = 0
    for dp, dn, fn in os.walk(path):
        for f in fn:
            p = os.path.join(dp, f)
            try:
                st = os.lstat(p)
            except OSError:
                continue
            total += st.st_size
            files += 1
            newest = max(newest, st.st_mtime)
    out.append({"name": name, "bytes": total, "files": files, "newest": newest,
                "plain_bytes": 0})
print(json.dumps({"home": home, "dirs": out, "now": time.time()}))
'''


def inventory(host):
    result = sh(host, "%s -c %s" % (PYTHON, shlex.quote(PROBE)), timeout=1800)
    if result.returncode or not result.stdout.strip():
        log("inventory_failed", host=host, error=(result.stderr or "")[-200:].strip())
        return None
    try:
        return json.loads(result.stdout.strip().splitlines()[-1])
    except Exception as exc:
        log("inventory_unparsable", host=host, error=str(exc))
        return None


def classify(entry, stale_days, now_ts):
    """-> (eligible, reason). A directory is a candidate only when its NAME says it is finished work AND nothing
    has touched it for stale_days AND it is not on NEVER."""
    name = entry["name"]
    if name in NEVER or any(name.startswith(n) for n in ("v15_run",)):
        return False, "never_list" if name in NEVER else "round_directory"
    if not any(tag in name for tag in DEAD_WORK):
        return False, "name_does_not_say_finished_work"
    age_days = (now_ts - (entry.get("newest") or 0)) / 86400.0
    if age_days < stale_days:
        return False, f"touched_{age_days:.1f}d_ago"
    if entry["files"] == 0:
        return False, "empty"
    return True, f"stale_{age_days:.1f}d"


SHELVE = r'''
import json, os, subprocess, sys
spec = json.load(sys.stdin)
root, min_size = spec["root"], spec["min_size"]
if subprocess.run(["lsof", "+D", root], capture_output=True).returncode == 0:
    print(json.dumps({"refused": "open_handle", "root": root})); raise SystemExit(0)
done, failed, freed = 0, [], 0
for dp, dn, fn in os.walk(root):
    for f in fn:
        p = os.path.join(dp, f)
        if f.endswith((".zst", ".gz", ".xz", ".bz2", ".zip", ".npz", ".7z")):
            continue
        try:
            before = os.path.getsize(p)
        except OSError:
            continue
        if before < min_size:
            continue
        if subprocess.run(["lsof", "--", p], capture_output=True).returncode == 0:
            failed.append([p, "open_handle"]); continue
        comp = subprocess.run(["nice", "-n", "19", "ionice", "-c3", "zstd", "-q", "-12", "-T1", "--rm", "--", p], capture_output=True, text=True)
        if comp.returncode or not os.path.isfile(p + ".zst"):
            failed.append([p, "zstd_rc_%d" % comp.returncode]); continue
        if subprocess.run(["zstd", "-t", "--", p + ".zst"], capture_output=True).returncode:
            failed.append([p, "zstd_t_failed"]); continue
        freed += before - os.path.getsize(p + ".zst")
        done += 1
print(json.dumps({"root": root, "compressed": done, "failed": failed[:20], "n_failed": len(failed), "freed_bytes": freed}))
'''


def shelve(host, root, min_size, apply_it, detach=False):
    """Compress a dead work directory in place. 114 GB of scratch is hours of zstd, so --detach nohups it on the
    host and returns: an ssh held open that long dies to the first network blip and leaves the shelf half done."""
    if not apply_it:
        return {"root": root, "dry_run": True}
    payload = json.dumps({"root": root, "min_size": min_size})
    if detach:
        tag = os.path.basename(root.rstrip("/")) or "shelf"
        stage = f"/tmp/v20_shelve_{tag}"
        # base64 instead of a heredoc, and setsid + </dev/null so the ssh channel closes immediately: with a
        # heredoc plus a trailing `&`, ssh kept the session open and the call timed out even though the shelve had
        # started, which reads like a failure when the work is in fact running.
        b64_py = __import__("base64").b64encode(SHELVE.encode()).decode()
        b64_js = __import__("base64").b64encode(payload.encode()).decode()
        setup = (f"mkdir -p ~/logs && echo {b64_py} | base64 -d > {stage}.py && echo {b64_js} | base64 -d > {stage}.json && "
                 f"( setsid nice -n 19 ionice -c3 {PYTHON} {stage}.py < {stage}.json >> ~/logs/v20_disk_floor_shelve.log 2>&1 & ) "
                 f"< /dev/null > /dev/null 2>&1; echo DETACHED")
        try:
            result = sh(host, setup, timeout=120)
            launched = "DETACHED" in (result.stdout or "")
            error = "" if result.returncode == 0 else (result.stderr or "")[-200:].strip()
        except subprocess.TimeoutExpired:
            # ssh can hold the channel open even after the shelve is running, so a timeout here is not a failure:
            # confirm by looking for the staged script in the process table rather than reporting a false negative.
            check = sh(host, f"pgrep -fa {stage}.py | head -2", timeout=60)
            launched = f"{stage}.py" in (check.stdout or "")
            error = "" if launched else "ssh timed out and no shelve process found"
        return {"root": root, "detached": launched, "log": "~/logs/v20_disk_floor_shelve.log", "error": error}
    result = subprocess.run(SSH + [f"niels@{HOSTS[host]}", "%s -c %s" % (PYTHON, shlex.quote(SHELVE))],
                            input=payload, capture_output=True, text=True, timeout=21600)
    try:
        return json.loads((result.stdout or "").strip().splitlines()[-1])
    except Exception:
        return {"root": root, "error": (result.stderr or result.stdout or "")[-300:].strip()}


def corpus_rung(host, apply_it):
    """Delegate to the gated absorb cycle: it owns the watermark proof, the fleet-wide round protection and the
    sha256 round-trip. Re-implementing any of that here would be a second, weaker gate."""
    command = (f"cd {SANDBOX} && nice -n 19 ionice -c3 {PYTHON} tools/v20_absorb_cycle.py "
               f"--phase inventory,compress,report --hosts {host}" + (" --apply" if apply_it else ""))
    result = sh(host, command, timeout=21600)
    freed = 0
    for line in (result.stdout or "").splitlines():
        if '"event": "cycle_end"' in line:
            try:
                freed = json.loads(line).get("freed_mb") or 0
            except Exception:
                pass
    return {"rc": result.returncode, "freed_mb": freed}


def main():
    parser = argparse.ArgumentParser(description="hold a free-space floor on every fleet host")
    parser.add_argument("--hosts", default="s1,s2,s5,s6")
    parser.add_argument("--floor-gb", type=int, default=FLOOR_GB)
    parser.add_argument("--stale-days", type=float, default=3.0)
    parser.add_argument("--min-file-mb", type=int, default=1, help="shelve: skip files below this size (zstd overhead dominates)")
    parser.add_argument("--rungs", default="corpus,shelve", help="comma list of corpus,shelve (offload is launched separately: it is hours of upload)")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--detach", action="store_true", help="shelve: nohup each directory on the host instead of waiting (a 114 GB shelf is hours)")
    parser.add_argument("--report", default=os.path.join(ROOT, "data", "reports", "v20_disk_floor_latest.json"))
    args = parser.parse_args()

    hosts = [h.strip() for h in args.hosts.split(",") if h.strip() in HOSTS]
    rungs = [r.strip() for r in args.rungs.split(",") if r.strip()]
    summary = {"contract": "v20_disk_floor_v1", "ts": now(), "floor_gb": args.floor_gb, "applied": bool(args.apply), "hosts": {}}

    for host in hosts:
        start = free_gb(host)
        block = {"free_gb_start": start, "rungs": [], "candidates": [], "shortfall_gb": max(0, args.floor_gb - start)}
        log("floor_check", host=host, free_gb=start, floor_gb=args.floor_gb, under=start < args.floor_gb)
        if start < 0:
            block["error"] = "df failed"
            summary["hosts"][host] = block
            continue
        if start >= args.floor_gb:
            summary["hosts"][host] = block
            continue

        if "corpus" in rungs:
            out = corpus_rung(host, args.apply)
            block["rungs"].append({"rung": "corpus", **out})
            log("rung_corpus", host=host, **out)
            if free_gb(host) >= args.floor_gb:
                block["free_gb_end"] = free_gb(host)
                summary["hosts"][host] = block
                continue

        inv = inventory(host)
        if inv:
            now_ts = inv["now"]
            for entry in sorted(inv["dirs"], key=lambda d: -d["bytes"]):
                if entry["bytes"] < (1 << 30):
                    continue
                ok, reason = classify(entry, args.stale_days, now_ts)
                block["candidates"].append({"name": entry["name"], "gb": round(entry["bytes"] / 2 ** 30, 1),
                                            "files": entry["files"], "eligible": ok, "reason": reason})
        if "shelve" in rungs:
            targets = [os.path.join(inv["home"], c["name"]) for c in block["candidates"] if c["eligible"]] if inv else []
            targets += [os.path.join(inv["home"], s) for s in SHELVES] if inv else []
            for root in targets:
                if not args.detach and free_gb(host) >= args.floor_gb:
                    break
                out = shelve(host, root, args.min_file_mb * 1048576, args.apply, args.detach)
                block["rungs"].append({"rung": "shelve", **out})
                log("rung_shelve", host=host, **out)

        block["free_gb_end"] = free_gb(host)
        block["shortfall_gb"] = max(0, args.floor_gb - block["free_gb_end"])
        if block["shortfall_gb"]:
            blockers = [c for c in block["candidates"] if not c["eligible"]]
            log("floor_not_met", host=host, free_gb=block["free_gb_end"], floor_gb=args.floor_gb,
                shortfall_gb=block["shortfall_gb"],
                top_blockers=[{"name": c["name"], "gb": c["gb"], "reason": c["reason"]} for c in blockers[:6]])
        summary["hosts"][host] = block

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    with open(args.report, "w") as fh:
        json.dump(summary, fh, indent=1, sort_keys=True)
    log("floor_summary", floor_gb=args.floor_gb, applied=bool(args.apply),
        free_gb={h: b.get("free_gb_end", b.get("free_gb_start")) for h, b in summary["hosts"].items()},
        shortfall_gb={h: b.get("shortfall_gb") for h, b in summary["hosts"].items()})
    print(f"[floor] report {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
