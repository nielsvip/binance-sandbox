
import asyncio
import json
import os
import sys
from datetime import datetime, timezone
from typing import Dict, Any, List

# Add current directory to path
sys.path.append(os.getcwd())

import config
import config_sandbox
from utils import safe_fetch_float, construct_position_key, SimpleRedisManager

# Mocking execute_now to avoid actual trades
async def mock_execute_now(*args, **kwargs):
    # print(f"MOCK_EXECUTE: {args[0]} action={args[11]} reason={args[9]}")
    return "SUCCESS_MOCK"

async def run_comparison():
    print("🚀 Starting Timeframe Focus Comparison Test...")
    
    # 1. Load Symbols for ang
    with open('symbols_ang_long.json', 'r') as f:
        symbols_long = json.load(f)
    with open('symbols_ang_short.json', 'r') as f:
        symbols_short = json.load(f)
    
    all_symbols = list(set(symbols_long + symbols_short))
    print(f"📋 Testing {len(all_symbols)} symbols for 'ang' account.")

    # 2. Load Indicators from latest_market_data.json
    indicators_path = 'data/latest_market_data.json'
    if not os.path.exists(indicators_path):
        print(f"❌ {indicators_path} not found. Aborting.")
        return
    
    with open(indicators_path, 'r') as f:
        all_indicators = json.load(f)
    print(f"✅ Loaded indicators for {len(all_indicators)} symbols.")

    # 3. Import Managers
    from ez_manage import MultiAccountTradeManager as LiveManager
    from ez_manage_sandbox import MultiAccountTradeManager as SandboxManager
    
    # We need to mock the environment for the managers
    # This is complex because they start loops. 
    # Instead, we will extract the specific logic we want to compare.
    
    results = []

    print(f"{'Symbol':<15} | {'Side':<6} | {'Live Decision':<30} | {'Sandbox Decision':<30} | {'Status'}")
    print("-" * 100)

    for symbol in all_symbols:
        for side in ['LONG', 'SHORT']:
            is_long = (side == 'LONG')
            pos_key = f"ang:{symbol}_{side}"
            
            # Fetch indicators from loaded dict
            ind = all_indicators.get(symbol)
            if not ind:
                continue
            
            current_price = safe_fetch_float(ind.get('current_price', 0))
            if current_price <= 0: continue

            # --- SIMULATE LIVE LOGIC (from ez_manage.py) ---
            # We recreate the gate logic here because instantiating the whole manager is too heavy/dangerous
            live_gate = "ALLOWED"
            
            # HTF TREND SCORING (Live)
            k_1h = safe_fetch_float(ind.get('stoch_k_1h'), 50); d_1h = safe_fetch_float(ind.get('stoch_d_1h'), 50)
            k_4h = safe_fetch_float(ind.get('stoch_k_4h'), 50); d_4h = safe_fetch_float(ind.get('stoch_d_4h'), 50)
            ha_1h = ind.get('ha_1h', 'neutral'); ha_4h = ind.get('ha_4h', 'neutral'); ha_D = ind.get('ha_D', 'neutral')
            dc_basis_1h = safe_fetch_float(ind.get('dc_basis_1h'), 0); dc_basis_4h = safe_fetch_float(ind.get('dc_basis_4h'), 0)
            sma_200_1h = safe_fetch_float(ind.get('sma_200_1h'), 0)
            
            _htf_bull = 0; _htf_bear = 0
            if k_1h > d_1h: _htf_bull += 1
            else: _htf_bear += 1
            if k_4h > d_4h: _htf_bull += 1
            else: _htf_bear += 1
            if ha_1h == 'green': _htf_bull += 1
            elif ha_1h == 'red': _htf_bear += 1
            if ha_4h == 'green': _htf_bull += 1
            elif ha_4h == 'red': _htf_bear += 1
            if ha_D == 'green': _htf_bull += 1
            elif ha_D == 'red': _htf_bear += 1
            if dc_basis_1h > 0 and current_price > dc_basis_1h: _htf_bull += 1
            elif dc_basis_1h > 0 and current_price < dc_basis_1h: _htf_bear += 1
            if dc_basis_4h > 0 and current_price > dc_basis_4h: _htf_bull += 1
            elif dc_basis_4h > 0 and current_price < dc_basis_4h: _htf_bear += 1
            if sma_200_1h > 0 and current_price > sma_200_1h: _htf_bull += 1
            elif sma_200_1h > 0 and current_price < sma_200_1h: _htf_bear += 1
            
            # Simple check for 'OPEN' (simulating entry)
            # In live, it doesn't have a hard TF_FOCUS gate yet (except the one I just added for ratio, but not the TF_FOCUS spec)
            
            # --- SIMULATE SANDBOX LOGIC (from ez_manage_sandbox.py) ---
            sandbox_gate = "ALLOWED"
            
            # TF_FOCUS ENTRY HARD GATE
            focus_tf = config_sandbox.Config.TF_FOCUS # '15m'
            focus_k = safe_fetch_float(ind.get(f'stoch_k_{focus_tf}'), 50.0)
            focus_d = safe_fetch_float(ind.get(f'stoch_d_{focus_tf}'), 50.0)
            
            if is_long and focus_k <= focus_d: sandbox_gate = f"BLOCKED_TF_FOCUS_{focus_tf}"
            if not is_long and focus_k >= focus_d: sandbox_gate = f"BLOCKED_TF_FOCUS_{focus_tf}"
            
            if sandbox_gate == "ALLOWED":
                # TF_ALIGNMENT MULTI-TF GATE
                short_tfs = ['1m', '3m', '5m', '15m']; long_tfs = ['1h', '4h', 'D']
                aligned_short = 0; aligned_long = 0
                for tf in short_tfs:
                    k_tf, d_tf = safe_fetch_float(ind.get(f'stoch_k_{tf}'), 50), safe_fetch_float(ind.get(f'stoch_d_{tf}'), 50)
                    if (is_long and k_tf > d_tf) or (not is_long and k_tf < d_tf): aligned_short += 1
                for tf in long_tfs:
                    k_tf, d_tf = safe_fetch_float(ind.get(f'stoch_k_{tf}'), 50), safe_fetch_float(ind.get(f'stoch_d_{tf}'), 50)
                    if (is_long and k_tf > d_tf) or (not is_long and k_tf < d_tf): aligned_long += 1
                total_aligned = aligned_short + aligned_long
                if total_aligned < 4 or aligned_short < 2 or aligned_long < 2:
                    sandbox_gate = f"BLOCKED_ALIGNMENT(S={aligned_short},L={aligned_long})"

            # --- SIZING COMPARISON ---
            # We'll use the ratio_mult logic I added to both
            # Assume 50/50 ratio for simplicity in this test, or fetch it
            
            status = "✅ MATCH" if live_gate == sandbox_gate else "❌ DIFF"
            if sandbox_gate != "ALLOWED" and live_gate == "ALLOWED":
                status = "📉 FILTERED"
            
            print(f"{symbol:<15} | {side:<6} | {live_gate:<30} | {sandbox_gate:<30} | {status}")
            
    print("-" * 100)
    print("✅ Comparison complete.")

if __name__ == "__main__":
    asyncio.run(run_comparison())
