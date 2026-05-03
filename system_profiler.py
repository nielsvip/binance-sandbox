import numpy as np
import json
import os
from itertools import product
from scipy.signal import lfilter

# --- PATHS ---
DATA_DIR = "./backtest_v8/indicators/"
SYMBOLS_FILE = "symbols.json"
BASE_CONFIG_FILE = "config_baseline.json"
OUTPUT_DIR = "./symbol_configs/"

# --- PARAMETER GRID (The "Meat") ---
GRID = {
    "bb_len": [20, 50],
    "bb_std": [2.0, 2.5],
    "wt_chan": [10, 12],
    "wt_avg": [21, 28],
    "dc_period": [20, 55],
    "timeframe": ["1h", "4h"] # Prioritize stability
}

# --- VECTORIZED UTILITIES ---
def vectorized_ema(data, window):
    alpha = 2 / (window + 1)
    b, a = [alpha], [1, -(1 - alpha)]
    zi = [data[0] * (1 - alpha)]
    y, _ = lfilter(b, a, data, zi=zi)
    return y

def get_metrics(equity, trades_count):
    if trades_count < 50: return -1, -1 # Statistical insignificance
    returns = np.diff(equity) / equity[:-1]
    # Annualized Sharpe (assuming hourly data)
    sharpe = np.mean(returns) / (np.std(returns) + 1e-9) * np.sqrt(24 * 365)
    final_gain = (equity[-1] - equity[0]) / equity[0]
    return sharpe, final_gain

def run_backtest(close, high, low, params, slippage=0.0002):
    """
    Simulated vectorized backtest. 
    Replace logic with your specific entry/exit triggers.
    """
    # 1. Calculate Indicators
    tp = (high + low + close) / 3
    esa = vectorized_ema(tp, params['wt_chan'])
    d = vectorized_ema(np.abs(tp - esa), params['wt_chan'])
    ci = (tp - esa) / (0.015 * d)
    wt1 = vectorized_ema(ci, params['wt_avg'])
    
    # Simple logic: WT1 Cross for demo; replace with your full script logic
    # Long when WT1 < -60 (Oversold), Exit when WT1 > 60
    signals = np.zeros_like(close)
    signals[wt1 < -60] = 1
    signals[wt1 > 60] = 0 # This is a simplified flip-flop
    
    # 2. Equity Curve with Slippage
    # Every '1' to '0' transition is a trade (Buy + Sell)
    trade_mask = np.diff(signals, prepend=0) != 0
    trades_count = np.sum(trade_mask)
    
    pct_change = (np.diff(close, prepend=close[0]) / close) * signals
    # Apply 0.02% slippage per side (total 0.04% per round trip)
    equity = np.cumprod(1 + pct_change - (trade_mask * slippage))
    
    return get_metrics(equity, trades_count)

# --- MAIN RUNNER ---
def main():
    if not os.path.exists(OUTPUT_DIR): os.makedirs(OUTPUT_DIR)
    
    with open(SYMBOLS_FILE, 'r') as f:
        symbols = json.load(f)
    
    with open(BASE_CONFIG_FILE, 'r') as f:
        base_config = json.load(f)

    for symbol in symbols:
        file_path = os.path.join(DATA_DIR, f"{symbol}.npz")
        if not os.path.exists(file_path): continue
        
        print(f">>> Auditing {symbol}...")
        data = np.load(file_path)
        c, h, l = data['close'], data['high'], data['low']
        
        best_score = -np.inf
        best_params = {}

        keys, values = zip(*GRID.items())
        for v in product(*values):
            p = dict(zip(keys, v))
            sharpe, gain = run_backtest(c, h, l, p)
            
            # Optimization Goal: Maximize Sharpe while keeping Gain > 0
            # We penalize low sharpe heavily to fix your current issue
            score = sharpe if gain > 0 else -10
            
            if score > best_score:
                best_score = score
                best_params = p

        # Merge with baseline and save
        final_config = base_config.copy()
        final_config.update(best_params)
        final_config['audit_metadata'] = {"sharpe": round(best_score, 4), "symbol": symbol}
        
        with open(f"{OUTPUT_DIR}{symbol}_config.json", 'w') as f:
            json.dump(final_config, f, indent=4)
        
        print(f"Done {symbol}: Sharpe {round(best_score, 2)}")

if __name__ == "__main__":
    main()