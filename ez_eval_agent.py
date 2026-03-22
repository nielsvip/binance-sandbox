#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""
LIVE EVALUATION AGENT — Monitors trade outcomes, compares before/after each BACKTEST_CHANGE,
and auto-reverts changes that cause negative performance.

Reads:
  - data/backtest_changes_100.xlsx (change registry with deployed_at timestamps)
  - data/decisions/*.jsonl (trade outcomes by account by day)
  - {account}/long_positions.json, short_positions.json (current positions)

Actions:
  - Compares WR%, PF, avg PnL for each change's before/after window
  - If change is net negative (WR drops 5%+ or PF drops 20%+), reverts the config
  - Logs all evaluations to data/eval_agent/eval_log.jsonl
  - Publishes alerts to Redis channel 'eval_agent_alerts'

Run: python3 ez_eval_agent.py  (continuous, 5-min scan interval)
"""
import asyncio
import json
import logging
import os
import platform
import re
import signal
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s [EVAL] %(message)s', stream=sys.stdout)
logger = logging.getLogger(__name__)

if platform.system() == "Darwin":
    BASE_PATH = Path("/Users/niels/Documents/binance")
else:
    BASE_PATH = Path("/home/niels/binance")

EVAL_DIR = BASE_PATH / "data" / "eval_agent"
EVAL_DIR.mkdir(parents=True, exist_ok=True)
EVAL_LOG = EVAL_DIR / "eval_log.jsonl"
CHANGES_XLSX = BASE_PATH / "data" / "backtest_changes_100.xlsx"
DECISIONS_DIR = BASE_PATH / "data" / "decisions"
CONFIG_PATH = BASE_PATH / "config.py"
CONFIG_TRADIER_PATH = BASE_PATH / "config_tradier.py"

SCAN_INTERVAL = 300  # 5 minutes
MIN_TRADES_FOR_EVAL = 20  # Need 20+ trades to evaluate
REVERT_WR_DROP_THRESHOLD = 5.0  # Revert if WR drops 5%+ after change
REVERT_PF_DROP_THRESHOLD = 0.20  # Revert if PF drops 20%+
REVERT_PNL_THRESHOLD = -50.0  # Revert if cumulative PnL < -$50 after change
LOOKBACK_DAYS = 7  # Compare last 7 days before vs after

shutdown_flag = False

def _handle_signal(sig, frame):
    global shutdown_flag
    shutdown_flag = True
signal.signal(signal.SIGINT, _handle_signal)
signal.signal(signal.SIGTERM, _handle_signal)


# ═══ CHANGE REGISTRY ═══════════════════════════════════════════════════════

# Maps BACKTEST_CHANGE IDs to their config attribute names and revert values
CHANGE_CONFIG_MAP = {
    # Crypto (config.py)
    "BACKTEST_CHANGE_111": {"file": "config.py", "attr": "RSI_ENTRY_GATE_ENABLED", "revert_value": "False", "current_value": "True"},
    "BACKTEST_CHANGE_112": {"file": "config.py", "attr": "GAIN_THRESHOLD_LOW", "revert_value": "0.15", "current_value": "1.0"},
    "BACKTEST_CHANGE_113": {"file": "config.py", "attr": "STOP_MAJOR_LOSS_BLOCK_ENABLED", "revert_value": "False", "current_value": "True"},
    "BACKTEST_CHANGE_114": {"file": "config.py", "attr": "IMMEDIATE_WRONG_WAY_ENABLED", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_115": {"file": "config.py", "attr": "FAST_RISER_DOUBLE_ENABLED", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_116": {"file": "config.py", "attr": "AUGMENT_PYRAMID_ENABLED", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_117": {"file": "config.py", "attr": "HEDGE_MOMENTUM_GATE", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_118": {"file": "config.py", "attr": "HEDGE_MAX_RATIO", "revert_value": "0.5", "current_value": "0.25"},
    "BACKTEST_CHANGE_119": {"file": "config.py", "attr": "HEDGE_OVERSIZE_RATIO", "revert_value": "1.0", "current_value": "0.25"},
    "BACKTEST_CHANGE_120": {"file": "config.py", "attr": "HEDGE_SAME_SYMBOL_ENABLED", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_121": {"file": "ez_manage.py", "attr": "_ratio_mult", "revert_value": "2.0", "current_value": "4.0"},
    # Tradier (config_tradier.py)
    "BACKTEST_CHANGE_T55": {"file": "config_tradier.py", "attr": "RSI_ENTRY_PERIOD_TRADIER", "revert_value": "2", "current_value": "10"},
    "BACKTEST_CHANGE_T56": {"file": "config_tradier.py", "attr": "RSI_EXIT_LONG_TRADIER", "revert_value": "70.0", "current_value": "85.0"},
    "BACKTEST_CHANGE_T57": {"file": "config_tradier.py", "attr": "SMA_FILTER_PERIOD_TRADIER", "revert_value": "200", "current_value": "100"},
    "BACKTEST_CHANGE_T58": {"file": "config_tradier.py", "attr": "ATR_TRAIL_2X_EXIT_ENABLED", "revert_value": "True", "current_value": "False"},
    "BACKTEST_CHANGE_T61": {"file": "config_tradier.py", "attr": "RATIO_MULTIPLIER_TRADIER", "revert_value": "2.0", "current_value": "3.5"},
}


def load_changes() -> pd.DataFrame:
    if not CHANGES_XLSX.exists():
        return pd.DataFrame()
    return pd.read_excel(CHANGES_XLSX)


def save_changes(df: pd.DataFrame):
    df.to_excel(CHANGES_XLSX, index=False)


# ═══ TRADE DATA LOADING ════════════════════════════════════════════════════

def load_decisions(start_date: datetime, end_date: datetime, accounts=None) -> List[dict]:
    """Load all trade decisions in date range."""
    if accounts is None:
        accounts = ['ang', 'inf', 'flz', 'men', 'fin']
    trades = []
    current = start_date
    while current <= end_date:
        date_str = current.strftime('%Y%m%d')
        for acct in accounts:
            path = DECISIONS_DIR / f"decisions_{acct}_{date_str}.jsonl"
            if not path.exists():
                continue
            try:
                for line in open(path):
                    try:
                        d = json.loads(line.strip())
                        d['_account'] = acct
                        d['_date'] = date_str
                        ts = d.get('timestamp', d.get('ts', ''))
                        if isinstance(ts, str) and 'T' in ts:
                            d['_ts'] = datetime.fromisoformat(ts.replace('Z', '+00:00'))
                        elif isinstance(ts, (int, float)):
                            d['_ts'] = datetime.fromtimestamp(ts, tz=timezone.utc)
                        else:
                            d['_ts'] = datetime(current.year, current.month, current.day, tzinfo=timezone.utc)
                        trades.append(d)
                    except Exception:
                        pass
            except Exception:
                pass
        current += timedelta(days=1)
    return trades


def compute_metrics(trades: List[dict]) -> dict:
    """Compute performance metrics from trade list."""
    if not trades:
        return {"n_trades": 0, "win_rate": 0, "profit_factor": 0, "total_pnl": 0, "avg_gain": 0}
    gains = []
    for t in trades:
        g = t.get('gain', t.get('pnl', t.get('realized_pnl', None)))
        if g is not None:
            try:
                gains.append(float(g))
                continue
            except (TypeError, ValueError):
                pass
        # Extract gain from reason string (e.g., "GAIN0.4", "gain=1.23", "_gain1.78%_")
        reason = str(t.get('reason', ''))
        gain_match = re.search(r'[Gg]ain[=_]?(-?\d+\.?\d*)', reason)
        if gain_match:
            try:
                gains.append(float(gain_match.group(1)))
                continue
            except (TypeError, ValueError):
                pass
        # Extract from snapshot
        snap = t.get('snapshot', {})
        if isinstance(snap, dict):
            sg = snap.get('gain', snap.get('pnl_pct', None))
            if sg is not None:
                try:
                    gains.append(float(sg))
                except (TypeError, ValueError):
                    pass
    if not gains:
        # Fall back to action-based analysis: count hedge churn as negative indicator
        actions = [t.get('action', '') for t in trades]
        reasons = [str(t.get('reason', '')) for t in trades]
        n_hedge = sum(1 for r in reasons if 'HEDGE' in r)
        n_reduce = sum(1 for a in actions if a in ('REDUCE', 'QUICK_CLOSE', 'CLOSE'))
        n_open = sum(1 for a in actions if a in ('OPEN', 'QUICK_OPEN', 'AUGMENT'))
        # Estimate WR: trades with positive gain in reason are wins
        wins_from_reason = sum(1 for r in reasons if re.search(r'[Gg]ain[=_]?(\d+\.?\d*)', r) and float(re.search(r'[Gg]ain[=_]?(\d+\.?\d*)', r).group(1)) > 0)
        total_with_gain = sum(1 for r in reasons if re.search(r'[Gg]ain[=_]?(-?\d+\.?\d*)', r))
        est_wr = (wins_from_reason / total_with_gain * 100) if total_with_gain > 0 else 0
        return {"n_trades": len(trades), "win_rate": round(est_wr, 1), "profit_factor": 0, "total_pnl": 0, "avg_gain": 0, "n_reduce": n_reduce, "n_open": n_open, "n_hedge": n_hedge, "hedge_pct": round(n_hedge / len(trades) * 100, 1) if trades else 0}
    wins = sum(1 for g in gains if g > 0)
    losses = sum(1 for g in gains if g <= 0)
    wr = wins / len(gains) * 100 if gains else 0
    gross_profit = sum(g for g in gains if g > 0)
    gross_loss = abs(sum(g for g in gains if g < 0))
    pf = gross_profit / gross_loss if gross_loss > 0 else 999
    total_pnl = sum(gains)
    avg_gain = sum(gains) / len(gains) if gains else 0
    return {"n_trades": len(gains), "win_rate": round(wr, 1), "profit_factor": round(pf, 3), "total_pnl": round(total_pnl, 2), "avg_gain": round(avg_gain, 4)}


# ═══ PATTERN DETECTION ═════════════════════════════════════════════════════

def detect_hedge_churn(trades: List[dict], window_minutes: int = 60) -> dict:
    """Detect excessive hedge open/close cycles."""
    hedge_actions = [t for t in trades if 'HEDGE' in str(t.get('reason', ''))]
    if len(hedge_actions) < 5:
        return {"churn": False, "count": len(hedge_actions)}
    # Count open/close pairs in rolling windows
    opens = [t for t in hedge_actions if t.get('action') in ('OPEN', 'QUICK_OPEN')]
    closes = [t for t in hedge_actions if t.get('action') in ('CLOSE', 'QUICK_CLOSE')]
    churn_ratio = min(len(opens), len(closes)) / max(len(opens), len(closes), 1)
    return {"churn": churn_ratio > 0.5 and len(hedge_actions) > 10, "count": len(hedge_actions), "opens": len(opens), "closes": len(closes), "ratio": round(churn_ratio, 2)}


def detect_rapid_loss(trades: List[dict], threshold_pct: float = -5.0) -> dict:
    """Detect rapid cumulative loss."""
    gains = [float(t.get('gain', 0) or 0) for t in trades if t.get('gain') is not None]
    if not gains:
        return {"rapid_loss": False, "cumulative": 0}
    cumulative = sum(gains)
    # Check rolling 1-hour windows
    worst_window = 0
    window_size = min(20, len(gains))
    for i in range(len(gains) - window_size + 1):
        w = sum(gains[i:i + window_size])
        worst_window = min(worst_window, w)
    return {"rapid_loss": worst_window < threshold_pct, "cumulative": round(cumulative, 2), "worst_window": round(worst_window, 2)}


def detect_entry_quality_drop(trades: List[dict]) -> dict:
    """Detect if recent entries are worse than historical."""
    if len(trades) < 20:
        return {"quality_drop": False}
    half = len(trades) // 2
    first_half = [float(t.get('gain', 0) or 0) for t in trades[:half] if t.get('gain') is not None]
    second_half = [float(t.get('gain', 0) or 0) for t in trades[half:] if t.get('gain') is not None]
    if not first_half or not second_half:
        return {"quality_drop": False}
    avg_first = sum(first_half) / len(first_half)
    avg_second = sum(second_half) / len(second_half)
    wr_first = sum(1 for g in first_half if g > 0) / len(first_half) * 100
    wr_second = sum(1 for g in second_half if g > 0) / len(second_half) * 100
    return {"quality_drop": wr_second < wr_first - 10 or avg_second < avg_first - 0.5, "wr_first": round(wr_first, 1), "wr_second": round(wr_second, 1), "avg_first": round(avg_first, 3), "avg_second": round(avg_second, 3)}


# ═══ REVERT ENGINE ═════════════════════════════════════════════════════════

def revert_config_value(change_id: str, config_map: dict) -> bool:
    """Revert a config value by modifying the config file."""
    if change_id not in config_map:
        logger.warning(f"[REVERT] {change_id}: No config mapping found. Manual revert needed.")
        return False
    info = config_map[change_id]
    file_path = BASE_PATH / info["file"]
    attr = info["attr"]
    revert_value = info["revert_value"]
    current_value = info["current_value"]
    if not file_path.exists():
        logger.error(f"[REVERT] {change_id}: File {file_path} not found")
        return False
    try:
        content = file_path.read_text()
        # Find the line with this attribute and current value
        # Pattern: attr_name: type = current_value  or  attr_name = current_value
        pattern = re.compile(rf'(\s+{re.escape(attr)}[^=]*=\s*){re.escape(current_value)}')
        if not pattern.search(content):
            # Try without type annotation
            pattern = re.compile(rf'(\s+{re.escape(attr)}\s*=\s*){re.escape(current_value)}')
        if not pattern.search(content):
            logger.warning(f"[REVERT] {change_id}: Could not find '{attr} = {current_value}' in {info['file']}")
            return False
        new_content = pattern.sub(rf'\g<1>{revert_value}', content, count=1)
        if new_content == content:
            logger.warning(f"[REVERT] {change_id}: No change after substitution")
            return False
        file_path.write_text(new_content)
        logger.critical(f"[REVERTED] {change_id}: {attr} reverted from {current_value} → {revert_value} in {info['file']}")
        return True
    except Exception as e:
        logger.error(f"[REVERT] {change_id}: Error: {e}")
        return False


# ═══ EVALUATION AGENT ═════════════════════════════════════════════════════

class EvalAgent:
    def __init__(self):
        self.last_eval_time = {}  # {change_id: timestamp}
        self.eval_results = {}  # {change_id: {before: metrics, after: metrics, verdict: str}}
        self.alerts = []
        self.stats = {"scans": 0, "evaluations": 0, "reverts": 0, "alerts": 0}

    def log_eval(self, entry: dict):
        with open(EVAL_LOG, 'a') as f:
            entry['timestamp'] = datetime.now(timezone.utc).isoformat()
            f.write(json.dumps(entry, default=str) + '\n')

    def publish_alert(self, message: str, severity: str = "WARNING"):
        self.alerts.append({"time": datetime.now(timezone.utc).isoformat(), "severity": severity, "message": message})
        self.stats["alerts"] += 1
        logger.warning(f"[ALERT:{severity}] {message}")
        try:
            import redis
            r = redis.Redis(host='127.0.0.1', port=6381 if platform.system() == "Darwin" else 6379, db=0, decode_responses=True)
            r.publish('eval_agent_alerts', json.dumps({"severity": severity, "message": message, "time": datetime.now(timezone.utc).isoformat()}))
        except Exception:
            pass

    def evaluate_change(self, change_id: str, deployed_at: datetime, now: datetime) -> dict:
        """Compare performance before vs after a change was deployed."""
        # Window: LOOKBACK_DAYS before deployed_at vs deployed_at to now
        before_start = deployed_at - timedelta(days=LOOKBACK_DAYS)
        before_trades = load_decisions(before_start, deployed_at - timedelta(seconds=1))
        after_trades = load_decisions(deployed_at, now)
        before_metrics = compute_metrics(before_trades)
        after_metrics = compute_metrics(after_trades)
        # Determine verdict
        verdict = "INSUFFICIENT_DATA"
        if before_metrics["n_trades"] >= MIN_TRADES_FOR_EVAL and after_metrics["n_trades"] >= MIN_TRADES_FOR_EVAL:
            wr_delta = after_metrics["win_rate"] - before_metrics["win_rate"]
            pf_delta = after_metrics["profit_factor"] - before_metrics["profit_factor"]
            pnl_after = after_metrics["total_pnl"]
            if wr_delta < -REVERT_WR_DROP_THRESHOLD:
                verdict = "NEGATIVE_WR_DROP"
            elif before_metrics["profit_factor"] > 0 and pf_delta / before_metrics["profit_factor"] < -REVERT_PF_DROP_THRESHOLD:
                verdict = "NEGATIVE_PF_DROP"
            elif pnl_after < REVERT_PNL_THRESHOLD:
                verdict = "NEGATIVE_PNL"
            elif wr_delta >= 0 and pf_delta >= 0:
                verdict = "POSITIVE"
            else:
                verdict = "NEUTRAL"
        elif after_metrics["n_trades"] < MIN_TRADES_FOR_EVAL:
            verdict = "INSUFFICIENT_AFTER_DATA"
        result = {"change_id": change_id, "deployed_at": deployed_at.isoformat(), "before": before_metrics, "after": after_metrics, "verdict": verdict}
        self.eval_results[change_id] = result
        self.stats["evaluations"] += 1
        return result

    def scan(self):
        """Main scan: evaluate all active changes and detect problems."""
        self.stats["scans"] += 1
        now = datetime.now(timezone.utc)
        # Load change registry
        changes_df = load_changes()
        if changes_df.empty:
            return
        # Evaluate each active change
        for _, row in changes_df.iterrows():
            if shutdown_flag:
                break
            change_id = str(row.get('ID', ''))
            status = str(row.get('status', 'active'))
            if status != 'active':
                continue
            deployed_str = str(row.get('deployed_at', ''))
            if not deployed_str or deployed_str == 'None':
                continue
            try:
                deployed_at = datetime.fromisoformat(deployed_str).replace(tzinfo=timezone.utc)
            except Exception:
                continue
            # Skip if evaluated recently (every 30 min per change)
            last_eval = self.last_eval_time.get(change_id, 0)
            if time.time() - last_eval < 1800:
                continue
            result = self.evaluate_change(change_id, deployed_at, now)
            self.last_eval_time[change_id] = time.time()
            self.log_eval(result)
            # Handle verdicts
            if result["verdict"].startswith("NEGATIVE"):
                self.publish_alert(f"{change_id} is NEGATIVE: {result['verdict']}. Before: WR={result['before']['win_rate']}% PF={result['before']['profit_factor']}. After: WR={result['after']['win_rate']}% PF={result['after']['profit_factor']}. PnL={result['after']['total_pnl']}", "CRITICAL")
                # Auto-revert
                reverted = revert_config_value(change_id, CHANGE_CONFIG_MAP)
                if reverted:
                    self.stats["reverts"] += 1
                    # Update xlsx status
                    changes_df.loc[changes_df['ID'] == change_id, 'status'] = 'REVERTED'
                    save_changes(changes_df)
                    self.publish_alert(f"{change_id} AUTO-REVERTED. Restart services to apply.", "CRITICAL")
            elif result["verdict"] == "POSITIVE":
                logger.info(f"[EVAL] {change_id}: POSITIVE — WR {result['before']['win_rate']}% → {result['after']['win_rate']}%, PF {result['before']['profit_factor']} → {result['after']['profit_factor']}")
        # Pattern detection on recent trades (last 2 hours)
        recent_start = now - timedelta(hours=2)
        recent_trades = load_decisions(recent_start, now)
        if recent_trades:
            # Hedge churn detection
            churn = detect_hedge_churn(recent_trades)
            if churn["churn"]:
                self.publish_alert(f"HEDGE CHURN detected: {churn['count']} hedge actions ({churn['opens']} opens, {churn['closes']} closes) in 2h. Ratio: {churn['ratio']}", "WARNING")
            # Rapid loss detection
            loss = detect_rapid_loss(recent_trades)
            if loss["rapid_loss"]:
                self.publish_alert(f"RAPID LOSS: cumulative {loss['cumulative']}%, worst 20-trade window: {loss['worst_window']}%", "CRITICAL")
            # Entry quality drop
            quality = detect_entry_quality_drop(recent_trades)
            if quality["quality_drop"]:
                self.publish_alert(f"ENTRY QUALITY DROP: WR {quality['wr_first']}% → {quality['wr_second']}%, avg gain {quality['avg_first']} → {quality['avg_second']}", "WARNING")

    def print_status(self):
        logger.info(f"[STATUS] Scans={self.stats['scans']} Evals={self.stats['evaluations']} Reverts={self.stats['reverts']} Alerts={self.stats['alerts']}")
        for cid, result in sorted(self.eval_results.items()):
            v = result['verdict']
            b = result['before']
            a = result['after']
            marker = "!!!" if v.startswith("NEGATIVE") else ("OK" if v == "POSITIVE" else "...")
            logger.info(f"  {marker} {cid}: {v} | Before: {b['n_trades']}t WR={b['win_rate']}% PF={b['profit_factor']} | After: {a['n_trades']}t WR={a['win_rate']}% PF={a['profit_factor']}")


async def main():
    logger.info("Evaluation Agent starting...")
    logger.info(f"  Changes file: {CHANGES_XLSX}")
    logger.info(f"  Decisions dir: {DECISIONS_DIR}")
    logger.info(f"  Scan interval: {SCAN_INTERVAL}s")
    logger.info(f"  Revert thresholds: WR drop>{REVERT_WR_DROP_THRESHOLD}%, PF drop>{REVERT_PF_DROP_THRESHOLD*100}%, PnL<${REVERT_PNL_THRESHOLD}")
    agent = EvalAgent()
    scan_count = 0
    while not shutdown_flag:
        try:
            agent.scan()
            scan_count += 1
            if scan_count % 6 == 1:  # Every 30 min
                agent.print_status()
        except Exception as e:
            logger.error(f"Scan error: {e}", exc_info=True)
        await asyncio.sleep(SCAN_INTERVAL)
    agent.print_status()
    logger.info("Evaluation Agent stopped.")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Interrupted.")
