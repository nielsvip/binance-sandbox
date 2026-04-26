"""Per-position close-or-cover recommendations — applies OPTIONS_OVERHAUL_FRAMEWORK §0.3 + Layer 1.T*.

READ-ONLY. Does not place orders. Outputs:
- data/options_state/recommendations.json
- stdout report (human-readable)

Inputs:
- data/options_state/current.json   (produced by tradier_options_state.py)
- 60 days of daily bars per underlying via tradier_api.get_history (computes DC zones inline)

Rules applied:
- §0.3 first-loss meta: if pnl < 0 → must CLOSE or COVER this tick (no hold).
- L1.T1/T3: underlying close < dc_low_20D (call) → KILL. Mirror for puts.
- L1.T9: DTE ≤ 5 → CLOSE.
- L1.T10: held ≥ 21 days → CLOSE.
- L1.B1: premium loss > 75% → emergency CLOSE.
- L1.B2: $ loss > 1.5% of $70k = $1050 → emergency CLOSE.
- §0.3 picker: COVER preferred when DTE > 14 AND no technical breach AND not already covered.

Usage:
  python3 tradier_options_recommendations.py
  python3 tradier_options_recommendations.py --account-equity 70000
  python3 tradier_options_recommendations.py --json-only         # suppress stdout
"""
import argparse
import asyncio
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient

STATE_DIR = BASE_PATH / "data" / "options_state"
CURRENT_PATH = STATE_DIR / "current.json"
RECS_PATH = STATE_DIR / "recommendations.json"

DC_LOOKBACK_DAYS = 20
DEFAULT_ACCOUNT_EQUITY = 70000.0
EMERGENCY_LOSS_PCT = 75.0
EMERGENCY_DOLLAR_LOSS_PCT_OF_EQUITY = 1.5
DTE_CLIFF = 5
MAX_HOLD_DAYS = 21
COVER_DTE_FLOOR = 14


def _load_current() -> dict:
    if not CURRENT_PATH.exists():
        sys.exit(f"FATAL: {CURRENT_PATH} not found. Run tradier_options_state.py first.")
    return json.loads(CURRENT_PATH.read_text())


async def _fetch_bars(client: TradierAPIClient, symbol: str, days: int) -> List[Dict]:
    end = datetime.now(timezone.utc).date()
    start = end - timedelta(days=days * 2)
    bars = await client.get_history(symbol=symbol, start=start.isoformat(), end=end.isoformat(), interval="daily")
    return bars or []


def _dc_bands(bars: List[Dict], lookback: int) -> Optional[Dict[str, float]]:
    if not bars or len(bars) < lookback:
        return None
    recent = bars[-lookback:]
    highs = [float(b.get("high", 0) or 0) for b in recent]
    lows = [float(b.get("low", 0) or 0) for b in recent]
    closes = [float(b.get("close", 0) or 0) for b in recent]
    return {
        "dc_high_D": max(highs),
        "dc_low_D": min(lows),
        "last_close": closes[-1] if closes else 0.0,
        "last_high": highs[-1] if highs else 0.0,
        "last_low": lows[-1] if lows else 0.0,
        "avg_close_5d": sum(closes[-5:]) / max(1, len(closes[-5:])),
        "n_bars": len(recent),
    }


def _days_held(date_acquired: Optional[str]) -> Optional[int]:
    if not date_acquired:
        return None
    try:
        dt = datetime.fromisoformat(date_acquired.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - dt).days
    except Exception:
        return None


def _evaluate_position(pos: dict, dc: Optional[dict], equity: float) -> dict:
    """Apply framework rules. Returns recommendation record."""
    flags: List[str] = []
    is_call = pos.get("option_type") == "call"
    pnl = float(pos.get("unrealized_pnl", 0) or 0)
    pnl_pct = float(pos.get("unrealized_pct", 0) or 0)
    dte = int(pos.get("dte", 0) or 0)
    underlying = float(pos.get("underlying_price", 0) or 0)
    held_days = _days_held(pos.get("date_acquired"))
    dollar_loss_limit = equity * (EMERGENCY_DOLLAR_LOSS_PCT_OF_EQUITY / 100.0)

    if dte <= DTE_CLIFF:
        flags.append(f"L1.T9 DTE={dte}<={DTE_CLIFF} (theta cliff)")
    if held_days is not None and held_days >= MAX_HOLD_DAYS:
        flags.append(f"L1.T10 held={held_days}d>={MAX_HOLD_DAYS}d (max hold)")
    if pnl_pct <= -EMERGENCY_LOSS_PCT:
        flags.append(f"L1.B1 prem_loss={pnl_pct:.1f}%<=-{EMERGENCY_LOSS_PCT}% (emergency)")
    if pnl <= -dollar_loss_limit:
        flags.append(f"L1.B2 $loss={pnl:.0f}<=-${dollar_loss_limit:.0f} (1.5% equity emergency)")

    technical_breach = False
    if dc and dc.get("n_bars", 0) >= DC_LOOKBACK_DAYS:
        last_close = dc["last_close"]
        if is_call and last_close < dc["dc_low_D"]:
            flags.append(f"L1.T3 close={last_close:.2f}<dc_low_D={dc['dc_low_D']:.2f} (RED ZONE call breach)")
            technical_breach = True
        if (not is_call) and last_close > dc["dc_high_D"]:
            flags.append(f"L1.T4 close={last_close:.2f}>dc_high_D={dc['dc_high_D']:.2f} (RED ZONE put breach)")
            technical_breach = True
        # Soft proximity warnings (within 1% of band, not yet breached)
        if is_call and last_close < dc["dc_low_D"] * 1.01 and not technical_breach:
            flags.append(f"WARN dc_low_D proximity ({((last_close/dc['dc_low_D'])-1)*100:+.1f}%)")
        if (not is_call) and last_close > dc["dc_high_D"] * 0.99 and not technical_breach:
            flags.append(f"WARN dc_high_D proximity ({((last_close/dc['dc_high_D'])-1)*100:+.1f}%)")

    in_loss = pnl < 0
    if in_loss:
        flags.append(f"§0.3 first-loss rule active (pnl=${pnl:.0f}, must CLOSE or COVER)")

    # Decision per §0.3 picker
    if not in_loss and not flags:
        action = "HOLD"
        rationale = f"In gain (+${pnl:.0f}), no flags."
    elif not in_loss and any("WARN" in f for f in flags) and not any(f.startswith("L1.") for f in flags):
        action = "HOLD_WATCH"
        rationale = "In gain, but underlying within 1% of opposite DC band — consider taking profit if proximity tightens."
    elif technical_breach:
        action = "CLOSE"
        rationale = "Technical RED ZONE breach (L1.T3/T4) — thesis broken."
    elif dte <= COVER_DTE_FLOOR:
        action = "CLOSE"
        rationale = f"DTE={dte}<={COVER_DTE_FLOOR} — cover too expensive on short-DTE; close per §0.3 picker."
    elif any(f.startswith("L1.B") for f in flags) or any(f.startswith("L1.T9") for f in flags) or any(f.startswith("L1.T10") for f in flags):
        action = "CLOSE"
        rationale = "Hard backstop or time-stop fired."
    elif in_loss:
        action = "COVER"
        # COVER suggestion: convert to vertical spread by selling a contrary-strike option
        sym = pos.get("symbol", "?")
        strike = float(pos.get("strike", 0) or 0)
        exp = pos.get("expiration", "?")
        if is_call:
            # Sell a higher-strike call → vertical debit spread, recoups some basis
            target_strike = round(strike * 1.05)  # ~5% OTM
            cover_descr = f"SELL {sym} {exp} ${target_strike}C (convert to call vertical debit spread, recoups basis, caps upside)"
            cover_alt = f"OR buy protective {sym} {exp} ${round(underlying * 0.97)}P (collar — costs more, full downside cap)"
        else:
            target_strike = round(strike * 0.95)
            cover_descr = f"SELL {sym} {exp} ${target_strike}P (convert to put vertical debit spread, recoups basis, caps downside)"
            cover_alt = f"OR buy protective {sym} {exp} ${round(underlying * 1.03)}C (collar — costs more, full upside cap)"
        rationale = f"In loss but no technical breach, DTE={dte}>{COVER_DTE_FLOOR}. Per §0.3 picker → COVER preferred. {cover_descr}. {cover_alt}"
    else:
        action = "HOLD"
        rationale = "No rule fired."

    return {
        "occ": pos.get("occ"),
        "symbol": pos.get("symbol"),
        "option_type": pos.get("option_type"),
        "strike": pos.get("strike"),
        "expiration": pos.get("expiration"),
        "qty": pos.get("qty"),
        "dte": dte,
        "held_days": held_days,
        "underlying_price": underlying,
        "avg_cost_per_contract": pos.get("avg_cost_per_contract"),
        "mid": pos.get("mid"),
        "unrealized_pnl": pnl,
        "unrealized_pct": pnl_pct,
        "dc_high_D": (dc or {}).get("dc_high_D"),
        "dc_low_D": (dc or {}).get("dc_low_D"),
        "flags": flags,
        "action": action,
        "rationale": rationale,
        "in_loss": in_loss,
        "technical_breach": technical_breach,
    }


async def build_recommendations(equity: float) -> dict:
    snap = _load_current()
    positions = snap.get("positions", [])
    if not positions:
        return {"ts": datetime.now(timezone.utc).isoformat(), "n_positions": 0, "recommendations": [], "summary": {}}

    # Fetch bars once per unique underlying
    config = TradierConfig()
    underlyings = sorted({p.get("symbol") for p in positions if p.get("symbol")})
    bars_by_sym: Dict[str, Optional[dict]] = {}
    # Use trb account for history reads (any account works for market data)
    client = TradierAPIClient(config=config, account_key="trb")
    setattr(client, "_account_key", "trb")
    try:
        for sym in underlyings:
            try:
                bars = await _fetch_bars(client, sym, DC_LOOKBACK_DAYS + 10)
                bars_by_sym[sym] = _dc_bands(bars, DC_LOOKBACK_DAYS)
            except Exception as e:
                print(f"WARN bars fetch failed for {sym}: {e}", file=sys.stderr)
                bars_by_sym[sym] = None
    finally:
        await client.close()

    recs = [_evaluate_position(p, bars_by_sym.get(p.get("symbol")), equity) for p in positions]
    summary = {
        "n_positions": len(recs),
        "n_close": sum(1 for r in recs if r["action"] == "CLOSE"),
        "n_cover": sum(1 for r in recs if r["action"] == "COVER"),
        "n_hold": sum(1 for r in recs if r["action"] in ("HOLD", "HOLD_WATCH")),
        "n_first_loss_active": sum(1 for r in recs if r["in_loss"]),
        "n_technical_breach": sum(1 for r in recs if r["technical_breach"]),
        "total_open_pnl": sum(r["unrealized_pnl"] for r in recs),
    }
    return {
        "ts": datetime.now(timezone.utc).isoformat(),
        "snapshot_ts": snap.get("ts"),
        "account_equity_assumed": equity,
        "summary": summary,
        "recommendations": recs,
    }


def _print_report(report: dict) -> None:
    s = report["summary"]
    print(f"\n=== options recommendations {report['ts']} ===")
    print(f"snapshot: {report['snapshot_ts']}  equity assumed: ${report['account_equity_assumed']:,.0f}")
    print(f"positions: {s['n_positions']}  CLOSE: {s['n_close']}  COVER: {s['n_cover']}  HOLD: {s['n_hold']}  "
          f"first-loss active: {s['n_first_loss_active']}  technical breach: {s['n_technical_breach']}")
    print(f"total open pnl: ${s['total_open_pnl']:+,.2f}\n")
    sorted_recs = sorted(report["recommendations"],
                         key=lambda r: (0 if r["action"] == "CLOSE" else 1 if r["action"] == "COVER" else 2,
                                        r["unrealized_pnl"]))
    for r in sorted_recs:
        action_emoji = {"CLOSE": "✗", "COVER": "○", "HOLD_WATCH": "·", "HOLD": "✓"}.get(r["action"], "?")
        sign = "+" if r["unrealized_pnl"] >= 0 else ""
        ot = "C" if r["option_type"] == "call" else "P"
        dc_h = r.get("dc_high_D")
        dc_l = r.get("dc_low_D")
        dc_str = f"DC[{dc_l:.2f}, {dc_h:.2f}]" if dc_h and dc_l else "DC[?]"
        print(f"  {action_emoji} {r['action']:11s} {r['occ']:25s} {ot} K=${r['strike']:>6.0f}  "
              f"qty={r['qty']:>3.0f}  ul=${r['underlying_price']:>7.2f} {dc_str}  "
              f"pnl={sign}${r['unrealized_pnl']:>8.2f} ({r['unrealized_pct']:+6.1f}%)  "
              f"DTE={r['dte']:>3d}  held={r['held_days']}d")
        for f in r["flags"]:
            print(f"      ⚑ {f}")
        if r["action"] in ("CLOSE", "COVER"):
            print(f"      → {r['rationale']}")
    print()


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--account-equity", type=float, default=DEFAULT_ACCOUNT_EQUITY,
                    help=f"For L1.B2 dollar-loss check. Default ${DEFAULT_ACCOUNT_EQUITY:,.0f}.")
    ap.add_argument("--json-only", action="store_true")
    args = ap.parse_args()
    report = await build_recommendations(args.account_equity)
    RECS_PATH.write_text(json.dumps(report, indent=2, default=str))
    if not args.json_only:
        _print_report(report)
    print(f"wrote {RECS_PATH}")


if __name__ == "__main__":
    asyncio.run(main())
