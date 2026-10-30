#!/usr/bin/env python3
"""Scheduled V20 absorption cycle: absorb every host's backtest output, then shrink what has been used.

USER 2026-10-26: *"create a scheduled V20 absorption job on s2 or s6 that uses all available data and discards or
compresses what has been used so disk usage everywhere starts diminishing"*, under the PURGE GATE
(BACKTEST_BIBLE.md § PURGE GATE): a run is only purgeable once the network has absorbed AND understood it.

Runs ON the orchestrator host (S2 by default), which reaches S1/S5/S6 directly over 10.0.0.x. Five phases:

  inventory  every run dir on every host, classified CURRENT / PREVIOUS / FINISHED, with per-file extraction state
  absorb     per host, `v18_brain.py extract --source logs` and `--source progress` -- INCREMENTAL: the extractor
             keeps `watermarks_<host>.json` {host:abspath -> {size, lines}} and skips a file whose size is unchanged.
             Spills (tens of MB) are then pulled to the orchestrator; raw logs (82 GB fleet-wide) never move.
  train      union train + export on the orchestrator over every spill, then the MANIFEST that records what is absorbed
  compress   zstd, in place, only files PROVEN used and safe (see below). Needs --apply; dry-run otherwise
  report     purge-gate readiness per run. This tool NEVER deletes a corpus -- compression is reversible, deletion is not

A file may be compressed only when ALL of these hold:
  1. its run is FINISHED -- never the current or previous round, so a pilot resume can still read its progress JSON
  2. the extractor's watermark proves it was read to its current size (delta logs), or the run's progress extract
     succeeded after the file stopped changing (progress JSON)
  3. for a delta log: its NEWEST ROW ts is older than the v15_brain window plus a margin (default 14 + 7 days).
     tools/v15_brain.py globs `~/v15_run*/[progress/]v15_delta_log/*.jsonl` HARDCODED and filters by row ts, so a
     file past that window contributes zero rows to the daily export and renaming it to .zst starves nothing.
     Inside the window, compressing WOULD silently train the next model on less -- the whole reason for the margin
  4. no process holds it open (`lsof`)
  5. the compressed copy passes `zstd -t` and round-trips to the original byte count before the original is removed

Progress JSON is the immediate win: measured 2026-10-26 it is ~40% of a finished run dir and is not on the brain's
jsonl glob at all (the brain reads it through `--source progress`), so it is compressible as soon as a round ends.
Delta logs are the larger but later win: they age out of the window run by run, and this job compresses them the day
they do, which is what makes disk shrink on its own instead of on someone remembering.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import socket
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

HOSTS = {"s1": "10.0.0.3", "s2": "10.0.0.4", "s5": "10.0.0.5", "s6": "10.0.0.6"}
SANDBOX = "~/binance-sandbox"
PYTHON = "/home/niels/.conda/envs/binance_env_mac/bin/python"
SPILL_REMOTE = "~/v20_spills"
UNION_DIR = "~/v20_union"
LEDGER = Path(os.environ.get("V20_ABSORB_LEDGER", Path.home() / "logs/v20_absorb_cycle.jsonl"))
RUN_RE = re.compile(r"v15_run(\d+)_(\d{8})$")
BRAIN_WINDOW_D = int(os.environ.get("V15_BRAIN_SINCE_D", "14"))
MARGIN_D = int(os.environ.get("V20_COMPRESS_MARGIN_D", "7"))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "ControlPath=none", "-o", "ServerAliveInterval=15", "-o", "ServerAliveCountMax=40", "-o", "StrictHostKeyChecking=no"]
PROTECTED_ROUNDS = 2
# A union train needs room for the sort temporaries. Measured 2026-10-27: a train over 931 MB of spills took S2 from
# 15.1 GB to 5.2 GB free before sort died with exit 2, so the margin is real and the refusal is cheap.
TRAIN_MIN_FREE_MB = int(os.environ.get("V20_TRAIN_MIN_FREE_MB", "20480"))


def log(event: str, **fields) -> None:
    row = {"ts": datetime.now(timezone.utc).isoformat(), "event": event, **fields}
    line = json.dumps(row, sort_keys=True, default=str)
    print(line, flush=True)
    try:
        LEDGER.parent.mkdir(parents=True, exist_ok=True)
        with LEDGER.open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
    except Exception:
        pass


def here() -> str:
    name = socket.gethostname()
    return {"niels": "s1"}.get(name, name)


def sh(host: str, command: str, timeout: int = 900, check: bool = False) -> subprocess.CompletedProcess:
    if host == here():
        return subprocess.run(["bash", "-lc", command], capture_output=True, text=True, timeout=timeout, check=check)
    return subprocess.run([*SSH, f"niels@{HOSTS[host]}", "bash", "-lc", shlex.quote(command)], capture_output=True, text=True, timeout=timeout, check=check)


# --------------------------------------------------------------------------- inventory
PROBE = r'''
import json, os, glob, re, subprocess
out = {"runs": {}}
runs = sorted(glob.glob(os.path.expanduser("~/v15_run*")))
def newest_row_ts(path):
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            fh.seek(max(0, size - 65536))
            tail = fh.read().splitlines()
        for raw in reversed(tail):
            try:
                ts = json.loads(raw).get("ts")
            except Exception:
                continue
            if ts:
                return ts
    except Exception:
        pass
    return ""
wms = {}
for wm in glob.glob(os.path.expanduser("SPILLGLOB")):
    try:
        wms.update(json.load(open(wm)))
    except Exception:
        pass
for run in runs:
    name = os.path.basename(run)
    files = []
    for pattern in ("progress/v15_delta_log/*.jsonl", "v15_delta_log/*.jsonl"):
        files += glob.glob(os.path.join(run, pattern))
    jsons = [p for p in glob.glob(os.path.join(run, "progress", "*.json"))]
    entry = {"delta_logs": [], "progress_json": [], "bytes_total": 0}
    for path in sorted(files):
        try:
            st = os.stat(path)
        except OSError:
            continue
        key = [k for k in wms if k.endswith(":" + os.path.abspath(path))]
        wm = wms.get(key[0]) if key else None
        entry["delta_logs"].append({"path": path, "bytes": st.st_size, "newest_row_ts": newest_row_ts(path),
                                    "watermark_size": (wm or {}).get("size"), "fully_extracted": bool(wm and wm.get("size") == st.st_size)})
        entry["bytes_total"] += st.st_size
    for path in sorted(jsons):
        try:
            st = os.stat(path)
        except OSError:
            continue
        entry["progress_json"].append({"path": path, "bytes": st.st_size, "mtime": st.st_mtime})
        entry["bytes_total"] += st.st_size
    for pattern in ("progress/v15_delta_log/*.jsonl.zst", "progress/*.json.zst"):
        entry.setdefault("already_compressed", 0)
        entry["already_compressed"] += len(glob.glob(os.path.join(run, pattern)))
    out["runs"][name] = entry
print(json.dumps(out))
'''


def inventory(hosts: list) -> dict:
    report = {}
    for host in hosts:
        probe = PROBE.replace("SPILLGLOB", f"{SPILL_REMOTE}/watermarks_*.json")
        result = sh(host, "%s -c %s" % (PYTHON, shlex.quote(probe)), timeout=600)
        if result.returncode or not result.stdout.strip():
            log("inventory_failed", host=host, error=result.stderr[-200:].strip())
            continue
        try:
            report[host] = json.loads(result.stdout.strip().splitlines()[-1])
        except Exception as exc:
            log("inventory_unparsable", host=host, error=str(exc))
    return report


def run_number(name: str) -> int:
    match = RUN_RE.search(name)
    return int(match.group(1)) if match else -1


def protected_rounds(report: dict) -> set:
    """The current and previous round numbers, taken FLEET-WIDE, not per host.

    Per-host "two highest" was wrong: a host that stopped taking work early (S1 holds run27/29/31/37) would have an
    October-11 run protected as if it were the previous round, while a busy host would expose a round the fleet is
    still writing. The round is a fleet-level fact, so it is computed once across every host.
    """
    numbers = {run_number(name) for block in report.values() for name in (block.get("runs") or {})}
    numbers.discard(-1)
    return set(sorted(numbers, reverse=True)[:PROTECTED_ROUNDS])


def progress_absorbed_through(host: str) -> float:
    """Epoch seconds of the most recent SUCCESSFUL progress extract for this host, from the ledger. A progress JSON is
    only 'used' if it stopped changing before that moment -- the progress extractor keeps no per-file watermark, so
    this cycle-level proof is the honest substitute. 0.0 means never absorbed, which refuses every candidate."""
    best = 0.0
    try:
        for line in LEDGER.read_text().splitlines():
            try:
                row = json.loads(line)
            except Exception:
                continue
            if row.get("event") == "absorb_extract" and row.get("host") == host and row.get("source") == "progress" and row.get("rc") == 0:
                stamp = str(row.get("ts") or "").replace("Z", "+00:00")
                try:
                    moment = datetime.fromisoformat(stamp).timestamp()
                except Exception:
                    continue
                best = max(best, moment)
    except Exception:
        return 0.0
    return best


# --------------------------------------------------------------------------- absorb
def absorb(hosts: list, orchestrator: str, full: bool = False) -> dict:
    results = {}
    for host in hosts:
        tag = f"{host}auto"
        ok = {}
        for source, glob_arg in (("logs", "'~/v15_run*/progress/v15_delta_log/*.jsonl' --from '~/v15_run*/v15_delta_log/*.jsonl'"),
                                 ("progress", "'~/v15_run*/progress/*_v14_progress.json'")):
            command = (f"cd {SANDBOX} && mkdir -p {SPILL_REMOTE} && nice -n 19 ionice -c3 {PYTHON} tools/v18_brain.py extract "
                       f"--source {source} --from {glob_arg} --host {tag} --spill-dir {SPILL_REMOTE}" + (" --full" if full else ""))
            result = sh(host, command, timeout=21600)
            ok[source] = result.returncode == 0
            counters = ""
            for line in reversed((result.stdout or "").splitlines()):
                if line.strip().startswith("{"):
                    counters = line.strip()[:400]
                    break
            log("absorb_extract", host=host, source=source, rc=result.returncode, counters=counters, error=(result.stderr[-200:].strip() if result.returncode else ""))
        if host != orchestrator:
            dest = os.path.expanduser(f"{UNION_DIR}/spills/{host}") if orchestrator == here() else f"{UNION_DIR}/spills/{host}"
            if orchestrator == here():
                # rsync creates only the LAST path component: without this, every pull died on
                # mkdir "~/v20_union/spills/<host>" failed: No such file or directory (2), so from install until
                # 2026-10-27 the union train saw ONLY the orchestrator's own spill and nothing from s1/s5/s6.
                os.makedirs(dest, exist_ok=True)
            # --exclude tmp/: the extractor's own scratch holds decompressed spill_*.tsv.u files. Pulling them made
            # the union train feed multi-GB temporaries to sort, which ran S2 from 15.1 GB to 5.2 GB free and died.
            pull = subprocess.run(["rsync", "-az", "--exclude", "tmp/", "-e", " ".join(SSH), f"niels@{HOSTS[host]}:{SPILL_REMOTE}/", f"{dest}/"], capture_output=True, text=True, timeout=7200) if orchestrator == here() else sh(orchestrator, f"mkdir -p {dest} && rsync -az --exclude 'tmp/' -e 'ssh -o BatchMode=yes -o StrictHostKeyChecking=no' niels@{HOSTS[host]}:{SPILL_REMOTE}/ {dest}/", timeout=7200)
            log("absorb_spill_pull", host=host, rc=pull.returncode, error=(pull.stderr or "")[-200:].strip())
            ok["pull"] = pull.returncode == 0
        results[host] = ok
    return results


# --------------------------------------------------------------------------- compress
ZST_PROBE = (
    "test -f tools/zreader.py "
    "&& grep -q 'zreader.expand_globs' tools/v18_brain.py "
    "&& grep -q 'zreader.open_binary' tools/v18_brain.py "
    "&& grep -q 'zreader.expand_globs' tools/v15_brain.py "
    "&& grep -q 'zreader.open_text' tools/v15_brain.py "
    "&& command -v zstd >/dev/null "
    "&& %s -c \"import sys; sys.path.insert(0, 'tools'); import zreader; print(zreader.plain_name('x.jsonl.zst'))\""
)


def zst_readable(host: str) -> bool:
    """True only when THIS host's brains read .zst, so compressing a log cannot blind the next training run.

    Measured 2026-10-27 on S2: extracting a plain delta log and the same log as .zst produced spill content with the
    identical sha256 (c02ae2b7..., 2,126 events / 810 rows / 11,434 lines) and the same watermark key, at 45.8x on
    that file. Until that holds on a host, the 21-day age margin below stays its only release -- a compressed log in
    front of a reader that globs `*.jsonl` is a corpus silently dropped from training, which is the one thing worse
    than a full disk.
    """
    result = sh(host, f"cd {SANDBOX} && " + (ZST_PROBE % PYTHON), timeout=120)
    ok = result.returncode == 0 and "x.jsonl" in (result.stdout or "")
    log("zst_readable", host=host, ok=ok, error=("" if ok else (result.stderr or result.stdout or "")[-200:].strip()))
    return ok


def compressible(host: str, run: str, state: str, entry: dict, cutoff: datetime, absorbed_through: float = 0.0, zst_ok: bool = False) -> tuple:
    """-> (delta_log_paths, progress_json_paths, skipped reasons). Conditions 1-3 of the docstring; 4 and 5 are
    enforced at compression time on the host itself."""
    if state != "FINISHED":
        return [], [], {"protected_round": len(entry.get("delta_logs", [])) + len(entry.get("progress_json", []))}
    logs, jsons, skipped = [], [], {}
    for item in entry.get("progress_json", []):
        if not absorbed_through:
            skipped["progress_never_absorbed"] = skipped.get("progress_never_absorbed", 0) + 1
            continue
        if float(item.get("mtime") or 0) >= absorbed_through:
            skipped["progress_changed_after_absorb"] = skipped.get("progress_changed_after_absorb", 0) + 1
            continue
        jsons.append(item["path"])
    for item in entry.get("delta_logs", []):
        if not item.get("fully_extracted"):
            skipped["not_fully_extracted"] = skipped.get("not_fully_extracted", 0) + 1
            continue
        stamp = (item.get("newest_row_ts") or "").replace("Z", "+00:00")
        try:
            newest = datetime.fromisoformat(stamp)
        except Exception:
            skipped["unreadable_newest_row_ts"] = skipped.get("unreadable_newest_row_ts", 0) + 1
            continue
        if newest.tzinfo is None:
            newest = newest.replace(tzinfo=timezone.utc)
        if newest >= cutoff and not zst_ok:
            skipped["inside_brain_window"] = skipped.get("inside_brain_window", 0) + 1
            continue
        logs.append(item["path"])
    return logs, jsons, skipped


COMPRESS = r'''
import hashlib, json, os, shlex, subprocess, sys
paths = json.load(sys.stdin)
done, failed, freed = [], [], 0


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1048576), b""):
            h.update(block)
    return h.hexdigest()


for path in paths:
    if not os.path.isfile(path) or path.endswith(".zst"):
        continue
    if subprocess.run(["lsof", "--", path], capture_output=True).returncode == 0:
        failed.append([path, "open_handle"]); continue
    before = os.path.getsize(path)
    digest = sha256(path)
    target = path + ".zst"
    comp = subprocess.run(["nice", "-n", "19", "ionice", "-c3", "zstd", "-q", "-f", "-T1", "-12", "-o", target, path], capture_output=True, text=True)
    if comp.returncode or not os.path.isfile(target):
        failed.append([path, "compress_rc_%d" % comp.returncode]); continue
    if subprocess.run(["zstd", "-t", target], capture_output=True).returncode:
        os.unlink(target); failed.append([path, "zstd_t_failed"]); continue
    # full round-trip: the decompressed stream must hash to the original, not merely match its length
    check = subprocess.run("zstd -dc %s | sha256sum" % shlex.quote(target), shell=True, capture_output=True, text=True)
    roundtrip = (check.stdout or "").split()[0] if check.stdout.strip() else ""
    if check.returncode or roundtrip != digest:
        os.unlink(target); failed.append([path, "roundtrip_mismatch"]); continue
    os.unlink(path)
    after = os.path.getsize(target)
    freed += before - after
    done.append([path, before, after, digest])
print(json.dumps({"compressed": done, "failed": failed, "freed_bytes": freed}))
'''


def compress(host: str, paths: list, apply: bool) -> dict:
    if not paths:
        return {"compressed": [], "failed": [], "freed_bytes": 0}
    if not apply:
        log("compress_plan", host=host, files=len(paths), sample=paths[:3])
        return {"planned": len(paths)}
    payload = json.dumps(paths)
    command = "%s -c %s" % (PYTHON, shlex.quote(COMPRESS))
    if host == here():
        result = subprocess.run(["bash", "-lc", command], input=payload, capture_output=True, text=True, timeout=43200)
    else:
        result = subprocess.run([*SSH, f"niels@{HOSTS[host]}", "bash", "-lc", shlex.quote(command)], input=payload, capture_output=True, text=True, timeout=43200)
    if result.returncode or not result.stdout.strip():
        log("compress_failed", host=host, rc=result.returncode, error=result.stderr[-300:].strip())
        return {"compressed": [], "failed": [["*", "remote_rc"]], "freed_bytes": 0}
    out = json.loads(result.stdout.strip().splitlines()[-1])
    log("compress_done", host=host, files=len(out["compressed"]), failed=len(out["failed"]), freed_mb=round(out["freed_bytes"] / 1048576, 1), first_failures=out["failed"][:3])
    return out


def corpus_provenance(report: dict) -> dict:
    per = report.get("per_cat_side") or {}
    observed = {}
    for cat_side, evidence in per.items():
        raw = evidence.get("last_ts")
        if not raw and not (evidence.get("counters") or {}).get("rows"):
            continue
        try:
            value = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
            if value.tzinfo is None or value.year < 1970:
                raise ValueError("missing timezone or sentinel date")
        except (TypeError, ValueError) as exc:
            raise ValueError(f"unknown corpus timestamp for {cat_side}: {raw!r}") from exc
        observed[cat_side] = value.astimezone(timezone.utc)
    if not observed:
        raise ValueError("no observed corpus timestamps in training report")
    return {"global_corpus_last_ts": max(observed.values()).isoformat(timespec="microseconds").replace("+00:00", "Z"), "corpus_last_ts_by_cat_side": {key: value.isoformat(timespec="microseconds").replace("+00:00", "Z") for key, value in observed.items()}, "corpus_timestamp_source": "v18_brain_report.per_cat_side.last_ts", "corpus_coverage": "latest observed events; not proof of source completeness"}


def train(orchestrator: str, hosts: list) -> dict:
    """Union train + export over EVERY spill on the orchestrator, then the MANIFEST that records what is absorbed.

    Absorbing without training changes nothing the pilot reads: the heads only learn here. The cut is now minus
    V18_CUT_DAYS (default 3); post-cut metrics are chronological predict-before-learn diagnostics, not a frozen
    model holdout. The MANIFEST is what the PURGE GATE reads, so it requires observed event provenance.
    """
    # A union train sorts every spill through a temp dir. Starting one on a host that is nearly full does not fail
    # cleanly: sort dies with exit 2 part-way, the run dir is left behind, and the host is now even fuller. Refuse
    # instead, and say so -- tools/v20_disk_floor.py is what makes the room.
    free = free_mb(orchestrator)
    if free < TRAIN_MIN_FREE_MB:
        log("train_refused", orchestrator=orchestrator, free_mb=free, need_mb=TRAIN_MIN_FREE_MB,
            why="not enough free disk for the sort temporaries; run tools/v20_disk_floor.py --apply first")
        return {"refused": "insufficient_free_disk", "free_mb": free, "need_mb": TRAIN_MIN_FREE_MB}
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    run_dir = f"{UNION_DIR}/auto_{stamp}"
    spills = " ".join(f"--spill-dir {UNION_DIR}/spills/{host}" for host in hosts) + f" --spill-dir {SPILL_REMOTE}"
    # --cut is REQUIRED by v18_brain.py train (prequential capture and the forward holdout are scored at and after
    # it). The first version of this phase omitted it, so every train died with "the following arguments are
    # required: --cut" -- absorbed rows piled up in the spills and the published heads never moved. Same horizon as
    # the proven tools/v18_brain_export.sh: now - V18_CUT_DAYS (default 3).
    cut = (datetime.now(timezone.utc) - timedelta(days=int(os.environ.get("V18_CUT_DAYS", "3")))).strftime("%Y-%m-%dT%H:%M:%SZ")
    steps = [
        ("train", f"cd {SANDBOX} && mkdir -p {run_dir}/brain_state {run_dir}/brain_union {run_dir}/tmp && nice -n 19 ionice -c3 {PYTHON} tools/v18_brain.py train "
                  f"{spills} --cut {cut} --tmp-dir {run_dir}/tmp --state {run_dir}/brain_state/v18_brain_state.pkl --out-dir {run_dir}/brain_union "
                  f"--report {run_dir}/brain_state/v18_brain_report.json --binding-map-dir {SANDBOX}/data/learn"),
        ("export", f"cd {SANDBOX} && nice -n 19 ionice -c3 {PYTHON} tools/v18_brain.py export "
                   f"--state {run_dir}/brain_state/v18_brain_state.pkl --out-dir {run_dir}/brain_union"),
    ]
    for name, command in steps:
        result = sh(orchestrator, command, timeout=43200)
        tail = (result.stdout or "")[-400:].strip().replace("\n", " | ")
        log(f"train_{name}", orchestrator=orchestrator, rc=result.returncode, run_dir=run_dir, tail=tail, error=(result.stderr[-300:].strip() if result.returncode else ""))
        if result.returncode:
            return {"ok": False, "step": name, "run_dir": run_dir}
    result = sh(orchestrator, f"cat {run_dir}/brain_state/v18_brain_report.json", timeout=120)
    try:
        if result.returncode:
            raise ValueError("training report could not be read")
        provenance = corpus_provenance(json.loads(result.stdout))
    except (ValueError, TypeError, AttributeError) as exc:
        log("train_manifest_refused", orchestrator=orchestrator, run_dir=run_dir, why=str(exc))
        return {"ok": False, "step": "provenance", "run_dir": run_dir, "why": str(exc)}
    manifest = {
        "built": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "trained_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z"),
        "kind": f"AUTO absorption cycle on {orchestrator} (tools/v20_absorb_cycle.py)",
        **provenance,
        "sources": [f"{host}auto(logs+progress, watermarked incremental)" for host in hosts],
        "run_dir": run_dir,
    }
    write = sh(orchestrator, "cat > %s/MANIFEST.json <<'MANIFEST_EOF'\n%s\nMANIFEST_EOF" % (run_dir, json.dumps(manifest, indent=1)), timeout=120)
    log("train_manifest", orchestrator=orchestrator, rc=write.returncode, run_dir=run_dir, sources=manifest["sources"])
    return {"ok": write.returncode == 0, "run_dir": run_dir}


def free_mb(host: str) -> int:
    result = sh(host, "df -Pm / | awk 'NR==2 {print $4}'", timeout=60)
    try:
        return int(result.stdout.strip())
    except Exception:
        return -1


def main() -> int:
    parser = argparse.ArgumentParser(description="scheduled V20 absorption + shrink cycle")
    parser.add_argument("--phase", default="inventory,absorb,compress,report", help="comma list of inventory,absorb,train,compress,report")
    parser.add_argument("--hosts", default="s1,s2,s5,s6")
    parser.add_argument("--orchestrator", default="s2")
    parser.add_argument("--apply", action="store_true", help="actually compress (phases are read-only without it)")
    parser.add_argument("--full", action="store_true", help="absorb: ignore watermarks and re-read every file")
    parser.add_argument("--window-days", type=int, default=BRAIN_WINDOW_D + MARGIN_D)
    args = parser.parse_args()

    phases = [p.strip() for p in args.phase.split(",") if p.strip()]
    hosts = [h.strip() for h in args.hosts.split(",") if h.strip() in HOSTS]
    cutoff = datetime.now(timezone.utc) - timedelta(days=args.window_days)
    before = {host: free_mb(host) for host in hosts}
    log("cycle_start", phases=phases, hosts=hosts, orchestrator=args.orchestrator, cutoff=cutoff.isoformat(), apply=args.apply, free_mb=before)

    if "absorb" in phases:
        absorb(hosts, args.orchestrator, args.full)
    if "train" in phases:
        train(args.orchestrator, hosts)

    report = inventory(hosts) if {"inventory", "compress", "report"} & set(phases) else {}
    totals = {"freed_mb": 0, "compressed": 0}
    protected = protected_rounds(report)
    log("rounds_protected", round_numbers=sorted(protected))
    for host, block in report.items():
        absorbed_through = progress_absorbed_through(host)
        zst_ok = zst_readable(host) if {"compress", "report"} & set(phases) else False
        for run, entry in sorted(block.get("runs", {}).items()):
            state = "PROTECTED" if run_number(run) in protected else "FINISHED"
            logs, jsons, skipped = compressible(host, run, state, entry, cutoff, absorbed_through, zst_ok)
            mb = entry.get("bytes_total", 0) / 1048576
            log("run_state", host=host, run=run, state=state, total_mb=round(mb, 1),
                delta_logs=len(entry.get("delta_logs", [])), progress_json=len(entry.get("progress_json", [])),
                compressible_logs=len(logs), compressible_json=len(jsons), skipped=skipped)
            if "compress" in phases and (logs or jsons):
                out = compress(host, logs + jsons, args.apply)
                totals["freed_mb"] += out.get("freed_bytes", 0) / 1048576
                totals["compressed"] += len(out.get("compressed", []))

    after = {host: free_mb(host) for host in hosts}
    log("cycle_end", free_mb_before=before, free_mb_after=after, freed_mb=round(totals["freed_mb"], 1), files_compressed=totals["compressed"],
        note="this tool never deletes a corpus; purge requires BACKTEST_BIBLE.md PURGE GATE tests 2 and 3")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
