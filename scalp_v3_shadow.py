"""
scalp_v3_shadow.py — V3 forward-test shadow runner with 100% live-function parity.

Reads the SAME data live reads (`data/market_data_*.json`), calls the SAME
functions live calls (`scalp_v3_live.check_scalp_v3_live_entry/exit`,
`hedge_decisions.should_close_hedge_wt3m1h`, `hedge_decisions.score_hedge_candidate`),
maintains virtual positions, logs every decision.

Why this exists: `scalp_v3_paper.py` runs its own custom entry/exit logic; PnL
output is silent on the actual live close paths. Sandbox numbers from that file
do not predict live behavior. This shadow runner produces apples-to-apples PnL.

Usage:
    python3 scalp_v3_shadow.py --account inf --interval 30 --variant default
    python3 scalp_v3_shadow.py --account inf --interval 30 --variant strict5 \
        --override HEDGE_STRICT_WT_MIN_TFS_AGAINST=5
    python3 scalp_v3_shadow.py --account inf --interval 30 --variant nostrict \
        --override HEDGE_STRICT_WT_ALL_TFS_ENABLED=False

Output:
    data/scalp_v3_shadow/<variant>_decisions_YYYYMMDD.jsonl
    data/scalp_v3_shadow/<variant>_state.json
    data/scalp_v3_shadow/<variant>_run.log
"""
from __future__ import annotations
import argparse, asyncio, glob, json, os, signal, sys, time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Dict, List, Optional

# Live functions — same module live uses, NO duplication.
import config as live_config
from scalp_v3_live import check_scalp_v3_live_entry, check_scalp_v3_live_exit
from hedge_decisions import should_close_hedge_wt3m1h, score_hedge_candidate

SHADOW_DIR = Path("data/scalp_v3_shadow")
SHADOW_DIR.mkdir(parents=True, exist_ok=True)


@dataclass
class VirtualPosition:
    position_key: str
    symbol: str
    side: str  # "LONG" or "SHORT"
    entry_price: float
    positionAmt: float  # signed: + for long, - for short
    opened_at: float    # epoch seconds
    augment_reason: str
    is_hedge: bool = False
    hedge_for: Optional[str] = None
    gain: float = 0.0
    last_seen_price: float = 0.0
    last_updated: float = 0.0

    def update_gain(self, current_price: float):
        if self.entry_price <= 0:
            return
        if self.side == "LONG":
            self.gain = ((current_price - self.entry_price) / self.entry_price) * 100.0
        else:
            self.gain = ((self.entry_price - current_price) / self.entry_price) * 100.0
        self.last_seen_price = current_price
        self.last_updated = time.time()


def parse_overrides(items: List[str]) -> Dict[str, object]:
    out = {}
    for it in items or []:
        if "=" not in it:
            continue
        k, v = it.split("=", 1)
        v = v.strip()
        if v.lower() in ("true", "false"):
            out[k.strip()] = (v.lower() == "true")
        else:
            try:
                out[k.strip()] = int(v)
            except ValueError:
                try:
                    out[k.strip()] = float(v)
                except ValueError:
                    out[k.strip()] = v
    return out


def make_config(overrides: Dict[str, object]):
    cfg = live_config.Config()
    for k, v in overrides.items():
        if hasattr(cfg, k):
            setattr(cfg, k, v)
        else:
            print(f"[shadow] WARN override {k} not on Config — ignored")
    return cfg


def latest_market_data_path() -> Optional[Path]:
    files = sorted(glob.glob("data/market_data_*.json"), reverse=True)
    return Path(files[0]) if files else None


def load_market_data() -> Dict[str, Dict]:
    p = latest_market_data_path()
    if not p:
        return {}
    try:
        import orjson
        with open(p, "rb") as f:
            return orjson.loads(f.read())
    except Exception as e:
        print(f"[shadow] load_market_data failed: {e}")
        return {}


def load_universe(account: str) -> List[str]:
    candidates = (
        Path(f"symbols_{account}_long.json"),
        Path(f"symbols_{account}_short.json"),
        Path(f"data/symbols_{account}_long.json"),
        Path(f"data/symbols_{account}_short.json"),
    )
    syms = set()
    for p in candidates:
        if not p.exists():
            continue
        try:
            arr = json.loads(p.read_text())
            for s in arr:
                if isinstance(s, str):
                    syms.add(s)
                elif isinstance(s, dict):
                    sym = s.get("symbol") or s.get("sym")
                    if sym: syms.add(sym)
        except Exception:
            pass
    return sorted(syms)


def state_path(variant: str) -> Path:
    return SHADOW_DIR / f"{variant}_state.json"


def decisions_path(variant: str) -> Path:
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    return SHADOW_DIR / f"{variant}_decisions_{today}.jsonl"


def load_state(variant: str) -> Dict[str, VirtualPosition]:
    p = state_path(variant)
    if not p.exists():
        return {}
    try:
        raw = json.loads(p.read_text())
        return {k: VirtualPosition(**v) for k, v in raw.get("positions", {}).items()}
    except Exception as e:
        print(f"[shadow] load_state corrupted, starting fresh: {e}")
        return {}


def save_state(variant: str, positions: Dict[str, VirtualPosition], realized_pct: float):
    p = state_path(variant)
    payload = {
        "saved_at": datetime.now(timezone.utc).isoformat(),
        "realized_pct": realized_pct,
        "positions": {k: asdict(v) for k, v in positions.items()},
    }
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, indent=2))
    tmp.replace(p)


def log_decision(variant: str, event: Dict):
    p = decisions_path(variant)
    event["_ts"] = datetime.now(timezone.utc).isoformat()
    with open(p, "a") as f:
        f.write(json.dumps(event) + "\n")


def cycle(variant: str, account: str, cfg, positions: Dict[str, VirtualPosition], realized_pct: float) -> tuple[float, int, int, int]:
    """Run one decision cycle. Returns (new_realized_pct, n_entries, n_exits, n_hedge_holds)."""
    market = load_market_data()
    if not market:
        return realized_pct, 0, 0, 0
    universe = load_universe(account)
    if not universe:
        # fallback: all symbols in market data
        universe = sorted(market.keys())[:200]
    n_entries = n_exits = n_hedge_holds = 0
    cap_usd = float(getattr(cfg, "SCALP_V3_POSITION_CAP_USD", 10.0))
    max_concurrent = int(getattr(cfg, "SCALP_V3_MAX_CONCURRENT", 3))
    n_open = sum(1 for p in positions.values() if abs(p.positionAmt) > 0)

    for sym in universe:
        ind = market.get(sym)
        if not ind:
            continue
        price = float(ind.get("current_price") or ind.get("close") or 0)
        if price <= 0:
            continue

        # Check both LONG and SHORT keys for this symbol
        for side in ("LONG", "SHORT"):
            key = f"{account}:{sym}_{side}"
            pos = positions.get(key)

            # Mark-to-market existing position
            if pos and abs(pos.positionAmt) > 0:
                pos.update_gain(price)

                # First check the V3 exit decision (technical reversal)
                exit_dec = check_scalp_v3_live_exit(key, ind, price, _ns_for(pos), cfg)
                if exit_dec:
                    # V3 exits ARE allowed at any P/L per the trend-follow design
                    realized_pct += pos.gain
                    log_decision(variant, {"event": "V3_EXIT", "key": key, "price": price, "entry": pos.entry_price,
                                           "gain_pct": round(pos.gain, 4), "reason": exit_dec.get("reason"),
                                           "age_min": round((time.time() - pos.opened_at) / 60.0, 2)})
                    n_exits += 1
                    n_open -= 1
                    pos.positionAmt = 0.0
                    continue

                # If position is_hedge, also evaluate the WT3m+1h hedge close gate (with NOLOSS hold)
                if pos.is_hedge:
                    hedge_dec = should_close_hedge_wt3m1h(ind, position_is_long=(pos.side == "LONG"),
                                                          hedge_gain=pos.gain, config=cfg)
                    if hedge_dec is not None:
                        if hedge_dec.get("fire"):
                            realized_pct += pos.gain
                            log_decision(variant, {"event": "HEDGE_WT_CLOSE", "key": key, "price": price,
                                                   "entry": pos.entry_price, "gain_pct": round(pos.gain, 4),
                                                   "wt": hedge_dec.get("wt"), "reason": "WT3M1H_AGAINST"})
                            n_exits += 1
                            n_open -= 1
                            pos.positionAmt = 0.0
                            continue
                        else:
                            log_decision(variant, {"event": "HEDGE_NOLOSS_HOLD", "key": key, "price": price,
                                                   "gain_pct": round(pos.gain, 4), "wt": hedge_dec.get("wt"),
                                                   "reason": "NOLOSS_HOLD"})
                            n_hedge_holds += 1

            else:
                # Try to open via V3 entry function — same one live calls
                if n_open >= max_concurrent:
                    continue
                entry_dec = check_scalp_v3_live_entry(sym, key, ind, price, None, account, cfg)
                if entry_dec and entry_dec.get("side") == side:
                    qty = cap_usd / price if price > 0 else 0
                    if qty <= 0:
                        continue
                    new_pos = VirtualPosition(
                        position_key=key, symbol=sym, side=side, entry_price=price,
                        positionAmt=(qty if side == "LONG" else -qty),
                        opened_at=time.time(), augment_reason=entry_dec.get("reason", "SCALP_V3_OPEN"),
                        is_hedge=False, hedge_for=None, gain=0.0, last_seen_price=price, last_updated=time.time(),
                    )
                    positions[key] = new_pos
                    n_entries += 1
                    n_open += 1
                    log_decision(variant, {"event": "V3_ENTRY", "key": key, "price": price, "qty": qty,
                                           "side": side, "reason": entry_dec.get("reason")})
    # Cleanup closed positions periodically (keep state file lean)
    closed = [k for k, p in positions.items() if abs(p.positionAmt) == 0]
    for k in closed:
        positions.pop(k, None)
    return realized_pct, n_entries, n_exits, n_hedge_holds


def _ns_for(pos: VirtualPosition) -> SimpleNamespace:
    """Adapter: V3 functions accept any object with attribute access."""
    return SimpleNamespace(positionAmt=pos.positionAmt, entry_price=pos.entry_price,
                           opened_at=pos.opened_at, augment_reason=pos.augment_reason,
                           gain=pos.gain, is_hedge=pos.is_hedge, hedge_for=pos.hedge_for)


async def main_async(args):
    overrides = parse_overrides(args.override or [])
    cfg = make_config(overrides)
    print(f"[shadow] variant={args.variant} account={args.account} interval={args.interval}s overrides={overrides}")
    print(f"[shadow] HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN={getattr(cfg,'HEDGE_WT_CLOSE_REQUIRE_NONNEG_GAIN',True)} "
          f"HEDGE_STRICT_WT_ALL_TFS_ENABLED={getattr(cfg,'HEDGE_STRICT_WT_ALL_TFS_ENABLED',True)} "
          f"HEDGE_STRICT_WT_MIN_TFS_AGAINST={getattr(cfg,'HEDGE_STRICT_WT_MIN_TFS_AGAINST',4)} "
          f"SCALP_V3_SIDE_MODE={getattr(cfg,'SCALP_V3_SIDE_MODE','BOTH')} "
          f"SCALP_V3_POSITION_CAP_USD={getattr(cfg,'SCALP_V3_POSITION_CAP_USD',10.0)}")

    positions = load_state(args.variant)
    state_payload = json.loads(state_path(args.variant).read_text()) if state_path(args.variant).exists() else {}
    realized = float(state_payload.get("realized_pct", 0.0))
    cycle_n = int(state_payload.get("cycle_n", 0))
    print(f"[shadow] resumed positions={len(positions)} realized_pct={realized:+.2f}% cycle_n={cycle_n}")

    stop = False
    def _h(*_):
        nonlocal stop; stop = True
    signal.signal(signal.SIGINT, _h); signal.signal(signal.SIGTERM, _h)

    while not stop:
        cycle_n += 1
        t0 = time.time()
        realized, n_entries, n_exits, n_holds = cycle(args.variant, args.account, cfg, positions, realized)
        n_open = sum(1 for p in positions.values() if abs(p.positionAmt) > 0)
        elapsed = time.time() - t0
        print(f"[{datetime.now(timezone.utc).strftime('%H:%M:%S')}] cycle#{cycle_n} ent={n_entries} exit={n_exits} "
              f"noloss_hold={n_holds} open={n_open} realized={realized:+.2f}% elapsed={elapsed:.2f}s")
        # Persist
        sp = state_path(args.variant)
        payload = {"saved_at": datetime.now(timezone.utc).isoformat(), "realized_pct": realized,
                   "cycle_n": cycle_n, "positions": {k: asdict(v) for k, v in positions.items()}}
        tmp = sp.with_suffix(".tmp"); tmp.write_text(json.dumps(payload, indent=2)); tmp.replace(sp)
        # Sleep until next cycle
        for _ in range(int(args.interval)):
            if stop: break
            await asyncio.sleep(1)
    print(f"[shadow] stopped. final realized={realized:+.2f}% positions={len(positions)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--account", default="inf")
    ap.add_argument("--variant", default="default", help="State file suffix; lets you A/B different overrides side-by-side")
    ap.add_argument("--interval", type=int, default=30, help="Seconds between decision cycles")
    ap.add_argument("--override", action="append", help="Config override e.g. --override HEDGE_STRICT_WT_MIN_TFS_AGAINST=5")
    args = ap.parse_args()
    asyncio.run(main_async(args))


if __name__ == "__main__":
    main()
