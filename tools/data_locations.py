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

# Positions are NOT in data/positions. They are per-account files `<base>/<acct>/{long,short}_positions.json`
# (also augmented_/reduced_/options_positions.json), which is what live_failover_state_check.side_files() reads.
# The first version of this registry guessed `data/positions` and the audit immediately reported 5 missing
# canonical paths -- that is the module working: a declared path that does not exist is an error, not a shrug.
CRYPTO_ACCOUNTS = ("ang", "fin", "flz", "inf", "men")
STOCKS_ACCOUNTS = ("tra", "trb", "trc")

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=15", "-o", "StrictHostKeyChecking=accept-new",
       "-o", "ControlMaster=no"]
HOSTS = {
    "mac": {"ssh": None, "base": MAC_BASE},
    "s1": {"ssh": "s1-int", "base": S1_LIVE, "sandbox": S1_SANDBOX},
    "s2": {"ssh": "s2", "base": S1_SANDBOX, "sandbox": S1_SANDBOX},
    "gateway": {"ssh": "157.90.168.35", "base": GATEWAY_BASE},
    "s5": {"ssh": "s5", "base": "/home/niels"},
    "s6": {"ssh": "s6", "base": "/home/niels"},
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
    n_links: int | None = None
    n_dirs: int | None = None

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
        "s1": [("{base}/ez_s1_stack/data/latest_market_data.json", "canonical", "isolated S1 stack output; this - NOT S1's ~/binance/data/latest_market_data.json, which was 4 h old on 2026-10-22 - is what tools/mac_market_data_pull.py pulls", None)],
        "s2": [("/home/niels/binance/ez_s2_stack/data/latest_market_data.json", "canonical", "second fallback producer stack (s2-crypto-producers.service, 2026-10-22); pulled by tools/mac_market_data_pull.py", None)],
    },
    "indicators_snapshot_stocks": {
        "mac": [("{base}/data/tradier/tradier_indicators_latest.json", "canonical", "stocks equivalent of the 90 s staleness probe", None)],
        "s1": [("{base}/data/tradier/tradier_indicators_latest.json", "canonical", "", None),
               ("{sandbox}/data/tradier/tradier_indicators_latest.json", "mirror", "", None)],
    },
    # ---------------------------------------------------------------- positions
    "positions_crypto": {
        "mac": [("{base}/{crypto_acct}", "canonical", "<acct>/{long,short}_positions.json — written ONLY by ez_positions_service (single-writer law 2026-10-14)", None)],
        "s1": [("{base}/{crypto_acct}", "canonical", "S1 writes these only while it holds the lease; stale copies here are staging leftovers, NOT the takeover source", None)],
    },
    "positions_stocks": {
        "mac": [("{base}/{stocks_acct}", "canonical", "<acct>/{long,short,options}_positions.json — tradier_positions is the truth vs the broker", None)],
        "s1": [("{base}/{stocks_acct}", "canonical", "", None)],
    },
    "positions_failover_mirror": {
        "mac": [("{base}/data/live_failover", "canonical", "the Mac SENDS state (it has no mac_state dir of its own); S1 holds the received copy", None)],
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
    # ------------------------------------------------- self-learning corpora (USER 2026-10-18)
    # "you can leave older backtest results on external drive but document clearly where v15_pilot's self learning
    # should find it". Every corpus v15_pilot's learning path reads is declared here, WITH the env var that moves it,
    # because the one that has no env var (the brain's delta logs) is the one archiving can silently starve.
    # RETENTION CONTRACT, measured 2026-10-18:
    #   * brain_corpus_delta_logs — tools/v15_brain.py globs ~/v15_run*/[progress/]v15_delta_log/*.jsonl and filters by
    #     ROW ts, not file mtime, over the last V15_BRAIN_SINCE_D (default 14) days; rebuilt daily 03:20 on S2 by
    #     tools/v15_brain_export.sh, models pushed to S1. On S2 this is 9.7 GB in 372 files of which only 0.1 GB is
    #     outside the 14-day window: the ACTIVE run dir holds 9.61 GB and must NOT be archived. Archive a run dir only
    #     once it is finished AND no row in it is younger than the window, and point a longer retrain at the archive
    #     with `v15_brain build --glob '<archive>/v15_run*/progress/v15_delta_log/*.jsonl'`.
    #   * eval_cache — the corpus tools/v17_priors_mine.py mines into causal priors. S1's 29.7 GB copy was deleted
    #     2026-10-18; the surviving native rows and the artefact rebuild live on TOSHIBA_EXT (see below).
    "brain_corpus_delta_logs": {
        "s2": [("/home/niels/v15_run*/progress/v15_delta_log", "canonical", "largest delta-log volume; the daily brain rebuild runs HERE (v15_brain_export.sh, 03:20)", None),
               ("/home/niels/v15_run*/v15_delta_log", "canonical", "older layout, same glob set", None)],
        "s1": [("/home/niels/v15_run*/progress/v15_delta_log", "canonical", "", None),
               ("/home/niels/v15_run*/v15_delta_log", "canonical", "", None)],
        "mac": [("/Users/niels/v15_run*/progress/v15_delta_log", "mirror", "only if a run was driven from the Mac", None)],
        "ext": [("{ext}/fleet_archive/*/v15_run*/progress/v15_delta_log", "archive", "finished runs only; reachable by v15_brain --glob, NOT by its default globs", None)],
    },
    "brain_models": {
        "s2": [("{sandbox}/data/brain", "canonical", "brain_<CAT_SIDE>.json x4, written atomically by v15_brain_export.sh", None)],
        "s1": [("{sandbox}/data/brain", "mirror", "rsynced from S2 after every successful rebuild", None)],
    },
    "eval_cache": {
        "mac": [("{base}/data/eval_cache_v1.db", "canonical", "this host's memoized real-engine evals", "V15_EVAL_CACHE_DB")],
        "s1": [("{sandbox}/data/eval_cache_v1.db", "canonical", "DELETED 2026-10-18 (29.7 GB, 927,777 rows, cold 33 h, champion env V15_EVAL_CACHE=0); rebuilt rows live on TOSHIBA_EXT", "V15_EVAL_CACHE_DB")],
        "s2": [("{sandbox}/data/eval_cache_v1.db", "canonical", "", "V15_EVAL_CACHE_DB"),
               ("/home/niels/eval_archive/eval_cache_v1_20261014.zst", "archive", "481 MB zstd -> 45.58 GB, 1,539,007 rows, window 2026-10-13 02:42..10-14 19:05", None),
               ("/home/niels/v15_bench/runs/*/*/eval_cache.db", "mirror", "per-side gate/bench caches, same schema; 14 dbs = 166,110 unique rows", None)],
        "ext": [("{ext}/binance_archive/eval_cache/eval_cache_v1_master.db", "archive", "the MASTER: native rows only (archive + bench + mac + E3T), real 40-hex keys", None),
                ("{ext}/binance_archive/eval_cache/eval_cache_rebuilt_v1.db", "archive", "artefact rebuild by tools/eval_cache_rebuild.py: metrics verbatim from the delta logs, overrides replayed; keys prefixed rebuilt1: so they can NEVER mis-hit the live cache", None),
                ("/Volumes/E3T/mac_backup_20261014/data/eval_cache_v1.db", "legacy", "657 rows, the pre-2026-10-18 'existing one on ext drive'; merged into the master", None)],
    },
    # ---------------------------------------------------------------- V18 experience store (work item E2, 2026-10-20)
    # tools/v18_experience.py: append-only shards per cat_side per month (experience_<CAT_SIDE>_<YYYYMM>.db) + experience_index.db.
    # Location rule in the tool: sticky existing store, else first real ext mount with >= 50 GB free, else the Mac boot disk.
    "experience_store": {
        "mac": [("{base}/data/experience", "canonical", "experience_<CAT_SIDE>_<YYYYMM>.db x4 cat_sides + experience_index.db (watermarks, proofs, colds); read via tools/v18_experience.py", "V18_EXPERIENCE_ROOT")],
        "s2": [("/home/niels/v18_scratch/shards", "mirror", "shards built ON S2 from rebuilt_v1.db (and S2 delta logs), rsynced to the Mac store; staging only", None)],
        "ext": [("{ext}/binance_archive/experience", "archive", "the tool's preferred home once an ext mount has >= 50 GB free; not created while TOSHIBA_EXT has 7.5 GB", "V18_EXPERIENCE_ROOT")],
    },
    # Cold zstd copies (+ <zst>.manifest.json with sha256) of eval caches whose rows are proven in the experience store.
    "eval_cache_cold": {
        "mac": [("{base}/data/eval_cache_cold", "canonical", "zstd -19 --long=27 + manifest of the staged bench caches and the S2 live-cache snapshot; reclaim needs the zst on a REAL ext mount", None)],
        "ext": [("{ext}/binance_archive/eval_cache_cold", "archive", "target for the reclaim step (copy the Mac zst + manifest here, re-verify sha256)", None)],
    },
    "priors_causal": {
        "s1": [("{sandbox}/data/causal/v17_priors.json", "canonical", "read by tools/v17_priors.load() inside v15_pilot", "V17_PRIORS_PATH")],
        "s2": [("{sandbox}/data/causal/v17_priors.json", "canonical", "", "V17_PRIORS_PATH")],
        "mac": [("{base}/data/causal/v17_priors.json", "mirror", "", "V17_PRIORS_PATH")],
    },
    "per_trade_returns": {
        "mac": [("{base}/data/per_trade_returns", "canonical", "daily *_<day>.jsonl mined by tools/v15_causal_learner.py", None)],
        "s1": [("{sandbox}/data/per_trade_returns", "canonical", "", None)],
    },
    "causal_learner_db": {
        "s1": [("{sandbox}/data/causal_learner.db", "canonical", "2.2 GB; built from per_trade_returns", None)],
    },
    "combo_library": {
        "s1": [("{sandbox}/data/v17_combo_library.sqlite", "canonical", "76k combos searched by tools/v17_combo_engine", "V17_COMBO_LIB")],
        "s2": [("{sandbox}/data/v17_combo_library.sqlite", "canonical", "", "V17_COMBO_LIB")],
    },
    "knowledge_graph": {
        "s1": [("{sandbox}/data/encyclopedia_v2/graph.json", "canonical", "tools/v15_graph_search", None)],
        "s2": [("{sandbox}/data/encyclopedia_v2/graph.json", "canonical", "", None)],
    },
    "endgame_knowledge": {
        "s1": [("{sandbox}/data/endgame/endgame_knowledge_fleet.json", "canonical", "tools/v15_endgame_knowledge_merge.load_ev, merged --pull --push hourly :23", "V15_ENDGAME_KNOWLEDGE")],
        "s2": [("{sandbox}/data/endgame/endgame_knowledge_fleet.json", "canonical", "", "V15_ENDGAME_KNOWLEDGE")],
    },
    "backtest_results_archive": {
        "ext": [("{ext}/fleet_archive", "archive", "per-host offloads of finished runs (S1_offload_<stamp>/ ...) with archive.log", None),
                ("{ext}/binance_archive", "archive", "long-lived archives: klines_archive, eval_cache, script_vault, per_sym_store_complete.db", None),
                ("{ext}/v15_mega_progress_mirror", "archive", "MEGA_{crypto,stocks}_jump boards", None),
                ("{ext}/s1_daily_reports_20261013", "archive", "per-day S1 report trees", None)],
    },
    "rebuild_scratch_s5": {
        "s5": [("{base}/rebuild_scratch_s5", "staging", "USER 2026-10-22: scratch until S5/S6 deletion after the weekend of 2026-10-24; everything here must be on an ext drive or proven duplicate before deletion; s2_run33/ s2_eval_archive/ manifests/", None)],
    },
    "rebuild_scratch_s6": {
        "s6": [("{base}/rebuild_scratch_s6", "staging", "USER 2026-10-22: scratch until S5/S6 deletion after the weekend of 2026-10-24; everything here must be on an ext drive or proven duplicate before deletion; s1_data_archive/ s1_backups/ manifests/", None)],
    },
}

KINDS = tuple(REGISTRY)


# --------------------------------------------------------------------------- resolution

def _expand(host: str, template: str) -> list[str]:
    info = HOSTS.get(host) or {}
    if "{ext}" in template:
        return [template.replace("{ext}", d) for d in EXT_DRIVES]
    base = info.get("base", "")
    sandbox = info.get("sandbox", base)
    resolved = template.replace("{base}", base).replace("{sandbox}", sandbox)
    if "{crypto_acct}" in resolved:
        return [resolved.replace("{crypto_acct}", a) for a in CRYPTO_ACCOUNTS]
    if "{stocks_acct}" in resolved:
        return [resolved.replace("{stocks_acct}", a) for a in STOCKS_ACCOUNTS]
    return [resolved]


def resolve(kind: str, host: str) -> list[Location]:
    """Every declared candidate for (kind, host), canonical first. Env overrides come FIRST of all."""
    if kind not in REGISTRY:
        raise LocationError(f"unknown kind {kind!r}; known: {', '.join(KINDS)}")
    entries = REGISTRY[kind].get(host)
    if entries is None and host in ("mac", "s1", "s2", "gateway", "s5", "s6"):
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
                # Count SYMLINKS and SUBDIRS too, and say so. Counting only regular files reported
                # indicators/_quarantine as "6 files" when it holds 482 entries (the NPZ adopter hardlinks/symlinks
                # them in) and .purged_npz as "0" when it holds dated SUBDIRS full of stores -- an audit that
                # undercounts is worse than no audit, because it reads as "nothing there".
                n = links = dirs = 0; size = 0; newest = 0.0
                with os.scandir(p) as it:
                    for e in it:
                        try:
                            st = e.stat(follow_symlinks=False)
                        except OSError:
                            continue
                        if e.is_symlink():
                            links += 1
                            size += st.st_size
                        elif e.is_dir(follow_symlinks=False):
                            dirs += 1
                        else:
                            n += 1; size += st.st_size
                        newest = max(newest, st.st_mtime)
                rec.update(n_files=n, n_links=links, n_dirs=dirs, bytes=size, newest=newest or None, is_dir=True)
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
            # base64 the probe AND its payload: passing either through an ssh command line as quoted JSON got it
            # mangled by the remote shell, which is why every remote host first audited as "?" -- a registry that
            # cannot see the hosts it describes is worth nothing.
            import base64
            src = _PROBE.replace("sys.argv[1]", repr(payload))
            blob = base64.b64encode(src.encode()).decode()
            py = "/home/niels/.conda/envs/binance_env_mac/bin/python"
            remote = f"{py} -c 'import base64;exec(base64.b64decode(\"{blob}\").decode())'"
            res = subprocess.run(SSH + [ssh_host, remote], capture_output=True, text=True, timeout=120)
        else:
            res = subprocess.run([sys.executable, "-c", _PROBE, payload], capture_output=True, text=True, timeout=180)
        text = (res.stdout or "").strip()
        if not text:
            for loc in locs:
                loc.note = (loc.note + f" [probe failed: {(res.stderr or '').strip()[:60]}]").strip()
            return
        rows = json.loads(text.splitlines()[-1])
    except Exception as exc:
        for loc in locs:
            loc.note = (loc.note + f" [probe error: {type(exc).__name__}]").strip()
        return
    by_path = {r["path"]: r for r in rows}
    for loc in locs:
        r = by_path.get(loc.path)
        if not r:
            continue
        loc.exists = r.get("exists")
        loc.n_files = r.get("n_files")
        loc.n_links = r.get("n_links")
        loc.n_dirs = r.get("n_dirs")
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
                          "exists": loc.exists, "n_files": loc.n_files, "n_links": loc.n_links,
                          "n_dirs": loc.n_dirs, "bytes": loc.bytes_,
                          "age_s": None if loc.age_s is None else round(loc.age_s), "note": loc.note})
            if loc.role == "canonical" and loc.exists is False:
                report["problems"].append(f"MISSING canonical {loc.kind} on {host}: {loc.path}")
            elif loc.role == "canonical" and loc.exists is None:
                # UNVERIFIED IS NOT OK. A probe that could not run leaves exists=None, and reporting that as
                # "no problems" is the failure mode this module exists to kill: silence reading as success.
                report["problems"].append(f"UNVERIFIED canonical {loc.kind} on {host}: {loc.path} ({loc.note[-90:] or 'probe did not run'})")
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
    ap.add_argument("--host", action="append", help="mac|s1|s2|s5|s6|gateway|ext (repeatable)")
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
                    extra = ""
                    if row.get("n_links"):
                        extra += f" links={row['n_links']}"
                    if row.get("n_dirs"):
                        extra += f" subdirs={row['n_dirs']}"
                    print(f"           files={row['n_files'] or 0:<7d} size={_fmt_bytes(row['bytes']):>8s} newest={_fmt_age(row['age_s']):>6s}{extra}")
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
