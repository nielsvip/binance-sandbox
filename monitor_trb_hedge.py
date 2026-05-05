"""
Hourly TRB position monitor — reads live Tradier positions, checks gains,
detects unhedged losers, and prints a structured status report.
Designed to be called by the scheduled hourly agent.
"""
import asyncio
import json
import logging
import os
import re
import sys
import warnings
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

warnings.filterwarnings("ignore")
logging.getLogger("aiohttp").setLevel(logging.ERROR)

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

from utils import load_environment_from_gpg
load_environment_from_gpg(None)

from tradier_api import TradierAPIClient
from config_tradier import TradierConfig
config = TradierConfig()

SECTOR_MAP = {
    "ETH": "Crypto", "BTC": "Crypto", "IBIT": "Crypto", "MSTR": "Crypto",
    "NVDA": "Tech", "AMD": "Tech", "INTC": "Tech", "LRCX": "Tech", "MU": "Tech",
    "AVGO": "Tech", "ARM": "Tech", "SNDK": "Tech", "PLTR": "Tech",
    "AMZN": "Tech", "GOOGL": "Tech", "MSFT": "Tech", "AAPL": "Tech", "PYPL": "Tech",
    "USO": "Energy", "XLE": "Energy", "XOP": "Energy", "OXY": "Energy",
    "DVN": "Energy", "FANG": "Energy", "COP": "Energy", "EOG": "Energy",
    "PBF": "Energy", "DINO": "Energy", "PSX": "Energy", "MPC": "Energy",
    "VLO": "Energy", "CHRD": "Energy", "CRK": "Energy", "RRC": "Energy",
    "COPX": "Materials", "XOM": "Energy", "BG": "Agriculture",
    "NEM": "Gold", "EGO": "Gold", "AEM": "Gold", "GLD": "Gold",
    "OKLO": "Nuclear", "NLR": "Nuclear",
    "XOM": "Energy", "WMB": "Energy", "OKE": "Energy",
}

LOSS_ALERT_PCT = -2.0   # alert if any position crosses this
LOSS_DANGER_PCT = -5.0  # danger level

def get_sector(sym: str) -> str:
    s = sym.upper().replace("USDC","").replace("USDT","")
    return SECTOR_MAP.get(s, "Other")

async def fetch_positions(account_key: str) -> list:
    try:
        client = TradierAPIClient(config, account_key=account_key)
        positions = await client.get_account_positions(account_key)
        return positions or []
    except Exception as e:
        return [{"_error": str(e)}]

async def fetch_quotes(symbols: list) -> dict:
    if not symbols:
        return {}
    try:
        client = TradierAPIClient(config, account_key="trb")
        syms_str = ",".join(set(symbols))
        res = await client._request("GET", "/markets/quotes", params={"symbols": syms_str, "greeks": "false"})
        quotes = {}
        if res and "quotes" in res:
            q = res["quotes"].get("quote", [])
            if isinstance(q, dict):
                q = [q]
            for item in q:
                quotes[item["symbol"]] = item.get("last") or item.get("bid", 0)
        return quotes
    except Exception as e:
        return {}

def reconstruct_from_decisions() -> dict:
    """Fallback: reconstruct open positions from today's decision JSONL."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    dec_file = ROOT / "data" / "decisions" / f"decisions_trb_{today}.jsonl"
    if not dec_file.exists():
        files = sorted((ROOT / "data" / "decisions").glob("decisions_trb_*.jsonl"))
        dec_file = files[-1] if files else None
    if not dec_file:
        return {}

    last_event = {}
    with open(dec_file) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                t = json.loads(line)
                pk = t.get("position_key", "")
                if "trb:" not in pk:
                    continue
                last_event[pk] = t
            except Exception:
                pass

    open_positions = {}
    for pk, t in last_event.items():
        action = t.get("action", "")
        trade = t.get("trade", {})
        gain = trade.get("gain_pct")
        entry = trade.get("entry_price")
        amt = trade.get("position_amt", 0)
        sym = pk.split(":")[-1].replace("_LONG", "").replace("_SHORT", "")
        side = "LONG" if "_LONG" in pk else "SHORT"
        if "CLOSE" in action and gain is not None:
            hold = re.search(r"_hold(\d+)m_", t.get("reason_text", ""))
            open_positions[pk] = {
                "symbol": sym,
                "side": side,
                "entry_price": entry,
                "gain_pct": gain,
                "qty": amt,
                "hold_min": int(hold.group(1)) if hold else 0,
                "last_action": action,
                "source": "decisions",
            }
    return open_positions


async def main():
    print("=" * 68)
    print(f"TRB HEDGE MONITOR — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("=" * 68)

    # Try live API first
    raw = await fetch_positions("trb")
    positions = {}

    if raw and "_error" not in (raw[0] if raw else {}):
        syms = [p.get("symbol", "") for p in raw]
        quotes = await fetch_quotes(syms)
        for p in raw:
            sym = p.get("symbol", "")
            qty = float(p.get("quantity", 0))
            cost = float(p.get("cost_basis", 0) or 0)
            entry = (cost / qty) if qty else 0
            last = float(quotes.get(sym, 0) or 0)
            if not last and p.get("date_acquired"):
                last = entry
            gain = ((last - entry) / entry * 100) if entry and qty > 0 else 0
            side = "LONG" if qty > 0 else "SHORT"
            pk = f"trb:{sym}_{side}"
            positions[pk] = {
                "symbol": sym, "side": side,
                "entry_price": round(entry, 4),
                "current_price": round(last, 4),
                "gain_pct": round(gain, 2),
                "qty": abs(qty),
                "source": "api",
            }
        print(f"Source: LIVE API ({len(positions)} positions)")
    else:
        positions = reconstruct_from_decisions()
        print(f"Source: DECISIONS FILE ({len(positions)} positions, last close signal per pk)")
        if raw and "_error" in (raw[0] if raw else {}):
            print(f"  API error: {raw[0]['_error']}")

    if not positions:
        print("\nNo open positions found for trb.")
        return

    # Group by sector
    by_sector = defaultdict(list)
    longs = {}
    shorts = {}
    alerts = []

    print(f"\n{'SYMBOL':<12} {'SIDE':<6} {'QTY':>8} {'ENTRY':>10} {'GAIN%':>8}  SECTOR")
    print("-" * 60)

    for pk, pos in sorted(positions.items(), key=lambda x: x[1].get("gain_pct", 0)):
        sym = pos["symbol"]
        side = pos["side"]
        gain = pos.get("gain_pct", 0) or 0
        entry = pos.get("entry_price", 0)
        qty = pos.get("qty", 0)
        sector = get_sector(sym)
        by_sector[sector].append((sym, side, gain))
        if side == "LONG":
            longs[sector] = longs.get(sector, []) + [(sym, gain)]
        else:
            shorts[sector] = shorts.get(sector, []) + [(sym, gain)]
        flag = ""
        if gain <= LOSS_DANGER_PCT:
            flag = " 🔴 DANGER"
            alerts.append(("DANGER", sym, side, gain, sector))
        elif gain <= LOSS_ALERT_PCT:
            flag = " 🟡 ALERT"
            alerts.append(("ALERT", sym, side, gain, sector))
        curr = pos.get("current_price", "")
        curr_str = f"→{curr:.3f}" if curr else ""
        print(f"{sym:<12} {side:<6} {qty:>8.0f} {entry:>10.4f}{curr_str:>12}  {gain:>+7.2f}%  {sector}{flag}")

    # Sector coverage check
    print(f"\n{'SECTOR COVERAGE':}")
    print("-" * 60)
    unhedged_sectors = []
    for sector in sorted(set(list(longs.keys()) + list(shorts.keys()))):
        l = longs.get(sector, [])
        s = shorts.get(sector, [])
        l_losers = [(sym, g) for sym, g in l if g <= LOSS_ALERT_PCT]
        hedged = "✅" if (l and s) else ("⬜" if not l else "❌ NO SHORT HEDGE")
        print(f"  {sector:<15} LONG: {[f'{sym}({g:+.1f}%)' for sym,g in l] or '—'}  SHORT: {[f'{sym}({g:+.1f}%)' for sym,g in s] or '—'}  {hedged}")
        if l_losers and not s:
            unhedged_sectors.append((sector, l_losers))

    # Alerts
    if alerts:
        print(f"\n{'ALERTS':}")
        print("-" * 60)
        for level, sym, side, gain, sector in alerts:
            same_side_short = shorts.get(sector, [])
            hedge_str = f"hedged by {[s for s,g in same_side_short]}" if same_side_short else "NO SECTOR HEDGE"
            print(f"  [{level}] {sym} {side}: {gain:+.2f}%  sector={sector}  {hedge_str}")

    if unhedged_sectors:
        print(f"\n⚠️  UNHEDGED LOSING SECTORS:")
        for sector, losers in unhedged_sectors:
            print(f"  {sector}: {losers} — no SHORT position in same sector")
        print(f"\n  HEDGE MODE STATUS: HEDGE_MODE_TRADIER={getattr(config, 'HEDGE_MODE_TRADIER', False)}")
        print(f"  ACTION: Manual hedge or enable HEDGE_MODE_TRADIER (review T31 backtest first)")
    else:
        print(f"\n✅ All losing positions have same-sector shorts.")

    print(f"\n{'NOLOSS STATUS':}")
    print(f"  NOLOSS_MIN_PROFIT_PCT_TRADIER = {getattr(config, 'NOLOSS_MIN_PROFIT_PCT_TRADIER', '?')}")
    print(f"  (0.0 = DISABLED — technical exits fire at any gain/loss)")
    print(f"  HEDGE_MODE_TRADIER = {getattr(config, 'HEDGE_MODE_TRADIER', False)}")
    print("=" * 68)


if __name__ == "__main__":
    asyncio.run(main())
