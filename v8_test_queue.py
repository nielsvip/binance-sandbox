#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""v8_test_queue.py — Process the /tmp/v8_test_queue.json A/B test queue.

Reads the queue written by sweep_cockpit.py (/param/<name>/queue_test),
runs each pending item as a two-config V8 backtest (value_a vs value_b),
records results, marks items completed, and prints a summary report.

Usage:
    python3 v8_test_queue.py                         # run all pending
    python3 v8_test_queue.py --mode tradier           # force tradier mode
    python3 v8_test_queue.py --dry-run               # show what would run
    python3 v8_test_queue.py --symbols AAPL,MSFT,QQQ # override symbols
    python3 v8_test_queue.py --apply-winner           # write winner to config
    python3 v8_test_queue.py --show                   # show queue without running
"""
import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

PYTHON = os.environ.get("V8_PYTHON", sys.executable)  # 2026-04-17: default to current interpreter — portable MB/S1/S2 without hardcoded paths
BASE = Path(__file__).resolve().parent
ENGINE = BASE / "backtest_v8_engine.py"
QUEUE_PATH = Path("/tmp/v8_test_queue.json")
RESULTS_DIR = BASE / "data" / "test_queue_results"
RESULTS_DIR.mkdir(parents=True, exist_ok=True)

# Default symbols per mode — representative, fast
DEFAULT_SYMBOLS_TRADIER = "AAPL,MSFT,NVDA,AMZN,AMD,XOM,QQQ,SPY"
DEFAULT_SYMBOLS_CRYPTO = "BTCUSDC,ETHUSDC,SOLUSDC,BNBUSDC"

# Default backtests date range
DEFAULT_START_TRADIER = "2024-06-01"
DEFAULT_START_CRYPTO = "2024-10-01"


def load_queue() -> list:
    if not QUEUE_PATH.exists():
        return []
    with open(QUEUE_PATH) as f:
        return json.load(f)


def save_queue(queue: list):
    with open(QUEUE_PATH, "w") as f:
        json.dump(queue, f, indent=2)


def infer_mode(config_file: str) -> str:
    if "tradier" in config_file.lower():
        return "tradier"
    return "crypto"


def infer_account(mode: str) -> str:
    return "trb" if mode == "tradier" else "ang"


def parse_v8_result(output: str) -> dict | None:
    """Parse V8_RESULT line. Supports two formats:

    NEW (2026-04+): `V8_RESULT: sharpe_w=X sharpe_pt=Y sharpe_ann=Z gain_pct=G closes=C wins=W losses=L`
    OLD (legacy):   `V8_RESULT: sharpe=X pnl=Y trades=N wins=W losses=L total_pnl_dollars=D avg_pnl=A`

    Returns normalized dict with 'sharpe' = sharpe_pt (per-trade, per memory `feedback_sharpe_per_trade_always`).
    """
    # NEW format (search LAST match so V8_RESULT_LIVE interim lines are skipped — final summary comes last)
    matches = list(re.finditer(
        r"V8_RESULT:\s+sharpe_w=([0-9.-]+)\s+sharpe_pt=([0-9.-]+)\s+sharpe_ann=([0-9.-]+)\s+gain_pct=([0-9.-]+)\s+closes=(\d+)\s+wins=(\d+)\s+losses=(\d+)",
        output,
    ))
    if matches:
        m = matches[-1]
        trades = int(m.group(5))
        wins = int(m.group(6))
        losses = int(m.group(7))
        return {
            "sharpe": float(m.group(2)),            # sharpe_pt — per-trade (primary)
            "sharpe_w": float(m.group(1)),
            "sharpe_pt": float(m.group(2)),
            "sharpe_ann": float(m.group(3)),
            "pnl": float(m.group(4)),               # gain_pct as %
            "trades": trades,
            "wins": wins,
            "losses": losses,
            "total_pnl_dollars": None,              # not in new format
            "avg_pnl": None,
            "_format": "new",
        }
    # OLD fallback
    m = re.search(
        r"V8_RESULT:\s+sharpe=([0-9.-]+)\s+pnl=([0-9.-]+)\s+trades=(\d+)\s+wins=(\d+)\s+losses=(\d+)\s+total_pnl_dollars=([0-9.-]+)\s+avg_pnl=([0-9.-]+)",
        output,
    )
    if m:
        return {
            "sharpe": float(m.group(1)),
            "pnl": float(m.group(2)),
            "trades": int(m.group(3)),
            "wins": int(m.group(4)),
            "losses": int(m.group(5)),
            "total_pnl_dollars": float(m.group(6)),
            "avg_pnl": float(m.group(7)),
            "_format": "old",
        }
    return None


def run_single(param: str, value, mode: str, account: str, symbols: str, start: str, capital: float, label: str, base_override: dict | None = None) -> dict:
    override = dict(base_override) if base_override else {}
    override[param] = value
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as tf:
        json.dump(override, tf)
        override_path = tf.name
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = override_path
    env["TEST_RATE_GUARD_MIN_PER_DAY"] = "0"
    env["V8_BACKTEST_TIMEOUT"] = str(int(os.environ.get("V8_BACKTEST_TIMEOUT", "3600")))
    if mode == "crypto":
        env["V8_SKIP_PROCESS_POSITION"] = "1"
    cmd = [
        PYTHON, "-u", str(ENGINE),
        "--mode", mode,
        "--account", account,
        "--start", start,
        "--capital", str(capital),
        "--symbols", symbols,
    ]
    print(f"  [{label}] {param}={value!r}  running ...", flush=True)
    # Default 3600s (1h) — real engine on 4 crypto syms × 18mo 3m bars needs ~45 min.
    # Override with V8_BACKTEST_TIMEOUT env var.
    _bt_timeout = int(os.environ.get("V8_BACKTEST_TIMEOUT", "3600"))
    result = subprocess.run(cmd, capture_output=True, text=True, env=env, cwd=str(BASE), timeout=_bt_timeout)
    os.unlink(override_path)
    output = result.stdout + result.stderr
    parsed = parse_v8_result(output)
    if parsed is None:
        parsed = {"sharpe": 0.0, "pnl": 0.0, "trades": 0, "wins": 0, "losses": 0, "total_pnl_dollars": 0.0, "avg_pnl": 0.0}
        print(f"    WARNING: no V8_RESULT found for {label}")
        last_lines = [l for l in output.splitlines() if l.strip()][-5:]
        for l in last_lines:
            print(f"    {l}")
    else:
        print(f"    [{label}] sharpe={parsed['sharpe']:.3f} trades={parsed['trades']} pnl={parsed['pnl']:.2f}")
    return parsed


def get_current_value(param: str, config_file: str) -> str | None:
    cfg_path = BASE / config_file
    if not cfg_path.exists():
        return None
    with open(cfg_path) as f:
        for line in f:
            if re.match(rf"^\s+{re.escape(param)}\s*[:=]", line):
                m = re.search(r"[:=]\s*(.+?)(?:\s*#.*)?$", line)
                if m:
                    return m.group(1).strip()
    return None


def apply_winner_to_config(param: str, winner_value: str, config_file: str, dry_run: bool = False):
    cfg_path = BASE / config_file
    with open(cfg_path) as f:
        lines = f.readlines()
    new_lines = []
    applied = False
    for line in lines:
        if re.match(rf"^\s+{re.escape(param)}\s*[:=]", line):
            indent = re.match(r"^(\s*)", line).group(1)
            # preserve comment if any
            comment_m = re.search(r"(#.*)", line)
            comment = f"  {comment_m.group(1)}" if comment_m else ""
            # detect type annotation
            ann_m = re.search(rf"{re.escape(param)}(\s*:\s*\S+)?\s*[:=]", line)
            ann = ann_m.group(1) if (ann_m and ann_m.group(1)) else ""
            new_line = f"{indent}{param}{ann} = {winner_value}{comment}\n"
            if dry_run:
                print(f"    [DRY-RUN] Would change: {line.rstrip()} → {new_line.rstrip()}")
            else:
                print(f"    Applied: {line.rstrip()} → {new_line.rstrip()}")
            new_lines.append(new_line)
            applied = True
        else:
            new_lines.append(line)
    if applied and not dry_run:
        with open(cfg_path, "w") as f:
            f.writelines(new_lines)
    elif not applied:
        print(f"    WARNING: param {param} not found in {config_file} — cannot apply")


def run_item(item: dict, args) -> dict:
    param = item["param"]
    config_file = item.get("config", "config_tradier.py")
    value_a_raw = item["value_a"]
    value_b_raw = item["value_b"]
    # 2026-04-17 HARD GUARD: crypto/tradier configs must never be confused.
    # When --mode is explicit and doesn't match the config file's inferred mode, REFUSE to run.
    # Silent 0-trade results from mode/config mismatch have cost weeks of test time.
    inferred = infer_mode(config_file)
    if args.mode and args.mode != inferred:
        print(f"\n{'='*60}\n❌ MODE_CONFIG_MISMATCH_SKIP: {param}\n  config={config_file} inferred={inferred}  but --mode={args.mode}\n  Refusing to run — route this test to a {inferred} machine.\n{'='*60}")
        # Preserve all original queue fields (value_a/b/notes) and add skip status so the main loop
        # marks it correctly. Queue stays navigable; item will be picked up on the right machine.
        skipped = dict(item)
        skipped["status"] = "mode_skip"
        skipped["mode_skip_reason"] = f"config={config_file} implies mode={inferred}, caller passed --mode={args.mode}"
        skipped["mode_skip_at"] = datetime.now(timezone.utc).isoformat()
        return skipped
    mode = args.mode or inferred
    account = infer_account(mode)
    symbols = args.symbols or item.get("symbols", "") or (DEFAULT_SYMBOLS_TRADIER if mode == "tradier" else DEFAULT_SYMBOLS_CRYPTO)
    start = args.start or item.get("start_date", "") or (DEFAULT_START_TRADIER if mode == "tradier" else DEFAULT_START_CRYPTO)
    capital = args.capital
    base_override = item.get("base_override", None)

    # Try to parse typed values (float, int, bool, str)
    def _parse(v):
        if v.lower() in ("true", "false"):
            return v.lower() == "true"
        try:
            return int(v)
        except ValueError:
            pass
        try:
            return float(v)
        except ValueError:
            pass
        return v

    value_a = _parse(value_a_raw)
    value_b = _parse(value_b_raw)

    print(f"\n{'='*60}")
    print(f"A/B TEST: {param}")
    print(f"  config: {config_file}  mode: {mode}  symbols: {symbols}")
    print(f"  A={value_a!r}  B={value_b!r}  notes: {item.get('notes','')}")
    print(f"{'='*60}")

    if args.dry_run:
        print(f"  [DRY-RUN] Would run A={value_a!r} vs B={value_b!r}")
        return {**item, "status": "dry_run"}

    result_a = run_single(param, value_a, mode, account, symbols, start, capital, "A", base_override=base_override)
    result_b = run_single(param, value_b, mode, account, symbols, start, capital, "B", base_override=base_override)

    # Determine winner
    sharpe_a = result_a["sharpe"]
    sharpe_b = result_b["sharpe"]
    if sharpe_a > sharpe_b:
        winner_val = value_a_raw
        winner_sharpe = sharpe_a
        loser_sharpe = sharpe_b
        edge = "A"
    else:
        winner_val = value_b_raw
        winner_sharpe = sharpe_b
        loser_sharpe = sharpe_a
        edge = "B"

    delta = abs(sharpe_a - sharpe_b)
    print(f"\n  RESULT: A={sharpe_a:.3f}({result_a['trades']}t)  B={sharpe_b:.3f}({result_b['trades']}t)  WINNER={edge}({winner_val})  Δ={delta:.3f}")

    # Save per-test result JSON
    ts = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    result_path = RESULTS_DIR / f"abtest_{param}_{ts}.json"
    result_data = {
        "param": param,
        "config": config_file,
        "mode": mode,
        "symbols": symbols,
        "value_a": value_a_raw,
        "value_b": value_b_raw,
        "result_a": result_a,
        "result_b": result_b,
        "winner": edge,
        "winner_value": winner_val,
        "winner_sharpe": winner_sharpe,
        "loser_sharpe": loser_sharpe,
        "delta_sharpe": delta,
        "notes": item.get("notes", ""),
        "tested_at": datetime.now(timezone.utc).isoformat(),
    }
    with open(result_path, "w") as f:
        json.dump(result_data, f, indent=2)
    print(f"  Saved: {result_path}")

    if args.apply_winner and delta >= 0.05:
        print(f"  Applying winner ({winner_val}) to {config_file} ...")
        apply_winner_to_config(param, winner_val, config_file, dry_run=False)
    elif args.apply_winner:
        print(f"  Δ={delta:.3f} < 0.05 threshold — skipping auto-apply (marginal result)")

    return {**item, "status": "completed", "winner": edge, "winner_value": winner_val,
            "result_a": result_a, "result_b": result_b, "tested_at": datetime.now(timezone.utc).isoformat()}


def print_queue(queue: list):
    pending = [i for i in queue if i.get("status") == "pending"]
    done = [i for i in queue if i.get("status") != "pending"]
    print(f"\nQueue: {len(pending)} pending, {len(done)} done")
    if pending:
        print("\nPENDING:")
        for item in pending:
            print(f"  [{item.get('config','?')}] {item['param']} A={item['value_a']} vs B={item['value_b']} | {item.get('notes','')}")
    if done:
        print("\nCOMPLETED:")
        for item in done:
            w = item.get("winner_value", "?")
            ra = item.get("result_a", {}); rb = item.get("result_b", {})
            print(f"  [{item.get('config','?')}] {item['param']} winner={w} (A={ra.get('sharpe',0):.3f}/B={rb.get('sharpe',0):.3f})")


def print_results_from_sweep_files():
    """Summarize key findings from existing sweep archive files."""
    summary = []
    archive = BASE / "backtest_v8" / "sweeps" / "archive"
    progress_dir = BASE / "backtest_v8" / "sweeps"
    # T25 tradier — latest progress
    t25 = progress_dir / "v8_sweep_tradier_t25_10sym_c6d70b_progress.json"
    if t25.exists():
        with open(t25) as f:
            runs = json.load(f)
        nonzero = [r for r in runs if isinstance(r, dict) and r.get("trades", 0) > 0]
        if nonzero:
            for key in ["RZ_EXIT_ENABLED", "SATOSHIT_ENABLED_TRADIER", "WT_CROSSUNDER_FINAL_ENABLED", "DELTA_ENGINE_ENABLED", "STRUCTURAL_RANGE_SHIFT_EXIT"]:
                t_grp = [r for r in nonzero if r.get("config", {}).get(key) is True]
                f_grp = [r for r in nonzero if r.get("config", {}).get(key) is False]
                if t_grp and f_grp:
                    t_avg = sum(r["sharpe"] for r in t_grp) / len(t_grp)
                    f_avg = sum(r["sharpe"] for r in f_grp) / len(f_grp)
                    winner = "True" if t_avg > f_avg else "False"
                    delta = abs(t_avg - f_avg)
                    summary.append(f"  tradier_t25 {key}: True={t_avg:.3f}({len(t_grp)}) False={f_avg:.3f}({len(f_grp)}) → winner={winner} Δ={delta:.3f}")
            for thresh in [35, 43, 55, 75]:
                g = [r for r in nonzero if r.get("config", {}).get("WT_DC_ENTRY_THRESHOLD") == thresh]
                if g:
                    avg = sum(r["sharpe"] for r in g) / len(g)
                    summary.append(f"  tradier_t25 WT_DC_THRESHOLD={thresh}: n={len(g)} avg_sharpe={avg:.3f}")
    # Crypto t13
    ct13 = progress_dir / "v8_sweep_crypto_t13_4sym_progress.json"
    if ct13.exists():
        with open(ct13) as f:
            runs = json.load(f)
        runs = runs if isinstance(runs, list) else list(runs.values())
        nonzero = [r for r in runs if isinstance(r, dict) and r.get("trades", 0) > 0]
        for thresh in [1.5, 2.5]:
            g = [r for r in nonzero if r.get("config", {}).get("DELTA_ENTRY_Z_THRESHOLD") == thresh]
            if g:
                avg = sum(r["sharpe"] for r in g) / len(g)
                summary.append(f"  crypto_t13 DELTA_ENTRY_Z_THRESHOLD={thresh}: n={len(g)} avg_sharpe={avg:.3f}")
    if summary:
        print("\n=== SWEEP RESULT SUMMARY ===")
        for line in summary:
            print(line)


def main():
    parser = argparse.ArgumentParser(description="Run pending V8 A/B tests from /tmp/v8_test_queue.json")
    parser.add_argument("--mode", choices=["tradier", "crypto"], default=None, help="Force backtest mode")
    parser.add_argument("--dry-run", action="store_true", help="Show what would run without running")
    parser.add_argument("--show", action="store_true", help="Show queue and results without running")
    parser.add_argument("--apply-winner", action="store_true", help="Write winner value to config file (Δ >= 0.05)")
    parser.add_argument("--symbols", type=str, default="", help="Override default symbols")
    parser.add_argument("--start", type=str, default="", help="Override default backtest start date (YYYY-MM-DD)")
    parser.add_argument("--capital", type=float, default=2000.0, help="Backtest capital (default 2000)")
    parser.add_argument("--results-summary", action="store_true", help="Print summary from sweep archive files")
    args = parser.parse_args()

    if args.results_summary:
        print_results_from_sweep_files()
        return

    queue = load_queue()
    if not queue:
        print(f"Queue is empty or {QUEUE_PATH} does not exist.")
        if args.results_summary or args.show:
            print_results_from_sweep_files()
        return

    print_queue(queue)

    if args.show:
        print_results_from_sweep_files()
        return

    pending = [i for i in queue if i.get("status") == "pending"]
    if not pending:
        print("No pending items.")
        return

    print(f"\nRunning {len(pending)} pending test(s) ...")
    updated_queue = list(queue)
    for idx, item in enumerate(updated_queue):
        if item.get("status") != "pending":
            continue
        try:
            result_item = run_item(item, args)
        except Exception as _exc:
            # 2026-04-28: per-item timeout/error catch so one bad A/B doesn't kill the whole queue.
            import traceback as _tb
            _err_msg = f"{type(_exc).__name__}: {_exc}"
            print(f"\n⚠️ ITEM_FAILED [{item.get('param','?')}]: {_err_msg}")
            _tb.print_exc()
            result_item = dict(item)
            result_item["status"] = "failed"
            result_item["error"] = _err_msg[:500]
            result_item["failed_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        updated_queue[idx] = result_item
        # 2026-04-28: persist queue after EACH item so a crash doesn't lose progress.
        save_queue(updated_queue)

    save_queue(updated_queue)
    print(f"\nQueue saved to {QUEUE_PATH}")
    print_queue(updated_queue)
    print_results_from_sweep_files()


if __name__ == "__main__":
    main()
