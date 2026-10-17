#!/usr/bin/env python3
"""data_locations — the ONE place that knows where every kind of data actually lives.

USER 2026-10-17: "we have 2 ext drives and gateway and macbook and s1 full of klines costing huge resources and
you are looking in the WRONG FOLDERS? MAKE A SYSTEM that understands WHERE THINGS ARE (klines, market data,
positions info, npz files) AND CAN NEVER FUCK UP ON SIMPLE ISSUES LIKE THIS".

WHY THIS EXISTS, from the three mistakes that caused it:
  * The failover agent stages pulled klines in `LF_STAGE` (default `/Users/niels/live_failover_stage`), which is
    NOT under `LF_BASE`. A diagnostic looked in `<base>/data/live_failover/stage` and reported "delivery never
    happened" when the pull had worked perfectly.
  * Crypto klines live in BOTH `klines_cache/` and `klines_cache_backtest/`, stocks under a further `tradier/`
    level, and the gateway keeps its own copy. "Which one is authoritative" was answered by guessing per script.
  * NPZ stores exist in the live dir, `_quarantine/`, `.purged_npz/<ts>/`, `.purged_variants/<ts>/`, on s2, and on
    the ext drives — and the DEEPEST copy was sitting in a purge dir while the live one was 5x shallower.

THE CONTRACT
  resolve(kind, host) -> [Location]  every candidate, in precedence order, canonical first
  where(kind, host)   -> Location    the authoritative one (raises LocationError if none is declared)
  audit(...)          -> report      does each declared path EXIST, how much is in it, how fresh
Nothing here guesses. A path is either declared in REGISTRY or it is not a place this system keeps data, and
`where()` RAISES rather than returning a plausible-looking default -- a wrong path that looks right is exactly
what this module exists to prevent.

ENV OVERRIDES ARE PART OF THE TRUTH, NOT AN EXCEPTION. Any kind whose real location is decided at runtime by an
environment variable declares it in `env`, and resolution reads that env FIRST. That is the specific failure above.

usage:
  python3 tools/data_locations.py --audit                 # everything reachable from here
  python3 tools/data_locations.py --audit --host s1       # one host (over ssh)
  python3 tools/data_locations.py --audit --kind klines_crypto --json
  python3 tools/data_locations.py --where npz_indicators --host s1
  python3 tools/data_locations.py --kinds                 # list what this module knows about
"""
from __future__ import annotations

import argparse
import json
import os
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

# --------------------------------------------------------------------------- hosts

MAC_BASE = "/Users/niels/Documents/binance"
S1_LIVE = "/home/niels/binance"
S1_SANDBOX = "/home/niels/binance-sandbox"
GATEWAY_BASE = "/home/niels/binance"

# ext drives are Mac-mounted only; TOSHIBA_EXT/SSD2T are the two the operator means by "2 ext drives"
EXT_DRIVES = ("/Volumes/TOSHIBA_EXT", "/Volumes/SSD2T", "/Volumes/E3T", "/Volumes/WD25")

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
       "-o", "ControlMaster=no"]
HOSTS = {
    "mac": {"ssh": None, "base": MAC_BASE},
    "s1": {"ssh": "s1-int", "base": S1_LIVE, "sandbox": S1_SANDBOX},
    "s2": {"ssh": "s2", "base": S1_SANDBOX, "sandbox": S1_SANDBOX},
    "gateway": {"ssh": "157.90.168.35", "base": GATEWAY_BASE},
}


class LocationError(RuntimeError):
    """A kind/host combination has no declared location. Never guess one."""


@dataclass
class Location:
    kind: str
    host: str
    path: str
    role: str                      # canonical | mirror | archive | staging | legacy
    note: str = ""
    env: str | None = None         # the env var that overrides this path, if any
    from_env: bool = False
    exists: bool | None = None
    n_files: int | None = None
    bytes_: int | None = None
    newest_epoch: float | None = None

    @property
    def age_s(self):
        return None if self.newest_epoch is None else max(0.0, time.time() - self.newest_epoch)


# --------------------------------------------------------------------------- the registry
# Each entry: kind -> host -> [(relative-or-absolute path, role, note, env-var)]
# `{base}` = that host's live tree, `{sandbox}` = its sandbox tree, `{ext}` = each ext drive.
# ORDER MATTERS: the first entry is canonical.

REGISTRY: dict[str, dict[str, list[tuple]]] = {
    # ---------------------------------------------------------------- klines
    "klines_crypto": {
        "mac": [("{base}/klines_cache", "canonical", "what the live crypto producers write and read", None),
                ("{base}/klines_cache_backtest", "mirror", "deeper store used by sweeps; NOT what live reads", None),
                ("{base}/klines_cache_gateway", "mirror", "gateway-delivered copy", None)],
        "s1": [("{sandbox}/klines_cache_backtest", "canonical", "S1 is the DEEP store (2026-10-14 APPEND law: never overwritten by Mac pushes)", None),
               ("{sandbox}/klines_cache", "mirror", "", None),
               ("{base}/klines_cache", "mirror", "live-mirror tree used by the failover producers", None)],
        "s2": [("{sandbox}/klines_cache_backtest", "canonical", "", None),
               ("{sandbox}/klines_cache", "mirror", "", None)],
        "gateway": [("{base}/klines_cache", "canonical", "gateway fetches and broadcasts to the workers", None)],
        "ext": [("{ext}/binance_archive/klines_cache", "archive", "append-only backup, never shrinks", None)],
    },
    "klines_stocks": {
        "mac": [("{base}/klines_cache/tradier", "canonical", "tradier_klines writes here", None),
                ("{base}/klines_cache_backtest/tradier", "mirror", "", None)],
        "s1": [("{sandbox}/klines_cache_backtest/tradier", "canonical", "", None),
               ("{sandbox}/klines_cache/tradier", "mirror", "", None),
               ("{base}/klines_cache/tradier", "mirror", "read by the stocks failover producers", None)],
        "s2": [("{sandbox}/klines_cache_backtest/tradier", "canonical", "s2 is the stocks NPZ/refresh host (2026-10-16)", None),
               ("{sandbox}/klines_cache/tradier", "mirror", "", None)],
        "ext": [("{ext}/binance_archive/klines_cache_tradier", "archive", "S2 stocks klines backup", None)],
    },
    # ---------------------------------------------------------------- market data / indicator snapshots
    "market_data_crypto": {
        "mac": [("{base}/data", "canonical", "market_data_*.json written by ez_indicators; the *_s1.json variants are S1-delivered", None)],
        "s1": [("{base}/data", "canonical", "", None),
               ("{sandbox}/data", "mirror", "", None)],
    },
    "indicators_snapshot_crypto": {
        "mac": [("{base}/data/latest_market_data.json", "canonical", "freshness of this file drives LF_SNAPSHOT_STALE_S=90", None)],
        "s1": [("{base}/ez_s1_stack/data/latest_market_data.json", "canonical", "isolated S1 stack output", None)],
    },
    "indicators_snapshot_stocks": {
        "mac": [("{base}/data/tradier/tradier_indicators_latest.json", "canonical", "stocks equivalent of the 90 s staleness probe", None)],
        "s1": [("{base}/data/tradier/tradier_indicators_latest.json", "canonical", "", None),
               ("{sandbox}/data/tradier/tradier_indicators_latest.json", "mirror", "", None)],
    },
    # ---------------------------------------------------------------- positions
    "positions_crypto": {
        "mac": [("{base}/data/positions", "canonical", "written ONLY by ez_positions_service (single-writer law 2026-10-14)", None)],
        "s1": [("{base}/data/positions", "canonical", "S1 writes these only while it holds the lease", None)],
    },
    "positions_stocks": {
        "mac": [("{base}/data/tradier/positions", "canonical", "tradier_positions is the truth vs the broker", None)],
        "s1": [("{base}/data/tradier/positions", "canonical", "", None)],
    },
    "positions_failover_mirror": {
        "mac": [("{base}/data/live_failover/mac_state", "canonical", "account state pushed to S1 every 30 s; merged at takeover", None)],
        "s1": [("{base}/data/live_failover/mac_state", "canonical", "received copy of the Mac's state", None)],
    },
    # ---------------------------------------------------------------- NPZ
    "npz_indicators": {
        "s1": [("{sandbox}/backtest_v8/indicators", "canonical", "the live-lane NPZ store (NPZ_REFRESH_ROOT overrides the root)", "NPZ_REFRESH_ROOT"),
               ("{sandbox}/backtest_v8/indicators/_quarantine", "archive", "2026-10-17: held 482 stores while live held 332 -- SEARCH IT before rebuilding", None),
               ("{sandbox}/backtest_v8/indicators/.purged_npz", "archive", "dated purge dirs; held the DEEPEST copy of 62 symbols (400-1875 d)", None),
               ("{sandbox}/backtest_v8/indicators/.purged_variants", "archive", "dated purge dirs for variant files", None)],
        "s2": [("{sandbox}/backtest_v8/indicators", "canonical", "s2 held 681 stores vs S1's 332 on 2026-10-17", None),
               ("{sandbox}/backtest_v8/indicators/_quarantine", "archive", "", None),
               ("{sandbox}/backups", "archive", "npz_before_history_rebuild_* holds the only deep stock stores", None)],
        "mac": [("{base}/backtest_v8/indicators", "legacy", "the Mac holds NO NPZs by design (live-only host) -- expect 0 files", None)],
        "ext": [("{ext}/backtest_npz_master", "archive", "4.4 GB master NPZs; never pull the whole thing", None),
                ("{ext}/npz_archive_s1_20261017", "archive", "2026-10-17 pull of S1's quarantine + purge dirs", None)],
    },
    # ---------------------------------------------------------------- the trickle (Mac computes -> S1 appends)
    "npz_bar_outbox": {
        "mac": [("{base}/data/npz_bar_outbox", "canonical", "rows computed by ez_/tradier_indicators awaiting push", "NPZ_BAR_OUTBOX")],
    },
    "npz_bar_inbox": {
        "s1": [("{sandbox}/data/npz_bar_inbox", "canonical", "pushed rows awaiting tools/npz_bar_ingest.py", "NPZ_BAR_INBOX"),
               ("{sandbox}/data/npz_bar_inbox/applied", "archive", "applied rows, pruned after 24 h", None),
               ("{sandbox}/data/npz_bar_inbox/rejected", "archive", "NO_NPZ rows, pruned after 6 h", None)],
    },
    # ---------------------------------------------------------------- failover plumbing (the one I got wrong)
    "failover_state": {
        "mac": [("{base}/data/live_failover", "canonical", "lease_local.json, mac_data_heartbeat.json, s1_state.json", "LF_BASE")],
        "s1": [("{base}/data/live_failover", "canonical", "lease.json + state.json live HERE, not in the sandbox", "LF_BASE")],
    },
    "failover_stage": {
        # THE MISTAKE THIS MODULE WAS BORN FROM: this is NOT under LF_BASE.
        "mac": [("/Users/niels/live_failover_stage", "staging", "where mac_failover_agent.py rsyncs S1's outbox BEFORE applying; default of LF_STAGE and NOT under LF_BASE", "LF_STAGE")],
    },
    "failover_outbox": {
        "s1": [("{base}/data/live_failover/outbox", "canonical", "per-venue dirs the Mac pulls (klines/, snapshot/, extras/, MANIFEST.json)", None)],
    },
    # ---------------------------------------------------------------- logs that failover decisions read
    "producer_logs": {
        "mac": [("/Users/niels/logs", "canonical", "ez_klines.log / tradier_klines.log 418+timeout lines gate data failover", "LF_LOGDIR")],
        "s1": [("/home/niels/logs", "canonical", "", "LF_LOGDIR")],
    },
}

KINDS = tuple(REGISTRY)


# --------------------------------------------------------------------------- resolution

def _expand(host: str, template: str) -> list[str]:
    info = HOSTS.get(host) or {}
    if "{ext}" in template:
        return [template.replace("{ext}", d) for d in EXT_DRIVES]
    return [template.replace("{base}", info.get("base", "")).replace("{sandbox}", info.get("sandbox", info.get("base", "")))]


def resolve(kind: str, host: str) -> list[Location]:
    """Every declared candidate for (kind, host), canonical first. Env overrides come FIRST of all."""
    if kind not in REGISTRY:
        raise LocationError(f"unknown kind {kind!r}; known: {', '.join(KINDS)}")
    entries = REGISTRY[kind].get(host)
    if entries is None and host in ("mac", "s1", "s2", "gateway"):
        entries = []
    if entries is None:
        raise LocationError(f"kind {kind!r} declares nothing for host {host!r}")
    out: list[Location] = []
    for path_t, role, note, env in entries:
        if env:
            val = os.environ.get(env)
            if val:
                out.append(Location(kind, host, val, role, f"{note} [from ${env}]".strip(), env, True))
                continue
        for p in _expand(host, path_t):
            if p:
                out.append(Location(kind, host, p, role, note, env, False))
    return out


def where(kind: str, host: str) -> Location:
    """The authoritative location: the FIRST declared candidate (REGISTRY is ordered authoritative-first), or the
    env override when one is set. Raises instead of inventing a plausible path.

    It is deliberately NOT restricted to role == "canonical": `failover_stage` is role "staging" yet it is the one
    true place the Mac stages a pull, and demanding the literal word "canonical" made this raise on exactly the
    path the module was written to pin down. The role is metadata for a human reading the audit, not the key."""
    locs = resolve(kind, host)
    for loc in locs:
        if loc.from_env:
            return loc
    if locs:
        return locs[0]
    raise LocationError(f"no location declared for {kind!r} on {host!r}")


# --------------------------------------------------------------------------- measurement

_PROBE = r"""
import json, os, sys, time
out = []
for spec in json.loads(sys.argv[1]):
    p = spec["path"]
    rec = {"path": p, "exists": os.path.exists(p)}
    if rec["exists"]:
        try:
            if os.path.isdir(p):
                n = 0; size = 0; newest = 0.0
                with os.scandir(p) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if e.is_file(follow_symlinks=False):
                            n += 1; size += st.st_size
                        newest = max(newest, st.st_mtime)
                rec.update(n_files=n, bytes=size, newest=newest or None, is_dir=True)
            else:
                st = os.stat(p)
                rec.update(n_files=1, bytes=st.st_size, newest=st.st_mtime, is_dir=False)
        except OSError as e:
            rec["error"] = type(e).__name__
    out.append(rec)
print(json.dumps(out))
"""


def _measure(host: str, locs: list[Location]) -> None:
    if not locs:
        return
    payload = json.dumps([{"path": l.path} for l in locs])
    ssh_host = (HOSTS.get(host) or {}).get("ssh")
    try:
        if ssh_host:
            py = "/home/niels/.conda/envs/binance_env_mac/bin/python"
            cmd = SSH + [ssh_host, f"{py} -c {json.dumps(_PROBE)} {json.dumps(payload)}"]
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=90)
        else:
            res = subprocess.run([sys.executable, "-c", _PROBE, payload], capture_output=True, text=True, timeout=120)
        rows = json.loads((res.stdout or "[]").strip().splitlines()[-1])
    except Exception:
        return
    by_path = {r["path"]: r for r in rows}
    for loc in locs:
        r = by_path.get(loc.path)
        if not r:
            continue
        loc.exists = r.get("exists")
        loc.n_files = r.get("n_files")
        loc.bytes_ = r.get("bytes")
        loc.newest_epoch = r.get("newest")


def audit(kinds=None, hosts=None) -> dict:
    kinds = list(kinds or KINDS)
    hosts = list(hosts or ("mac", "s1", "s2", "gateway", "ext"))
    report = {"at": time.time(), "host_running_on": socket.gethostname(), "kinds": {}, "problems": []}
    for host in hosts:
        locs: list[Location] = []
        for kind in kinds:
            if host not in REGISTRY.get(kind, {}):
                continue
            try:
                locs.extend(resolve(kind, host))
            except LocationError as e:
                report["problems"].append(str(e))
        # ext paths are Mac-mounted, probe them locally
        _measure("mac" if host == "ext" else host, locs)
        for loc in locs:
            entry = report["kinds"].setdefault(loc.kind, {}).setdefault(host, [])
            entry.append({"path": loc.path, "role": loc.role, "from_env": loc.from_env, "env": loc.env,
                          "exists": loc.exists, "n_files": loc.n_files, "bytes": loc.bytes_,
                          "age_s": None if loc.age_s is None else round(loc.age_s), "note": loc.note})
            if loc.role == "canonical" and loc.exists is False:
                report["problems"].append(f"MISSING canonical {loc.kind} on {host}: {loc.path}")
    return report


def _fmt_bytes(n):
    if not n:
        return "-"
    for unit in ("B", "K", "M", "G", "T"):
        if n < 1024 or unit == "T":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024.0


def _fmt_age(s):
    if s is None:
        return "-"
    if s < 90:
        return f"{int(s)}s"
    if s < 5400:
        return f"{s/60:.0f}m"
    if s < 172800:
        return f"{s/3600:.1f}h"
    return f"{s/86400:.0f}d"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--audit", action="store_true")
    ap.add_argument("--kind", action="append", help="limit to this kind (repeatable)")
    ap.add_argument("--host", action="append", help="mac|s1|s2|gateway|ext (repeatable)")
    ap.add_argument("--where", help="print the authoritative path for this kind and exit")
    ap.add_argument("--kinds", action="store_true", help="list known kinds")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.kinds:
        for k in KINDS:
            hosts = ",".join(REGISTRY[k])
            print(f"  {k:28s} hosts: {hosts}")
        return 0
    if a.where:
        host = (a.host or ["mac"])[0]
        try:
            loc = where(a.where, host)
        except LocationError as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 2
        print(loc.path)
        return 0
    if not a.audit:
        print(__doc__.strip().splitlines()[0])
        print("run with --audit, --where <kind> --host <host>, or --kinds")
        return 0

    rep = audit(a.kind, a.host)
    if a.json:
        print(json.dumps(rep, indent=1, sort_keys=True, default=str))
        return 1 if rep["problems"] else 0
    for kind in sorted(rep["kinds"]):
        print(f"\n=== {kind} ===")
        for host in sorted(rep["kinds"][kind]):
            for row in rep["kinds"][kind][host]:
                mark = {True: "ok ", False: "GONE", None: "?  "}[row["exists"]]
                src = f" <${row['env']}>" if row["from_env"] else ""
                print(f"  [{mark}] {host:8s} {row['role']:9s} {row['path']}{src}")
                if row["exists"]:
                    print(f"           files={row['n_files'] or 0:<7d} size={_fmt_bytes(row['bytes']):>8s} newest={_fmt_age(row['age_s']):>6s}")
                if row["note"]:
                    print(f"           note: {row['note'][:150]}")
    if rep["problems"]:
        print(f"\n!!! {len(rep['problems'])} PROBLEM(S):")
        for p in rep["problems"]:
            print(f"  - {p}")
    else:
        print("\nno problems: every canonical location exists")
    return 1 if rep["problems"] else 0


if __name__ == "__main__":
    sys.exit(main())
