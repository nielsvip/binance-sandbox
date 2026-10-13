#!/usr/bin/env python3
"""klines_vault — S1 append-only vault + writer-killer. USER 2026-10-13.

S1's klines_cache/ is the deep repository. NOTHING may shrink it.
Two duties (stdlib only, S1 runs this via cron):

  --seal     copy every parseable klines file into klines_vault/ as a COMPRESSED
             read-only generation (*.json.gz, chmod 444), but ONLY when the new
             content strictly dominates the sealed one (more rows AND >= span
             AND newer-or-equal tail). Two generations kept (.json.gz +
             .prev.json.gz). gzip because a plain duplicate of the repo (~27G)
             filled S1's disk (99%) on the first seal run.
  --enforce  for every vault-tracked file: if the live file parses to FEWER
             rows than sealed, or fails to parse while the seal is good:
               1. find PIDs holding the live path open for WRITING (/proc fd
                  scan, deleted-inode handles skipped),
               2. SIGKILL them (never self, never PID 1, never sshd),
               3. restore the sealed generation over the live path,
               4. write data/klines_vault_kills.jsonl receipt (killed pids +
                  file + before/after).
             Files with no seal: alert-only, never kill.

  python3 tools/klines_vault.py --seal      # cron hourly (stat-gated after first run)
  python3 tools/klines_vault.py --enforce   # cron */2 (vault-compare)
"""
import argparse
import gzip
import json
import os
import signal
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LIVE = ROOT / "klines_cache"
VAULT = ROOT / "klines_vault"
KILLS = ROOT / "data" / "klines_vault_kills.jsonl"
STATE = ROOT / "data" / "klines_vault_state.json"
SELF = os.getpid()
# Same staging exclusion as klines_guard: rolling feed dirs shrink by design —
# sealing/enforcing them would false-positive on every refresh (and kill feeders).
STAGING_DIRS = {"gateway", "macbook", "backups", "consolidated_klines"}


def _is_staging(rel):
    return bool(set(Path(rel).parts[:-1]) & STAGING_DIRS)


def _tail(rows):
    try:
        return rows[-1].get("timestamp", "") if rows else ""
    except Exception:
        return ""


def _load_live(p):
    try:
        rows = json.loads(Path(p).read_text())
        return rows if isinstance(rows, list) and rows else None
    except Exception:
        return None


def _vault_names(rel):
    cur = VAULT / (rel + ".gz")
    leg = VAULT / rel
    stem = rel[:-len(".json")] if rel.endswith(".json") else rel
    prev = VAULT / (stem + ".prev.json.gz")
    return cur, leg, prev


def _load_seal(rel):
    cur, leg, _ = _vault_names(rel)
    for cand, gz in ((cur, True), (leg, False)):
        try:
            if not cand.exists():
                continue
            raw = gzip.decompress(cand.read_bytes()) if gz else cand.read_bytes()
            rows = json.loads(raw.decode())
            if isinstance(rows, list) and rows:
                return rows
        except Exception:
            continue
    return None


def _writable_fd_holders(path):
    """PIDs with `path` open for writing (fdinfo flags O_WRONLY/O_RDWR).

    Skips deleted-inode handles (stale pre-replace fds), self, PID 1, sshd.
    """
    holders = []
    want = str(path)
    for d in os.listdir("/proc"):
        if not d.isdigit():
            continue
        pid = int(d)
        if pid == SELF or pid == 1:
            continue
        try:
            with open(f"/proc/{pid}/comm") as fh:
                if fh.read().strip() in ("sshd",):
                    continue
        except Exception:
            continue
        fdroot = f"/proc/{pid}/fd"
        try:
            fds = os.listdir(fdroot)
        except Exception:
            continue
        for fd in fds:
            try:
                tgt = os.readlink(f"{fdroot}/{fd}")
            except Exception:
                continue
            if tgt != want or " (deleted)" in tgt:
                continue
            try:
                info = open(f"/proc/{pid}/fdinfo/{fd}").read()
                flags = int([l for l in info.splitlines() if l.startswith("flags:")][0].split(":")[1].strip(), 8)
            except Exception:
                continue
            if flags & 0o3 in (0o1, 0o2):
                holders.append({"pid": pid, "fd": fd})
    return holders


def _cmdline(pid):
    try:
        raw = open(f"/proc/{pid}/cmdline", "rb").read().replace(b"\0", b" ").decode("utf8", "replace")
        return raw[:300]
    except Exception:
        return ""


def _seal_one(rel, live_rows):
    cur, leg, prev = _vault_names(rel)
    sealed = _load_seal(rel)
    if sealed is not None:
        if not (len(live_rows) > len(sealed) and _tail(live_rows) >= _tail(sealed)):
            return "held"
    try:
        cur.parent.mkdir(parents=True, exist_ok=True)
        if sealed is not None and cur.exists():
            if prev.exists():
                prev.unlink()
            cur.rename(prev)
            os.chmod(prev, 0o444)
        tmp = cur.with_suffix(".tmp")
        with gzip.open(tmp, "wt") as fh:
            fh.write(json.dumps(live_rows))
        os.replace(tmp, cur)
        os.chmod(cur, 0o444)
        if leg.exists():
            leg.unlink()
        return "sealed"
    except Exception as e:
        return f"error {e}"[:100]


def _vault_rels():
    rels = set()
    if VAULT.is_dir():
        for p in VAULT.rglob("*"):
            if not p.is_file() or ".tmp" in p.name:
                continue
            s = str(p.relative_to(VAULT))
            if s.endswith(".prev.json.gz"):
                continue
            if s.endswith(".json.gz"):
                rels.add(s[:-len(".gz")])
            elif s.endswith(".json"):
                rels.add(s)
    return sorted(r for r in rels if not _is_staging(r))


def prune_staging():
    """Delete vault generations under staging dirs (rolling feeds, not repository)."""
    n = 0
    if VAULT.is_dir():
        for p in list(VAULT.rglob("*")):
            if not p.is_file():
                continue
            s = str(p.relative_to(VAULT))
            base = s[:-len(".gz")] if s.endswith(".json.gz") else s
            if base.endswith(".json") and _is_staging(base):
                try:
                    p.unlink()
                    n += 1
                except Exception:
                    pass
    print(json.dumps({"pruned_staging_files": n}))


def seal():
    if not LIVE.is_dir():
        print(json.dumps({"seal": "no-live-dir"}))
        return
    try:
        prev = json.loads(STATE.read_text()).get("sigs", {}) if STATE.exists() else {}
    except Exception:
        prev = {}
    sigs = {}
    tally = {}
    for p in sorted(LIVE.rglob("*.json")):
        if ".tmp" in p.name or p.name.startswith("."):
            continue
        rel = str(p.relative_to(LIVE))
        if _is_staging(rel):
            continue
        try:
            st = p.stat()
            sigs[rel] = [st.st_mtime, st.st_size]
        except Exception:
            continue
        cur, leg, _ = _vault_names(rel)
        if prev.get(rel) == sigs[rel] and (cur.exists() or leg.exists()):
            tally["unchanged"] = tally.get("unchanged", 0) + 1
            continue
        rows = _load_live(p)
        if rows is None:
            tally["unparseable-live"] = tally.get("unparseable-live", 0) + 1
            continue
        r = _seal_one(rel, rows)
        tally[r] = tally.get(r, 0) + 1
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps({"ts": time.time(), "seal_tally": tally, "sigs": sigs}))
    print(json.dumps({"sealed_rels": len(_vault_rels()), "tally": tally}))


def enforce():
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    kills = []
    restored = []
    shrunk = []
    for rel in _vault_rels():
        live = LIVE / rel
        sealed = _load_seal(rel)
        if sealed is None:
            continue
        live_rows = _load_live(live) if live.exists() else None
        bad = False
        why = ""
        if live_rows is None:
            bad, why = True, "live-unparseable-or-missing"
        elif len(live_rows) < len(sealed):
            bad, why = True, f"rows {len(sealed)}->{len(live_rows)}"
        if not bad:
            continue
        shrunk.append({"file": rel, "why": why})
        holders = _writable_fd_holders(live.resolve()) if live.exists() else []
        killed = []
        for h in holders:
            pid = h["pid"]
            cmd = _cmdline(pid)
            try:
                os.kill(pid, signal.SIGKILL)
                killed.append({"pid": pid, "cmd": cmd})
            except Exception as e:
                killed.append({"pid": pid, "cmd": cmd, "kill_fail": str(e)[:60]})
        try:
            live.parent.mkdir(parents=True, exist_ok=True)
            tmp = live.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(sealed))
            os.replace(tmp, live)
            restored.append(rel)
        except Exception as e:
            shrunk[-1]["restore_fail"] = str(e)[:100]
        kills.append({"ts": now, "file": rel, "why": why,
                      "sealed_rows": len(sealed), "killed": killed,
                      "restored": rel in restored})
    if kills:
        try:
            KILLS.parent.mkdir(parents=True, exist_ok=True)
            with open(KILLS, "a") as fh:
                for k in kills:
                    fh.write(json.dumps(k) + "\n")
        except Exception:
            pass
    print(json.dumps({"checked": True, "shrunk": len(shrunk), "killed_procs": sum(len(k["killed"]) for k in kills),
                      "restored": len(restored), "files": [s["file"] for s in shrunk][:10]}))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seal", action="store_true")
    ap.add_argument("--enforce", action="store_true")
    ap.add_argument("--prune-staging", action="store_true")
    a = ap.parse_args()
    if a.seal:
        return seal()
    if a.enforce:
        return enforce()
    if a.prune_staging:
        return prune_staging()
    ap.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
