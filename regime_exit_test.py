#!/usr/bin/env python3
"""
Exit Rule A/B Tester — 80 simultaneous paper portfolios testing different exit rules.

ENTRY: same for all — breakout (dc_position >= 0.85 on 2+ of 3m/15m/1h, k3m > 30, k3m rising)
EXIT: different per test — combinations of:
  - Price action: lower_high_1m, lower_high_3m, lower_low_1m, lower_low_3m, LH+LL_1m, LH+LL_3m
  - K threshold: k_15m > {80,85,90,95}, k_1h > {80,85,90,95}
  - With/without WT velocity negative check

Runs against live Redis data. Reports winner every 30 minutes.

Usage:
    python3 regime_exit_test.py          # Run 24h test
    python3 regime_exit_test.py --report # Show current results
"""
import argparse
import json
import logging
import math
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from itertools import product
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = BASE_PATH / "data" / "exit_tests"
DATA_DIR.mkdir(parents=True, exist_ok=True)
RESULTS_FILE = DATA_DIR / "results.json"
TRADES_FILE = DATA_DIR / "trades.jsonl"

logging.basicConfig(level=logging.INFO, format="%(asctime)s [EXIT_TEST] %(message)s")
logger = logging.getLogger("exit_test")


def get_redis():
    import redis
    return redis.Redis(host="localhost", port=config.REDIS_PORT, db=0)


def _sf(v, d=0.0):
    try:
        f = float(v or d)
        return f if math.isfinite(f) else d
    except (ValueError, TypeError):
        return d


# ═══════════════════════════════════════════════════════════════════════
# EXIT RULES — each is a function(pos, metrics) → reason or None
# ═══════════════════════════════════════════════════════════════════════

def make_exit_rule(pa_type: str, k_field: str, k_thresh: float, require_wt_vel: bool):
    """Factory: create an exit rule function from parameters."""
    def rule(pos, m):
        # Check K threshold first — only exit when overextended
        k_val = _sf(m.get(k_field), 50)
        is_long = pos["side"] == "LONG"
        if is_long and k_val < k_thresh:
            return None  # Not overextended enough
        if not is_long and k_val > (100 - k_thresh):
            return None
        # WT velocity check (optional)
        if require_wt_vel:
            wt_vel = _sf(m.get("wt_velocity_3m"), 0)
            if is_long and wt_vel > -1.0:
                return None  # Velocity not confirming decline
            if not is_long and wt_vel < 1.0:
                return None
        # Price action check
        price = _sf(m.get("current_price"))
        if price <= 0:
            return None
        if pa_type == "LH_1m":
            high_1m = _sf(m.get("high_1m", m.get("high_3m")))  # 1m high if available
            high_1m_prev = _sf(m.get("high_1m_prev", m.get("high_3m_prev")))
            if high_1m <= 0 or high_1m_prev <= 0:
                return None
            if is_long and high_1m < high_1m_prev:
                return f"LH_1m(h={high_1m:.6g}<hp={high_1m_prev:.6g})"
            if not is_long and _sf(m.get("low_1m", m.get("low_3m"))) > _sf(m.get("low_1m_prev", m.get("low_3m_prev"))):
                return f"HL_1m"
        elif pa_type == "LH_3m":
            high_3m = _sf(m.get("high_3m"))
            high_3m_prev = _sf(m.get("high_3m_prev"))
            if high_3m <= 0 or high_3m_prev <= 0:
                return None
            if is_long and high_3m < high_3m_prev:
                return f"LH_3m(h={high_3m:.6g}<hp={high_3m_prev:.6g})"
            if not is_long and _sf(m.get("low_3m")) > _sf(m.get("low_3m_prev")):
                return f"HL_3m"
        elif pa_type == "LL_1m":
            low_1m = _sf(m.get("low_1m", m.get("low_3m")))
            low_1m_prev = _sf(m.get("low_1m_prev", m.get("low_3m_prev")))
            if low_1m <= 0 or low_1m_prev <= 0:
                return None
            if is_long and low_1m < low_1m_prev:
                return f"LL_1m(l={low_1m:.6g}<lp={low_1m_prev:.6g})"
            if not is_long and _sf(m.get("high_1m", m.get("high_3m"))) > _sf(m.get("high_1m_prev", m.get("high_3m_prev"))):
                return f"HH_1m"
        elif pa_type == "LL_3m":
            low_3m = _sf(m.get("low_3m"))
            low_3m_prev = _sf(m.get("low_3m_prev"))
            if low_3m <= 0 or low_3m_prev <= 0:
                return None
            if is_long and low_3m < low_3m_prev:
                return f"LL_3m(l={low_3m:.6g}<lp={low_3m_prev:.6g})"
            if not is_long and _sf(m.get("high_3m")) > _sf(m.get("high_3m_prev")):
                return f"HH_3m"
        elif pa_type == "LH_LL_1m":
            # Both lower high AND lower low on 1m
            h = _sf(m.get("high_1m", m.get("high_3m")))
            hp = _sf(m.get("high_1m_prev", m.get("high_3m_prev")))
            l = _sf(m.get("low_1m", m.get("low_3m")))
            lp = _sf(m.get("low_1m_prev", m.get("low_3m_prev")))
            if h <= 0 or hp <= 0 or l <= 0 or lp <= 0:
                return None
            if is_long and h < hp and l < lp:
                return f"LH_LL_1m"
            if not is_long and h > hp and l > lp:
                return f"HH_HL_1m"
        elif pa_type == "LH_LL_3m":
            h = _sf(m.get("high_3m"))
            hp = _sf(m.get("high_3m_prev"))
            l = _sf(m.get("low_3m"))
            lp = _sf(m.get("low_3m_prev"))
            if h <= 0 or hp <= 0 or l <= 0 or lp <= 0:
                return None
            if is_long and h < hp and l < lp:
                return f"LH_LL_3m"
            if not is_long and h > hp and l > lp:
                return f"HH_HL_3m"
        elif pa_type == "PRICE_BELOW_LOW_3M_PREV":
            low_3m_prev = _sf(m.get("low_3m_prev"))
            high_3m_prev = _sf(m.get("high_3m_prev"))
            if is_long and low_3m_prev > 0 and price < low_3m_prev:
                return f"P<LOW3P({price:.6g}<{low_3m_prev:.6g})"
            if not is_long and high_3m_prev > 0 and price > high_3m_prev:
                return f"P>HI3P({price:.6g}>{high_3m_prev:.6g})"
        elif pa_type == "DC_BREACH_3m":
            dc_low = _sf(m.get("dc_low_3m"))
            dc_high = _sf(m.get("dc_high_3m"))
            if is_long and dc_low > 0 and price < dc_low:
                return f"DC_BREACH(p<dc_low_3m)"
            if not is_long and dc_high > 0 and price > dc_high:
                return f"DC_BREACH(p>dc_high_3m)"
        return None
    rule.__name__ = f"{pa_type}_{k_field}>{k_thresh}{'_wtv' if require_wt_vel else ''}"
    return rule


# ═══════════════════════════════════════════════════════════════════════
# BUILD THE TEST GRID — ~80 combinations
# ═══════════════════════════════════════════════════════════════════════

def build_test_grid():
    pa_types = ["LH_3m", "LL_3m", "LH_LL_3m", "PRICE_BELOW_LOW_3M_PREV", "DC_BREACH_3m",
                "LH_1m", "LL_1m", "LH_LL_1m"]
    k_configs = [
        ("stoch_k_15m", 80), ("stoch_k_15m", 85), ("stoch_k_15m", 90), ("stoch_k_15m", 95),
        ("stoch_k_1h", 80), ("stoch_k_1h", 85), ("stoch_k_1h", 90), ("stoch_k_1h", 95),
    ]
    wt_options = [False, True]
    tests = {}
    for pa, (k_field, k_thresh), wt_vel in product(pa_types, k_configs, wt_options):
        k_short = k_field.replace("stoch_k_", "k")
        name = f"{pa}_{k_short}>{k_thresh}{'_wtv' if wt_vel else ''}"
        tests[name] = {
            "rule": make_exit_rule(pa, k_field, k_thresh, wt_vel),
            "pa": pa, "k_field": k_field, "k_thresh": k_thresh, "wt_vel": wt_vel,
        }
    logger.info(f"Built {len(tests)} test configurations")
    return tests


# ═══════════════════════════════════════════════════════════════════════
# PAPER PORTFOLIO — one per test
# ═══════════════════════════════════════════════════════════════════════

class MiniPortfolio:
    __slots__ = ("name", "positions", "closed_pnl", "wins", "losses", "max_drawdown", "peak_pnl")

    def __init__(self, name):
        self.name = name
        self.positions = {}  # sym → {side, entry_price, entry_time, max_gain}
        self.closed_pnl = 0.0
        self.wins = 0
        self.losses = 0
        self.max_drawdown = 0.0
        self.peak_pnl = 0.0

    def open(self, sym, side, price):
        key = f"{sym}_{side}"
        if key in self.positions:
            return
        self.positions[key] = {"side": side, "entry_price": price, "entry_time": time.time(), "max_gain": 0.0}

    def check_exit(self, sym, side, price, exit_rule, metrics):
        key = f"{sym}_{side}"
        pos = self.positions.get(key)
        if not pos:
            return None
        pnl = ((price - pos["entry_price"]) / pos["entry_price"] * 100) if side == "LONG" else ((pos["entry_price"] - price) / pos["entry_price"] * 100)
        pos["max_gain"] = max(pos["max_gain"], pnl)
        # UNIVERSAL: never go below 0 after being up 0.15%+
        if pos["max_gain"] >= 0.15 and pnl <= 0.02:
            self._close(key, pnl)
            return f"PROTECT(max={pos['max_gain']:.2f}%,now={pnl:.2f}%)"
        # UNIVERSAL: hard stop at -1%
        if pnl < -1.0:
            self._close(key, pnl)
            return f"HARD_STOP({pnl:.2f}%)"
        # TEST-SPECIFIC exit rule
        reason = exit_rule(pos, metrics)
        if reason and pnl > -0.5:  # Only exit on PA signal if not deeply underwater
            self._close(key, pnl)
            return reason
        return None

    def _close(self, key, pnl):
        self.closed_pnl += pnl
        if pnl > 0:
            self.wins += 1
        else:
            self.losses += 1
        self.peak_pnl = max(self.peak_pnl, self.closed_pnl)
        self.max_drawdown = min(self.max_drawdown, self.closed_pnl - self.peak_pnl)
        del self.positions[key]

    def stats(self):
        total = self.wins + self.losses
        wr = (self.wins / total * 100) if total > 0 else 0
        return {
            "pnl": round(self.closed_pnl, 3), "wins": self.wins, "losses": self.losses,
            "total": total, "wr": round(wr, 1), "drawdown": round(self.max_drawdown, 3),
            "open": len(self.positions),
        }


# ═══════════════════════════════════════════════════════════════════════
# MAIN RUNNER
# ═══════════════════════════════════════════════════════════════════════

class ExitTester:
    def __init__(self):
        self.tests = build_test_grid()
        self.portfolios = {name: MiniPortfolio(name) for name in self.tests}
        self._running = True
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, '_running', False))
        signal.signal(signal.SIGINT, lambda *_: setattr(self, '_running', False))
        self.symbols_json = json.load(open(BASE_PATH / "symbols.json"))
        self.cycle = 0
        self.start_time = time.time()

    def _load_all(self, r):
        raw = r.get("latest_market_data")
        return json.loads(raw) if raw else {}

    def _should_enter(self, sym, m):
        """Universal entry: breakout on 2+ LTFs, k3m rising, not overextended."""
        dc_3m = _sf(m.get("dc_position_3m"), 0.5)
        dc_15m = _sf(m.get("dc_position_15m"), 0.5)
        dc_1h = _sf(m.get("dc_position_1h"), 0.5)
        k3m = _sf(m.get("stoch_k_3m", m.get("k_3m")), 50)
        k3m_prev = _sf(m.get("k_3m_prev", m.get("stoch_k_3m_prev")), 50)
        price = _sf(m.get("current_price"))
        if price <= 0:
            return None, None
        # LONG: 2+ TFs at top of channel, k3m rising, not already OB on 2+ TFs
        breakout_up = sum(1 for d in [dc_3m, dc_15m, dc_1h] if d >= 0.85)
        if breakout_up >= 2 and k3m > 30 and k3m < 88 and k3m > k3m_prev:
            return "LONG", price
        # SHORT: 2+ TFs at bottom of channel
        breakdown = sum(1 for d in [dc_3m, dc_15m, dc_1h] if d <= 0.15)
        if breakdown >= 2 and k3m < 70 and k3m > 12 and k3m < k3m_prev:
            return "SHORT", price
        return None, None

    def scan(self, all_m):
        """One scan cycle: check entries + exits across all tests."""
        entered = 0
        exited = 0
        for sym in all_m:
            if sym not in self.symbols_json:
                continue
            m = all_m[sym]
            price = _sf(m.get("current_price"))
            if price <= 0:
                continue
            # Entry (same for all tests)
            side, entry_price = self._should_enter(sym, m)
            if side:
                for name, pf in self.portfolios.items():
                    if len(pf.positions) < 20:  # Max 20 positions per test
                        pf.open(sym, side, entry_price)
                        entered += 1
            # Exits (different per test)
            for name, test in self.tests.items():
                pf = self.portfolios[name]
                for side_check in ["LONG", "SHORT"]:
                    key = f"{sym}_{side_check}"
                    if key in pf.positions:
                        reason = pf.check_exit(sym, side_check, price, test["rule"], m)
                        if reason:
                            exited += 1
        return entered, exited

    def report(self, force=False):
        """Generate and save results sorted by PnL."""
        results = []
        for name, pf in self.portfolios.items():
            s = pf.stats()
            s["name"] = name
            s["config"] = {k: v for k, v in self.tests[name].items() if k != "rule"}
            results.append(s)
        results.sort(key=lambda x: -x["pnl"])
        elapsed_h = (time.time() - self.start_time) / 3600
        summary = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "elapsed_hours": round(elapsed_h, 2),
            "total_tests": len(results),
            "top_10": results[:10],
            "bottom_5": results[-5:],
            "all_results": results,
        }
        with open(RESULTS_FILE, "w") as f:
            json.dump(summary, f, indent=2, default=str)
        # Log top 10
        if force or self.cycle % 60 == 0:
            logger.info(f"{'='*80}")
            logger.info(f"EXIT TEST RESULTS — {elapsed_h:.1f}h elapsed, cycle #{self.cycle}")
            logger.info(f"{'Name':<45} {'PnL%':>7} {'WR':>5} {'W':>3} {'L':>3} {'DD':>7} {'Open':>4}")
            logger.info(f"{'-'*80}")
            for r in results[:15]:
                logger.info(f"{r['name']:<45} {r['pnl']:>+6.2f}% {r['wr']:>4.0f}% {r['wins']:>3} {r['losses']:>3} {r['drawdown']:>+6.2f}% {r['open']:>4}")
            logger.info(f"{'='*80}")
            if results:
                best = results[0]
                worst = results[-1]
                logger.info(f"BEST:  {best['name']} → {best['pnl']:+.2f}% WR={best['wr']}%")
                logger.info(f"WORST: {worst['name']} → {worst['pnl']:+.2f}% WR={worst['wr']}%")

    def run(self):
        logger.info(f"Starting exit rule A/B test with {len(self.tests)} configurations")
        r = get_redis()
        last_report = 0
        while self._running:
            try:
                all_m = self._load_all(r)
                if not all_m:
                    time.sleep(1)
                    continue
                entered, exited = self.scan(all_m)
                self.cycle += 1
                # Report every 30 minutes
                now = time.time()
                if now - last_report > 1800 or self.cycle == 1:
                    self.report(force=True)
                    last_report = now
                elif self.cycle % 300 == 0:
                    self.report()
                time.sleep(3)  # 3s between scans (80 portfolios × 196 symbols = fast enough)
            except KeyboardInterrupt:
                break
            except Exception as e:
                logger.error(f"Error: {e}", exc_info=True)
                time.sleep(5)
        self.report(force=True)
        logger.info("Test complete.")


def show_report():
    if not RESULTS_FILE.exists():
        print("No results yet. Run the test first.")
        return
    with open(RESULTS_FILE) as f:
        data = json.load(f)
    print(f"\nExit Rule A/B Test — {data['elapsed_hours']:.1f}h elapsed")
    print(f"{'='*90}")
    print(f"{'#':>3} {'Name':<45} {'PnL%':>7} {'WR':>5} {'W':>3} {'L':>3} {'DD':>7} {'Open':>4}")
    print(f"{'-'*90}")
    for i, r in enumerate(data.get("all_results", [])[:30], 1):
        marker = " ★" if i <= 3 else ""
        print(f"{i:>3} {r['name']:<45} {r['pnl']:>+6.2f}% {r['wr']:>4.0f}% {r['wins']:>3} {r['losses']:>3} {r['drawdown']:>+6.2f}% {r['open']:>4}{marker}")
    print(f"{'='*90}")
    top = data.get("top_10", [{}])[0]
    if top:
        print(f"\nWINNER: {top.get('name', '?')} → PnL {top.get('pnl', 0):+.2f}% | WR {top.get('wr', 0)}% | {top.get('wins', 0)}W/{top.get('losses', 0)}L")
        cfg = top.get("config", {})
        print(f"  PA: {cfg.get('pa', '?')} | K: {cfg.get('k_field', '?')} > {cfg.get('k_thresh', '?')} | WT vel: {cfg.get('wt_vel', '?')}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", action="store_true", help="Show current results")
    args = parser.parse_args()
    if args.report:
        show_report()
    else:
        tester = ExitTester()
        tester.run()
