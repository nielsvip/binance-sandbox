"""tradier_emergency_brake.py — Independent emergency brake for naked options.

Designed to prevent the 80-90% premium-loss disaster pattern (PLTR/NEM Apr 2026)
even if the main trading system (tradier_manage / tradier_options_analyzer) is
broken or hung. Runs as its own process, own launchd plist, own state files,
own credentials path.

TIERS (configurable via data/emergency_brake/config.json — DRY_RUN by default):

    -30% premium loss → ALERT only      (write to alerts file + log critical)
    -50% premium loss → AUTO HEDGE      (sell_short equity for losing call,
                                         buy equity for losing put;
                                         qty = ceil(|delta| × contracts × 100))
    -70% premium loss → DOUBLE HEDGE    (1.5× delta sizing — go slightly net-short
                                         to capture continued decline)
    -85% premium loss → CIRCUIT BREAKER (cancel all pending option BUY orders,
                                         write data/emergency_brake/halt.flag,
                                         urgent alert. Does NOT close the option
                                         — the option keeps its tail-recovery
                                         optionality, just stops bleeding new
                                         capital and freezes new entries)

Independence guarantees:
  - Own TradierAPIClient instance, doesn't share with running daemons
  - Reads/writes its OWN state in data/emergency_brake/ — separate from the
    options_equity_hedges.json the analyzer uses (we cooperate via dedupe but
    don't depend on it)
  - launchd KeepAlive restarts on crash
  - Per-OCC, per-tier dedupe — fires each tier ONCE per OCC per day
  - DRY_RUN flag in config blocks all order placement (default ON until owner
    flips after observing one full session of dry-run logs)

CLI:
    python3 tradier_emergency_brake.py                    # one-shot tick
    python3 tradier_emergency_brake.py --loop 60          # poll every 60s
    python3 tradier_emergency_brake.py --tier-test 75     # simulate tier-3 fire
    python3 tradier_emergency_brake.py --halt-status      # show halt-flag state
    python3 tradier_emergency_brake.py --reset-halt       # clear halt flag (manual)
"""
import argparse
import asyncio
import json
import logging
import math
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig
from tradier_api import TradierAPIClient
from tradier_options_analyzer import (
    bs_greeks, get_option_positions, parse_occ_symbol,
)

BRAKE_DIR = BASE_PATH / "data" / "emergency_brake"
BRAKE_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_PATH = BRAKE_DIR / "config.json"
STATE_PATH = BRAKE_DIR / "state.json"
HALT_FLAG_PATH = BRAKE_DIR / "halt.flag"

DEFAULT_CONFIG = {
    "ENABLED": True,
    "DRY_RUN": True,
    "ACCOUNTS": ["trb"],
    "POLL_INTERVAL_SEC": 60,
    "OPENING_BUFFER_MIN": 30.0,
    "TIERS": {
        "alert": {"loss_pct": -30.0, "hedge_mult": 0.0},
        "hedge": {"loss_pct": -50.0, "hedge_mult": 1.0},
        "double": {"loss_pct": -70.0, "hedge_mult": 1.5},
        "halt":   {"loss_pct": -85.0, "hedge_mult": 0.0}
    },
    "RISK_FREE_RATE": 0.045,
    "DEFAULT_IV": 0.30,
    "ALERT_EMAIL": "nielsvip@gmail.com",
    "MIN_HEDGE_SHARES": 1,
    "MAX_HEDGE_SHARES_PER_OCC": 10000,
    "_doc": "Edit DRY_RUN=false ONLY after observing one full market day of dry-run logs in data/emergency_brake/<date>.jsonl. The brake will fire real orders when DRY_RUN=false. Halt flag at data/emergency_brake/halt.flag — when present, NEW option opens should be blocked by main system."
}


def setup_logger() -> logging.Logger:
    log = logging.getLogger("emergency_brake")
    log.setLevel(logging.INFO)
    log.propagate = False
    if log.handlers:
        return log
    log_dir = Path(os.path.expanduser("~")) / "logs"
    log_dir.mkdir(exist_ok=True)
    fh = logging.FileHandler(log_dir / "emergency_brake.log")
    fh.setLevel(logging.INFO)
    fh.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s"))
    log.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setLevel(logging.INFO)
    sh.setFormatter(logging.Formatter("[BRAKE] %(asctime)s %(levelname)s: %(message)s"))
    log.addHandler(sh)
    return log


logger = setup_logger()


def load_config() -> Dict[str, Any]:
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
        logger.warning(f"created default config at {CONFIG_PATH} — DRY_RUN=true")
    try:
        loaded = json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        logger.error(f"config parse failed: {e} — using defaults")
        return DEFAULT_CONFIG
    out = dict(DEFAULT_CONFIG)
    out.update(loaded)
    out["TIERS"] = {**DEFAULT_CONFIG["TIERS"], **loaded.get("TIERS", {})}
    return out


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"fired": {}, "last_tick": None}
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {"fired": {}, "last_tick": None}


def save_state(state: Dict[str, Any]) -> None:
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2, default=str))
    tmp.replace(STATE_PATH)


def in_opening_buffer(min_minutes: float) -> Tuple[bool, float]:
    if min_minutes <= 0:
        return False, 0.0
    try:
        import pytz
        now_et = datetime.now(pytz.timezone("America/New_York"))
        if now_et.weekday() >= 5:
            return False, 0.0
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        if now_et < open_et or now_et > close_et:
            return False, -1.0
        mins = (now_et - open_et).total_seconds() / 60.0
        if 0 <= mins < min_minutes:
            return True, mins
        return False, mins
    except Exception:
        return False, 0.0


def is_market_open() -> bool:
    try:
        import pytz
        now_et = datetime.now(pytz.timezone("America/New_York"))
        if now_et.weekday() >= 5:
            return False
        open_et = now_et.replace(hour=9, minute=30, second=0, microsecond=0)
        close_et = now_et.replace(hour=16, minute=0, second=0, microsecond=0)
        return open_et <= now_et <= close_et
    except Exception:
        return False


def append_jsonl(path: Path, record: Dict[str, Any]) -> None:
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def write_halt_flag(reason: str) -> None:
    payload = {"set_at": datetime.now(timezone.utc).isoformat(), "reason": reason}
    HALT_FLAG_PATH.write_text(json.dumps(payload, indent=2))
    logger.critical(f"[HALT_FLAG] WRITTEN — {reason}")


def is_halted() -> Tuple[bool, Optional[Dict]]:
    if not HALT_FLAG_PATH.exists():
        return False, None
    try:
        return True, json.loads(HALT_FLAG_PATH.read_text())
    except Exception:
        return True, None


def clear_halt_flag() -> bool:
    if HALT_FLAG_PATH.exists():
        HALT_FLAG_PATH.unlink()
        logger.warning("[HALT_FLAG] CLEARED manually")
        return True
    return False


def compute_premium_loss_pct(pos: Dict[str, Any]) -> Tuple[float, float, float]:
    """Return (loss_pct, mid_price, avg_cost_per_contract).
    Loss is computed against AVG cost per contract using mid as exit proxy.
    Mid = (bid+ask)/2 if both present, else last."""
    qty = abs(float(pos.get("quantity", 0) or 0))
    cost_basis = abs(float(pos.get("cost_basis", 0) or 0))
    if qty <= 0 or cost_basis <= 0:
        return 0.0, 0.0, 0.0
    avg_cost = cost_basis / (qty * 100.0)
    bid = float(pos.get("bid", 0) or 0)
    ask = float(pos.get("ask", 0) or 0)
    last = float(pos.get("last", 0) or 0)
    if bid > 0 and ask > 0:
        mid = (bid + ask) / 2.0
    else:
        mid = last
    if mid <= 0 or avg_cost <= 0:
        return 0.0, mid, avg_cost
    loss_pct = ((mid - avg_cost) / avg_cost) * 100.0
    return loss_pct, mid, avg_cost


async def fetch_quote(client: TradierAPIClient, occ: str) -> Dict[str, Any]:
    try:
        q = await client.get_quote(occ)
        return q or {}
    except Exception as e:
        logger.warning(f"fetch_quote({occ}) failed: {e}")
        return {}


async def compute_delta_for_position(client: TradierAPIClient, pos: Dict[str, Any], cfg: Dict[str, Any]) -> float:
    """Compute |delta| for sizing the equity hedge. Use API greeks if present,
    else BS fallback with a default IV."""
    occ = pos.get("occ_symbol") or pos.get("symbol")
    parsed = parse_occ_symbol(occ) if occ else None
    if not parsed:
        return 0.0
    is_call = parsed["option_type"] == "call"
    strike = parsed["strike"]
    try:
        exp_dt = datetime.strptime(parsed["expiration"], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        dte_days = max(0, (exp_dt - datetime.now(timezone.utc)).days)
    except Exception:
        dte_days = 60
    T = max(1.0 / 365.0, dte_days / 365.0)
    underlying_q = await fetch_quote(client, parsed["symbol"])
    S = float(underlying_q.get("last", 0) or 0)
    if S <= 0:
        return 0.0
    iv = pos.get("greeks_smv_vol")
    try:
        iv = float(iv) if iv else None
    except Exception:
        iv = None
    if not iv or iv <= 0 or iv > 5:
        iv = float(cfg.get("DEFAULT_IV", 0.30))
    g = bs_greeks(S=S, K=strike, T=T, r=float(cfg.get("RISK_FREE_RATE", 0.045)), sigma=iv, is_call=is_call)
    return abs(float(g.get("delta", 0) or 0))


async def cancel_pending_buy_options(client: TradierAPIClient) -> List[Dict[str, Any]]:
    """Cancel every pending option BUY order. Used by Tier 4 (HALT)."""
    try:
        orders = await client.get_orders()
    except Exception as e:
        logger.error(f"cancel_pending_buy_options: get_orders failed: {e}")
        return []
    cancelled: List[Dict[str, Any]] = []
    for o in (orders or []):
        st = (o.get("status") or "").lower()
        if st not in ("open", "pending"):
            continue
        cls = (o.get("class") or "").lower()
        side = (o.get("side") or "").lower()
        if cls != "option":
            continue
        if "buy" not in side:
            continue
        oid = o.get("id")
        try:
            res = await client.cancel_order(account_key=None, order_id=oid)
            cancelled.append({"order_id": oid, "side": side, "occ": o.get("option_symbol", o.get("symbol", "")), "result": res})
            logger.critical(f"[HALT_CANCEL] order {oid} {side} {o.get('option_symbol', '?')} cancelled")
        except Exception as e:
            logger.error(f"cancel order {oid} failed: {e}")
    return cancelled


def _brake_load_underlying_indicators(underlying: str) -> Dict[str, Any]:
    # Read the live indicator snapshot used by tradier_manage / analyzer.
    # Same path the analyzer uses; falls back to empty if file is missing.
    try:
        path = Path("/Users/niels/Documents/binance/data/tradier/tradier_indicators_latest.json")
        if not path.exists():
            path = Path("/home/niels/binance/data/tradier/tradier_indicators_latest.json")
        if path.exists():
            with open(path) as f:
                _map = json.load(f)
            if isinstance(_map, dict):
                return _map.get(underlying, {}) or {}
    except Exception:
        pass
    return {}


def _brake_direction_blocks(side: str, ind: Dict[str, Any], und_px: float) -> Optional[str]:
    # Mirror of tradier_options_analyzer DIRECTION_GUARD.
    # Never sell_short into an uptick. Never buy into a downtick. Hedging is not suicide.
    if not ind or und_px <= 0:
        return None
    close_5m_prev = float(ind.get("close_5m_prev", 0) or 0)
    dch5 = float(ind.get("dc_high_5m", 0) or 0)
    dcl5 = float(ind.get("dc_low_5m", 0) or 0)
    wt_vel_5m = float(ind.get("wt_velocity_5m", 0) or 0)
    going_up = (close_5m_prev > 0 and und_px > close_5m_prev) or (dch5 > 0 and und_px > dch5) or (wt_vel_5m > 0)
    going_down = (close_5m_prev > 0 and und_px < close_5m_prev) or (dcl5 > 0 and und_px < dcl5) or (wt_vel_5m < 0)
    if side == "sell_short" and going_up:
        return f"market_UP px={und_px:.2f} prev5m={close_5m_prev:.2f} dch5={dch5:.2f} wt_vel5m={wt_vel_5m:+.1f}"
    if side == "buy" and going_down:
        return f"market_DOWN px={und_px:.2f} prev5m={close_5m_prev:.2f} dcl5={dcl5:.2f} wt_vel5m={wt_vel_5m:+.1f}"
    return None


def _brake_cap_qty(delta_qty: int, und_px: float, opt_cost_basis: float) -> Tuple[int, str]:
    # Mirror of tradier_options_analyzer _hedge_qty_capped. Bounded by delta-equivalent
    # shares, % of option cost basis, absolute notional, MAX_POSITION_SIZE, MAX_ORDER_VALUE.
    if delta_qty <= 0 or und_px <= 0:
        return 0, "qty<=0"
    cfg_obj = TradierConfig()
    caps: List[Tuple[int, str]] = [(delta_qty, "delta")]
    pct = float(getattr(cfg_obj, "OPTIONS_EQUITY_HEDGE_MAX_PCT_OF_OPT_COST", 100.0) or 100.0)
    if opt_cost_basis > 0 and pct > 0:
        caps.append((int((opt_cost_basis * pct / 100.0) / und_px), f"opt_cost_x{pct:.0f}%"))
    abs_usd = float(getattr(cfg_obj, "OPTIONS_EQUITY_HEDGE_MAX_NOTIONAL_USD", 2500.0) or 0.0)
    if abs_usd > 0:
        caps.append((int(abs_usd / und_px), f"abs_${abs_usd:.0f}"))
    pos_size = float(getattr(cfg_obj, "MAX_POSITION_SIZE", 2500.0) or 0.0)
    if pos_size > 0:
        caps.append((int(pos_size / und_px), f"MAX_POSITION_SIZE_${pos_size:.0f}"))
    order_cap = float(getattr(cfg_obj, "MAX_ORDER_VALUE", 1000.0) or 0.0)
    if order_cap > 0:
        caps.append((int(order_cap / und_px), f"MAX_ORDER_VALUE_${order_cap:.0f}"))
    qty, label = min(caps, key=lambda x: x[0])
    return max(0, qty), label


async def _brake_get_underlying_price(client: TradierAPIClient, underlying: str, ind: Dict[str, Any]) -> float:
    px = float(ind.get("current_price", 0) or ind.get("mark_price", 0) or 0)
    if px > 0:
        return px
    try:
        q = await client.get_quote(underlying)
        return float(q.get("last", 0) or 0)
    except Exception:
        return 0.0


async def place_emergency_hedge(
    client: TradierAPIClient,
    account_key: str,
    underlying: str,
    side: str,
    qty: int,
    reason: str,
    dry_run: bool,
) -> Dict[str, Any]:
    if qty <= 0:
        return {"skipped": "qty<=0"}
    if dry_run:
        logger.warning(f"[DRY_RUN] would place {side} x{qty} {underlying} — {reason}")
        return {"dry_run": True, "side": side, "qty": qty, "underlying": underlying}
    try:
        res = await client.place_order(
            account_key=account_key, symbol=underlying, side=side,
            quantity=qty, order_type="market", duration="day",
        )
        logger.critical(f"[EMERGENCY_HEDGE_OPEN] {underlying} {side} x{qty} — {reason} — {str(res)[:200]}")
        return res
    except Exception as e:
        logger.error(f"place_emergency_hedge({underlying},{side},{qty}) error: {e}")
        return {"error": str(e)}


def _occ_to_underlying_and_type(occ: str) -> Tuple[Optional[str], Optional[str]]:
    parsed = parse_occ_symbol(occ)
    if not parsed:
        return None, None
    return parsed["symbol"], parsed["option_type"]


def _classify_tier(loss_pct: float, tiers: Dict[str, Dict[str, float]]) -> Optional[str]:
    """Return the highest tier name whose threshold is breached, else None.
    Tiers are listed alert/hedge/double/halt — higher tier = deeper loss."""
    order = ["halt", "double", "hedge", "alert"]
    for name in order:
        t = tiers.get(name, {})
        if loss_pct <= float(t.get("loss_pct", -999.0)):
            return name
    return None


async def evaluate_account(client: TradierAPIClient, account_key: str, cfg: Dict[str, Any], state: Dict[str, Any], today: str) -> List[Dict[str, Any]]:
    """Run brake logic for a single account. Returns list of action records."""
    actions: List[Dict[str, Any]] = []
    try:
        positions = await get_option_positions(client)
    except Exception as e:
        logger.error(f"[{account_key}] fetch positions failed: {e}")
        return actions
    if not positions:
        return actions
    occs = [p.get("occ_symbol") or p.get("symbol") for p in positions]
    occs = [o for o in occs if o]
    underlyings = list({_occ_to_underlying_and_type(o)[0] for o in occs if _occ_to_underlying_and_type(o)[0]})
    quote_syms = list(set(occs + underlyings))
    quotes: Dict[str, Dict[str, Any]] = {}
    try:
        quotes = await client.get_quotes(quote_syms) if quote_syms else {}
    except Exception as e:
        logger.warning(f"[{account_key}] get_quotes failed: {e}")
    fired = state.setdefault("fired", {})
    halt_triggered = False
    for pos in positions:
        occ = pos.get("occ_symbol") or pos.get("symbol")
        if not occ:
            continue
        underlying, opt_type = _occ_to_underlying_and_type(occ)
        if not underlying or opt_type not in ("call", "put"):
            continue
        opt_q = quotes.get(occ, {}) or {}
        pos["bid"] = float(opt_q.get("bid", 0) or 0)
        pos["ask"] = float(opt_q.get("ask", 0) or 0)
        pos["last"] = float(opt_q.get("last", 0) or 0)
        if isinstance(opt_q.get("greeks"), dict):
            pos["greeks_smv_vol"] = opt_q["greeks"].get("smv_vol")
        loss_pct, mid, avg_cost = compute_premium_loss_pct(pos)
        tier = _classify_tier(loss_pct, cfg["TIERS"])
        if not tier:
            continue
        fire_key = f"{today}|{occ}|{tier}"
        if fired.get(fire_key):
            continue
        record_base = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "account": account_key, "occ": occ, "underlying": underlying,
            "option_type": opt_type, "loss_pct": round(loss_pct, 2),
            "mid": round(mid, 4), "avg_cost": round(avg_cost, 4),
            "qty": float(pos.get("quantity", 0) or 0), "tier": tier,
            "tier_threshold": cfg["TIERS"][tier]["loss_pct"],
            "dry_run": bool(cfg.get("DRY_RUN", True)),
        }
        if tier == "alert":
            rec = {**record_base, "action": "ALERT",
                   "note": f"Premium loss {loss_pct:.1f}% crossed Tier-1 (-30%) threshold. Watch closely."}
            logger.warning(f"[BRAKE_ALERT] {occ} loss={loss_pct:.1f}% — Tier 1")
            actions.append(rec)
        elif tier == "hedge":
            delta = await compute_delta_for_position(client, pos, cfg)
            qty_pos = abs(float(pos.get("quantity", 0) or 0))
            mult = float(cfg["TIERS"]["hedge"].get("hedge_mult", 1.0))
            hedge_side = "sell_short" if opt_type == "call" else "buy"
            _opt_cb = abs(float(pos.get("cost_basis", 0) or 0))
            _und_ind = _brake_load_underlying_indicators(underlying)
            _und_px = await _brake_get_underlying_price(client, underlying, _und_ind)
            _dir_block = _brake_direction_blocks(hedge_side, _und_ind, _und_px)
            if _dir_block:
                logger.critical(f"[BRAKE_HEDGE_DIR_BLOCK] {underlying} {hedge_side} REFUSED — {_dir_block}. HEDGING IS NOT SUICIDE.")
                rec = {**record_base, "action": "HEDGE_BLOCKED_DIRECTION",
                       "hedge_side": hedge_side, "block_reason": _dir_block}
                actions.append(rec)
                fired[fire_key] = record_base["ts"]
                continue
            _delta_qty = max(int(cfg.get("MIN_HEDGE_SHARES", 1)), math.ceil(delta * qty_pos * 100.0 * mult))
            _delta_qty = min(_delta_qty, int(cfg.get("MAX_HEDGE_SHARES_PER_OCC", 10000)))
            hedge_qty, _cap_label = _brake_cap_qty(_delta_qty, _und_px or 1.0, _opt_cb)
            if hedge_qty <= 0:
                logger.warning(f"[BRAKE_HEDGE_CAP_ZERO] {underlying} {hedge_side} — capped to 0 (uncapped={_delta_qty}, cap={_cap_label})")
                rec = {**record_base, "action": "HEDGE_CAPPED_TO_ZERO",
                       "uncapped_qty": _delta_qty, "cap_label": _cap_label}
                actions.append(rec)
                fired[fire_key] = record_base["ts"]
                continue
            if hedge_qty < _delta_qty:
                logger.warning(f"[BRAKE_HEDGE_CAP_HIT] {underlying} qty {_delta_qty}→{hedge_qty} (cap={_cap_label} cb=${_opt_cb:.0f} px=${_und_px:.2f})")
            reason = f"BRAKE_TIER2_HEDGE loss={loss_pct:.1f}% delta={delta:.2f} qty={qty_pos:.0f} cap={_cap_label}"
            res = await place_emergency_hedge(client, account_key, underlying, hedge_side, hedge_qty, reason, cfg.get("DRY_RUN", True))
            rec = {**record_base, "action": "HEDGE", "hedge_side": hedge_side,
                   "hedge_qty": hedge_qty, "uncapped_qty": _delta_qty, "cap_label": _cap_label,
                   "delta": round(delta, 4),
                   "hedge_mult": mult, "reason": reason, "result": res}
            logger.critical(f"[BRAKE_HEDGE] {occ} loss={loss_pct:.1f}% → {hedge_side} {underlying} x{hedge_qty} (cap={_cap_label})")
            actions.append(rec)
        elif tier == "double":
            delta = await compute_delta_for_position(client, pos, cfg)
            qty_pos = abs(float(pos.get("quantity", 0) or 0))
            mult = float(cfg["TIERS"]["double"].get("hedge_mult", 1.5))
            hedge_side = "sell_short" if opt_type == "call" else "buy"
            _opt_cb = abs(float(pos.get("cost_basis", 0) or 0))
            _und_ind = _brake_load_underlying_indicators(underlying)
            _und_px = await _brake_get_underlying_price(client, underlying, _und_ind)
            _dir_block = _brake_direction_blocks(hedge_side, _und_ind, _und_px)
            if _dir_block:
                logger.critical(f"[BRAKE_DOUBLE_DIR_BLOCK] {underlying} {hedge_side} REFUSED — {_dir_block}. HEDGING IS NOT SUICIDE.")
                rec = {**record_base, "action": "DOUBLE_HEDGE_BLOCKED_DIRECTION",
                       "hedge_side": hedge_side, "block_reason": _dir_block}
                actions.append(rec)
                fired[fire_key] = record_base["ts"]
                continue
            _delta_qty = max(int(cfg.get("MIN_HEDGE_SHARES", 1)), math.ceil(delta * qty_pos * 100.0 * mult))
            _delta_qty = min(_delta_qty, int(cfg.get("MAX_HEDGE_SHARES_PER_OCC", 10000)))
            hedge_qty, _cap_label = _brake_cap_qty(_delta_qty, _und_px or 1.0, _opt_cb)
            if hedge_qty <= 0:
                logger.warning(f"[BRAKE_DOUBLE_CAP_ZERO] {underlying} {hedge_side} — capped to 0 (uncapped={_delta_qty}, cap={_cap_label})")
                rec = {**record_base, "action": "DOUBLE_HEDGE_CAPPED_TO_ZERO",
                       "uncapped_qty": _delta_qty, "cap_label": _cap_label}
                actions.append(rec)
                fired[fire_key] = record_base["ts"]
                continue
            if hedge_qty < _delta_qty:
                logger.warning(f"[BRAKE_DOUBLE_CAP_HIT] {underlying} qty {_delta_qty}→{hedge_qty} (cap={_cap_label} cb=${_opt_cb:.0f} px=${_und_px:.2f})")
            reason = f"BRAKE_TIER3_DOUBLE loss={loss_pct:.1f}% delta={delta:.2f} mult={mult} cap={_cap_label}"
            res = await place_emergency_hedge(client, account_key, underlying, hedge_side, hedge_qty, reason, cfg.get("DRY_RUN", True))
            rec = {**record_base, "action": "DOUBLE_HEDGE", "hedge_side": hedge_side,
                   "hedge_qty": hedge_qty, "uncapped_qty": _delta_qty, "cap_label": _cap_label,
                   "delta": round(delta, 4),
                   "hedge_mult": mult, "reason": reason, "result": res}
            logger.critical(f"[BRAKE_DOUBLE] {occ} loss={loss_pct:.1f}% → {hedge_side} {underlying} x{hedge_qty} (1.5×, cap={_cap_label})")
            actions.append(rec)
        elif tier == "halt":
            cancelled = []
            if not cfg.get("DRY_RUN", True):
                cancelled = await cancel_pending_buy_options(client)
            else:
                logger.warning(f"[DRY_RUN] would cancel pending option BUYs for {account_key}")
            write_halt_flag(f"BRAKE_TIER4_HALT triggered by {occ} at loss={loss_pct:.1f}%")
            rec = {**record_base, "action": "HALT", "cancelled_orders": cancelled,
                   "halt_flag": str(HALT_FLAG_PATH),
                   "note": "CIRCUIT BREAKER — pending BUY orders cancelled, halt.flag written. Manual reset required (see tradier_emergency_brake.py --reset-halt)."}
            logger.critical(f"[BRAKE_HALT] {occ} loss={loss_pct:.1f}% — CIRCUIT BREAKER, cancelled {len(cancelled)} buy orders")
            halt_triggered = True
            actions.append(rec)
        fired[fire_key] = record_base["ts"]
    if halt_triggered:
        logger.critical(f"[BRAKE] HALT was triggered this tick — main system should observe halt.flag and refuse new option opens")
    return actions


async def tick(cfg: Dict[str, Any]) -> Dict[str, Any]:
    """Run one tick of the brake. Returns summary dict."""
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    summary = {"ts": datetime.now(timezone.utc).isoformat(), "actions": [], "errors": []}
    if not cfg.get("ENABLED", True):
        return {**summary, "skipped": "BRAKE_DISABLED"}
    if not is_market_open():
        return {**summary, "skipped": "MARKET_CLOSED"}
    in_buf, mins = in_opening_buffer(float(cfg.get("OPENING_BUFFER_MIN", 30.0)))
    if in_buf:
        return {**summary, "skipped": f"OPENING_BUFFER({mins:.0f}m)"}
    state = load_state()
    config_obj = TradierConfig()
    for ak in cfg.get("ACCOUNTS", ["trb"]):
        client = TradierAPIClient(config=config_obj, account_key=ak)
        setattr(client, "_account_key", ak)
        try:
            actions = await evaluate_account(client, ak, cfg, state, today)
            for a in actions:
                summary["actions"].append(a)
                append_jsonl(BRAKE_DIR / f"{today}.jsonl", a)
        except Exception as e:
            err = {"account": ak, "error": str(e)}
            summary["errors"].append(err)
            logger.error(f"[{ak}] brake tick error: {e}")
        finally:
            try:
                await client.close()
            except Exception:
                pass
    state["last_tick"] = summary["ts"]
    save_state(state)
    return summary


def print_halt_status() -> None:
    halted, payload = is_halted()
    if halted:
        print(f"HALT_FLAG ACTIVE")
        if payload:
            print(json.dumps(payload, indent=2))
        else:
            print("(unparseable payload)")
    else:
        print("no halt flag — system armed")


async def main_async():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=float, default=0,
                    help="seconds between ticks; 0 = one-shot (default)")
    ap.add_argument("--halt-status", action="store_true", help="print halt-flag state and exit")
    ap.add_argument("--reset-halt", action="store_true", help="clear halt flag (manual)")
    args = ap.parse_args()
    if args.halt_status:
        print_halt_status()
        return
    if args.reset_halt:
        cleared = clear_halt_flag()
        print("cleared" if cleared else "no flag to clear")
        return
    cfg = load_config()
    halted, _ = is_halted()
    if halted:
        logger.critical("[BRAKE_BOOT] halt.flag present — brake will still tick (to log new tier breaches) but main system should be paused")
    if args.loop <= 0:
        summary = await tick(cfg)
        print(json.dumps(summary, indent=2, default=str))
        return
    logger.info(f"loop mode every {args.loop}s — DRY_RUN={cfg.get('DRY_RUN', True)} ACCOUNTS={cfg.get('ACCOUNTS', ['trb'])}")
    while True:
        try:
            summary = await tick(cfg)
            n_actions = len(summary.get("actions", []))
            if n_actions > 0 or summary.get("errors"):
                logger.warning(f"tick summary: {summary.get('skipped','')} actions={n_actions} errors={len(summary.get('errors',[]))}")
            else:
                logger.info(f"tick: {summary.get('skipped','OK')}")
        except Exception as e:
            logger.error(f"tick fatal: {e}")
        await asyncio.sleep(args.loop)


if __name__ == "__main__":
    asyncio.run(main_async())
