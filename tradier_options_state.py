"""Options position admin / snapshot — Phase 0.1 of OPTIONS_OVERHAUL_FRAMEWORK_20260425.md

Single source of truth for "what options do we own right now?" for accounts trb + trc.

Outputs:
- data/options_state/current.json        — latest snapshot (atomic write)
- data/options_state/<YYYYMMDD>.jsonl    — append-log of snapshots
- data/options_trades/<occ>.jsonl        — per-OCC lifecycle (RECONCILE_NEW / RECONCILE_CLOSED / SNAPSHOT entries)

CLI:
  python3 tradier_options_state.py                # one-shot snapshot, both accounts
  python3 tradier_options_state.py --account trb  # one-shot, single account
  python3 tradier_options_state.py --loop 30      # continuous loop, 30s cadence (use during market hours)
"""
import argparse
import asyncio
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import (
    OptionPosition, bs_greeks, get_option_positions, parse_occ_symbol,
)

STATE_DIR = BASE_PATH / "data" / "options_state"
TRADES_DIR = BASE_PATH / "data" / "options_trades"
DAILY_DIR = BASE_PATH / "data" / "options_daily"
for d in (STATE_DIR, TRADES_DIR, DAILY_DIR):
    d.mkdir(parents=True, exist_ok=True)

CURRENT_PATH = STATE_DIR / "current.json"
RISK_FREE_RATE = 0.045


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _load_prev_snapshot() -> Optional[dict]:
    if not CURRENT_PATH.exists():
        return None
    try:
        return json.loads(CURRENT_PATH.read_text())
    except Exception:
        return None


def _stamp_position(pos: dict, underlying_price: float, ts: str) -> dict:
    """Compute Greeks + per-position derived fields. Mutates and returns the dict."""
    occ = pos.get("occ_symbol") or pos.get("symbol")
    parsed = parse_occ_symbol(occ) if occ else None
    if not parsed:
        return pos
    is_call = parsed["option_type"] == "call"
    strike = parsed["strike"]
    try:
        exp_dt = datetime.strptime(parsed["expiration"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dte_days = max(0, (exp_dt - datetime.now(timezone.utc)).days)
    except Exception:
        dte_days = 0
    T = max(1.0 / 365.0, dte_days / 365.0)
    iv = pos.get("iv_current") or pos.get("greeks_smv_vol") or 0.30
    try:
        iv = float(iv) if iv else 0.30
    except (TypeError, ValueError):
        iv = 0.30
    if iv <= 0 or iv > 5:
        iv = 0.30
    greeks = bs_greeks(S=underlying_price, K=strike, T=T, r=RISK_FREE_RATE, sigma=iv, is_call=is_call)
    qty = float(pos.get("quantity", 0) or 0)
    cost_basis = float(pos.get("cost_basis", 0) or 0)
    avg_cost_per_contract = (cost_basis / qty) if qty else 0.0
    mid = float(pos.get("mid", 0) or 0)
    market_value = mid * qty * 100.0
    unrealized_pnl = market_value - cost_basis
    unrealized_pct = (unrealized_pnl / abs(cost_basis) * 100.0) if cost_basis else 0.0
    pos.update({
        "ts": ts,
        "symbol": parsed["symbol"],
        "occ": occ,
        "option_type": parsed["option_type"],
        "strike": strike,
        "expiration": parsed["expiration"],
        "dte": dte_days,
        "underlying_price": round(underlying_price, 4),
        "iv_used": round(iv, 4),
        "qty": qty,
        "avg_cost_per_contract": round(avg_cost_per_contract, 4),
        "cost_basis": round(cost_basis, 2),
        "mid": round(mid, 4),
        "market_value": round(market_value, 2),
        "unrealized_pnl": round(unrealized_pnl, 2),
        "unrealized_pct": round(unrealized_pct, 2),
        "delta": greeks["delta"],
        "gamma": greeks["gamma"],
        "theta": greeks["theta"],
        "vega": greeks["vega"],
    })
    return pos


async def fetch_account_options(account_key: str, config: TradierConfig) -> List[dict]:
    """Fetch + enrich options positions for one account."""
    client = TradierAPIClient(config=config, account_key=account_key)
    setattr(client, "_account_key", account_key)
    try:
        raw_positions = await get_option_positions(client, config=config)
        if not raw_positions:
            return []
        # Build symbol set: option OCCs + their underlyings
        occs = [p.get("occ_symbol") or p.get("symbol") for p in raw_positions]
        underlyings = list({(parse_occ_symbol(o) or {}).get("symbol") for o in occs if o})
        underlyings = [u for u in underlyings if u]
        # Single batched quote call: option + underlying
        all_syms = list(set(occs + underlyings))
        quotes = await client.get_quotes(all_syms) if all_syms else {}
        ts = datetime.now(timezone.utc).isoformat()
        enriched = []
        for pos in raw_positions:
            occ = pos.get("occ_symbol") or pos.get("symbol")
            parsed = parse_occ_symbol(occ) if occ else None
            if not parsed:
                continue
            opt_q = quotes.get(occ, {}) or {}
            ul_q = quotes.get(parsed["symbol"], {}) or {}
            bid = float(opt_q.get("bid", 0) or 0)
            ask = float(opt_q.get("ask", 0) or 0)
            last = float(opt_q.get("last", 0) or 0)
            mid = (bid + ask) / 2.0 if bid > 0 and ask > 0 else (last or 0.0)
            ul_last = float(ul_q.get("last", 0) or 0)
            iv_q = opt_q.get("greeks", {}).get("smv_vol") if isinstance(opt_q.get("greeks"), dict) else None
            pos["bid"] = bid
            pos["ask"] = ask
            pos["last"] = last
            pos["mid"] = mid
            pos["iv_current"] = iv_q
            pos["account_key"] = account_key
            _stamp_position(pos, ul_last or 0.0, ts)
            enriched.append(pos)
        return enriched
    finally:
        await client.close()


def _portfolio_aggregates(positions: List[dict]) -> dict:
    if not positions:
        return {"net_delta": 0.0, "net_gamma": 0.0, "net_theta": 0.0, "net_vega": 0.0,
                "total_premium": 0.0, "total_market_value": 0.0, "open_pnl": 0.0,
                "n_positions": 0, "calls$": 0.0, "puts$": 0.0}
    net_d = net_g = net_t = net_v = 0.0
    prem = mv = pnl = calls = puts = 0.0
    for p in positions:
        q = float(p.get("qty", 0) or 0)
        net_d += float(p.get("delta", 0) or 0) * q * 100
        net_g += float(p.get("gamma", 0) or 0) * q * 100
        net_t += float(p.get("theta", 0) or 0) * q * 100
        net_v += float(p.get("vega", 0) or 0) * q * 100
        prem += float(p.get("cost_basis", 0) or 0)
        mv += float(p.get("market_value", 0) or 0)
        pnl += float(p.get("unrealized_pnl", 0) or 0)
        if p.get("option_type") == "call":
            calls += float(p.get("market_value", 0) or 0)
        else:
            puts += float(p.get("market_value", 0) or 0)
    return {"net_delta": round(net_d, 2), "net_gamma": round(net_g, 4),
            "net_theta": round(net_t, 2), "net_vega": round(net_v, 2),
            "total_premium": round(prem, 2), "total_market_value": round(mv, 2),
            "open_pnl": round(pnl, 2), "n_positions": len(positions),
            "calls$": round(calls, 2), "puts$": round(puts, 2)}


def _reconcile(prev_positions: List[dict], curr_positions: List[dict], ts: str) -> List[dict]:
    """Detect new / closed OCCs vs prior snapshot and emit per-OCC lifecycle records."""
    prev_occs = {p.get("occ"): p for p in (prev_positions or []) if p.get("occ")}
    curr_occs = {p.get("occ"): p for p in curr_positions if p.get("occ")}
    events = []
    for occ in curr_occs.keys() - prev_occs.keys():
        rec = {"ts": ts, "occ": occ, "action": "RECONCILE_NEW",
               "qty": curr_occs[occ].get("qty"), "mid": curr_occs[occ].get("mid"),
               "cost_basis": curr_occs[occ].get("cost_basis"),
               "account_key": curr_occs[occ].get("account_key"),
               "note": "appeared in API positions; no local entry record"}
        _append_jsonl(TRADES_DIR / f"{occ}.jsonl", rec)
        events.append(rec)
    for occ in prev_occs.keys() - curr_occs.keys():
        rec = {"ts": ts, "occ": occ, "action": "RECONCILE_CLOSED",
               "last_known_qty": prev_occs[occ].get("qty"),
               "last_known_mid": prev_occs[occ].get("mid"),
               "account_key": prev_occs[occ].get("account_key"),
               "note": "disappeared from API positions; closed externally or filled"}
        _append_jsonl(TRADES_DIR / f"{occ}.jsonl", rec)
        events.append(rec)
    return events


async def snapshot_once(account_keys: List[str]) -> dict:
    config = TradierConfig()
    ts = datetime.now(timezone.utc).isoformat()
    all_positions: List[dict] = []
    per_account: Dict[str, List[dict]] = {}
    for ak in account_keys:
        try:
            positions = await fetch_account_options(ak, config)
            per_account[ak] = positions
            all_positions.extend(positions)
        except Exception as e:
            print(f"[{ak}] fetch error: {e}", file=sys.stderr)
            per_account[ak] = []
    prev = _load_prev_snapshot()
    prev_positions = (prev or {}).get("positions", [])
    reconcile_events = _reconcile(prev_positions, all_positions, ts)
    snapshot = {
        "ts": ts,
        "n_positions": len(all_positions),
        "n_accounts": len(account_keys),
        "portfolio": _portfolio_aggregates(all_positions),
        "by_account": {ak: _portfolio_aggregates(per_account[ak]) for ak in account_keys},
        "positions": all_positions,
        "reconcile_events": reconcile_events,
    }
    _atomic_write_json(CURRENT_PATH, snapshot)
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    _append_jsonl(STATE_DIR / f"{today}.jsonl", {
        "ts": ts,
        "portfolio": snapshot["portfolio"],
        "positions": all_positions,
    })
    return snapshot


def _print_summary(snap: dict) -> None:
    p = snap["portfolio"]
    print(f"\n=== options snapshot {snap['ts']} ===")
    print(f"positions: {p['n_positions']}  premium: ${p['total_premium']:.2f}  "
          f"MV: ${p['total_market_value']:.2f}  open_pnl: ${p['open_pnl']:+.2f}")
    print(f"net_delta: {p['net_delta']:+.2f}  net_gamma: {p['net_gamma']:+.4f}  "
          f"net_theta: {p['net_theta']:+.2f}/day  net_vega: {p['net_vega']:+.2f}/IV-pt")
    print(f"calls$: ${p['calls$']:.2f}  puts$: ${p['puts$']:.2f}")
    if snap.get("reconcile_events"):
        print(f"reconcile events: {len(snap['reconcile_events'])}")
        for e in snap["reconcile_events"]:
            print(f"  {e['action']:18s} {e['occ']:25s} {e.get('note', '')}")
    print(f"--- positions ---")
    for pos in sorted(snap["positions"], key=lambda x: x.get("unrealized_pnl", 0)):
        sign = "+" if pos.get("unrealized_pnl", 0) >= 0 else ""
        print(f"  {pos['account_key']:3s} {pos['occ']:25s} qty={pos['qty']:>4.0f}  "
              f"avg=${pos['avg_cost_per_contract']:>7.2f}  mid=${pos['mid']:>7.2f}  "
              f"pnl={sign}${pos['unrealized_pnl']:>8.2f} ({pos['unrealized_pct']:+6.1f}%)  "
              f"DTE={pos['dte']:>3d}  Δ={pos['delta']:+.3f}  Θ={pos['theta']:+.4f}")


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", choices=["trb", "trc", "all"], default="all")
    ap.add_argument("--loop", type=float, default=0,
                    help="seconds between snapshots; 0 = one-shot (default)")
    ap.add_argument("--quiet", action="store_true", help="suppress stdout summary")
    args = ap.parse_args()
    accounts = ["trb", "trc"] if args.account == "all" else [args.account]
    if args.loop <= 0:
        snap = await snapshot_once(accounts)
        if not args.quiet:
            _print_summary(snap)
        return
    print(f"loop mode every {args.loop}s for accounts {accounts}")
    while True:
        try:
            snap = await snapshot_once(accounts)
            if not args.quiet:
                _print_summary(snap)
        except Exception as e:
            print(f"snapshot error: {e}", file=sys.stderr)
        await asyncio.sleep(args.loop)


if __name__ == "__main__":
    asyncio.run(main())
