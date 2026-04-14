#!/usr/bin/env python3
"""Quick portfolio snapshot — positions, exposure, P/L. Used by monitoring loop."""
import asyncio, sys, json
from pathlib import Path
BASE = Path("/Users/niels/Documents/binance")
sys.path.insert(0, str(BASE))
from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import parse_occ_symbol

async def check():
    config = TradierConfig()
    client = TradierAPIClient(config, account_key="trb")
    await client.connect()
    try:
        account_id = client._current_id
        res = await client._request("GET", f"/accounts/{account_id}/positions", use_data_context=False)
        positions = []
        if res and "positions" in res:
            inner = res["positions"]
            if isinstance(inner, dict) and "position" in inner:
                p = inner["position"]
                positions = p if isinstance(p, list) else [p]
        # Load allowed symbols
        allowed_calls = set()
        allowed_puts = set()
        for f, s in [("symbols_trb_long.json", allowed_calls), ("symbols_trb_short.json", allowed_puts)]:
            fp = BASE / f
            if fp.exists():
                with open(fp) as fh:
                    s.update(sym for sym in json.load(fh) if isinstance(sym, str) and len(sym) <= 5)
        options = []
        stocks = []
        for p in positions:
            sym = p.get("symbol", "")
            parsed = parse_occ_symbol(sym)
            if parsed:
                options.append({**p, **parsed, "occ": sym})
            else:
                stocks.append(p)
        # Options summary
        total_opt_cost = 0
        total_opt_pnl = 0
        n_calls = 0
        n_puts = 0
        rogue_count = 0
        worst = []
        for op in options:
            cost = abs(float(op.get("cost_basis", 0)))
            qty = abs(float(op.get("quantity", 0)))
            total_opt_cost += cost
            is_call = op["option_type"] == "call"
            is_rogue = False
            if is_call:
                n_calls += qty
                if op["symbol"] not in allowed_calls:
                    is_rogue = True
            else:
                n_puts += qty
                if op["symbol"] not in allowed_puts:
                    is_rogue = True
            if is_rogue:
                rogue_count += 1
        # Stock summary
        n_long = sum(1 for s in stocks if float(s.get("quantity", 0)) > 0)
        n_short = sum(1 for s in stocks if float(s.get("quantity", 0)) < 0)
        # Balance
        bal = await client._request("GET", f"/accounts/{account_id}/balances", use_data_context=False)
        equity = 0
        day_pnl = 0
        if bal and "balances" in bal:
            b = bal["balances"]
            equity = b.get("total_equity", 0) or b.get("equity", 0) or 0
            day_pnl = b.get("close_pl", 0) or 0
        print(f"PORTFOLIO SNAPSHOT — {__import__('datetime').datetime.utcnow().strftime('%H:%M:%S')} UTC")
        print(f"  Equity: ${equity:,.0f} | Day P/L: ${day_pnl:+,.0f}")
        print(f"  Options: {len(options)} positions ({n_calls:.0f}C/{n_puts:.0f}P) | Cost: ${total_opt_cost:,.0f}/$7,000 ceiling")
        print(f"  Rogues remaining: {rogue_count}")
        print(f"  Stocks: {n_long} long, {n_short} short")
        over = total_opt_cost - 7000
        if over > 0:
            print(f"  *** OVER CEILING by ${over:,.0f} — watchdog should be closing rogues ***")
        else:
            print(f"  Within ceiling (${-over:,.0f} headroom)")
    finally:
        await client.close()

asyncio.run(check())
