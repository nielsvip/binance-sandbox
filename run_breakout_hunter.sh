#!/bin/bash
cd /Users/niels/Documents/binance
/opt/anaconda3/envs/binance_env/bin/python -c "
import asyncio, sys
sys.path.insert(0, '.')
from ez_positions_quick import BreakoutHunter
async def run():
    h = BreakoutHunter.get()
    await h.run()
asyncio.run(run())
" >> /Users/niels/logs/ez_breakout_hunter.log 2>&1
