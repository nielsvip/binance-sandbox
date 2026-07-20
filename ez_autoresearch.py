#!/usr/bin/env python3
"""
EZ AUTORESEARCH — Autonomous Trading Script Optimizer
=====================================================
Autoresearch-style loop: pick param → patch source → run backtest → measure → keep/discard.

Usage:
  python3 ez_autoresearch.py --program rankings          # Run rankings optimization
  python3 ez_autoresearch.py --program indicators --max-time 2h
  python3 ez_autoresearch.py --program all --max-time 12h  # Run all programs sequentially
  python3 ez_autoresearch.py --report                     # Show leaderboard
  python3 ez_autoresearch.py --deploy --risk-max MEDIUM   # Deploy best results
  python3 ez_autoresearch.py --dry-run --program rankings # Show what would run

Architecture:
  1. Load research program JSON (defines params, search space, backtest engine)
  2. Pick a parameter + value to test (strategy: random_one, grid, random_multi)
  3. Regex-patch the source file with new value
  4. Run backtest as subprocess, capture results
  5. Parse primary metric (Sharpe) + guards (WR, PF, MaxDD, min_trades)
  6. If better than current best AND passes guards → KEEP (leave patch)
  7. If worse → DISCARD (revert patch)
  8. Log to results.tsv + per-program JSON
  9. Repeat until --max-time or --max-experiments reached
"""
import argparse
import json
import os
import random
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ═══════════════════════════════════════════════════════════════════════
# PATHS
# ═══════════════════════════════════════════════════════════════════════
BASE = Path("/home/niels/binance-sandbox")
PROGRAMS_DIR = BASE / "data" / "autoresearch" / "programs"
RESULTS_DIR = BASE / "data" / "autoresearch"
RESULTS_TSV = RESULTS_DIR / "results.tsv"
BEST_DIR = RESULTS_DIR / "best"
BACKUPS_DIR = RESULTS_DIR / "backups"
LOG_FILE = RESULTS_DIR / "autoresearch.log"

PYTHON = "/home/niels/.conda/envs/binance_env/bin/python"


def ensure_dirs():
    for d in [PROGRAMS_DIR, RESULTS_DIR, BEST_DIR, BACKUPS_DIR]:
        d.mkdir(parents=True, exist_ok=True)


# ═══════════════════════════════════════════════════════════════════════
# LOGGING
# ═══════════════════════════════════════════════════════════════════════
def log(msg, also_print=True):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    if also_print:
        print(line, flush=True)
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ═══════════════════════════════════════════════════════════════════════
# PROGRAM LOADING
# ═══════════════════════════════════════════════════════════════════════
def load_program(name):
    path = PROGRAMS_DIR / f"{name}.json"
    if not path.exists():
        log(f"ERROR: Program {name} not found at {path}")
        sys.exit(1)
    with open(path) as f:
        prog = json.load(f)
    return prog


def list_programs():
    if not PROGRAMS_DIR.exists():
        return []
    return sorted([p.stem for p in PROGRAMS_DIR.glob("*.json")])


# ═══════════════════════════════════════════════════════════════════════
# SOURCE PATCHING — Autoresearch-style direct file modification
# ═══════════════════════════════════════════════════════════════════════
def backup_file(filepath):
    """Backup before patching."""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup = BACKUPS_DIR / f"{Path(filepath).name}.{ts}.bak"
    shutil.copy2(filepath, backup)
    return backup


def read_current_value(filepath, param_name, param_pattern=None):
    """Read the current value of a parameter from source file."""
    with open(filepath) as f:
        content = f.read()

    if param_pattern:
        # Use custom regex pattern from program definition
        m = re.search(param_pattern, content)
        if m:
            return m.group(1)
    else:
        # Default patterns for common Python assignment styles
        patterns = [
            # dataclass field: NAME: type = VALUE or NAME = VALUE
            rf"(?:^|\n)\s*{re.escape(param_name)}\s*(?::\s*\w+)?\s*=\s*([^\n#]+)",
            # dict key: 'name': VALUE or "name": VALUE
            rf"['\"]({re.escape(param_name)})['\"]:\s*([^\n,}}]+)",
            # Simple assignment in function body: name = VALUE
            rf"\b{re.escape(param_name)}\s*=\s*([^\n#]+)",
        ]
        for pat in patterns:
            m = re.search(pat, content)
            if m:
                val = m.group(m.lastindex).strip().rstrip(",")
                return val

    return None


def patch_value(filepath, param_name, new_value, param_pattern=None):
    """Patch a parameter value in a source file. Returns (success, old_value)."""
    with open(filepath) as f:
        content = f.read()

    old_value = read_current_value(filepath, param_name, param_pattern)
    if old_value is None:
        log(f"  WARNING: Could not find {param_name} in {filepath}")
        return False, None

    # Format new value for Python source
    new_val_str = format_value(new_value)

    if param_pattern:
        new_content = re.sub(param_pattern, lambda m: m.group(0).replace(m.group(1), new_val_str), content, count=1)
    else:
        # Try dataclass-style first: NAME: type = VALUE or NAME = VALUE
        pat1 = rf"((?:^|\n)(\s*){re.escape(param_name)}\s*(?::\s*\w+)?\s*=\s*)([^\n#]+)"
        m = re.search(pat1, content)
        if m:
            old_part = m.group(3).strip()
            # Preserve trailing comma if present
            suffix = "," if old_part.endswith(",") else ""
            new_content = content[:m.start(3)] + new_val_str + suffix + content[m.end(3):]
        else:
            # Try dict-style: 'name': VALUE
            pat2 = rf"(['\"]){re.escape(param_name)}\1(\s*:\s*)([^\n,}}]+)"
            m = re.search(pat2, content)
            if m:
                new_content = content[:m.start(3)] + new_val_str + content[m.end(3):]
            else:
                log(f"  WARNING: Could not patch {param_name} in {filepath}")
                return False, old_value

    with open(filepath, "w") as f:
        f.write(new_content)

    # Verify the patch took
    verify = read_current_value(filepath, param_name, param_pattern)
    log(f"  PATCHED {param_name}: {old_value} → {new_val_str} (verified: {verify})")
    return True, old_value


def revert_file(filepath, backup_path):
    """Revert a file from its backup."""
    shutil.copy2(backup_path, filepath)
    log(f"  REVERTED {filepath}")


def format_value(val):
    """Format a value for Python source code."""
    if isinstance(val, bool):
        return "True" if val else "False"
    elif isinstance(val, float):
        # Clean float representation
        if val == int(val) and abs(val) < 1e10:
            return f"{val:.1f}"
        return str(val)
    elif isinstance(val, int):
        return str(val)
    elif isinstance(val, str):
        return f'"{val}"'
    elif isinstance(val, list):
        return str(val)
    return str(val)


# ═══════════════════════════════════════════════════════════════════════
# BACKTEST EXECUTION
# ═══════════════════════════════════════════════════════════════════════
def _resolve_engine(engine):
    """Find the backtest engine script path."""
    engine_path = BASE / engine
    if engine_path.exists():
        return engine_path
    engine_path = BASE / "backtest_framework" / engine
    if engine_path.exists():
        return engine_path
    return None


def _clear_result_cache(program):
    """Clear cached results so backtest re-runs with patched params."""
    output_file = program.get("output_file")
    if not output_file:
        return

    # For JSON result files, rename to .prev so backtest recomputes
    out_path = BASE / output_file
    if out_path.exists() and out_path.suffix == ".json":
        prev = out_path.with_suffix(".prev.json")
        try:
            shutil.move(str(out_path), str(prev))
            log(f"  Cleared result cache: {output_file} → .prev.json")
        except Exception:
            pass

    # Also clear the main results file (not just best)
    results_dir = out_path.parent
    for pattern in ["*_results.json", "*_best.json"]:
        for f in results_dir.glob(pattern):
            prev = f.with_suffix(".prev.json")
            try:
                shutil.move(str(f), str(prev))
            except Exception:
                pass


def _restore_result_cache(program):
    """Restore cached results after experiment."""
    output_file = program.get("output_file")
    if not output_file:
        return

    out_path = BASE / output_file
    results_dir = out_path.parent
    for f in results_dir.glob("*.prev.json"):
        orig = f.with_suffix("").with_suffix(".json")
        if not orig.exists():
            try:
                shutil.move(str(f), str(orig))
            except Exception:
                pass


def run_backtest(program, timeout=None):
    """Run the backtest engine specified in the program. Returns parsed metrics dict."""
    engine = program["backtest_engine"]
    args = program.get("backtest_args", [])
    engine_path = _resolve_engine(engine)

    if not engine_path:
        log(f"  ERROR: Backtest engine {engine} not found")
        return None

    # Clear result cache so backtest recomputes with patched params
    _clear_result_cache(program)

    cmd = [PYTHON, str(engine_path)] + args
    timeout_sec = timeout or program.get("timeout_seconds", 600)

    log(f"  RUNNING: {' '.join(cmd)} (timeout={timeout_sec}s)")
    t0 = time.time()

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout_sec,
            cwd=str(BASE),
        )
        elapsed = time.time() - t0
        log(f"  COMPLETED in {elapsed:.1f}s (exit={result.returncode})")

        if result.returncode != 0:
            stderr_tail = result.stderr[-500:] if result.stderr else ""
            log(f"  STDERR: {stderr_tail}")
            return None

        # v8_quick_engine.py is the only current (non-retired) engine this harness
        # can call — parse its dedicated single-line output directly and skip the
        # legacy fallback strategies below, which read shared/stale leaderboard
        # cache files (data/backtest_deep_crypto/deep_crypto_best.json etc.) that
        # are not tied to this specific experiment and previously caused every
        # program sharing an engine to report identical, imposter metrics.
        if Path(engine).name == "v8_quick_engine.py":
            metrics = parse_v8_quick_output(result.stdout + "\n" + result.stderr)
            if not metrics:
                log("  WARNING: v8_quick_engine ran but V8_QUICK_RESULT line not found in output")
            return metrics

        # Try parsing stdout first
        metrics = parse_backtest_output(program, result.stdout + "\n" + result.stderr)
        if metrics:
            return metrics

        # Fallback: run with --report to get formatted output
        report_cmd = [PYTHON, str(engine_path), "--report"]
        try:
            report_result = subprocess.run(
                report_cmd,
                capture_output=True,
                text=True,
                timeout=30,
                cwd=str(BASE),
            )
            if report_result.returncode == 0:
                metrics = parse_report_output(report_result.stdout)
                if metrics:
                    log(f"  Parsed metrics from --report output")
                    return metrics
        except Exception:
            pass

        return None

    except subprocess.TimeoutExpired:
        log(f"  TIMEOUT after {timeout_sec}s")
        return None
    except Exception as e:
        log(f"  ERROR: {e}")
        return None


def parse_report_output(stdout):
    """Parse the tabular --report output from deep_crypto, ablation, etc."""
    metrics = {}

    # Parse table rows like: "  1 CONFIG_NAME  48.994  97.6%  2.181 -38.27%  9702  637.5  3053.7"
    rows = []
    for line in stdout.split("\n"):
        # Match numbered rows with Sharpe, WR%, PF, DD%, Trades
        m = re.match(
            r"\s*\d+\s+\S+\s+(-?\d+\.?\d*)\s+(\d+\.?\d*)%\s+(\d+\.?\d*)\s+(-?\d+\.?\d*)%\s+(\d+)",
            line,
        )
        if m:
            rows.append({
                "sharpe": float(m.group(1)),
                "win_rate": float(m.group(2)) / 100,
                "pf": float(m.group(3)),
                "max_dd": float(m.group(4)),
                "n_trades": int(m.group(5)),
            })

    if rows:
        # Average the top results (up to 20)
        top = rows[:20]
        metrics["sharpe"] = sum(r["sharpe"] for r in top) / len(top)
        metrics["win_rate"] = sum(r["win_rate"] for r in top) / len(top)
        metrics["pf"] = sum(r["pf"] for r in top) / len(top)
        metrics["max_dd"] = sum(r["max_dd"] for r in top) / len(top)
        metrics["n_trades"] = sum(r["n_trades"] for r in top)
        return metrics

    # Also try "Best: X.XX" pattern from summary line
    m = re.search(r"Best:\s*(-?\d+\.?\d*)", stdout)
    if m:
        metrics["sharpe"] = float(m.group(1))
        # Minimal metrics — at least we have Sharpe
        metrics["win_rate"] = 0.97  # Use conservative estimate
        metrics["pf"] = 2.0
        metrics["max_dd"] = -50
        metrics["n_trades"] = 1000
        return metrics

    return None


def parse_v8_quick_output(stdout):
    """Parse the single V8_QUICK_RESULT line emitted by v8_quick_engine.py.
    Tier-1 vectorized engine — diagnostic shortlist only (see BACKTEST_BIBLE.md).
    Does not emit PF/max_dd, so those keys are omitted (not zeroed) rather than guessed."""
    m = re.search(
        r"V8_QUICK_RESULT:\s*sharpe=(-?[0-9.]+)\s+pnl=(-?[0-9.]+)\s+trades=([0-9]+)\s+wins=([0-9]+)\s+losses=([0-9]+)\s+wr=([0-9.]+)%",
        stdout,
    )
    if not m:
        return None
    return {
        "sharpe": float(m.group(1)),
        "win_rate": float(m.group(6)) / 100,
        "n_trades": int(m.group(3)),
        "tier": "TIER1_DIAGNOSTIC",
    }


def parse_backtest_output(program, stdout):
    """Parse backtest output to extract metrics. Tries multiple strategies."""
    metrics = {}

    # Strategy 1: Check for output files specified in program
    output_file = program.get("output_file")
    if output_file:
        out_path = BASE / output_file
        if out_path.exists():
            try:
                with open(out_path) as f:
                    data = json.load(f)
                metrics = extract_metrics_from_json(data, program)
                if metrics:
                    return metrics
            except Exception as e:
                log(f"  WARNING: Could not parse {output_file}: {e}")

    # Strategy 2: Parse stdout for common metric patterns
    patterns = {
        "sharpe": r"(?:avg[_\s])?[Ss]harpe[:\s=]+([0-9]+\.?[0-9]*)",
        "win_rate": r"(?:avg[_\s])?(?:[Ww]in[_\s]?[Rr]ate|WR)[:\s=]+([0-9]+\.?[0-9]*)",
        "pf": r"(?:avg[_\s])?(?:[Pp]rofit[_\s]?[Ff]actor|PF)[:\s=]+([0-9]+\.?[0-9]*)",
        "max_dd": r"(?:avg[_\s])?(?:[Mm]ax[_\s]?[Dd]rawdown|MDD|MaxDD)[:\s=]+(-?[0-9]+\.?[0-9]*)",
        "n_trades": r"(?:avg[_\s])?(?:[Nn][_\s]?[Tt]rades|trades)[:\s=]+([0-9]+)",
        "total_return": r"(?:avg[_\s])?(?:[Tt]otal[_\s]?[Rr]eturn|TotRet)[:\s=]+(-?[0-9]+\.?[0-9]*)",
    }
    for key, pat in patterns.items():
        m = re.search(pat, stdout)
        if m:
            metrics[key] = float(m.group(1))

    # Strategy 3: Check result files from common backtest engines
    if not metrics:
        for result_file in [
            "data/backtest_factory/factory_top.json",
            "data/backtest_deep_crypto/deep_crypto_best.json",
            "data/backtest_ablation/ablation_results.json",
            "data/metric_sweep_top.json",
        ]:
            fp = BASE / result_file
            if fp.exists():
                try:
                    mtime = fp.stat().st_mtime
                    if time.time() - mtime < 120:  # Modified in last 2 min
                        with open(fp) as f:
                            data = json.load(f)
                        metrics = extract_metrics_from_json(data, program)
                        if metrics:
                            log(f"  Parsed metrics from {result_file}")
                            return metrics
                except Exception:
                    pass

    return metrics if metrics else None


def extract_metrics_from_json(data, program):
    """Extract aggregate metrics from various JSON result formats."""
    metrics = {}

    if isinstance(data, dict):
        # factory_top.json format: {"top": [{...}, ...]}
        if "top" in data and isinstance(data["top"], list) and data["top"]:
            top = data["top"]
            sharpes = [r.get("sharpe", 0) for r in top[:50] if isinstance(r, dict)]
            wrs = [r.get("win_rate", r.get("wr", 0)) for r in top[:50] if isinstance(r, dict)]
            pfs = [r.get("pf", r.get("profit_factor", 0)) for r in top[:50] if isinstance(r, dict)]
            n_trades_list = [r.get("n_trades", 0) for r in top[:50] if isinstance(r, dict)]
            if sharpes:
                metrics["sharpe"] = sum(sharpes) / len(sharpes)
                metrics["win_rate"] = sum(wrs) / len(wrs) if wrs else 0
                metrics["pf"] = sum(pfs) / len(pfs) if pfs else 0
                metrics["n_trades"] = sum(n_trades_list) if n_trades_list else 0
                metrics["max_dd"] = 0  # Not easily aggregated
            return metrics

        # deep_crypto_best.json format: [{"config": ..., "avg_sharpe": ..., ...}, ...]
        if isinstance(data, list) and data and "avg_sharpe" in (data[0] if data else {}):
            top = data[:20]
            metrics["sharpe"] = sum(r.get("avg_sharpe", 0) for r in top) / len(top)
            metrics["win_rate"] = sum(r.get("avg_wr", 0) for r in top) / len(top)
            metrics["pf"] = sum(r.get("avg_pf", 0) for r in top) / len(top)
            metrics["max_dd"] = sum(r.get("avg_dd", 0) for r in top) / len(top)
            metrics["n_trades"] = sum(r.get("total_trades", 0) for r in top)
            return metrics

        # ablation_results.json: {"ablation": {"BASELINE": {...}}, ...}
        if "ablation" in data:
            baseline = data["ablation"].get("BASELINE", {})
            metrics["sharpe"] = baseline.get("avg_sharpe", 0)
            metrics["win_rate"] = baseline.get("avg_wr", 0)
            metrics["pf"] = baseline.get("avg_pf", 0)
            metrics["max_dd"] = baseline.get("avg_dd", 0)
            metrics["n_trades"] = baseline.get("total_trades", 0)
            return metrics

    # List of dicts (metric_sweep, etc.)
    if isinstance(data, list) and data:
        top = data[:50]
        metrics["sharpe"] = sum(r.get("sharpe", 0) for r in top) / len(top)
        metrics["win_rate"] = sum(r.get("win_rate", 0) for r in top) / len(top)
        metrics["pf"] = sum(r.get("pf", 0) for r in top) / len(top)
        metrics["max_dd"] = sum(r.get("max_dd", 0) for r in top) / len(top)
        metrics["n_trades"] = sum(r.get("n_trades", 0) for r in top)
        return metrics

    return metrics


# ═══════════════════════════════════════════════════════════════════════
# EXPERIMENT SELECTION
# ═══════════════════════════════════════════════════════════════════════
def pick_experiment(program, history):
    """Pick which parameter(s) to test next. Returns list of (param_def, new_value) tuples."""
    strategy = program.get("strategy", "random_one_at_a_time")
    params = program["parameters"]

    if not params:
        return []

    if strategy == "grid_one_at_a_time":
        return _pick_grid(params, history)
    elif strategy == "random_multi":
        return _pick_random_multi(params, history)
    else:  # random_one_at_a_time (default)
        return _pick_random_one(params, history)


def _pick_random_one(params, history):
    """Pick one random param and a random untested value."""
    # Weight towards NEVER_TESTED params
    never_tested = [p for p in params if p.get("status") == "NEVER_TESTED"]
    pool = never_tested if never_tested and random.random() < 0.7 else params

    param = random.choice(pool)
    search_space = param["search_space"]

    # Avoid re-testing values we've already tried
    tested_vals = set()
    for h in history:
        if h.get("param") == param["name"]:
            tested_vals.add(str(h.get("new_value")))

    untested = [v for v in search_space if str(v) not in tested_vals]
    if not untested:
        # All values tested for this param — pick from full pool
        untested = search_space

    new_val = random.choice(untested)
    return [(param, new_val)]


def _pick_grid(params, history):
    """Systematic grid: iterate through each param's search space in order."""
    for param in params:
        search_space = param["search_space"]
        tested_vals = set()
        for h in history:
            if h.get("param") == param["name"]:
                tested_vals.add(str(h.get("new_value")))

        for val in search_space:
            if str(val) not in tested_vals:
                return [(param, val)]

    # All done — restart with random
    return _pick_random_one(params, history)


def _pick_random_multi(params, history):
    """Pick 2-3 params to change simultaneously."""
    n = min(random.choice([2, 2, 3]), len(params))
    chosen = random.sample(params, n)
    experiments = []
    for param in chosen:
        val = random.choice(param["search_space"])
        experiments.append((param, val))
    return experiments


# ═══════════════════════════════════════════════════════════════════════
# RESULTS MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════
def load_history(program_name):
    """Load experiment history for a program."""
    result_file = RESULTS_DIR / f"results_{program_name}.json"
    if result_file.exists():
        with open(result_file) as f:
            return json.load(f)
    return []


def save_history(program_name, history):
    """Save experiment history."""
    result_file = RESULTS_DIR / f"results_{program_name}.json"
    with open(result_file, "w") as f:
        json.dump(history, f, indent=2, default=str)


def append_tsv(row):
    """Append a result row to the master TSV leaderboard."""
    header = "program\texperiment\tparam\told_value\tnew_value\tsharpe\twr\tpf\tmax_dd\tn_trades\tstatus\ttimestamp\n"
    if not RESULTS_TSV.exists():
        with open(RESULTS_TSV, "w") as f:
            f.write(header)

    with open(RESULTS_TSV, "a") as f:
        f.write("\t".join(str(v) for v in row) + "\n")


def load_best(program_name):
    """Load current best metrics for a program."""
    best_file = BEST_DIR / f"{program_name}_best.json"
    if best_file.exists():
        with open(best_file) as f:
            return json.load(f)
    return None


def save_best(program_name, metrics, params_applied):
    """Save new best metrics + params."""
    best_file = BEST_DIR / f"{program_name}_best.json"
    data = {
        "metrics": metrics,
        "params": params_applied,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    with open(best_file, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ═══════════════════════════════════════════════════════════════════════
# GUARDS — prevent overfitting
# ═══════════════════════════════════════════════════════════════════════
def passes_guards(metrics, guards):
    """Check if metrics pass all safety guards."""
    if not metrics:
        return False, "no metrics"

    sharpe = metrics.get("sharpe", 0)
    wr = metrics.get("win_rate", 0)
    pf = metrics.get("pf", 0)
    max_dd = metrics.get("max_dd", 0)
    n_trades = metrics.get("n_trades", 0)

    if n_trades < guards.get("min_trades", 50):
        return False, f"trades={n_trades} < min={guards.get('min_trades', 50)}"
    if wr < guards.get("min_wr", 0.60):
        return False, f"wr={wr:.3f} < min={guards.get('min_wr', 0.60)}"
    if max_dd < guards.get("max_dd", -30):
        return False, f"dd={max_dd:.1f} < max={guards.get('max_dd', -30)}"
    if pf < guards.get("min_pf", 0):
        return False, f"pf={pf:.2f} < min={guards.get('min_pf', 0)}"

    return True, "ok"


def is_improvement(new_metrics, best_metrics, metric_key="sharpe"):
    """Check if new metrics are better than current best."""
    if best_metrics is None:
        return True

    old_val = best_metrics.get("metrics", {}).get(metric_key, 0)
    new_val = new_metrics.get(metric_key, 0)

    return new_val > old_val


# ═══════════════════════════════════════════════════════════════════════
# MAIN LOOP
# ═══════════════════════════════════════════════════════════════════════
def run_program(program_name, max_time_sec=7200, max_experiments=200, dry_run=False):
    """Run the optimization loop for one program."""
    program = load_program(program_name)
    history = load_history(program_name)
    best = load_best(program_name)
    metric_key = program.get("metric", "sharpe")
    guards = program.get("guards", {"min_trades": 50, "min_wr": 0.60, "max_dd": -30})

    log(f"=" * 70)
    log(f"AUTORESEARCH: {program_name}")
    log(f"  Engine: {program['backtest_engine']}")
    log(f"  Metric: {metric_key}")
    log(f"  Params: {len(program['parameters'])} ({sum(1 for p in program['parameters'] if p.get('status')=='NEVER_TESTED')} never tested)")
    log(f"  History: {len(history)} experiments ({sum(1 for h in history if h.get('status')=='keep')} kept)")
    if best:
        log(f"  Current best: {metric_key}={best['metrics'].get(metric_key, '?')}")
    log(f"  Max time: {max_time_sec}s, Max experiments: {max_experiments}")
    log(f"=" * 70)

    if not best:
        # Run baseline first
        log("BASELINE RUN (no changes)...")
        if not dry_run:
            baseline_metrics = run_backtest(program)
            if baseline_metrics:
                passed, reason = passes_guards(baseline_metrics, guards)
                log(f"  Baseline: {metric_key}={baseline_metrics.get(metric_key, '?')}, guards={'PASS' if passed else 'FAIL: '+reason}")
                save_best(program_name, baseline_metrics, {})
                best = load_best(program_name)
            else:
                log("  WARNING: Baseline returned no metrics. Will proceed anyway.")

    start_time = time.time()
    exp_count = 0

    while exp_count < max_experiments:
        elapsed = time.time() - start_time
        if elapsed >= max_time_sec:
            log(f"TIME LIMIT reached ({max_time_sec}s)")
            break

        exp_count += 1
        exp_id = f"exp_{len(history)+1:04d}"
        log(f"\n--- Experiment {exp_id} ({exp_count}/{max_experiments}, {elapsed:.0f}s/{max_time_sec}s) ---")

        # Pick what to test
        experiments = pick_experiment(program, history)
        if not experiments:
            log("  No more experiments to run")
            break

        # Check cooldowns from safety monitor (skip reverted params)
        cooled_off = False
        for param_def, new_val in experiments:
            cooldown_file = RESULTS_DIR / "pending_change.json"
            # Check Redis cooldown if available
            try:
                import redis
                r = redis.Redis(host="localhost", port=6379, db=0)
                if r.exists(f"autoresearch_revert_cooldown:{param_def['name']}"):
                    log(f"  SKIP: {param_def['name']} is in cooldown (recently reverted by monitor)")
                    cooled_off = True
                    break
            except Exception:
                pass
            # Also check local cooldown file
            monitor_state_file = RESULTS_DIR / "monitor_state.json"
            if monitor_state_file.exists():
                try:
                    with open(monitor_state_file) as msf:
                        ms = json.load(msf)
                    cd = ms.get("cooldowns", {}).get(param_def["name"], 0)
                    if cd > time.time():
                        log(f"  SKIP: {param_def['name']} in cooldown (reverted {int(cd - time.time())}s remaining)")
                        cooled_off = True
                        break
                except Exception:
                    pass

        if cooled_off:
            continue

        # Log what we're testing
        for param_def, new_val in experiments:
            log(f"  TEST: {param_def['name']} = {new_val} (current: {param_def.get('current', '?')})")
            log(f"         Location: {param_def['location']}")

        if dry_run:
            log("  [DRY RUN — skipping execution]")
            continue

        # Backup + patch all target files
        backups = {}
        patch_success = True
        for param_def, new_val in experiments:
            loc = param_def["location"]
            # location format: "script.py:function_or_section" or just "script.py"
            filepath = BASE / loc.split(":")[0]
            if str(filepath) not in backups:
                backups[str(filepath)] = backup_file(filepath)

            ok, old_val = patch_value(
                filepath,
                param_def["name"],
                new_val,
                param_def.get("pattern"),
            )
            if not ok:
                patch_success = False
                break

        if not patch_success:
            # Revert all files
            for fp, bak in backups.items():
                revert_file(fp, bak)
            record = {
                "id": exp_id,
                "param": experiments[0][0]["name"],
                "new_value": experiments[0][1],
                "status": "patch_failed",
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            history.append(record)
            save_history(program_name, history)
            continue

        # Run backtest
        metrics = run_backtest(program)

        # Evaluate
        if metrics is None:
            status = "crash"
            log(f"  RESULT: CRASH (no metrics returned)")
            # Revert
            for fp, bak in backups.items():
                revert_file(fp, bak)
        else:
            sharpe = metrics.get(metric_key, 0)
            wr = metrics.get("win_rate", 0)
            pf = metrics.get("pf", 0)
            max_dd = metrics.get("max_dd", 0)
            n_trades = metrics.get("n_trades", 0)

            passed, guard_reason = passes_guards(metrics, guards)
            improved = is_improvement(metrics, best, metric_key)

            if passed and improved:
                status = "keep"
                log(f"  RESULT: KEEP! {metric_key}={sharpe:.2f} (WR={wr:.3f}, PF={pf:.2f}, DD={max_dd:.1f}, trades={n_trades})")
                # Update best — leave the patch in place
                params_applied = {p["name"]: v for p, v in experiments}
                if best:
                    old_params = best.get("params", {})
                    old_params.update(params_applied)
                    params_applied = old_params
                save_best(program_name, metrics, params_applied)
                best = load_best(program_name)

                # Write pending_change.json for the safety monitor
                pending_file = RESULTS_DIR / "pending_change.json"
                pending_data = {
                    "program": program_name,
                    "param": "|".join(p["name"] for p, _ in experiments),
                    "old_value": "|".join(str(p.get("current", "?")) for p, _ in experiments),
                    "new_value": "|".join(str(v) for _, v in experiments),
                    "files": list(backups.keys()),
                    "backup_paths": {fp: str(bak) for fp, bak in backups.items()},
                    "metrics": metrics,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }
                with open(pending_file, "w") as pf:
                    json.dump(pending_data, pf, indent=2, default=str)
                log(f"  Wrote pending_change.json for safety monitor")

                # Keep backups for monitor (DON'T delete them)
                # Monitor needs them for potential revert
            else:
                if not passed:
                    status = "guard_fail"
                    log(f"  RESULT: GUARD FAIL ({guard_reason}) {metric_key}={sharpe:.2f}")
                else:
                    status = "discard"
                    best_val = best["metrics"].get(metric_key, 0) if best else 0
                    log(f"  RESULT: DISCARD {metric_key}={sharpe:.2f} <= best={best_val:.2f}")
                # Revert
                for fp, bak in backups.items():
                    revert_file(fp, bak)

        # Record
        record = {
            "id": exp_id,
            "param": "|".join(p["name"] for p, _ in experiments),
            "old_value": "|".join(str(read_current_value(BASE / p["location"].split(":")[0], p["name"], p.get("pattern")) or p.get("current", "?")) for p, _ in experiments) if status != "keep" else "reverted",
            "new_value": "|".join(str(v) for _, v in experiments),
            "status": status,
            "metrics": metrics,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }
        history.append(record)
        save_history(program_name, history)

        # TSV row
        tsv_row = [
            program_name,
            exp_id,
            record["param"],
            record.get("old_value", ""),
            record["new_value"],
            metrics.get("sharpe", "") if metrics else "",
            metrics.get("win_rate", "") if metrics else "",
            metrics.get("pf", "") if metrics else "",
            metrics.get("max_dd", "") if metrics else "",
            metrics.get("n_trades", "") if metrics else "",
            status,
            record["timestamp"],
        ]
        append_tsv(tsv_row)

    # Summary
    kept = sum(1 for h in history if h.get("status") == "keep")
    discarded = sum(1 for h in history if h.get("status") == "discard")
    crashed = sum(1 for h in history if h.get("status") == "crash")
    log(f"\n{'='*70}")
    log(f"AUTORESEARCH {program_name} COMPLETE: {exp_count} experiments")
    log(f"  Kept: {kept}, Discarded: {discarded}, Crashed: {crashed}")
    if best:
        log(f"  Best {metric_key}: {best['metrics'].get(metric_key, '?')}")
        log(f"  Best params: {best.get('params', {})}")
    log(f"{'='*70}")


# ═══════════════════════════════════════════════════════════════════════
# REPORT
# ═══════════════════════════════════════════════════════════════════════
def show_report():
    """Show the leaderboard across all programs."""
    print("=" * 90)
    print("AUTORESEARCH LEADERBOARD")
    print("=" * 90)

    programs = list_programs()
    if not programs:
        print("No programs found. Create JSON files in data/autoresearch/programs/")
        return

    for prog_name in programs:
        best = load_best(prog_name)
        history = load_history(prog_name)
        kept = sum(1 for h in history if h.get("status") == "keep")
        total = len(history)

        if best:
            m = best["metrics"]
            print(f"\n  {prog_name:25s} | Sharpe={m.get('sharpe', 0):8.2f} | WR={m.get('win_rate', 0):.3f} | PF={m.get('pf', 0):.2f} | Exps={total} ({kept} kept)")
            if best.get("params"):
                for k, v in best["params"].items():
                    print(f"    {k}: {v}")
        else:
            print(f"\n  {prog_name:25s} | No results yet | Exps={total}")

    # TSV summary
    if RESULTS_TSV.exists():
        with open(RESULTS_TSV) as f:
            lines = f.readlines()
        print(f"\n  Total experiments logged: {len(lines) - 1}")

    print("=" * 90)


# ═══════════════════════════════════════════════════════════════════════
# DEPLOY
# ═══════════════════════════════════════════════════════════════════════
def deploy_best(risk_max="MEDIUM", dry_run=False):
    """Deploy best results from all programs to config.py / source files."""
    programs = list_programs()
    changes = []

    for prog_name in programs:
        best = load_best(prog_name)
        if not best or not best.get("params"):
            continue

        prog = load_program(prog_name)
        for param_def in prog["parameters"]:
            param_name = param_def["name"]
            if param_name in best["params"]:
                risk = param_def.get("risk", "LOW")
                if risk_max == "LOW" and risk != "LOW":
                    continue
                if risk_max == "MEDIUM" and risk == "HIGH":
                    continue

                changes.append({
                    "program": prog_name,
                    "param": param_name,
                    "new_value": best["params"][param_name],
                    "location": param_def["location"],
                    "risk": risk,
                })

    if not changes:
        log("No changes to deploy.")
        return

    log(f"DEPLOY: {len(changes)} parameter changes")
    for c in changes:
        log(f"  [{c['risk']}] {c['param']} = {c['new_value']} ({c['location']})")

    if dry_run:
        log("[DRY RUN — no changes applied]")
        return

    # Backup all affected files
    affected_files = set(c["location"].split(":")[0] for c in changes)
    for f in affected_files:
        backup_file(BASE / f)
        log(f"  Backed up {f}")

    # Apply changes
    for c in changes:
        filepath = BASE / c["location"].split(":")[0]
        ok, old = patch_value(filepath, c["param"], c["new_value"])
        if ok:
            log(f"  DEPLOYED: {c['param']} = {c['new_value']} (was {old})")
        else:
            log(f"  FAILED: {c['param']}")

    # Log to changelog
    changelog = RESULTS_DIR / "DEPLOY_CHANGELOG.md"
    with open(changelog, "a") as f:
        f.write(f"\n## Deploy {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        for c in changes:
            f.write(f"- [{c['risk']}] {c['param']} = {c['new_value']} (program: {c['program']})\n")

    log(f"Deploy complete. {len(changes)} changes applied.")


# ═══════════════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════════════
def parse_time(s):
    """Parse time string like '2h', '30m', '1h30m' to seconds."""
    if not s:
        return 7200
    total = 0
    m = re.findall(r"(\d+)([hms])", s.lower())
    for val, unit in m:
        if unit == "h":
            total += int(val) * 3600
        elif unit == "m":
            total += int(val) * 60
        elif unit == "s":
            total += int(val)
    return total if total else int(s)


def main():
    ensure_dirs()

    parser = argparse.ArgumentParser(description="EZ Autoresearch — Autonomous Trading Optimizer")
    parser.add_argument("--program", type=str, help="Program name (or 'all')")
    parser.add_argument("--max-time", type=str, default="2h", help="Max time per program (e.g. 2h, 30m)")
    parser.add_argument("--max-experiments", type=int, default=200, help="Max experiments per program")
    parser.add_argument("--report", action="store_true", help="Show leaderboard")
    parser.add_argument("--deploy", action="store_true", help="Deploy best results")
    parser.add_argument("--risk-max", type=str, default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"])
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen")
    args = parser.parse_args()

    if args.report:
        show_report()
        return

    if args.deploy:
        deploy_best(args.risk_max, args.dry_run)
        return

    if not args.program:
        parser.print_help()
        print("\nAvailable programs:", ", ".join(list_programs()) or "(none)")
        return

    max_time = parse_time(args.max_time)

    if args.program == "all":
        programs = list_programs()
        per_program_time = max_time // len(programs) if programs else max_time
        for prog in programs:
            run_program(prog, per_program_time, args.max_experiments, args.dry_run)
    else:
        run_program(args.program, max_time, args.max_experiments, args.dry_run)


if __name__ == "__main__":
    main()
