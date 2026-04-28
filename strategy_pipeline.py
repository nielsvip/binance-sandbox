#!/usr/bin/env python3
"""
strategy_pipeline.py — Automated strategy evaluation pipeline.

Flow per strategy:
  REGISTERED → TIER1_RUNNING → TIER1_PASS/FAIL
                                    ↓ PASS
                             TIER2_RUNNING → TIER2_PASS/FAIL
                                                 ↓ PASS
                                          LIVE_CANDIDATE (requires user approval)
                                                 ↓ --apply
                                          APPLIED
  Any FAIL → USELESS (marks config with # PIPELINE_USELESS_YYYYMMDD)

Tier-1: v8_test_queue A/B on 4 symbols × ~2yr (fast viability check)
Tier-2: backtest_v8_engine on 48 symbols × 4yr (real validation, all 5 metrics)

Usage:
  python3 strategy_pipeline.py --status            # show all strategy states
  python3 strategy_pipeline.py --run-tier1         # queue + launch pending tier-1 tests
  python3 strategy_pipeline.py --run-tier2         # queue + launch pending tier-2 tests
  python3 strategy_pipeline.py --collect           # collect results, advance states
  python3 strategy_pipeline.py --apply NAME        # apply LIVE_CANDIDATE to config
  python3 strategy_pipeline.py --discard NAME      # force USELESS + mark config
  python3 strategy_pipeline.py --full              # --collect + --run-tier1 + --run-tier2
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

BASE = Path(__file__).parent
STATE_FILE = BASE / "data" / "strategy_pipeline_state.json"
RESULTS_DIR = BASE / "data" / "test_queue_results"
QUEUE_CRYPTO = Path("/tmp/v8_test_queue_pipeline_crypto.json")
QUEUE_TRADIER = Path("/tmp/v8_test_queue_pipeline_tradier.json")

PYTHON_LOCAL = "/opt/anaconda3/envs/binance_env/bin/python"
PYTHON_S1 = "/home/niels/.conda/envs/binance_env/bin/python"
PYTHON_S2 = "/home/niels/miniconda3/envs/binance_env/bin/python"
ENGINE = BASE / "backtest_v8_engine.py"

# ── Thresholds ────────────────────────────────────────────────────────────────
TIER1_MIN_DELTA = 0.03       # ON must beat OFF by at least this in Sharpe
TIER1_MIN_SHARPE = 0.10      # absolute Sharpe floor for the winner arm
TIER2_MIN_POOL_SHARPE = 1.0  # per CLAUDE.md trash floor
TIER2_MIN_TRADES = 1440      # 48 syms × 30 trades each
TIER2_MAX_DD_PCT = 40.0      # max acceptable drawdown

# ── Strategy Registry ─────────────────────────────────────────────────────────
# Each entry defines what to test (ON vs OFF), which config file, which mode,
# and any blockers (e.g. "needs_precompute").
STRATEGIES = [
    # ── Already wired in v8_quick_engine, A/B queued ─────────────────────
    {"name": "LH_HL_FILTER",          "param": "LH_HL_FILTER_ENABLED",         "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "LH_HL_FILTER_TRADIER",  "param": "LH_HL_FILTER_ENABLED",         "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "STDEV_BREAKOUT",        "param": "STDEV_BREAKOUT_ENABLED",        "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "STDEV_BREAKOUT_TR",     "param": "STDEV_BREAKOUT_ENABLED",        "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "MI_ENTRY",              "param": "MI_ENTRY_ENABLED",              "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "MI_ENTRY_TRADIER",      "param": "MI_ENTRY_ENABLED_TRADIER",      "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "HLR_RALLY",             "param": "HLR_RALLY_ENABLED",             "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "HLR_RALLY_TRADIER",     "param": "HLR_RALLY_ENABLED",             "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "DELTA_ENGINE_OFF",      "param": "DELTA_ENGINE_ENABLED",          "config": "config.py",          "mode": "crypto",  "tier1_done": False, "notes": "Testing OFF vs ON (default True)"},
    {"name": "WT_COMP_DELTA_EXIT",    "param": "WT_COMP_DELTA_EXIT_ENABLED",    "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "EXIT_GAIN_EROSION",     "param": "EXIT_GAIN_EROSION_ENABLED",     "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "EXIT_TREND_REVERSAL",   "param": "EXIT_TREND_REVERSAL_ENABLED",   "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "TR_CHOP4H_GATE",        "param": "TR_CHOP4H_GATE_ENABLED",        "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "TR_ADX4H_GATE",         "param": "TR_ADX4H_GATE_ENABLED",         "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "AUGMENT_WT_CROSS",      "param": "AUGMENT_WT_CROSS_ENABLED",      "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "SYMBOL_PERF",           "param": "SYMBOL_PERF_ENABLED",           "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    # ── Needs precompute extension before tier-1 ──────────────────────────
    {"name": "SMFI",                  "param": "SMFI_ENABLED",                  "config": "config_tradier.py",  "mode": "tradier", "blocked_by": "needs_npz_smfi",    "notes": "smfi/smfi_sma missing from NPZ"},
    {"name": "CONNORS_RSI",           "param": "CONNORS_RSI_ENABLED",           "config": "config_tradier.py",  "mode": "tradier", "blocked_by": "needs_npz_connors", "notes": "connors_rsi missing from NPZ"},
    {"name": "CLENOW",                "param": "CLENOW_ENABLED",                "config": "config_tradier.py",  "mode": "tradier", "blocked_by": "needs_npz_clenow",  "notes": "slope_ann/r_squared missing from NPZ"},
    {"name": "ORB",                   "param": "TRC_ORB_ENABLED",               "config": "config_tradier.py",  "mode": "tradier", "blocked_by": "needs_npz_orb",     "notes": "session-range not in precompute; ORB_WINDOW_MINUTES param dead"},
    # ── Additional untested switches ──────────────────────────────────────
    {"name": "LS_RATIO_ENFORCE",      "param": "LS_RATIO_ENFORCE",              "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "LS_RATIO_ENFORCE_TR",   "param": "LS_RATIO_ENFORCE_TRADIER",      "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
    {"name": "PEAK_GIVEBACK",         "param": "PEAK_GIVEBACK_PROTECTION_ENABLED","config": "config.py",         "mode": "crypto",  "tier1_done": False},
    {"name": "MI_EXIT",               "param": "MI_EXIT_ENABLED",               "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "WT_COMPOSITE_SCORING",  "param": "WT_COMPOSITE_SCORING_ENABLED",  "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "SECTOR_LS_RATIO",       "param": "SECTOR_LS_RATIO_ENABLED",       "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "ROTATION_ANTONACCI",    "param": "ROTATION_ANTONACCI_ABS_MOM_ENABLED","config": "config_tradier.py","mode": "tradier","tier1_done": False, "notes": "Just implemented 2026-04-28"},
    {"name": "MACD_EXIT",             "param": "MACD_EXIT_ENABLED",             "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "BOUNCE_TOP_EXIT",       "param": "BOUNCE_TOP_EXIT_ENABLED",       "config": "config.py",          "mode": "crypto",  "tier1_done": False},
    {"name": "SBA",                   "param": "SBA_ENABLED",                   "config": "config.py",          "mode": "crypto",  "tier1_done": False, "notes": "Augment-only, hard to vectorize"},
    {"name": "SBA_TRADIER",           "param": "SBA_ENABLED_TRADIER",           "config": "config_tradier.py",  "mode": "tradier", "tier1_done": False},
]


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def load_state() -> dict:
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not STATE_FILE.exists():
        return {}
    with open(STATE_FILE) as f:
        return json.load(f)


def save_state(state: dict):
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_strategy_state(state: dict, name: str) -> dict:
    if name not in state:
        reg = next((s for s in STRATEGIES if s["name"] == name), {})
        state[name] = {
            "status": "BLOCKED" if reg.get("blocked_by") else "REGISTERED",
            "blocked_by": reg.get("blocked_by", ""),
            "param": reg.get("param", ""),
            "config": reg.get("config", ""),
            "mode": reg.get("mode", ""),
            "notes": reg.get("notes", ""),
            "tier1_result": None,
            "tier2_result": None,
            "created_at": now_iso(),
            "updated_at": now_iso(),
        }
    return state[name]


def set_status(state: dict, name: str, status: str, extra: dict = None):
    s = get_strategy_state(state, name)
    s["status"] = status
    s["updated_at"] = now_iso()
    if extra:
        s.update(extra)
    save_state(state)


# ── Tier-1: v8_test_queue A/B ─────────────────────────────────────────────────
def build_queue_items(strategies: list, mode: str) -> list:
    items = []
    for reg in strategies:
        if reg.get("mode") != mode:
            continue
        if reg.get("blocked_by"):
            continue
        name = reg["name"]
        param = reg["param"]
        cfg = reg["config"]
        # Determine A/B values: default ON=True vs OFF=False
        # Special case: if default is True, test False vs True (measure removal impact)
        value_a = "True"
        value_b = "False"
        items.append({
            "param": param,
            "value_a": value_a,
            "value_b": value_b,
            "config": cfg,
            "status": "pending",
            "pipeline_name": name,
        })
    return items


def read_tier1_results() -> dict:
    """Read all abtest_*.json results files. Return dict keyed by param name."""
    results = {}
    if not RESULTS_DIR.exists():
        return results
    for f in sorted(RESULTS_DIR.glob("abtest_*.json")):
        try:
            with open(f) as fh:
                data = json.load(fh)
            param = data.get("param", "")
            if not param:
                continue
            sharpe_a = float(data.get("result_a", {}).get("sharpe", 0) or 0)
            sharpe_b = float(data.get("result_b", {}).get("sharpe", 0) or 0)
            trades_a = int(data.get("result_a", {}).get("trades", 0) or 0)
            trades_b = int(data.get("result_b", {}).get("trades", 0) or 0)
            results[param] = {
                "param": param,
                "sharpe_on": sharpe_a,    # value_a = True = ON
                "sharpe_off": sharpe_b,   # value_b = False = OFF
                "trades_on": trades_a,
                "trades_off": trades_b,
                "delta": sharpe_a - sharpe_b,
                "winner": "ON" if sharpe_a >= sharpe_b else "OFF",
                "source_file": f.name,
                "ts": data.get("timestamp", ""),
            }
        except Exception:
            continue
    return results


def collect_tier1(state: dict) -> int:
    """Match abtest results to strategies, advance passing ones to TIER2_PENDING."""
    results = read_tier1_results()
    advanced = 0
    for reg in STRATEGIES:
        name = reg["name"]
        s = get_strategy_state(state, name)
        if s["status"] not in ("REGISTERED", "TIER1_RUNNING"):
            continue
        param = reg["param"]
        if param not in results:
            continue
        r = results[param]
        s["tier1_result"] = r
        delta = r["delta"]
        sharpe_on = r["sharpe_on"]
        if delta >= TIER1_MIN_DELTA and sharpe_on >= TIER1_MIN_SHARPE:
            print(f"  ✅ TIER1 PASS  {name}: ON={sharpe_on:.3f} OFF={r['sharpe_off']:.3f} Δ={delta:+.3f}")
            set_status(state, name, "TIER2_PENDING", {"tier1_result": r})
            advanced += 1
        else:
            reason = f"delta={delta:+.3f} (need >={TIER1_MIN_DELTA}) on_sharpe={sharpe_on:.3f} (need >={TIER1_MIN_SHARPE})"
            print(f"  ❌ TIER1 FAIL  {name}: {reason}")
            set_status(state, name, "USELESS", {"tier1_result": r, "fail_reason": reason})
            mark_useless_in_config(reg["config"], reg["param"])
            advanced += 1
    return advanced


# ── Config file marking ───────────────────────────────────────────────────────
def mark_useless_in_config(config_file: str, param: str):
    path = BASE / config_file
    if not path.exists():
        return
    today = datetime.now().strftime("%Y%m%d")
    tag = f"PIPELINE_USELESS_{today}"
    lines = path.read_text().splitlines()
    new_lines = []
    for line in lines:
        if re.match(rf"\s+{re.escape(param)}\s*[:=]", line) and tag not in line:
            stripped = line.rstrip()
            # Don't double-tag
            if "PIPELINE_USELESS" not in stripped:
                line = stripped + f"  # {tag}\n"
        new_lines.append(line if line.endswith("\n") else line + "\n")
    path.write_text("".join(new_lines))
    print(f"    Marked {param} as {tag} in {config_file}")


def apply_winner_to_config(config_file: str, param: str, value: str):
    """Update the config default for a winning parameter."""
    path = BASE / config_file
    if not path.exists():
        return
    today = datetime.now().strftime("%Y%m%d")
    lines = path.read_text().splitlines()
    new_lines = []
    changed = False
    for line in lines:
        if re.match(rf"\s+{re.escape(param)}\s*[:=]", line) and not changed:
            # Replace the value: bool True/False
            if value.lower() in ("true", "false"):
                new_val = value.capitalize()
                line = re.sub(r"(:\s*bool\s*=\s*|=\s*)(True|False)", rf"\g<1>{new_val}", line)
            line = line.rstrip() + f"  # PIPELINE_APPLIED_{today}\n"
            changed = True
        new_lines.append(line if line.endswith("\n") else line + "\n")
    path.write_text("".join(new_lines))
    print(f"    Applied {param}={value} in {config_file}")


# ── Tier-2: backtest_v8_engine 48 syms × 4yr ─────────────────────────────────
def run_tier2(reg: dict, state: dict) -> dict:
    name = reg["name"]
    param = reg["param"]
    mode = reg["mode"]
    config_file = reg["config"]
    set_status(state, name, "TIER2_RUNNING")

    symbols_file = BASE / "backtest_48_symbols.json"
    if not symbols_file.exists():
        # Fallback: 12 symbols
        if mode == "tradier":
            symbols = "AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY,META,GOOGL,JPM,TSM"
        else:
            symbols = "BTCUSDT,ETHUSDT,SOLUSDT,BNBUSDT,XRPUSDT,ADAUSDT,MATICUSDT,DOGEUSDT,AVAXUSDT,LTCUSDT,LINKUSDT,DOTUSDT"
        n_syms = 12
    else:
        with open(symbols_file) as f:
            sym_list = json.load(f)
        if mode == "tradier":
            # Stock symbols are different
            sym_list = sym_list[:48]
        symbols = ",".join(sym_list[:48])
        n_syms = min(48, len(sym_list))

    override = {param: True}
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(override, tf)
        override_path = tf.name

    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = override_path
    python = PYTHON_LOCAL

    cmd = [python, "-u", str(ENGINE),
           "--mode", mode,
           "--account", "trb" if mode == "tradier" else "ang",
           "--start", "2022-01-01",
           "--capital", "5000",
           "--symbols", symbols]

    print(f"  [{name}] Tier-2: {n_syms} syms × 4yr ... (may take 10-30 min)")
    t0 = time.time()
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, env=env,
                                cwd=str(BASE), timeout=7200)
    except subprocess.TimeoutExpired:
        os.unlink(override_path)
        return {"error": "timeout"}
    os.unlink(override_path)
    elapsed = time.time() - t0
    output = result.stdout + result.stderr

    parsed = _parse_v8_result(output)
    if parsed is None:
        parsed = {"sharpe": 0.0, "trades": 0, "pnl": 0.0}
        last = [l for l in output.splitlines() if l.strip()][-8:]
        print(f"    WARNING: no V8_RESULT — last lines:")
        for l in last:
            print(f"    {l}")
    else:
        print(f"    sharpe={parsed['sharpe']:.3f} trades={parsed['trades']} pnl={parsed['pnl']:.2f}% elapsed={elapsed:.0f}s")
    return {**parsed, "elapsed_s": elapsed, "n_syms": n_syms, "mode": mode}


def _parse_v8_result(output: str) -> dict | None:
    matches = list(re.finditer(
        r"V8_RESULT:\s+sharpe_w=([0-9.-]+)\s+sharpe_pt=([0-9.-]+)\s+sharpe_ann=([0-9.-]+)\s+gain_pct=([0-9.-]+)\s+closes=(\d+)\s+wins=(\d+)\s+losses=(\d+)",
        output))
    if matches:
        m = matches[-1]
        return {"sharpe": float(m.group(2)), "pnl": float(m.group(4)),
                "trades": int(m.group(5)), "wins": int(m.group(6)), "losses": int(m.group(7))}
    m = re.search(
        r"V8_RESULT:\s+sharpe=([0-9.-]+)\s+pnl=([0-9.-]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)",
        output)
    if m:
        return {"sharpe": float(m.group(1)), "pnl": float(m.group(2)),
                "trades": int(m.group(3)), "wins": int(m.group(4)), "losses": int(m.group(5))}
    return None


def evaluate_tier2(name: str, result: dict, state: dict, reg: dict):
    sharpe = result.get("sharpe", 0)
    trades = result.get("trades", 0)
    if sharpe >= TIER2_MIN_POOL_SHARPE and trades >= TIER2_MIN_TRADES:
        print(f"  ✅ TIER2 PASS  {name}: sharpe={sharpe:.3f} trades={trades}")
        set_status(state, name, "LIVE_CANDIDATE", {"tier2_result": result})
        print(f"    → Run: python3 strategy_pipeline.py --apply {name}")
    else:
        reason = f"sharpe={sharpe:.3f} (need>={TIER2_MIN_POOL_SHARPE}), trades={trades} (need>={TIER2_MIN_TRADES})"
        print(f"  ❌ TIER2 FAIL  {name}: {reason}")
        set_status(state, name, "USELESS", {"tier2_result": result, "fail_reason": reason})
        mark_useless_in_config(reg["config"], reg["param"])


# ── Status display ────────────────────────────────────────────────────────────
STATUS_ORDER = ["LIVE_CANDIDATE", "APPLIED", "TIER2_RUNNING", "TIER2_PENDING",
                "TIER1_RUNNING", "REGISTERED", "BLOCKED", "USELESS"]
STATUS_ICON = {
    "LIVE_CANDIDATE": "🟢", "APPLIED": "✅", "TIER2_RUNNING": "🔄",
    "TIER2_PENDING": "📋", "TIER1_RUNNING": "🔄", "REGISTERED": "⏳",
    "BLOCKED": "🚫", "USELESS": "❌",
}


def show_status(state: dict):
    print(f"\n{'='*70}")
    print(f"STRATEGY PIPELINE  —  {now_iso()}")
    print(f"{'='*70}")
    groups = {}
    for reg in STRATEGIES:
        name = reg["name"]
        s = get_strategy_state(state, name)
        st = s["status"]
        groups.setdefault(st, []).append((name, s, reg))
    for status in STATUS_ORDER:
        items = groups.get(status, [])
        if not items:
            continue
        print(f"\n{STATUS_ICON.get(status,'?')} {status} ({len(items)})")
        for name, s, reg in items:
            t1 = s.get("tier1_result") or {}
            t2 = s.get("tier2_result") or {}
            detail = ""
            if t1:
                detail += f"  T1: on={t1.get('sharpe_on',0):.3f} off={t1.get('sharpe_off',0):.3f} Δ={t1.get('delta',0):+.3f}"
            if t2:
                detail += f"  T2: sh={t2.get('sharpe',0):.3f} tr={t2.get('trades',0)}"
            if s.get("blocked_by"):
                detail += f"  [blocked: {s['blocked_by']}]"
            if s.get("notes"):
                detail += f"  ({s['notes']})"
            if s.get("fail_reason"):
                detail += f"  FAIL: {s['fail_reason']}"
            print(f"  {name:<30} {reg.get('param',''):<40}{detail}")
    print()
    # Summary counts
    counts = {st: len(groups.get(st, [])) for st in STATUS_ORDER}
    print("Summary: " + " | ".join(f"{st}={counts[st]}" for st in STATUS_ORDER if counts[st]))


# ── Main ──────────────────────────────────────────────────────────────────────
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--status",      action="store_true", help="Show all strategy states")
    ap.add_argument("--run-tier1",   action="store_true", help="Queue tier-1 A/B tests via v8_test_queue")
    ap.add_argument("--run-tier2",   action="store_true", help="Run tier-2 tests for TIER2_PENDING strategies")
    ap.add_argument("--collect",     action="store_true", help="Collect tier-1 results, advance states")
    ap.add_argument("--apply",       metavar="NAME",      help="Apply LIVE_CANDIDATE strategy to config")
    ap.add_argument("--discard",     metavar="NAME",      help="Force USELESS state + mark config")
    ap.add_argument("--unblock",     metavar="NAME",      help="Remove BLOCKED status (precompute done)")
    ap.add_argument("--full",        action="store_true", help="--collect + --run-tier1 + --run-tier2")
    ap.add_argument("--reset",       metavar="NAME",      help="Reset strategy to REGISTERED state")
    args = ap.parse_args()

    state = load_state()
    # Ensure all strategies are initialized
    for reg in STRATEGIES:
        get_strategy_state(state, reg["name"])
    save_state(state)

    if args.status or (not any([args.run_tier1, args.run_tier2, args.collect,
                                 args.apply, args.discard, args.unblock, args.full, args.reset])):
        show_status(state)
        return

    if args.reset:
        reg = next((s for s in STRATEGIES if s["name"] == args.reset), None)
        if not reg:
            print(f"Unknown strategy: {args.reset}")
            return
        state[args.reset] = None
        get_strategy_state(state, args.reset)
        save_state(state)
        print(f"Reset {args.reset} to REGISTERED")
        return

    if args.unblock:
        name = args.unblock
        s = get_strategy_state(state, name)
        s["status"] = "REGISTERED"
        s["blocked_by"] = ""
        s["updated_at"] = now_iso()
        save_state(state)
        print(f"Unblocked {name} → REGISTERED")
        return

    if args.discard:
        name = args.discard
        reg = next((s for s in STRATEGIES if s["name"] == name), None)
        if not reg:
            print(f"Unknown strategy: {name}")
            return
        set_status(state, name, "USELESS", {"fail_reason": "manual discard"})
        mark_useless_in_config(reg["config"], reg["param"])
        print(f"Discarded {name}")
        return

    if args.apply:
        name = args.apply
        s = get_strategy_state(state, name)
        reg = next((r for r in STRATEGIES if r["name"] == name), None)
        if not reg:
            print(f"Unknown strategy: {name}")
            return
        if s["status"] != "LIVE_CANDIDATE":
            print(f"Strategy {name} is not LIVE_CANDIDATE (status: {s['status']})")
            return
        apply_winner_to_config(reg["config"], reg["param"], "True")
        set_status(state, name, "APPLIED")
        print(f"Applied {name} to {reg['config']}")
        print(f"⚠️  Remember to: backup, rsync to S1+S2, restart workers")
        return

    if args.collect or args.full:
        print("\n=== Collecting Tier-1 results ===")
        n = collect_tier1(state)
        print(f"  Advanced {n} strategies")

    if args.run_tier1 or args.full:
        print("\n=== Queuing Tier-1 tests ===")
        for mode in ("crypto", "tradier"):
            pending = [
                reg for reg in STRATEGIES
                if reg.get("mode") == mode
                and not reg.get("blocked_by")
                and get_strategy_state(state, reg["name"])["status"] == "REGISTERED"
            ]
            if not pending:
                print(f"  {mode}: nothing pending")
                continue
            items = build_queue_items(pending, mode)
            qpath = QUEUE_CRYPTO if mode == "crypto" else QUEUE_TRADIER
            with open(qpath, "w") as f:
                json.dump(items, f, indent=2)
            print(f"  {mode}: wrote {len(items)} items to {qpath}")
            for reg in pending:
                set_status(state, reg["name"], "TIER1_RUNNING")
            # Print launch commands
            if mode == "crypto":
                print(f"  Launch on S1: ssh s1-int 'cd /home/niels/binance-sandbox && cp {qpath} /tmp/v8_test_queue.json && nohup {PYTHON_S1} -u v8_test_queue.py --mode crypto >> ~/logs/v8_pipeline_crypto.log 2>&1 &'")
            else:
                print(f"  Launch on S2: ssh s2-int 'cd /home/niels/binance-sandbox && cp {qpath} /tmp/v8_test_queue.json && nohup {PYTHON_S2} -u v8_test_queue.py --mode tradier >> ~/logs/v8_pipeline_tradier.log 2>&1 &'")

    if args.run_tier2 or args.full:
        print("\n=== Running Tier-2 tests ===")
        tier2_pending = [
            reg for reg in STRATEGIES
            if get_strategy_state(state, reg["name"])["status"] == "TIER2_PENDING"
        ]
        if not tier2_pending:
            print("  Nothing in TIER2_PENDING")
        for reg in tier2_pending:
            name = reg["name"]
            print(f"\n  Testing {name} ({reg['param']}) ...")
            result = run_tier2(reg, state)
            evaluate_tier2(name, result, state, reg)

    show_status(state)


if __name__ == "__main__":
    main()
