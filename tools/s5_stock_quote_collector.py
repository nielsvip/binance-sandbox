#!/usr/bin/env python3
"""Collect Tradier quotes on S5 for the independent stock bar clock."""
import argparse
import asyncio
import json
import time
from pathlib import Path

from stock_15m_bar_clock import quote_mark, stock_symbols
from tools.npz_cutoffs import stock_session_bounds


async def collect_once(api, base, now_fn=time.time):
    from datetime import datetime
    from zoneinfo import ZoneInfo
    now = now_fn()
    bounds = stock_session_bounds(datetime.fromtimestamp(now, ZoneInfo('America/New_York')).date())
    if bounds is None or not bounds[0] - 3600 <= now < bounds[1]:
        return {'at': now, 'status': 'OUTSIDE_WINDOW', 'universe': 0, 'accepted': 0}
    symbols = stock_symbols(base)
    batches = [symbols[i:i + 50] for i in range(0, len(symbols), 50)]
    results = await asyncio.gather(*(asyncio.wait_for(api.get_quotes(batch), timeout=10) for batch in batches), return_exceptions=True)
    received = now_fn()
    quotes = {}
    errors = []
    for batch, result in zip(batches, results):
        if isinstance(result, BaseException):
            errors.append(f'{batch[0]}:{type(result).__name__}:{str(result)[:80]}')
            continue
        for symbol, quote in result.items():
            try:
                if symbol in batch and quote_mark(quote) is not None:
                    quotes[symbol] = dict(quote, received_at=received)
            except (TypeError, ValueError):
                continue
    if quotes:
        target = Path(base) / 'data/stock_quotes_latest.json'
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(f'.{target.name}.{__import__("os").getpid()}.tmp')
        temporary.write_text(json.dumps({'data': quotes, 'received_at': received}))
        temporary.replace(target)
    report = {'at': received, 'status': 'OK' if len(quotes) == len(symbols) else 'PARTIAL', 'universe': len(symbols), 'accepted': len(quotes), 'errors': errors}
    target = Path(base) / 'data/reports/s5_stock_quote_collector_latest.json'
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report))
    return report


async def main(base, once):
    from utils import load_environment_from_gpg
    load_environment_from_gpg(None)
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    api = TradierAPIClient(TradierConfig(), account_key='tra')
    await api.connect()
    try:
        while True:
            start = time.time()
            try:
                print(json.dumps(await collect_once(api, base)), flush=True)
            except Exception as exc:
                print(json.dumps({'at': time.time(), 'status': 'ERROR', 'error': f'{type(exc).__name__}: {exc}'}), flush=True)
            if once:
                return
            await asyncio.sleep(max(0.5, 5 - (time.time() - start)))
    finally:
        await api.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', default=str(Path(__file__).resolve().parents[1]))
    parser.add_argument('--once', action='store_true')
    args = parser.parse_args()
    asyncio.run(main(Path(args.base), args.once))
