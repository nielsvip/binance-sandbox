#!/usr/bin/env python3
"""Systematic bool switch ablation: flip each switch ONE AT A TIME vs baseline.
Reports marginal impact of each switch on Sharpe/PnL.
Runs on all available NPZ data. Output: SQLite + CSV ranking."""
import json, os, re, sqlite3, subprocess, sys, time
from pathlib import Path

MODE = os.environ.get("SWEEP_MODE", "tradier")
ACCOUNT = os.environ.get("SWEEP_ACCOUNT", "trb")
START = os.environ.get("SWEEP_START", "2026-01-01")
CAPITAL = os.environ.get("SWEEP_CAPITAL", "10000")
SYMBOLS = os.environ.get("SWEEP_SYMBOLS", "AAPL,SPY,NVDA,GOOGL,AMD,MSFT,TSLA,META,QQQ,AMZN")
TIMEOUT = int(os.environ.get("SWEEP_TIMEOUT", "1200"))
OUT_DIR = Path("backtest_v8/sweeps/bool_ablation")
OUT_DIR.mkdir(parents=True, exist_ok=True)
DB = OUT_DIR / "bool_ablation.sqlite"

# Parse all bool switches from config
CFG_FILE = "config_tradier.py" if MODE == "tradier" else "config.py"
text = Path(CFG_FILE).read_text()
switches = re.findall(r'^\s+(\w+)\s*:\s*bool\s*=\s*(True|False)', text, re.MULTILINE)
# Skip noise
SKIP = {"DEBUG","VERBOSE","VERBOSE2","VERBOSE_FETCH_LOGGING","VERBOSE_STOPS","ENABLE_IP_ROTATION","REV_MODE","EXTREME_MODE","LIGHT_MODE"}
switches = [(n, v) for n, v in switches if n not in SKIP]
# V8-LIVE ONLY: skip switches that are in dead code paths (execute_trade_action/bg loops)
LIVE_ONLY = set(['FH_MOMENTUM_ENABLED', 'FH_MOMENTUM_MFI_CONFIRM', 'FH_MOMENTUM_DC_CONFIRM', 'BEAR_MARKET_MODE_TRADIER', 'STDEV_BREAKOUT_ENABLED', 'MI_STRUCT_EXIT_ENABLED_TRADIER', 'MI_EXHAUST_EXIT_ENABLED_TRADIER', 'MI_DIV_EXIT_ENABLED_TRADIER', 'MI_VELOCITY_EXIT_ENABLED_TRADIER', 'MI_WAVE_EXIT_ENABLED_TRADIER', 'EXIT_K5M_BOUNCE_ENABLED', 'EXIT_HARD_DROP_5M_ENABLED', 'EXIT_ALGO_SCORE_ENABLED', 'EXIT_STRUCT_BREAK_5M_ENABLED', 'EXIT_IBS_EXHAUSTION_ENABLED', 'EXIT_SENTIMENT_ENABLED', 'EXIT_MI_ENABLED', 'EXIT_CONV_FAIL_ENABLED', 'EXIT_BOUNCE_TOP_ENABLED', 'EXIT_HTF_QUICK_TP_ENABLED', 'EXIT_STRUCT_DC_BREAK_ENABLED', 'EXIT_MAX_HOLD_ENABLED', 'DELTA_ENGINE_ENABLED', 'DELTA_ENTRY_ENABLED', 'DELTA_EXIT_ENABLED', 'DELTA_ATR_ENTRY_FILTER', 'STRUCTURAL_RANGE_SHIFT_EXIT', 'RZ_ENTRY_ENABLED', 'RZ_EXIT_ENABLED', 'RZ_REQUIRE_STRUCT', 'RZ_ZSCORE_ZONE_ENABLED', 'RZ_TWO_PHASE_EXIT_ENABLED', 'RZ_DIV_EXIT_ENABLED', 'RZ_ZSCORE_EXIT_ENABLED'])
switches = [(n, v) for n, v in switches if n in LIVE_ONLY]
print(f"Testing {len(switches)} bool switches in {CFG_FILE}")

V8_CMD = [sys.executable, "backtest_v8_engine.py", "--mode", MODE, "--account", ACCOUNT,
           "--start", START, "--capital", CAPITAL, "--symbols", SYMBOLS]

def init_db():
    c = sqlite3.connect(DB)
    c.execute("""CREATE TABLE IF NOT EXISTS results (
        name TEXT PRIMARY KEY, switch TEXT, flipped_to TEXT,
        sharpe REAL, pnl REAL, trades INT, wins INT, losses INT,
        pnl_dollars REAL, avg_pnl REAL, elapsed REAL, is_baseline INT)""")
    c.commit()
    return c

def run_config(name, overrides, con):
    # Check if already done
    row = con.execute("SELECT sharpe FROM results WHERE name=?", (name,)).fetchone()
    if row:
        print(f"  SKIP {name} (already done: sharpe={row[0]:.3f})")
        return
    override_file = OUT_DIR / f"override_{name}.json"
    override_file.write_text(json.dumps(overrides))
    env = os.environ.copy()
    env["V8_OVERRIDE_FILE"] = str(override_file)
    log_file = OUT_DIR / f"{name}.log"
    t0 = time.time()
    try:
        with open(log_file, "w") as lf:
            subprocess.run(V8_CMD, stdout=lf, stderr=subprocess.STDOUT, timeout=TIMEOUT, env=env)
        elapsed = time.time() - t0
        # Parse result
        for line in open(log_file):
            if line.startswith("V8_RESULT:"):
                parts = dict(re.findall(r'(\w+)=([0-9.-]+)', line))
                con.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (name, overrides.get("_switch","baseline"), overrides.get("_flipped_to",""),
                     float(parts.get("sharpe_w",parts.get("sharpe",0))),
                     float(parts.get("pnl",parts.get("gain_pct",0))),
                     int(parts.get("trades",parts.get("closes",0))),
                     int(parts.get("wins",0)), int(parts.get("losses",0)),
                     float(parts.get("total_pnl_dollars",parts.get("gain_dollars",0))),
                     float(parts.get("avg_pnl",0)), elapsed,
                     1 if name == "BASELINE" else 0))
                con.commit()
                print(f"  {name}: sharpe={parts.get('sharpe_w',parts.get('sharpe','?'))} pnl={parts.get('pnl','?')} trades={parts.get('trades',parts.get('closes','?'))} [{elapsed:.0f}s]")
                return
        print(f"  {name}: NO V8_RESULT [{elapsed:.0f}s]")
        con.execute("INSERT OR REPLACE INTO results VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (name, overrides.get("_switch",""), overrides.get("_flipped_to",""),
             0, 0, 0, 0, 0, 0, 0, elapsed, 1 if name == "BASELINE" else 0))
        con.commit()
    except subprocess.TimeoutExpired:
        print(f"  {name}: TIMEOUT [{TIMEOUT}s]")

def main():
    con = init_db()
    # 1. BASELINE (no overrides)
    print("\n=== BASELINE ===")
    run_config("BASELINE", {"_switch": "baseline", "_flipped_to": ""}, con)
    # 2. Each switch flipped
    print(f"\n=== TESTING {len(switches)} SWITCHES ===")
    for i, (name, default) in enumerate(switches):
        flipped = "False" if default == "True" else "True"
        print(f"[{i+1}/{len(switches)}] {name}: {default} → {flipped}")
        overrides = {name: flipped == "True", "_switch": name, "_flipped_to": flipped}
        run_config(f"FLIP_{name}", overrides, con)
    # 3. Report
    print("\n" + "="*80)
    print("BOOL SWITCH ABLATION RESULTS (sorted by Sharpe delta vs baseline)")
    print("="*80)
    baseline = con.execute("SELECT sharpe, pnl FROM results WHERE is_baseline=1").fetchone()
    if baseline:
        b_sharpe, b_pnl = baseline
        print(f"BASELINE: sharpe={b_sharpe:.3f} pnl={b_pnl:.2f}%\n")
        rows = con.execute("SELECT name, switch, flipped_to, sharpe, pnl, trades FROM results WHERE is_baseline=0 ORDER BY sharpe DESC").fetchall()
        print(f"{'Switch':<45} {'Flipped':<8} {'Sharpe':>7} {'Δ':>7} {'PnL%':>7} {'Trades':>7}")
        print("-"*90)
        for name, switch, flipped, sharpe, pnl, trades in rows:
            d = sharpe - b_sharpe
            marker = "✅" if d > 0.01 else ("❌" if d < -0.01 else "  ")
            print(f"{marker} {switch:<43} {flipped:<8} {sharpe:>7.3f} {d:>+7.3f} {pnl:>7.2f} {trades:>7}")
    # Save CSV
    csv_path = OUT_DIR / f"bool_ablation_{time.strftime('%Y%m%d_%H%M')}.csv"
    with open(csv_path, "w") as f:
        f.write("switch,default,flipped_to,sharpe,sharpe_delta,pnl,trades\n")
        if baseline:
            for name, switch, flipped, sharpe, pnl, trades in rows:
                f.write(f"{switch},{flipped},{sharpe:.4f},{sharpe-b_sharpe:.4f},{pnl:.2f},{trades}\n")
    print(f"\nSaved: {csv_path}")

if __name__ == "__main__":
    main()
