#!/usr/bin/env python3
"""trb_size_guardian.py — active size watch on tradier TRB (real money; TRC paper ignored).

USER 2026-10-11: PERF_TIER mults to 4x on stocks must be watched. Polls Tradier
trb positions + open orders every --interval seconds and alerts when any
NON-MANDATORY symbol approaches tier-implied extremes:

  WARN: notional >= 3x START_POSITION_SIZE ($1500 at $500 base)
  CRIT: notional >= 4x START_POSITION_SIZE ($2000 at $500 base)
  HARD: notional >= TRB_MAX_SYMBOL_VALUE ($2500) or side total >= TRB_MAX_LONG/SHORT_VALUE

Thresholds are read live from config_tradier (never hardcoded). Mandatory
holdings (TRADIER_MANDATORY_LONG/SHORT_TRB: META/MSTR/VT...) are user-ordered
and exempt from LEVEL alerts but still counted in side totals. Alerts fire only
on new/escalating state (no off-hours spam). Alert-only by default; --halt
writes the HALT_TRADING_trb sentinel (honored by tradier_manage) on HARD.

Outputs: data/TRB_SIZE_ALERT.jsonl, data/trb_size_guardian_state.json, log file,
macOS notification on CRIT/HARD.

USAGE:
    python3 trb_size_guardian.py --once              # single check + JSON report
    python3 trb_size_guardian.py --interval 60       # daemon (launchd com.niels.trb-size-guardian)
    python3 trb_size_guardian.py --once --halt       # also halt trb on HARD breach
"""
import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ALERT_JSONL = ROOT / "data" / "TRB_SIZE_ALERT.jsonl"
STATE_PATH = ROOT / "data" / "trb_size_guardian_state.json"
SENTINEL_PATH = ROOT / "data" / "HALT_TRADING_trb"
LOG_PATH = ROOT / "data" / "trb_size_guardian.log"

WARN_X = 3.0
CRIT_X = 4.0

logger = logging.getLogger("trb_size_guardian")


def classify(notional, base, symbol_cap=None):
    """Pure: (level, implied_mult) for a dollar notional. level in OK/WARN/CRIT/HARD."""
    try:
        n = float(notional)
        b = float(base)
    except (TypeError, ValueError):
        return "OK", 0.0
    if b <= 0:
        return "OK", 0.0
    m = n / b
    if symbol_cap and n >= float(symbol_cap):
        return "HARD", round(m, 2)
    if m >= CRIT_X:
        return "CRIT", round(m, 2)
    if m >= WARN_X:
        return "WARN", round(m, 2)
    return "OK", round(m, 2)


def _load_cfg():
    import config_tradier as ct
    c = ct.TradierConfig
    mand = set(getattr(c, "TRADIER_MANDATORY_LONG_TRB", []) or []) | set(getattr(c, "TRADIER_MANDATORY_SHORT_TRB", []) or [])
    return {
        "base": float(getattr(c, "TRB_START_POSITION_SIZE", getattr(c, "START_POSITION_SIZE", 500.0))),
        "symbol_cap": float(getattr(c, "TRB_MAX_SYMBOL_VALUE", 2500.0)),
        "max_long": float(getattr(c, "TRB_MAX_LONG_VALUE", 12500.0)),
        "max_short": float(getattr(c, "TRB_MAX_SHORT_VALUE", 12500.0)),
        "mandatory": {str(s).upper() for s in mand},
    }


def _load_state():
    try:
        d = json.loads(STATE_PATH.read_text())
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _save_state(state):
    try:
        STATE_PATH.write_text(json.dumps(state, indent=1))
    except Exception as e:
        logger.warning(f"[STATE] write failed: {e}")


def _notify(title, msg):
    try:
        subprocess.run(["osascript", "-e", f'display notification "{msg[:180]}" with title "{title[:60]}"'], timeout=10, check=False)
    except Exception:
        pass


def _emit_alert(kind, symbol, level, notional, implied_mult, extra=""):
    rec = {"at": datetime.now(timezone.utc).isoformat(), "kind": kind, "symbol": symbol, "level": level, "notional_usd": round(float(notional), 2), "implied_mult": implied_mult, "extra": extra}
    try:
        with open(ALERT_JSONL, "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception as e:
        logger.warning(f"[ALERT] jsonl write failed: {e}")
    logger.critical(f"[TRB_SIZE_{level}] {kind} {symbol}: ${float(notional):,.2f} (x{implied_mult} of ${extra})" if extra else f"[TRB_SIZE_{level}] {kind} {symbol}: ${float(notional):,.2f} x{implied_mult}")
    if level in ("CRIT", "HARD"):
        _notify(f"TRB SIZE {level}", f"{symbol} ${float(notional):,.0f} x{implied_mult}")


def _num(*vals):
    for v in vals:
        try:
            if v is not None:
                return float(v)
        except (TypeError, ValueError):
            continue
    return 0.0


async def poll_once(client, cfg):
    """Returns (findings, totals). findings: list of dicts for non-exempt symbols at WARN+."""
    positions = await client.get_account_positions("trb") or []
    try:
        orders = await client.get_orders("trb") or []
    except Exception as e:
        logger.warning(f"[POLL] orders fetch failed: {e}")
        orders = []
    syms = {}
    for p in positions:
        if not isinstance(p, dict):
            continue
        s = str(p.get("symbol") or "").upper()
        if not s:
            continue
        qty = _num(p.get("quantity"))
        mv = _num(p.get("market_value"), p.get("marketValue"), p.get("market_val"))
        if mv == 0:
            mv = _num(p.get("cost_basis"), p.get("costBasis"))  # already a total, never x qty
        if mv == 0 and qty != 0:
            mv = abs(qty) * _num(p.get("last"), p.get("close"))
        syms[s] = syms.get(s, 0.0) + abs(mv)
    for o in orders:
        if not isinstance(o, dict):
            continue
        if str(o.get("status") or "").lower() not in ("open", "pending", "partially_filled"):
            continue
        s = str(o.get("symbol") or "").upper()
        if not s:
            continue
        q = _num(o.get("quantity"), o.get("qty"))
        px = _num(o.get("price"), o.get("avg_fill_price"), o.get("last"))
        if q and px:
            syms[s] = syms.get(s, 0.0) + abs(q * px)
    findings = []
    for s, n in sorted(syms.items()):
        if s in cfg["mandatory"]:
            continue
        level, m = classify(n, cfg["base"], cfg["symbol_cap"])
        if level != "OK":
            findings.append({"symbol": s, "notional": round(n, 2), "level": level, "implied_mult": m})
    long_tot = sum(n for s, n in syms.items() if n > 0 and s not in cfg["mandatory"])
    return findings, {"symbols": len(syms), "long_total": round(long_tot, 2), "long_total_all": round(sum(n for n in syms.values() if n > 0), 2)}


def check(halt_on_hard=False):
    import tradier_api
    cfg = _load_cfg()

    async def _run():
        client = tradier_api.TradierAPIClient()
        try:
            await client.connect()
            return await poll_once(client, cfg)
        finally:
            try:
                await client.close()
            except Exception:
                pass

    findings, totals = asyncio.run(_run())
    state = _load_state()
    new_state = {}
    order = {"OK": 0, "WARN": 1, "CRIT": 2, "HARD": 3}
    for f in findings:
        s = f["symbol"]
        new_state[s] = f["level"]
        if order[f["level"]] > order.get(state.get(s, "OK"), 0):
            _emit_alert("POSITION", s, f["level"], f["notional"], f["implied_mult"], f"{cfg['base']:.0f} base")
    if totals["long_total"] >= cfg["max_long"]:
        if state.get("_TOTAL_LONG") != "HARD":
            _emit_alert("TOTAL_LONG", "TRB", "HARD", totals["long_total"], round(totals["long_total"] / cfg["base"], 2), f"{cfg['base']:.0f} base cap {cfg['max_long']:.0f}")
        new_state["_TOTAL_LONG"] = "HARD"
    if halt_on_hard and any(f["level"] == "HARD" for f in findings):
        try:
            SENTINEL_PATH.write_text(f"HARD size breach {datetime.now(timezone.utc).isoformat()}\n")
            logger.critical("[HALT] wrote HALT_TRADING_trb")
        except Exception as e:
            logger.warning(f"[HALT] sentinel write failed: {e}")
    _save_state(new_state)
    return {"at": datetime.now(timezone.utc).isoformat(), "cfg": {k: v for k, v in cfg.items() if k != "mandatory"}, "mandatory_exempt": sorted(cfg["mandatory"]), "totals": totals, "findings": findings}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=60.0)
    ap.add_argument("--halt", action="store_true")
    a = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
    if a.once:
        print(json.dumps(check(halt_on_hard=a.halt), indent=1))
        return
    logger.info(f"[START] trb size guardian interval={a.interval}s halt={a.halt}")
    while True:
        try:
            r = check(halt_on_hard=a.halt)
            logger.info(f"[POLL] symbols={r['totals']['symbols']} long_total=${r['totals']['long_total']:,.2f} findings={len(r['findings'])}")
        except Exception as e:
            logger.warning(f"[POLL] failed: {e}")
        time.sleep(max(15.0, a.interval))


if __name__ == "__main__":
    main()
