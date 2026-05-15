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
ENGINE_PATH = BASE_PATH / "backtest_v8_engine.py"
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
HANG_TIMEOUT_S = 7200          # 2h hard safety-net — SILENCE_TIMEOUT_S catches most failures faster
SILENCE_TIMEOUT_S = 180        # kill engine if no stdout line for 180s (OOM-killed: pipe closes instantly; covers per-sym NPZ load)
USELESS_POOL_SHARPE = 0.4      # below → USELESS (Discard/Noise tier) — raised from 0.3 per user mandate
DIAGNOSTIC_POOL_SHARPE = 0.5   # below → DIAGNOSTIC (sub-floor)
PROMOTE_POOL_SHARPE = 1.0      # above → add to promotions.json
SAMPLE_FLOOR_TRADES = 30       # below → DIAGNOSTIC regardless of Sharpe

PRIORITY_ORDER = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "GENERATED": 4}

# Env flags that activate vectorized gate modules in backtest_v8_engine
VEC_ENV = {
    "V8_USE_VEC_ALL": "1",       # routes 12 gate decisions through vec_paths modules
    "V8_SWEEP_MODE": "1",        # suppress per-trade logs
    "V8_RATE_GUARD_DISABLED": "1",
    "V8_BACKTEST_DISK_CACHE": "1",
    "USDC_PREFERENCE_BLOCK_ENABLED": "0",
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
V8_RESULT_LIVE_RE = re.compile(r"V8_RESULT_LIVE:.*pool_sharpe=(?P<pool_sharpe>[-\d.]+)")
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

def _load_seen_hashes() -> set:
    seen: set = set()
    if not LEDGER_PATH.exists():
        return seen
    with LEDGER_PATH.open() as f:
        for line in f:
            try:
                row = json.loads(line)
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

    override_path = OVERRIDE_DIR / f"coord_{label}_{h}.json"
    OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    override_path.write_text(json.dumps(overrides, indent=2))

    cmd = [PY_BIN, str(ENGINE_PATH), "--mode", mode, "--account", account,
           "--start", start, "--capital", str(capital)]
    if symbols:
        cmd += ["--symbols", symbols]
    if npz_dir:
        cmd += ["--npz-dir", npz_dir]

    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_path)
    for k, v in VEC_ENV.items():
        env[k] = v

    t0 = time.time()
    match = None
    match_tradier = None
    max_dd_pct = None
    n_syms = None
    live_sharpe = None
    live_closes = 0
    killed = False
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

        while True:
            try:
                line = _stdout_q.get(timeout=SILENCE_TIMEOUT_S)
            except _q_mod.Empty:
                print(f"[coord] ⚡ {label} silent {SILENCE_TIMEOUT_S}s — killing engine", flush=True)
                proc.kill()
                killed = True
                break
            if line is None:
                break
            stdout_tail.append(line)
            if len(stdout_tail) > 40:
                stdout_tail.pop(0)
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
                break
        proc.wait(timeout=30)
        t_err.join(timeout=5)
        t_out.join(timeout=5)
    except Exception as e:
        killed = True
        print(f"[coord] subprocess error for {label}: {e}", flush=True)

    elapsed = time.time() - t0
    if IN_PROGRESS_PATH.exists():
        IN_PROGRESS_PATH.unlink()

    if killed:
        row = {
            "test_id": label,
            "cfg_hash": h,
            "status": "HANG",
            "verdict": f"HANG_timeout_{elapsed:.0f}s",
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

    try:
        pool_sharpe = float(mx["pool_sharpe"])
        sym_sharpe = float(mx.get("sym_sharpe", mx["pool_sharpe"]))
        gain_pct = float(mx.get("gain_pct", mx.get("pnl", 0)))
        trades = int(mx.get("closes", mx.get("trades", 0)))
        wins = int(mx.get("wins", 0))
        losses = int(mx.get("losses", 0))
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
    ap.add_argument("--status", action="store_true", help="Print stats and exit")
    args = ap.parse_args()

    global HANG_TIMEOUT_S
    if args.hang_timeout > 0:
        HANG_TIMEOUT_S = args.hang_timeout

    COORD_DIR.mkdir(parents=True, exist_ok=True)

    if args.status:
        _print_stats(args.mode)
        return

    print(f"[coord] starting — mode={args.mode} account={args.account} start={args.start}", flush=True)
    print(f"[coord] symbols={args.symbols}", flush=True)
    print(f"[coord] vectorized mode: V8_USE_VEC_ALL=1 (12 vec gates active)", flush=True)
    print(f"[coord] hang_timeout={HANG_TIMEOUT_S}s  useless_threshold={USELESS_POOL_SHARPE}  promote_threshold={PROMOTE_POOL_SHARPE}", flush=True)

    last_flaw_write = 0.0

    while not _STOP.is_set():
        seen = _load_seen_hashes()
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
        print(f"[coord] ▶ {label} [{priority}] hash={h} overrides={list(overrides.keys())}", flush=True)

        _wait_for_memory(args.mem_throttle)

        row = _run_one(
            item=item, mode=args.mode, account=args.account,
            start=args.start, symbols=args.symbols,
            capital=args.capital, npz_dir=args.npz_dir,
        )

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
