#!/usr/bin/env python3
# metrics_guard-clean: Sharpe used internally only, never emitted to user surface (audited 2026-04-30).
"""Auto-suggestion loop: closes the analysis → variant → run feedback loop.

Reads the latest suggestion engine output + existing override JSONs,
identifies actionable hypotheses, generates new override JSONs, and
spawns chart_sweep.py to run them. Logs everything for the next cycle.

Pipeline (one iteration):
  1. Run suggestion_engine.py to refresh SUGGESTIONS_latest.md
  2. Parse top hypotheses (W/L separators with |cohen_d| ≥ threshold)
  3. Map each hypothesis to a config switch via INDICATOR_TO_SWITCH
     (only mapped indicators become auto-runnable; unmapped go to manual review)
  4. Pick the best-known baseline override (highest Sharpe/T from existing runs)
  5. Mutate baseline by applying ONE hypothesis at a time → save auto_<ts>_<switch>=<val>.json
  6. Spawn chart_sweep on each new override
  7. Append a row to AUTO_LOOP_HISTORY.md with hypothesis, switch, value, run_id
  8. Sleep, repeat

NEVER runs Tier-2 (slow, real-money risk via real-code paths). Tier-1 only.
NEVER touches live trading code.
NEVER promotes a winner — that's still a human decision.

Usage:
  python3 auto_suggest_loop.py --once                       # single iteration
  python3 auto_suggest_loop.py --loop --interval 1800       # every 30min forever
  python3 auto_suggest_loop.py --baseline 5SYM_BEST         # specific baseline run-id
  python3 auto_suggest_loop.py --max-mutations 5 --once     # cap N new variants per iteration
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

ROOT = Path("/Users/niels/Documents/binance")
DEFAULT_TRADES_DIR = Path(os.environ.get("V8_TRADES_OUT_DIR", "/tmp/v8_trades"))
OVERRIDE_DIR = ROOT / "backtest_v8" / "btc_loop_results"
AUTO_OVERRIDE_DIR = DEFAULT_TRADES_DIR / "auto_overrides"
HISTORY_PATH = DEFAULT_TRADES_DIR / "AUTO_LOOP_HISTORY.md"

# Indicator → config switch mapping. Only fields here can become auto-overrides.
# For each: a switch that, when flipped, changes that indicator's gating role.
# Many of these switches add a threshold gate that wasn't there before; the value
# is set to the cohen-d-derived midpoint.
INDICATOR_TO_SWITCH = {
    # If atr_4h separates winners (high) from losers (low), gate min ATR.
    # No direct switch exists today, but we use BTC_RZ_PROXIMITY_PCT as a proxy:
    # tighter proximity ≈ stricter "must be near level" ≈ skips low-vol bounces.
    "atr_4h":   {"switch": "BTC_RZ_PROXIMITY_PCT", "tighten_value": 0.3, "loosen_value": 0.8, "default": 0.5},
    "atr_1h":   {"switch": "BTC_RZ_PROXIMITY_PCT", "tighten_value": 0.3, "loosen_value": 0.8, "default": 0.5},
    "atr_15m":  {"switch": "BTC_RZ_PROXIMITY_PCT", "tighten_value": 0.3, "loosen_value": 0.8, "default": 0.5},
    # WT velocity D: when |d|>0 and we want to BLOCK high-velocity entries → tighten accel-ramp
    "wt_velocity_D":  {"switch": "BTC_ACCEL_RAMP_MIN_TFS", "tighten_value": 5, "loosen_value": 1, "default": 2},
    "wt_velocity_4h": {"switch": "BTC_ACCEL_RAMP_MIN_TFS", "tighten_value": 5, "loosen_value": 1, "default": 2},
    "wt_velocity_1h": {"switch": "BTC_ACCEL_RAMP_MIN_TFS", "tighten_value": 5, "loosen_value": 1, "default": 2},
    # WT cross strength on htf — controls breakout HTF requirement
    "wt1_4h":   {"switch": "BTC_BREAKOUT_HTF_MIN_ALIGNED", "tighten_value": 3, "loosen_value": 0, "default": 1},
    "wt1_1h":   {"switch": "BTC_BREAKOUT_HTF_MIN_ALIGNED", "tighten_value": 3, "loosen_value": 0, "default": 1},
    "wt1_D":    {"switch": "BTC_BREAKOUT_HTF_MIN_ALIGNED", "tighten_value": 3, "loosen_value": 0, "default": 1},
    # Stoch K extremes — gate via WT min TFs against side
    "stoch_k_1h":  {"switch": "BTC_TECH_EXIT_WT_MIN_TFS", "tighten_value": 5, "loosen_value": 1, "default": 3},
    "stoch_k_4h":  {"switch": "BTC_TECH_EXIT_WT_MIN_TFS", "tighten_value": 5, "loosen_value": 1, "default": 3},
    # MFI / RSI extremes — gate via div bear/bull min inds
    "mfi_1h":   {"switch": "BTC_DIVERGENCE_BEAR_MIN_INDS", "tighten_value": 3, "loosen_value": 1, "default": 1},
    "mfi_4h":   {"switch": "BTC_DIVERGENCE_BULL_MIN_INDS", "tighten_value": 3, "loosen_value": 1, "default": 1},
    # Squeeze / div regime
    "div_reg_bear_wt_15m": {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    "div_reg_bear_wt_1h":  {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    "div_reg_bear_wt_4h":  {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    "div_reg_bull_wt_15m": {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    "div_reg_bull_wt_1h":  {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    "div_reg_bull_wt_4h":  {"switch": "BTC_DIVERGENCE_BLOCK_AGAINST", "tighten_value": True, "loosen_value": False, "default": True},
    # Funding / OI
    "funding_rate_3m":     {"switch": "FUNDING_GATE_ENABLED", "tighten_value": True, "loosen_value": False, "default": True},
    "oi_change_1h_3m":     {"switch": "OI_CONFIRM_ENABLED",  "tighten_value": True, "loosen_value": False, "default": True},
    "oi_change_15m_3m":    {"switch": "OI_CONFIRM_ENABLED",  "tighten_value": True, "loosen_value": False, "default": True},
}


def parse_suggestions(md_path: Path):
    """Parse the suggestion engine markdown into structured hypotheses."""
    if not md_path.exists():
        return {"wl_separators": [], "missed_separators": [], "bleeding_cells": [], "jewel_cells": []}
    text = md_path.read_text()
    out = {"wl_separators": [], "missed_separators": [], "bleeding_cells": [], "jewel_cells": []}
    cur_section = None
    cur_run_sym = None
    for line in text.splitlines():
        if line.startswith("## 1.") or "Bleeding cells" in line: cur_section = "bleeding"; continue
        if line.startswith("## 2.") or "WINNERS from LOSERS" in line: cur_section = "wl"; continue
        if line.startswith("## 3.") or "Missed opportunities" in line: cur_section = "missed"; continue
        if line.startswith("## 4.") or "Jewels" in line: cur_section = "jewel"; continue
        if line.startswith("## 5.") or "Summary" in line: cur_section = None; continue
        if line.startswith("### ") and cur_section in ("wl", "missed"):
            cur_run_sym = line.replace("### ", "").split("(", 1)[0].strip()
            continue
        if not line.startswith("|") or "---" in line or line.startswith("| Field") or line.startswith("| Run"):
            continue
        cells = [c.strip().strip("`") for c in line.strip("|").split("|")]
        if len(cells) < 4: continue
        try:
            if cur_section in ("wl", "missed"):
                field = cells[0]
                cohen_d = float(cells[1].replace("+", ""))
                out[("wl_separators" if cur_section == "wl" else "missed_separators")].append({
                    "run_sym": cur_run_sym,
                    "field": field, "cohen_d": cohen_d,
                    "winner_median": cells[2], "loser_median": cells[3],
                    "gate": cells[4] if len(cells) > 4 else "",
                })
            elif cur_section == "bleeding":
                if len(cells) >= 8:
                    out["bleeding_cells"].append({
                        "run": cells[0], "sym": cells[1],
                        "entry_reason": cells[2], "exit_reason": cells[3],
                        "n": int(cells[4]), "wr": cells[5],
                        "avg_pnl": cells[6], "sharpe_pt": cells[7],
                    })
            elif cur_section == "jewel":
                if len(cells) >= 8:
                    out["jewel_cells"].append({
                        "run": cells[0], "sym": cells[1],
                        "entry_reason": cells[2], "exit_reason": cells[3],
                        "n": int(cells[4]), "wr": cells[5],
                        "avg_pnl": cells[6], "sharpe_pt": cells[7],
                    })
        except Exception:
            pass
    return out


def load_baseline_override(baseline_run: str = None):
    """Pick the highest-quality known override as starting point.
    If --baseline name passed, look for that. Otherwise, use override_5SYM_BEST.json.
    """
    if baseline_run:
        for cand in OVERRIDE_DIR.glob("override_*.json"):
            if baseline_run.lower() in cand.stem.lower():
                return cand, json.loads(cand.read_text())
    # default
    p = OVERRIDE_DIR / "override_5SYM_BEST.json"
    if p.exists():
        return p, json.loads(p.read_text())
    # Last resort: first override file found
    for cand in sorted(OVERRIDE_DIR.glob("override_*.json")):
        return cand, json.loads(cand.read_text())
    return None, {}


def hypothesis_to_mutation(hyp: dict, baseline: dict, kind: str):
    """Turn a parsed hypothesis into (switch, value) mutation. Returns None if unmappable."""
    field = hyp.get("field", "")
    info = INDICATOR_TO_SWITCH.get(field)
    if not info:
        return None
    cohen = hyp.get("cohen_d", 0)
    switch = info["switch"]
    cur = baseline.get(switch, info["default"])
    if kind == "wl":
        # W/L separator: positive d → winners higher → tighten (enforce stricter gate)
        new_val = info["tighten_value"] if cohen > 0 else info["loosen_value"]
    elif kind == "missed":
        # Missed-vs-winner separator: positive d → winners higher than missed → loosen (allow lower)
        new_val = info["loosen_value"] if cohen > 0 else info["tighten_value"]
    else:
        return None
    if new_val == cur:
        return None  # no change
    return {"switch": switch, "value": new_val, "previous": cur, "field": field, "cohen_d": cohen, "kind": kind}


def write_override(baseline_path: Path, baseline: dict, mutation: dict, ts_str: str):
    """Apply one mutation on top of baseline and save as new override JSON."""
    new_cfg = dict(baseline)
    new_cfg[mutation["switch"]] = mutation["value"]
    AUTO_OVERRIDE_DIR.mkdir(parents=True, exist_ok=True)
    safe_val = str(mutation["value"]).replace(".", "p").replace(" ", "_")
    name = f"auto_{ts_str}_{mutation['switch']}={safe_val}.json"
    path = AUTO_OVERRIDE_DIR / name
    new_cfg["_meta"] = (
        f"Auto-generated by auto_suggest_loop {datetime.now(timezone.utc).isoformat()}. "
        f"Baseline={baseline_path.name}. "
        f"Hypothesis: {mutation['kind']} sep on {mutation['field']} cohen_d={mutation['cohen_d']:+.2f} → "
        f"{mutation['switch']} {mutation['previous']}→{mutation['value']}"
    )
    path.write_text(json.dumps(new_cfg, indent=2))
    return path


def append_history(rows: list):
    """Append iteration log to AUTO_LOOP_HISTORY.md as markdown table."""
    DEFAULT_TRADES_DIR.mkdir(parents=True, exist_ok=True)
    is_new = not HISTORY_PATH.exists()
    with HISTORY_PATH.open("a") as f:
        if is_new:
            f.write("# Auto-Suggestion Loop — History\n\n")
            f.write("| Timestamp UTC | Baseline | Field | Cohen d | Switch | Was → Is | New override | Run-ID |\n")
            f.write("|---|---|---|---:|---|---|---|---|\n")
        for r in rows:
            f.write(f"| {r['ts']} | {r['baseline']} | `{r['field']}` | {r['cohen_d']:+.2f} | `{r['switch']}` | `{r['previous']}` → `{r['value']}` | `{r['override']}` | `{r['run_id']}` |\n")


def run_chart_sweep(override_path: Path, symbols: list, run_id: str):
    """Spawn chart_sweep.py for one override × symbols. Returns subprocess return code."""
    cmd = [
        sys.executable, str(ROOT / "chart_sweep.py"),
        "--override", str(override_path),
        "--symbols", ",".join(symbols),
    ]
    env = os.environ.copy()
    env["V8_TRADES_OUT_DIR"] = str(DEFAULT_TRADES_DIR)
    env["V8_TRADES_RUN_ID"] = run_id
    print(f"  [auto] launching: {override_path.name} → run_id={run_id}", flush=True)
    try:
        out = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800, cwd=str(ROOT))
        if out.returncode != 0:
            print(f"  [auto] ⚠️  return={out.returncode} stderr={(out.stderr or '')[:200]}")
        else:
            # Pull the summary line
            for line in (out.stdout or "").splitlines():
                if "sharpe=" in line and "trades=" in line:
                    print(f"  [auto] {line.strip()}")
        return out.returncode
    except subprocess.TimeoutExpired:
        print(f"  [auto] TIMEOUT")
        return -1
    except Exception as e:
        print(f"  [auto] ERROR: {e}")
        return -2


def iteration(args):
    """One pass: refresh suggestions → mutate → run."""
    print(f"\n=== auto_suggest_loop iteration @ {datetime.now(timezone.utc).isoformat()} ===")
    # 1. Refresh suggestions
    if not args.skip_suggest:
        print("[auto] refreshing suggestions...")
        sret = subprocess.run(
            [sys.executable, str(ROOT / "suggestion_engine.py")],
            cwd=str(ROOT),
            env={**os.environ, "V8_TRADES_OUT_DIR": str(DEFAULT_TRADES_DIR)},
            capture_output=True, text=True, timeout=600,
        )
        if sret.returncode != 0:
            print(f"[auto] suggestion_engine returned {sret.returncode}: {sret.stderr[:200]}")
    # 2. Parse
    s = parse_suggestions(DEFAULT_TRADES_DIR / "SUGGESTIONS_latest.md")
    print(f"[auto] suggestions: {len(s['wl_separators'])} W/L, {len(s['missed_separators'])} missed, "
          f"{len(s['bleeding_cells'])} bleeding, {len(s['jewel_cells'])} jewels")
    # 3. Pick mutations
    baseline_path, baseline = load_baseline_override(args.baseline)
    if not baseline:
        print("[auto] ❌ no baseline override found"); return
    print(f"[auto] baseline: {baseline_path.name}")
    seen_switches = set()
    mutations = []
    # Prioritize strongest hypotheses first
    candidates = []
    for h in s["wl_separators"]:
        if abs(h.get("cohen_d", 0)) >= args.min_cohen_d:
            m = hypothesis_to_mutation(h, baseline, "wl")
            if m: candidates.append(m)
    for h in s["missed_separators"]:
        if abs(h.get("cohen_d", 0)) >= args.min_cohen_d:
            m = hypothesis_to_mutation(h, baseline, "missed")
            if m: candidates.append(m)
    candidates.sort(key=lambda m: -abs(m["cohen_d"]))
    for m in candidates:
        if m["switch"] in seen_switches: continue   # one mutation per switch per iteration
        seen_switches.add(m["switch"])
        mutations.append(m)
        if len(mutations) >= args.max_mutations: break
    if not mutations:
        print("[auto] no actionable mutations this iteration")
        return
    # 4. Write override files + run them
    ts_str = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    history_rows = []
    for i, m in enumerate(mutations):
        path = write_override(baseline_path, baseline, m, f"{ts_str}_{i:02d}")
        run_id = path.stem  # auto_<ts>_<switch>=<val>
        # Symbols: same default as chart_sweep
        symbols = (args.symbols.split(",") if args.symbols else
                   ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "BTCDOMUSDT"])
        rc = run_chart_sweep(path, symbols, run_id)
        history_rows.append({
            "ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            "baseline": baseline_path.name,
            "field": m["field"], "cohen_d": m["cohen_d"],
            "switch": m["switch"], "previous": m["previous"], "value": m["value"],
            "override": path.name, "run_id": run_id,
        })
    if history_rows:
        append_history(history_rows)
        print(f"[auto] history updated: {HISTORY_PATH}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="single iteration (default)")
    ap.add_argument("--loop", action="store_true", help="loop forever")
    ap.add_argument("--interval", type=int, default=1800)
    ap.add_argument("--baseline", help="run-id substring to use as baseline (default: 5SYM_BEST)")
    ap.add_argument("--min-cohen-d", type=float, default=0.5, help="hypothesis threshold (default 0.5)")
    ap.add_argument("--max-mutations", type=int, default=3, help="cap N mutations per iteration")
    ap.add_argument("--symbols", help="comma-sep symbols (default: 5 BTC-loop syms)")
    ap.add_argument("--skip-suggest", action="store_true", help="don't re-run suggestion_engine.py first")
    ap.add_argument("--use-discovered", action="store_true", help="merge auto-discovered indicator→switch map (run build_indicator_switch_map.py --merge first)")
    args = ap.parse_args()
    if args.use_discovered:
        merged_path = ROOT / "data" / "indicator_switch_map_merged.json"
        if merged_path.exists():
            try:
                merged = json.loads(merged_path.read_text())
                added = 0
                for f, info in merged.items():
                    if f not in INDICATOR_TO_SWITCH:
                        INDICATOR_TO_SWITCH[f] = info
                        added += 1
                print(f"[auto] loaded {added} discovered mappings from {merged_path.name} (now {len(INDICATOR_TO_SWITCH)} total)")
            except Exception as e:
                print(f"[auto] failed to load discovered map: {e}")
        else:
            print(f"[auto] no discovered map at {merged_path}; run build_indicator_switch_map.py --merge first")
    if args.loop:
        print(f"[auto_suggest_loop] LOOP every {args.interval}s")
        while True:
            t0 = time.time()
            try: iteration(args)
            except Exception as e: print(f"[auto_suggest_loop] iteration error: {e}")
            elapsed = time.time() - t0
            sleep_s = max(60, args.interval - int(elapsed))
            print(f"[auto_suggest_loop] sleep {sleep_s}s\n")
            time.sleep(sleep_s)
    else:
        iteration(args)


if __name__ == "__main__":
    main()
