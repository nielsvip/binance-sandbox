#!/usr/bin/env python3
"""
sweep_coordinator.py — 24/7 prioritized sweep coordinator for S1.

Runs forever. Managed by watchdog_sweep_s1.sh (pgrep -f sweep_coordinator).
Designed for the "millions of tests" backlog with:
  - Dedup by config hash (never re-run a seen config)
  - Hang detection + auto-advance (timeout per run)
  - Useless classification (sub-floor Sharpe → skip, never promote)
  - Ordered priority queue (CRITICAL > HIGH > MEDIUM > LOW > GENERATED)
  - Daily flaw list written to data/sweep_coordinator/flaws.json
  - All Sharpe through metrics_guard — NO LIES MANDATE enforced

VECTORIZED MODE: sets V8_USE_VEC_ALL=1 on every run so the 12 vec gate
modules in vec_paths/ short-circuit the engine's hot loop. This is the
"migrate to vectorized tests" mode per 2026-05-12 mandate.

PARITY NOTES (honest gaps between vec_engine_v1 and backtest_v8_engine):
  1. HedgeEngine: backtest_v8_engine runs full stateful HedgeEngine;
     vec_engine_v1 approximates via entry/exit gate checks. Real numbers
     come from backtest_v8_engine — always use that for promotion.
  2. PPL v2: 3-step partial-profit-lock requires sub-bar state;
     vec_engine_v1 approximates. backtest_v8_engine has the real impl.
  3. DELTA_ENGINE: vec_engine_v1 uses velocity proxy; real uses full delta tracker.
  4. Engine non-determinism: asyncio task ordering in backtest_v8_engine
     causes ±10-15% trade count drift across identical-input runs.
     vec_engine_v1 is deterministic (no asyncio). Task #34.
  WIRED in both (V8_USE_VEC_ALL=1): noloss_gate, augment_eligibility,
  emergency_brake, hedge_scan_gates, stale_mark, newborn_protect,
  cooldown_locks, protect_balance_overtrade, circuit_sharpe_gates,
  tradeable_state_gates, open_intent_size_gates, quarantine_strategy.

Usage:
  python3 sweep_coordinator.py --mode crypto --account ang --start 2022-01-01 \\
      --symbols BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,XRPUSDC
  python3 sweep_coordinator.py --mode tradier --account trb --start 2024-01-01
  python3 sweep_coordinator.py --status   # print queue/ledger stats and exit
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import metrics_guard  # noqa: E402 — NO-LIES mandate

BASE_PATH = Path(__file__).resolve().parent
# 2026-05-19 VEC-DISPATCH MANDATE — sweep_coordinator now dispatches v8_vec_sweep.py
# (pure-vectorized, 4-13x faster, deterministic). backtest_v8_engine.py is reserved
# for small-sample parity tests vs vec only — never the sweep workhorse.
# Per-arm wall-clock budget: ~minutes, not hours. Old slow-engine runs at 390s
# timed out without ever emitting a V8_RESULT line; the vec engine completes
# fast enough that the coord's silence-detector + heartbeat catches real hangs.
ENGINE_PATH = BASE_PATH / "v8_vec_sweep.py"
OVERRIDE_DIR = BASE_PATH / "data" / "sweep_overrides"
COORD_DIR = BASE_PATH / "data" / "sweep_coordinator"

# Queue sources (loaded in order, all merged into priority queue)
QUEUE_SOURCES = [
    BASE_PATH / "data" / "sweep_coordinator" / "queue.json",  # manual additions
    BASE_PATH / "data" / "v8_test_queue.json",                # outstanding tests
]

LEDGER_PATH = COORD_DIR / "ledger.jsonl"
IN_PROGRESS_PATH = COORD_DIR / "in_progress.json"
PROMOTIONS_PATH = COORD_DIR / "promotions.json"
FLAWS_PATH = COORD_DIR / "flaws.json"

PY_BIN = sys.executable

# Classification thresholds
HANG_TIMEOUT_S = 7200          # 2h hard safety-net — phase-aware silence catches most failures faster
PRE_SIM_SILENCE_S = 600        # 2026-05-19 raised 300→600 for v8_vec_sweep dispatch. NPZ load + first sym's 4-year vec loop (BTCUSDC 414k bars ≈ 215s) means the first V8_VEC_PROGRESS line can arrive up to ~5min after launch. 300s killed every full-universe run before it could heartbeat.
POST_SIM_SILENCE_S = 600       # 2026-05-19 raised 180→600 for v8_vec_sweep dispatch. v8_vec_sweep emits one V8_VEC_PROGRESS per (sym,side) cell; on full crypto universe (60 syms × 4yr × 2 sides) each cell takes ~200-400s, so the gap between heartbeats can exceed 180s. 600s lets long-bar cells finish while still catching real hangs (>10min silence).
SILENCE_TIMEOUT_S = PRE_SIM_SILENCE_S  # legacy alias; dynamic per phase inside run_test()
USELESS_POOL_SHARPE = 0.4      # below → USELESS (Discard/Noise tier) — raised from 0.3 per user mandate
DIAGNOSTIC_POOL_SHARPE = 0.5   # below → DIAGNOSTIC (sub-floor)
PROMOTE_POOL_SHARPE = 1.0      # above → add to promotions.json
SAMPLE_FLOOR_TRADES = 30       # below → DIAGNOSTIC regardless of Sharpe

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "GENERATED": 4}

# 2026-05-17 — Vec-aware knob guard.
# After 6 distinct cfg_hashes converged on pool_sharpe=0.1619/trades=86 because
# coordinator runs with V8_USE_VEC_ALL=1 (vec engine) but the flipped knobs
# were only read in the slow engine path. Load the curated vec-aware knob set
# and refuse to run any arm whose flipped knobs are NOT in this set.
# File maintained by `python3 tools/audit_vec_aware_knobs.py`.
# 2026-05-18 — path now relative to BASE_PATH (works on Mac + S1).
_VEC_AWARE_FILE = BASE_PATH / "data" / "vec_aware_knobs.txt"
_ENGINE_ONLY_FILE = BASE_PATH / "data" / "engine_only_knobs.txt"
_VEC_AWARE_CONTEXT = {"USDC_PREFERENCE_BLOCK_ENABLED"}  # ignored: coordinator/runner context flags

def _load_knob_list(path: Path) -> set:
    if not path.exists():
        return set()
    return {ln.strip() for ln in path.read_text().splitlines() if ln.strip() and not ln.startswith("#")}

def _load_vec_aware_knobs() -> set:
    return _load_knob_list(_VEC_AWARE_FILE)

def _load_engine_only_knobs() -> set:
    return _load_knob_list(_ENGINE_ONLY_FILE)

def _arm_is_vec_aware(overrides: dict, vec_aware: set, engine_only: set | None = None) -> tuple[bool, list, list]:
    """Returns (vec_safe, non_vec_keys, engine_only_keys).

    - non_vec_keys: flipped keys NOT in vec_aware (and not context) — refuse.
    - engine_only_keys: subset of non_vec_keys explicitly confirmed engine-only
      (read by backtest_v8_engine/ez_manage/tradier_manage but NOT by any vec_paths/*).
      Reported alongside for clearer diagnostics; refusal is the same either way.
    """
    flipped = set((overrides or {}).keys()) - _VEC_AWARE_CONTEXT
    if not flipped:
        return True, [], []  # control/baseline arm — no flips, always vec-safe
    non_vec = sorted(flipped - vec_aware)
    eo = sorted(set(non_vec) & (engine_only or set()))
    return (len(non_vec) == 0), non_vec, eo

# 2026-05-17 — Duplicate-result failsafe. After 6 distinct configs produced
# the byte-identical (pool_sharpe, trades, wins, losses) tuple, we add a
# post-run check: if the result matches a prior arm's signature, write a
# FLAW row and never run any other PENDING arm with the same dead-knob set.
def _result_signature(row: dict) -> tuple:
    ps = row.get("pool_sharpe")
    if ps is None:
        return None
    return (
        round(float(ps), 4),
        int(row.get("trades", 0) or 0),
        int(row.get("wins", 0) or 0),
        int(row.get("losses", 0) or 0),
        str(row.get("mode", "")),
        str(row.get("symbols", "")),
        str(row.get("start", "")),
    )

def _load_seen_signatures() -> dict:
    seen = {}
    if not LEDGER_PATH.exists():
        return seen
    with open(LEDGER_PATH) as fh:
        for line in fh:
            try:
                e = __import__("json").loads(line)
            except Exception:
                continue
            sig = _result_signature(e)
            if sig is None:
                continue
            seen.setdefault(sig, []).append((e.get("test_id"), e.get("cfg_hash"), tuple(sorted((e.get("config_changes") or {}).keys()))))
    return seen


# Env flags that activate vectorized gate modules in backtest_v8_engine
VEC_ENV = {
    "V8_USE_VEC_ALL": "1",       # routes 12 gate decisions through vec_paths modules
    "V8_SWEEP_MODE": "1",        # suppress per-trade logs
    "V8_RATE_GUARD_DISABLED": "1",
    "V8_BACKTEST_DISK_CACHE": "1",
    "USDC_PREFERENCE_BLOCK_ENABLED": "0",
    # 2026-05-21 20:42 — bypass v8_vec_sweep's per-account (sym, side) allowlist filter.
    # Coord sweeps test arbitrary universes that don't have to match the live account's
    # hand-picked tradeable_keys. Without this, --account ang --symbols BTC/ETH/SOL/XRP
    # produces tasks=0 (ang_long.json doesn't include those + ang_short.json is empty),
    # killing every crypto arm as USELESS with 0 trades.
    "V8_VEC_SWEEP_BYPASS_ACCT_FILTER": "1",
}

V8_RESULT_RE = re.compile(
    r"V8_RESULT:\s*"
    r"pool_sharpe=(?P<pool_sharpe>[-\d.]+)\s+"
    r"sym_sharpe=(?P<sym_sharpe>[-\d.]+)\s+"
    r"sharpe=(?P<sharpe_alias>[-\d.]+)\s+"
    r"gain_pct=(?P<gain_pct>[-+\d.]+)\s+"
    r"closes=(?P<closes>\d+)\s+"
    r"wins=(?P<wins>\d+)\s+"
    r"losses=(?P<losses>\d+)"
)
V8_RESULT_TRADIER_RE = re.compile(
    r"V8_RESULT:\s*"
    r"pool_sharpe=(?P<pool_sharpe>[-\d.]+)\s+sym_sharpe=(?P<sym_sharpe>[-\d.]+)\s+"
    r"sharpe=(?P<sharpe_alias>[-\d.]+)\s+"
    r"pnl=(?P<pnl>[-+\d.]+)\s+"
    r"trades=(?P<trades>\d+)\s+"
    r"wins=(?P<wins>\d+)\s+"
    r"losses=(?P<losses>\d+)"
)
V8_RESULT_LIVE_RE = re.compile(
    r"V8_RESULT_LIVE:\s*pool_sharpe=(?P<pool_sharpe>[-\d.]+)"
    r"(?:.*gain_pct=(?P<gain_pct>[-+\d.]+))?"
    r"(?:.*pnl=(?P<pnl>[-+\d.]+))?"
    r"(?:.*closes=(?P<closes>\d+))?"
    r"(?:.*wins=(?P<wins>\d+))?"
    r"(?:.*losses=(?P<losses>\d+))?"
)
V8_LIVE_CLOSES_RE = re.compile(r"V8_RESULT_LIVE:.*closes=(?P<closes>\d+)")
V8_NEW_SWITCHES_RE = re.compile(r"V8_NEW_SWITCHES:.*dd_min=(?P<dd_min>[-\d.]+)")
V8_INIT_RE = re.compile(
    r"V8_INIT_HEARTBEAT:.*stores_loaded=(?P<n_syms>\d+)\s+"
    r"skipped_mode=(?P<n_mode>\d+)\s+skipped_stale=(?P<n_stale>\d+)"
)

_STOP = threading.Event()

def _sig_handler(sig, frame):
    print("[coord] SIGTERM/SIGINT received — finishing current run then stopping.", flush=True)
    _STOP.set()

signal.signal(signal.SIGTERM, _sig_handler)
signal.signal(signal.SIGINT, _sig_handler)


# ─── Ledger helpers ───────────────────────────────────────────────────────────

def _cfg_hash(overrides: dict) -> str:
    canonical = json.dumps(overrides, sort_keys=True, separators=(",", ":"))
    return hashlib.md5(canonical.encode()).hexdigest()[:12]

def _load_seen_hashes(mode: str = "") -> set:
    seen: set = set()
    if not LEDGER_PATH.exists():
        return seen
    with LEDGER_PATH.open() as f:
        for line in f:
            try:
                row = json.loads(line)
                row_mode = row.get("mode", "")
                if mode and row_mode and row_mode != mode:
                    continue
                row_status = row.get("status", "")
                if row_status in ("PARSE_ERROR", "NO_RESULT"):
                    continue
                h = row.get("cfg_hash")
                if h:
                    seen.add(h)
            except Exception:
                pass
    return seen

def _append_ledger(row: dict) -> None:
    COORD_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open("a") as f:
        f.write(json.dumps(row) + "\n")

def _load_promotions() -> list:
    if PROMOTIONS_PATH.exists():
        try:
            return json.loads(PROMOTIONS_PATH.read_text())
        except Exception:
            return []
    return []

def _save_promotions(items: list) -> None:
    PROMOTIONS_PATH.write_text(json.dumps(items, indent=2))


# ─── Queue loading ────────────────────────────────────────────────────────────

def _load_queue_source(path: Path, mode: str) -> list:
    if not path.exists():
        return []
    try:
        items = json.loads(path.read_text())
        if not isinstance(items, list):
            return []
        result = []
        for item in items:
            if not isinstance(item, dict):
                continue
            if item.get("status", "PENDING") != "PENDING":
                continue
            item_mode = item.get("mode", mode)
            if item_mode != mode:
                continue
            result.append(item)
        return result
    except Exception as e:
        print(f"[coord] warn: failed to load {path}: {e}", flush=True)
        return []

def _build_priority_queue(mode: str, seen_hashes: set) -> list:
    """Load all queue sources, dedup by hash, sort by priority."""
    raw: list = []
    for src in QUEUE_SOURCES:
        items = _load_queue_source(src, mode)
        for item in items:
            overrides = item.get("config_changes", item.get("overrides", {}))
            h = _cfg_hash(overrides)
            if h in seen_hashes:
                continue
            item["_hash"] = h
            item["_source"] = str(src)
            raw.append(item)

    raw.sort(key=lambda x: (
        PRIORITY_ORDER.get(x.get("priority", "MEDIUM"), 2),
        x.get("created", ""),
    ))
    return raw


# ─── Runner ───────────────────────────────────────────────────────────────────

def _wait_for_memory(threshold_pct: float = 90.0) -> None:
    try:
        import psutil
        while True:
            vm = psutil.virtual_memory()
            if vm.percent < threshold_pct:
                break
            print(f"[coord] memory {vm.percent:.0f}% > {threshold_pct:.0f}% — waiting 30s", flush=True)
            time.sleep(30)
    except ImportError:
        pass

def _run_one(
    item: dict,
    mode: str,
    account: str,
    start: str,
    symbols: str,
    capital: float,
    npz_dir: str,
) -> dict:
    """Run one queue item via backtest_v8_engine subprocess. Returns ledger row."""
    label = item.get("test_id", item.get("label", "unnamed"))
    overrides = dict(item.get("config_changes", item.get("overrides", {})))
    overrides["USDC_PREFERENCE_BLOCK_ENABLED"] = False
    h = item.get("_hash") or _cfg_hash(overrides)

    # 2026-05-19 — still write an override JSON for audit / parity-recheck
    # workflows (humans + tools/v8_live_vs_backtest_compare.py read these).
    # The actual config flip is now passed via `--override KEY=VAL` args to
    # v8_vec_sweep.py — V8_OVERRIDE_FILE was only honoured by the slow engine.
    override_path = OVERRIDE_DIR / f"coord_{label}_{h}.json"
    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    override_path.write_text(json.dumps(overrides, indent=2))

    # v8_vec_sweep CLI:
    #   --mode {crypto,tradier} --account <acc> --symbols A,B,C --start YYYY-MM-DD
    #   --override KEY=VAL  (repeatable)  --no-history  --workers N
    # NOT accepted: --capital, --npz-dir (vec engine reads npz from
    # backtest_v8/indicators/ directly; capital is irrelevant to per-trade
    # returns which is what pool_sharpe is computed from).
    # 2026-05-19 PARALLEL: v8_vec_sweep run_sweep now supports ProcessPool.
    # Coord defaults workers via SWEEP_COORD_WORKERS (env), else 4 — same as
    # gr_dcbb path. Passing --workers 0 lets the engine auto-pick cpu-2 capped 8.
    _v8_workers = int(os.environ.get("SWEEP_COORD_WORKERS", "4"))
    cmd = [PY_BIN, "-u", str(ENGINE_PATH), "--mode", mode, "--account", account,
           "--start", start, "--no-history", "--workers", str(_v8_workers)]
    if symbols:
        cmd += ["--symbols", symbols]
    for k, v in overrides.items():
        # render bools/numbers/strings as KEY=VAL; coord refuses unknown knobs
        # upstream via the vec-aware guard, so any KEY here is one the vec
        # engine should understand.
        cmd += ["--override", f"{k}={v}"]

    env = os.environ.copy()
    # V8_OVERRIDE_FILE kept for tools that read it post-hoc, but vec engine
    # ignores it — the source-of-truth flip is in --override args above.
    env["V8_OVERRIDE_FILE"] = str(override_path)
    for k, v in VEC_ENV.items():
        env[k] = v
    # Allow sub-floor DIAGNOSTIC rows (the coord handles classification);
    # without this the vec engine exits non-zero on small samples and the
    # coord ledger captures only NO_RESULT.
    env["V8_VEC_ALLOW_DIAGNOSTIC"] = "1"

    t0 = time.time()
    match = None
    match_tradier = None
    max_dd_pct = None
    n_syms = None
    live_sharpe = None
    live_closes = 0
    live_gain_pct = 0.0
    live_wins = 0
    live_losses = 0
    live_match_line = None
    killed = False
    _kill_reason = "HANG_timeout"
    stdout_tail: list = []
    stderr_lines: list = []

    IN_PROGRESS_PATH.parent.mkdir(parents=True, exist_ok=True)
    IN_PROGRESS_PATH.write_text(json.dumps({"test_id": label, "hash": h, "started_utc": datetime.now(timezone.utc).isoformat()}))

    try:
        import queue as _q_mod
        proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)

        def _read_stderr():
            for ln in proc.stderr:
                stderr_lines.append(ln.rstrip())

        _stdout_q: _q_mod.Queue = _q_mod.Queue()

        def _read_stdout():
            try:
                for ln in proc.stdout:
                    _stdout_q.put(ln.rstrip())
            except Exception:
                pass
            finally:
                _stdout_q.put(None)

        t_err = threading.Thread(target=_read_stderr, daemon=True)
        t_out = threading.Thread(target=_read_stdout, daemon=True)
        t_err.start()
        t_out.start()

        _sim_started = False
        _silence_phase = "pre_sim"
        _silence_limit = PRE_SIM_SILENCE_S
        while True:
            try:
                line = _stdout_q.get(timeout=_silence_limit)
            except _q_mod.Empty:
                print(f"[coord] ⚡ {label} silent {_silence_limit}s ({_silence_phase}) — killing engine", flush=True)
                proc.kill()
                killed = True
                _kill_reason = f"SILENT_{_silence_phase}_{_silence_limit}s"
                break
            if line is None:
                break
            stdout_tail.append(line)
            if len(stdout_tail) > 40:
                stdout_tail.pop(0)
            # Phase change: slow engine prints "starting simulation"; vec
            # engine prints "V8_VEC_PROGRESS:" once per (sym,side) cell — the
            # first such line means the sweep is past NPZ load and looping.
            if not _sim_started and ("starting simulation" in line or "V8_VEC_PROGRESS:" in line):
                _sim_started = True
                _silence_phase = "post_sim"
                _silence_limit = POST_SIM_SILENCE_S
                print(f"[coord] ↳ {label} simulation started — silence window tightened to {POST_SIM_SILENCE_S}s", flush=True)
            m = V8_INIT_RE.search(line)
            if m:
                try:
                    n_syms = int(m["n_syms"])
                except Exception:
                    pass
            m = V8_NEW_SWITCHES_RE.search(line)
            if m:
                try:
                    max_dd_pct = abs(float(m["dd_min"]))
                except Exception:
                    pass
            m = V8_RESULT_LIVE_RE.search(line)
            if m:
                try:
                    live_sharpe = float(m["pool_sharpe"])
                    live_match_line = line
                    try:
                        live_gain_pct = float(m["gain_pct"] or m["pnl"] or 0)
                    except Exception:
                        pass
                    try:
                        live_wins = int(m["wins"] or 0)
                    except Exception:
                        pass
                    try:
                        live_losses = int(m["losses"] or 0)
                    except Exception:
                        pass
                except Exception:
                    pass
            m = V8_LIVE_CLOSES_RE.search(line)
            if m:
                try:
                    live_closes = int(m["closes"])
                except Exception:
                    pass
            m = V8_RESULT_RE.search(line)
            if m:
                match = m
            m = V8_RESULT_TRADIER_RE.search(line)
            if m:
                match_tradier = m
            if time.time() - t0 > HANG_TIMEOUT_S:
                proc.kill()
                killed = True
                _kill_reason = "HANG_timeout"
                break
        proc.wait(timeout=30)
        t_err.join(timeout=5)
        t_out.join(timeout=5)
    except Exception as e:
        killed = True
        _kill_reason = f"subprocess_error"
        print(f"[coord] subprocess error for {label}: {e}", flush=True)

    elapsed = time.time() - t0
    if IN_PROGRESS_PATH.exists():
        IN_PROGRESS_PATH.unlink()

    if killed:
        if live_sharpe is not None and live_closes >= SAMPLE_FLOOR_TRADES:
            live_trades = live_wins + live_losses if (live_wins + live_losses) > 0 else live_closes
            print(f"[coord] ⚡ {label} HANG but live result captured — pool_sharpe={live_sharpe:.4f} closes={live_closes} [DIAGNOSTIC]", flush=True)
            row = {
                "test_id": label,
                "cfg_hash": h,
                "status": "DIAGNOSTIC",
                "verdict": f"DIAGNOSTIC_live_partial_{_kill_reason}_{elapsed:.0f}s",
                "pool_sharpe": round(live_sharpe, 4),
                "sym_sharpe": round(live_sharpe, 4),
                "gain_pct": round(live_gain_pct, 4),
                "trades": live_trades,
                "wins": live_wins,
                "losses": live_losses,
                "elapsed_s": round(elapsed, 1),
                "mode": mode,
                "symbols": symbols,
                "start": start,
                "config_changes": overrides,
                "source": item.get("_source", ""),
                "live_line": live_match_line,
                "ts_utc": datetime.now(timezone.utc).isoformat(),
            }
        else:
            row = {
                "test_id": label,
                "cfg_hash": h,
                "status": "HANG",
                "verdict": f"{_kill_reason}_{elapsed:.0f}s",
                "elapsed_s": round(elapsed, 1),
                "mode": mode,
                "symbols": symbols,
                "start": start,
                "config_changes": overrides,
                "source": item.get("_source", ""),
                "ts_utc": datetime.now(timezone.utc).isoformat(),
            }
        return row

    mx = match or match_tradier
    if mx is None:
        row = {
            "test_id": label,
            "cfg_hash": h,
            "status": "NO_RESULT",
            "verdict": "NO_RESULT_no_V8_RESULT_line",
            "elapsed_s": round(elapsed, 1),
            "mode": mode,
            "symbols": symbols,
            "start": start,
            "config_changes": overrides,
            "stdout_tail": stdout_tail[-10:],
            "ts_utc": datetime.now(timezone.utc).isoformat(),
        }
        return row

    def _grp(m, *names, default=0):
        for name in names:
            try:
                v = m[name]
                if v is not None:
                    return v
            except IndexError:
                pass
        return str(default)

    try:
        pool_sharpe = float(mx["pool_sharpe"])
        sym_sharpe = float(mx["sym_sharpe"])
        gain_pct = float(_grp(mx, "gain_pct", "pnl", default=0))
        trades = int(_grp(mx, "closes", "trades", default=0))
        wins = int(mx["wins"])
        losses = int(mx["losses"])
    except Exception as e:
        row = {
            "test_id": label, "cfg_hash": h, "status": "PARSE_ERROR",
            "verdict": f"PARSE_ERROR_{e}", "elapsed_s": round(elapsed, 1),
            "mode": mode, "symbols": symbols, "start": start,
            "config_changes": overrides, "ts_utc": datetime.now(timezone.utc).isoformat(),
        }
        return row

    n_syms_val = n_syms or len([s for s in symbols.split(",") if s]) or 1
    years_val = (datetime.now(timezone.utc).year - int(start[:4])) + (datetime.now().month - 1) / 12.0
    years_val = max(years_val, 0.1)
    avg_gain_trade = gain_pct / trades if trades > 0 else 0.0
    gain_per_yr = gain_pct / years_val
    gain_sym_yr = gain_pct / n_syms_val / years_val

    # Classify
    if pool_sharpe >= PROMOTE_POOL_SHARPE and trades >= SAMPLE_FLOOR_TRADES:
        status = "PROMOTE"
        verdict = f"PUBLISHABLE_pool_sharpe_{pool_sharpe:.4f}"
    elif pool_sharpe >= DIAGNOSTIC_POOL_SHARPE and trades >= SAMPLE_FLOOR_TRADES:
        status = "DIAGNOSTIC"
        verdict = f"DIAGNOSTIC_pool_sharpe_{pool_sharpe:.4f}"
    elif pool_sharpe < USELESS_POOL_SHARPE or trades < SAMPLE_FLOOR_TRADES:
        status = "USELESS"
        verdict = f"USELESS_pool_sharpe_{pool_sharpe:.4f}_trades_{trades}"
    else:
        status = "DIAGNOSTIC"
        verdict = f"DIAGNOSTIC_pool_sharpe_{pool_sharpe:.4f}"

    row = {
        "test_id": label,
        "cfg_hash": h,
        "status": status,
        "verdict": verdict,
        "pool_sharpe": round(pool_sharpe, 6),
        "sym_sharpe": round(sym_sharpe, 6),
        "avg_gain_trade": round(avg_gain_trade, 4),
        "gain_per_yr": round(gain_per_yr, 4),
        "gain_sym_yr": round(gain_sym_yr, 6),
        "trades": trades,
        "max_dd_pct": round(max_dd_pct, 4) if max_dd_pct is not None else None,
        "n_syms": n_syms_val,
        "years": round(years_val, 2),
        "wins": wins,
        "losses": losses,
        "wr_pct": round(100 * wins / (wins + losses), 2) if (wins + losses) > 0 else 0.0,
        "elapsed_s": round(elapsed, 1),
        "mode": mode,
        "symbols": symbols,
        "start": start,
        "config_changes": {k: v for k, v in overrides.items() if k != "USDC_PREFERENCE_BLOCK_ENABLED"},
        "description": item.get("description", ""),
        "source": item.get("_source", ""),
        "ts_utc": datetime.now(timezone.utc).isoformat(),
    }
    return row


# ─── Main loop ────────────────────────────────────────────────────────────────

def _print_stats(mode: str) -> None:
    counts: dict = {}
    if LEDGER_PATH.exists():
        with LEDGER_PATH.open() as f:
            for line in f:
                try:
                    r = json.loads(line)
                    s = r.get("status", "?")
                    counts[s] = counts.get(s, 0) + 1
                except Exception:
                    pass
    total = sum(counts.values())
    print(f"[coord] ledger: {total} entries — {counts}", flush=True)
    promotions = _load_promotions()
    print(f"[coord] promotions: {len(promotions)}", flush=True)


def _write_flaws(mode: str) -> None:
    """Analyse recent ledger, write flaw summary for daily audit agent."""
    rows: list = []
    if LEDGER_PATH.exists():
        with LEDGER_PATH.open() as f:
            for line in f:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        return
    hangs = [r for r in rows if r.get("status") == "HANG"]
    no_result = [r for r in rows if r.get("status") == "NO_RESULT"]
    useless = [r for r in rows if r.get("status") == "USELESS"]
    promotions_count = len([r for r in rows if r.get("status") == "PROMOTE"])
    # Dead-knob analysis: group by changed param, check if result varies
    param_results: dict = {}
    for r in rows:
        changes = r.get("config_changes", {})
        ps = r.get("pool_sharpe", 0.0)
        for k, v in changes.items():
            if k == "USDC_PREFERENCE_BLOCK_ENABLED":
                continue
            param_results.setdefault(k, []).append((v, ps))
    dead_knobs = []
    for k, vps in param_results.items():
        if len(vps) < 3:
            continue
        sharpes = [ps for _, ps in vps]
        if max(sharpes) - min(sharpes) < 0.05:
            dead_knobs.append({"param": k, "n_tests": len(vps), "sharpe_range": round(max(sharpes) - min(sharpes), 4)})
    flaw_report = {
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "mode": mode,
        "total_runs": len(rows),
        "hang_count": len(hangs),
        "no_result_count": len(no_result),
        "useless_count": len(useless),
        "promotions_count": promotions_count,
        "hang_test_ids": [r.get("test_id") for r in hangs[-10:]],
        "no_result_test_ids": [r.get("test_id") for r in no_result[-10:]],
        "dead_knobs": dead_knobs,
        "action_items": [],
    }
    if len(hangs) > 3:
        flaw_report["action_items"].append(f"INVESTIGATE: {len(hangs)} hangs — check memory / NPZ availability")
    if dead_knobs:
        flaw_report["action_items"].append(f"DEAD_KNOBS: {[d['param'] for d in dead_knobs]} — verify wiring in engine")
    if promotions_count == 0 and len(rows) > 20:
        flaw_report["action_items"].append("NO_PROMOTIONS: queue may be exhausted or threshold too high")
    FLAWS_PATH.write_text(json.dumps(flaw_report, indent=2))
    print(f"[coord] flaw report written: {len(flaw_report['action_items'])} action items", flush=True)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--mode", choices=["crypto", "tradier"], default="crypto")
    ap.add_argument("--account", default="ang")
    ap.add_argument("--start", default="2022-01-01", help="Backtest start date (crypto: 2022-01-01)")
    ap.add_argument("--symbols", default="BTCUSDC,ETHUSDC,SOLUSDC,XRPUSDC,ADAUSDC,BNBUSDC,AVAXUSDC,LINKUSDC")
    ap.add_argument("--capital", type=float, default=10000.0)
    ap.add_argument("--npz-dir", default="")
    ap.add_argument("--sleep-empty", type=int, default=120, help="Seconds to sleep when queue is empty")
    ap.add_argument("--mem-throttle", type=float, default=88.0, help="Pause if RAM%% exceeds this")
    ap.add_argument("--hang-timeout", type=int, default=0, help="Override HANG_TIMEOUT_S (0=use module default)")
    ap.add_argument("--silence-timeout", type=int, default=0, help="Override SILENCE_TIMEOUT_S (0=use module default)")
    ap.add_argument("--status", action="store_true", help="Print stats and exit")
    args = ap.parse_args()

    global HANG_TIMEOUT_S, SILENCE_TIMEOUT_S, PRE_SIM_SILENCE_S, POST_SIM_SILENCE_S
    if args.hang_timeout > 0:
        HANG_TIMEOUT_S = args.hang_timeout
    if args.silence_timeout > 0:
        SILENCE_TIMEOUT_S = args.silence_timeout
        PRE_SIM_SILENCE_S = args.silence_timeout
        POST_SIM_SILENCE_S = max(POST_SIM_SILENCE_S, min(args.silence_timeout // 2, 600))

    COORD_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        _print_stats(args.mode)
        return

    print(f"[coord] starting — mode={args.mode} account={args.account} start={args.start}", flush=True)
    print(f"[coord] symbols={args.symbols}", flush=True)
    print(f"[coord] vectorized mode: V8_USE_VEC_ALL=1 (12 vec gates active)", flush=True)
    print(f"[coord] hang_timeout={HANG_TIMEOUT_S}s  silence_pre_sim={PRE_SIM_SILENCE_S}s  silence_post_sim={POST_SIM_SILENCE_S}s  useless_threshold={USELESS_POOL_SHARPE}  promote_threshold={PROMOTE_POOL_SHARPE}", flush=True)

    last_flaw_write = 0.0
    vec_aware = _load_vec_aware_knobs()
    engine_only = _load_engine_only_knobs()
    _vec_mtime = _VEC_AWARE_FILE.stat().st_mtime if _VEC_AWARE_FILE.exists() else 0.0
    _eo_mtime = _ENGINE_ONLY_FILE.stat().st_mtime if _ENGINE_ONLY_FILE.exists() else 0.0
    print(f"[coord] vec_aware_knobs loaded: {len(vec_aware)} (from {_VEC_AWARE_FILE})", flush=True)
    print(f"[coord] engine_only_knobs loaded: {len(engine_only)} (from {_ENGINE_ONLY_FILE})", flush=True)
    seen_signatures = _load_seen_signatures()
    print(f"[coord] result signatures from prior ledger: {len(seen_signatures)}", flush=True)

    while not _STOP.is_set():
        # Hot-reload vec-aware set if the file changed on disk (so newly wired
        # gates become valid without restarting the coordinator).
        if _VEC_AWARE_FILE.exists():
            cur_mtime = _VEC_AWARE_FILE.stat().st_mtime
            if cur_mtime > _vec_mtime:
                vec_aware = _load_vec_aware_knobs()
                _vec_mtime = cur_mtime
                print(f"[coord] vec_aware_knobs reloaded: {len(vec_aware)}", flush=True)
        if _ENGINE_ONLY_FILE.exists():
            cur_eo_mtime = _ENGINE_ONLY_FILE.stat().st_mtime
            if cur_eo_mtime > _eo_mtime:
                engine_only = _load_engine_only_knobs()
                _eo_mtime = cur_eo_mtime
                print(f"[coord] engine_only_knobs reloaded: {len(engine_only)}", flush=True)
        seen = _load_seen_hashes(args.mode)
        queue = _build_priority_queue(args.mode, seen)

        if not queue:
            if time.time() - last_flaw_write > 3600:
                _write_flaws(args.mode)
                last_flaw_write = time.time()
            print(f"[coord] queue empty — sleeping {args.sleep_empty}s", flush=True)
            _STOP.wait(args.sleep_empty)
            continue

        item = queue[0]
        label = item.get("test_id", "unnamed")
        h = item["_hash"]
        overrides = item.get("config_changes", item.get("overrides", {}))
        priority = item.get("priority", "MEDIUM")
        # ──────────────────────────────────────────────────────────────────
        # 2026-05-17 DEAD-KNOB GUARD — refuse to run an arm whose flipped
        # knobs aren't in the vec-aware set. V8_USE_VEC_ALL=1 routes through
        # vec_paths/* which only reads vec-aware knobs; flipping non-vec
        # knobs produces baseline-identical results (6-arm cluster wasted
        # 7.5h of compute 2026-05-17). The guard fails-open if vec_aware is
        # empty (file missing) — so coordinator still works if list isn't
        # provisioned.
        # ──────────────────────────────────────────────────────────────────
        vec_ok, non_vec_keys, engine_only_keys = (True, [], [])
        if vec_aware:
            vec_ok, non_vec_keys, engine_only_keys = _arm_is_vec_aware(overrides, vec_aware, engine_only)
        if not vec_ok:
            tag = "ENGINE_ONLY" if engine_only_keys else "NON_VEC"
            print(f"[coord] ⊗ {label} [{priority}] hash={h} BLOCKED [{tag}] non_vec_keys={non_vec_keys} engine_only={engine_only_keys}", flush=True)
            _append_ledger({
                "test_id": label, "cfg_hash": h, "status": "BLOCKED_NON_VEC_KNOBS",
                "verdict": f"BLOCKED_{tag}_keys={','.join(non_vec_keys)}",
                "non_vec_keys": non_vec_keys,
                "engine_only_keys": engine_only_keys,
                "config_changes": overrides,
                "mode": args.mode, "elapsed_s": 0.0,
                "ts_utc": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
            })
            continue
        print(f"[coord] ▶ {label} [{priority}] hash={h} overrides={list(overrides.keys())}", flush=True)

        _wait_for_memory(args.mem_throttle)

        row = _run_one(
            item=item, mode=args.mode, account=args.account,
            start=args.start, symbols=args.symbols,
            capital=args.capital, npz_dir=args.npz_dir,
        )

        # 2026-05-17 DUPLICATE-RESULT DETECTION — post-run failsafe. If the
        # result tuple matches a previously-completed run with a DIFFERENT
        # cfg_hash, tag the verdict and surface to flaws log. Don't block
        # the current run (already done), but the operator sees the pattern.
        _row_sig = _result_signature(row)
        if _row_sig is not None:
            prior = seen_signatures.get(_row_sig, [])
            distinct = [(tid, ch, keys) for tid, ch, keys in prior if ch != row.get("cfg_hash") and tid != row.get("test_id")]
            if distinct:
                prior_label = distinct[0][0]
                prior_keys = distinct[0][2]
                cur_keys = tuple(sorted((overrides or {}).keys()))
                row["verdict"] = f"DUPLICATE_OF_{prior_label}_" + (row.get("verdict") or "")
                row["duplicate_of"] = prior_label
                row["duplicate_diff_keys"] = sorted(set(cur_keys) ^ set(prior_keys))
                print(f"[coord] ⚠ DUPLICATE_RESULT {label} → matches {prior_label} (signature={_row_sig})", flush=True)
            seen_signatures.setdefault(_row_sig, []).append((row.get("test_id"), row.get("cfg_hash"), tuple(sorted((overrides or {}).keys()))))

        _append_ledger(row)

        status = row.get("status", "?")
        verdict = row.get("verdict", "")
        elapsed = row.get("elapsed_s", 0)
        ps = row.get("pool_sharpe", "-")
        trades = row.get("trades", "-")
        print(f"[coord] ✓ {label} → {status} | pool_sharpe={ps} trades={trades} | {verdict} | {elapsed:.0f}s", flush=True)

        if status == "PROMOTE":
            promotions = _load_promotions()
            promotions.append(row)
            _save_promotions(promotions)
            mandatory = (
                f"pool_sharpe={row['pool_sharpe']:.4f} | "
                f"sym_sharpe={row.get('sym_sharpe', '?'):.4f} | "
                f"avg_gain_trade={row.get('avg_gain_trade', '?'):.4f}%/trade | "
                f"gain_per_yr={row.get('gain_per_yr', '?'):.2f}%/yr | "
                f"gain_sym_yr={row.get('gain_sym_yr', '?'):.4f}%/sym/yr | "
                f"trades={row['trades']} | "
                f"dd={row.get('max_dd_pct', '?')}% | "
                f"n_syms={row.get('n_syms', '?')} | "
                f"years={row.get('years', '?')}"
            )
            print(f"[coord] 🏆 PROMOTION: {label}", flush=True)
            print(f"[coord]    {mandatory}", flush=True)

        if time.time() - last_flaw_write > 3600:
            _write_flaws(args.mode)
            last_flaw_write = time.time()

        if _STOP.is_set():
            break

    print("[coord] stopped cleanly.", flush=True)


if __name__ == "__main__":
    main()
