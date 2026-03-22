#!/opt/anaconda3/envs/binance_env/bin/python
"""Auto-Optimizer Agent — Reads audit reports, analyzes performance, makes code changes.
Runs after each trade_quality_auditor report. Uses Claude Haiku to reason about findings.

Usage:
  python auto_optimizer.py --report audit_reports/audit_20260318.txt
  python auto_optimizer.py --latest   # Find most recent report
"""

import json
import logging
import os
import re
import shutil
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import anthropic
except ImportError:
    anthropic = None
try:
    import openpyxl
except ImportError:
    openpyxl = None

sys.path.insert(0, str(Path(__file__).parent))
try:
    from utils import load_environment_from_gpg
    load_environment_from_gpg(None)
except Exception:
    pass
logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s %(message)s")
logger = logging.getLogger("auto_optimizer")
logs_dir = Path.home() / "logs"
logs_dir.mkdir(parents=True, exist_ok=True)
from logging.handlers import RotatingFileHandler
fh = RotatingFileHandler(str(logs_dir / "auto_optimizer.log"), maxBytes=50 * 1024 * 1024, backupCount=3)
fh.setFormatter(logging.Formatter("[%(asctime)s] %(levelname)s %(message)s"))
logger.addHandler(fh)

BASE_PATH = Path(__file__).parent
BACKTEST_FILE = BASE_PATH / "BACKTEST_CHANGES_100.xlsx"
CONFIG_FILE = BASE_PATH / "config.py"
CHANGELOG = BASE_PATH / "audit_reports" / "OPTIMIZER_CHANGELOG.md"

# Safety limits
MAX_CONFIG_CHANGES_PER_RUN = 5
ALLOWED_CONFIG_PARAMS = [
    "ORDER_CACHE_TTL", "CIRCUIT_BREAKER_COOLDOWN", "START_POSITION_SIZE",
    "MIN_POSITION_SIZE", "MIN_GAIN_TO_BUY_AGGRESSIVELY", "HEDGE_TRIGGER_LOSS_PCT",
    "SCALP_MODE", "ENTRY_VOL_MIN_RATIO", "SHORT_RSI_MIN_1H",
    "LONG_STOCH_CHASE_BLOCK", "ENTRY_ATR_PCT_MIN", "SHORT_ABOVE_SMA20_BONUS",
    "CYCLE_TP_PCT", "TREND_HTF_MIN_BULL", "TREND_HTF_MIN_BEAR",
    "TF_FOCUS_WEIGHT", "TF_ALIGNMENT_MIN_TOTAL", "K3M_CAP", "K3M_FLOOR",
    "GAIN_THRESHOLD_LOW", "LS_RATIO_MIN", "LS_RATIO_MAX",
    "AUGMENTATION_COOLDOWN_SECONDS", "REDUCTION_COOLDOWN_SECONDS",
    "EMA_DIST_LONG_THRESHOLD", "EMA_DIST_SHORT_THRESHOLD",
    "MOM3_LONG_THRESHOLD", "MOM3_SHORT_THRESHOLD",
    "BB_ENTRY_LONG_THRESHOLD", "BB_ENTRY_SHORT_THRESHOLD",
    "SMA200_DIST_LONG_THRESHOLD", "DC_WIDTH_MAX_MULT",
    "HEDGE_OVERSIZE_RATIO", "CLOSE_ZONE_SIZE_MULT",
    "OPTIMAL_HOLD_BARS_3M", "OPTIMAL_HOLD_BARS_15M",
]


def load_previous_changes():
    """Load the optimizer changelog to know what was already changed."""
    if not CHANGELOG.exists():
        return []
    try:
        return CHANGELOG.read_text().split("\n---\n")
    except Exception:
        return []


def load_recent_reports(n=3):
    """Load the last N audit reports for trend analysis."""
    report_dir = BASE_PATH / "audit_reports"
    reports = sorted(report_dir.glob("audit_*.txt"), reverse=True)[:n]
    texts = []
    for rp in reports:
        try:
            texts.append({"path": str(rp.name), "content": rp.read_text()[:8000]})
        except Exception:
            continue
    return texts


def load_config_values():
    """Load current config.py values for the allowed params."""
    values = {}
    try:
        with open(CONFIG_FILE) as f:
            content = f.read()
        for param in ALLOWED_CONFIG_PARAMS:
            match = re.search(rf"{param}\s*[:=]\s*(.+?)(?:\s*#|$)", content, re.MULTILINE)
            if match:
                values[param] = match.group(1).strip()
    except Exception as e:
        logger.error(f"Failed to load config: {e}")
    return values


def load_backtest_rules():
    """Load existing BACKTEST_CHANGES rows."""
    rules = []
    if not openpyxl or not BACKTEST_FILE.exists():
        return rules
    try:
        wb = openpyxl.load_workbook(str(BACKTEST_FILE))
        ws = wb[wb.sheetnames[0]]
        headers = None
        for row in ws.iter_rows(values_only=True):
            if headers is None:
                headers = [str(c).strip() if c else "" for c in row]
                continue
            rule = dict(zip(headers, [c for c in row]))
            if rule.get("Change_ID"):
                rules.append(rule)
    except Exception as e:
        logger.error(f"Failed to load backtest rules: {e}")
    return rules


def ask_claude(prompt, max_tokens=4000):
    """Call Claude via CLI (uses existing Claude Max subscription — no extra API cost).
    Falls back to anthropic SDK with OAuth if available."""
    import subprocess
    try:
        result = subprocess.run(["claude", "-p", prompt, "--model", "haiku", "--output-format", "text", "--max-turns", "1"], capture_output=True, text=True, timeout=90, cwd=str(BASE_PATH))
        if result.returncode == 0 and result.stdout.strip():
            logger.info("Got response from claude CLI")
            return result.stdout.strip()
        logger.warning(f"claude CLI failed (rc={result.returncode}): {result.stderr[:200]}")
    except FileNotFoundError:
        logger.warning("claude CLI not found in PATH")
    except subprocess.TimeoutExpired:
        logger.warning("claude CLI timed out after 120s")
    except Exception as e:
        logger.warning(f"claude CLI error: {e}")
    if anthropic and os.environ.get("ANTHROPIC_API_KEY"):
        try:
            client = anthropic.Anthropic()
            response = client.messages.create(model="claude-haiku-4-5-20251001", max_tokens=max_tokens, messages=[{"role": "user", "content": prompt}])
            return response.content[0].text
        except Exception as e:
            logger.error(f"Anthropic SDK fallback failed: {e}")
    return None


def load_live_pnl():
    """Load current unrealized P&L from position files."""
    pnl_data = {}
    for acct in ["ang", "inf", "men", "fin", "flz"]:
        for side in ["long", "short"]:
            path = BASE_PATH / acct / f"{side}_positions.json"
            if not path.exists():
                continue
            try:
                with open(path) as f:
                    positions = json.load(f)
                for pk, pos in positions.items():
                    gain = pos.get("gain", 0)
                    amt = abs(float(pos.get("positionAmt", 0) or 0))
                    entry = float(pos.get("entry_price", 0) or 0)
                    mark = float(pos.get("mark_price", entry) or entry)
                    notional = amt * mark if mark > 0 else 0
                    if amt > 0 and notional > 0.5:
                        pnl_data[pk] = {"gain": gain, "notional": round(notional, 2), "entry": entry, "mark": mark}
            except Exception:
                continue
    if not pnl_data:
        return "No live position data available"
    winners = sorted([(k, v) for k, v in pnl_data.items() if v["gain"] > 0], key=lambda x: -x[1]["gain"])[:10]
    losers = sorted([(k, v) for k, v in pnl_data.items() if v["gain"] < 0], key=lambda x: x[1]["gain"])[:10]
    total_notional = sum(v["notional"] for v in pnl_data.values())
    total_unrealized = sum(v["notional"] * v["gain"] / 100 for v in pnl_data.values())
    n_pos = len(pnl_data)
    n_winning = sum(1 for v in pnl_data.values() if v["gain"] > 0)
    lines = [f"LIVE POSITIONS: {n_pos} open, {n_winning} winning ({n_winning/n_pos*100:.0f}%), total notional ${total_notional:.0f}, unrealized PnL ${total_unrealized:.2f}"]
    if winners:
        lines.append("TOP WINNERS: " + ", ".join(f"{k.split(':')[1]}={v['gain']:.2f}%/${v['notional']:.0f}" for k, v in winners[:5]))
    if losers:
        lines.append("TOP LOSERS: " + ", ".join(f"{k.split(':')[1]}={v['gain']:.2f}%/${v['notional']:.0f}" for k, v in losers[:5]))
    return "\n".join(lines)


def analyze_report(report_path):
    """Read the audit report and ask Haiku for actionable changes."""
    report_text = Path(report_path).read_text()[:6000]
    recent = load_recent_reports(3)
    previous_changes = load_previous_changes()
    config_values = load_config_values()
    backtest_rules = load_backtest_rules()
    live_pnl = load_live_pnl()
    rules_summary = "\n".join([f"  {r.get('Change_ID','?')}: {r.get('Parameter','?')} = {r.get('New_Value','?')} ({r.get('Status','?')}) — {str(r.get('Logic',''))[:80]}" for r in backtest_rules])
    config_summary = "\n".join([f"  {k} = {v}" for k, v in config_values.items()])
    previous_summary = "\n".join(previous_changes[-5:]) if previous_changes else "No previous optimizer changes."
    prompt = f"""You are an ACTIVE trading system optimizer that makes intraday adjustments. Analyze this report AND live P&L to recommend changes.

## CURRENT AUDIT REPORT
{report_text}

## LIVE P&L (unrealized positions right now)
{live_pnl}

## CURRENT CONFIG VALUES
{config_summary}

## EXISTING BACKTEST RULES (from BACKTEST_CHANGES_100.xlsx)
{rules_summary}

## PREVIOUS OPTIMIZER CHANGES
{previous_summary}

## YOUR MANDATE
You MUST make at least 1 change per run unless everything is perfect. You are the intraday tuner.
Focus areas:
- If execution rate < 50%: loosen entry gates (alignment, thresholds)
- If top losers are large: tighten risk (lower thresholds, tighter holds)
- If winners are being cut short: raise GAIN_THRESHOLD_LOW or OPTIMAL_HOLD_BARS
- If too many blocks on one reason: address that specific blocker
- Compare live P&L to yesterday: is the system improving or degrading?

## RULES
1. Only change these params: {', '.join(ALLOWED_CONFIG_PARAMS)}
2. Maximum {MAX_CONFIG_CHANGES_PER_RUN} changes per run
3. Each change must have clear evidence from report OR live P&L
4. Small adjustments — 10-20% moves, not dramatic rewrites
5. If a previous change made things worse, REVERT it
6. NEVER set stop-loss thresholds — system is STRICT_NO_LOSS

## OUTPUT FORMAT (strict JSON)
Return a JSON object with:
{{
  "conclusions": "2-3 sentences on what the data shows and whether previous changes helped",
  "changes": [
    {{
      "param": "CONFIG_PARAM_NAME",
      "old_value": "current value",
      "new_value": "proposed value",
      "evidence": "specific data from the report that supports this",
      "expected_impact": "what this should improve",
      "risk": "LOW/MEDIUM/HIGH",
      "backtest_change_id": "BACKTEST_CHANGE_NNN or NEW"
    }}
  ],
  "new_backtest_rows": [
    {{
      "change_id": "BACKTEST_CHANGE_NNN",
      "parameter": "param name",
      "old_value": "X",
      "new_value": "Y",
      "logic": "why",
      "evidence": "data",
      "risk": "LOW/MEDIUM/HIGH",
      "revert": "how to revert"
    }}
  ],
  "no_change_reasons": ["reasons why certain params were NOT changed"]
}}

If no changes are warranted, return empty changes array with explanation in conclusions."""

    logger.info("Asking Haiku for analysis...")
    response = ask_claude(prompt)
    if not response:
        return None
    try:
        json_match = re.search(r'\{[\s\S]*\}', response)
        if json_match:
            return json.loads(json_match.group())
    except json.JSONDecodeError as e:
        logger.error(f"Failed to parse Haiku response as JSON: {e}")
        logger.info(f"Raw response: {response[:500]}")
    return None


def apply_config_change(param, old_value, new_value):
    """Apply a single config change to config.py with backup."""
    try:
        with open(CONFIG_FILE) as f:
            content = f.read()
        pattern = rf"({param}\s*[:=]\s*){re.escape(str(old_value))}"
        match = re.search(pattern, content)
        if not match:
            pattern2 = rf"({param}\s*[:=]\s*)\S+"
            match = re.search(pattern2, content)
        if not match:
            logger.error(f"Could not find {param} in config.py")
            return False
        backup_path = BASE_PATH / "backups" / f"before_optimizer_{param}_{datetime.now().strftime('%Y%m%d%H%M')}.py"
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(CONFIG_FILE, backup_path)
        new_content = content[:match.start()] + match.group(1) + str(new_value) + content[match.end():]
        with open(CONFIG_FILE, "w") as f:
            f.write(new_content)
        logger.info(f"Applied: {param} = {old_value} -> {new_value} (backup: {backup_path.name})")
        return True
    except Exception as e:
        logger.error(f"Failed to apply {param} change: {e}")
        return False


def update_backtest_xlsx(analysis):
    """Update BACKTEST_CHANGES_100.xlsx with status notes and new rows."""
    if not openpyxl or not BACKTEST_FILE.exists():
        return
    try:
        wb = openpyxl.load_workbook(str(BACKTEST_FILE))
        ws = wb[wb.sheetnames[0]]
        headers = [str(c.value).strip() if c.value else "" for c in ws[1]]
        status_col = headers.index("Status") + 1 if "Status" in headers else None
        for change in analysis.get("changes", []):
            cid = change.get("backtest_change_id", "")
            if cid and cid != "NEW":
                for row in ws.iter_rows(min_row=2):
                    if str(row[0].value) == cid and status_col:
                        now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                        old_status = str(row[status_col - 1].value or "")
                        row[status_col - 1].value = f"APPLIED_{now_str} (was: {old_status}). {change.get('evidence', '')[:80]}"
                        break
        for new_row in analysis.get("new_backtest_rows", []):
            next_id = new_row.get("change_id", f"AUTO_{datetime.now().strftime('%Y%m%d%H%M')}")
            ws.append([next_id, datetime.now().strftime("%Y-%m-%d"), "auto_optimizer", new_row.get("parameter", ""), "config.py", new_row.get("old_value", ""), new_row.get("new_value", ""), new_row.get("logic", ""), new_row.get("evidence", ""), new_row.get("risk", ""), new_row.get("revert", ""), "APPLIED_BY_OPTIMIZER"])
        backup = BASE_PATH / "backups" / f"BACKTEST_CHANGES_100_{datetime.now().strftime('%Y%m%d%H%M')}.xlsx"
        backup.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(BACKTEST_FILE, backup)
        wb.save(str(BACKTEST_FILE))
        logger.info(f"Updated {BACKTEST_FILE.name} (backup: {backup.name})")
    except Exception as e:
        logger.error(f"Failed to update XLSX: {e}")


def log_to_changelog(analysis, applied_changes):
    """Append to the optimizer changelog."""
    try:
        entry = f"""## {datetime.now().strftime('%Y-%m-%d %H:%M')} — Auto-Optimizer Run

**Conclusions**: {analysis.get('conclusions', 'N/A')}

**Changes Applied ({len(applied_changes)}):**
"""
        for c in applied_changes:
            entry += f"- `{c['param']}`: {c['old_value']} → {c['new_value']} — {c.get('evidence', '')[:100]}\n"
        if not applied_changes:
            entry += "- No changes applied this run\n"
        no_change = analysis.get("no_change_reasons", [])
        if no_change:
            entry += f"\n**Not Changed**: {'; '.join(no_change)}\n"
        entry += "\n---\n"
        with open(CHANGELOG, "a") as f:
            f.write(entry)
    except Exception as e:
        logger.error(f"Failed to write changelog: {e}")


def rule_based_analysis(report_path):
    """Fallback when no Haiku API key: apply PENDING_BACKTEST rules from XLSX."""
    report_text = Path(report_path).read_text()[:8000]
    rules = load_backtest_rules()
    config_values = load_config_values()
    changes = []
    for rule in rules:
        status = str(rule.get("Status", ""))
        if "PENDING" not in status:
            continue
        param = str(rule.get("Parameter", ""))
        if param not in ALLOWED_CONFIG_PARAMS:
            continue
        new_val = rule.get("New_Value", "")
        old_val = config_values.get(param, "")
        cid = str(rule.get("Change_ID", ""))
        if str(old_val).strip() == str(new_val).strip():
            continue
        changes.append({"param": param, "old_value": old_val, "new_value": new_val, "evidence": str(rule.get("Evidence", ""))[:100], "expected_impact": str(rule.get("Logic", ""))[:100], "risk": str(rule.get("Risk", "LOW")).split("—")[0].strip().split(" ")[0], "backtest_change_id": cid})
    total_trades = 0
    m = re.search(r"TOTAL:\s+(\d+)\s+actual trades", report_text)
    if m:
        total_trades = int(m.group(1))
    total_blocks = 0
    m2 = re.search(r"Total blocks:\s+(\d+)", report_text)
    if m2:
        total_blocks = int(m2.group(1))
    return {"conclusions": f"Rule-based analysis (no Haiku API key). {total_trades} actual trades, {total_blocks} blocks. Applying {len(changes)} PENDING_BACKTEST rules from XLSX.", "changes": changes[:MAX_CONFIG_CHANGES_PER_RUN], "new_backtest_rows": [], "no_change_reasons": [f"Haiku unavailable — only applying PENDING rules from XLSX"]}


def run_optimizer(report_path=None):
    """Main optimizer flow."""
    if not report_path:
        report_dir = BASE_PATH / "audit_reports"
        reports = sorted(report_dir.glob("audit_*.txt"), reverse=True)
        if not reports:
            logger.error("No audit reports found")
            return
        report_path = str(reports[0])
    logger.info(f"Running optimizer on: {report_path}")
    analysis = None
    if shutil.which("claude") or (anthropic and os.environ.get("ANTHROPIC_API_KEY")):
        analysis = analyze_report(report_path)
    if not analysis:
        logger.info("Using rule-based fallback (no Haiku API key or API call failed)")
        analysis = rule_based_analysis(report_path)
    if not analysis:
        logger.error("Failed to get analysis")
        return
    logger.info(f"Conclusions: {analysis.get('conclusions', 'N/A')}")
    applied = []
    changes = analysis.get("changes", [])
    if len(changes) > MAX_CONFIG_CHANGES_PER_RUN:
        logger.warning(f"Haiku suggested {len(changes)} changes, capping at {MAX_CONFIG_CHANGES_PER_RUN}")
        changes = changes[:MAX_CONFIG_CHANGES_PER_RUN]
    for change in changes:
        param = change.get("param", "")
        if param not in ALLOWED_CONFIG_PARAMS:
            logger.warning(f"Skipping unauthorized param: {param}")
            continue
        risk = str(change.get("risk", "")).upper()
        if risk == "HIGH":
            logger.warning(f"Skipping HIGH risk change: {param}")
            continue
        old_val = change.get("old_value", "")
        new_val = change.get("new_value", "")
        logger.info(f"Applying: {param} = {old_val} -> {new_val} (risk: {risk})")
        if apply_config_change(param, old_val, new_val):
            applied.append(change)
    update_backtest_xlsx(analysis)
    log_to_changelog(analysis, applied)
    logger.info(f"Optimizer complete: {len(applied)}/{len(changes)} changes applied")
    print(f"\n{'='*60}")
    print(f"  AUTO-OPTIMIZER RESULTS")
    print(f"{'='*60}")
    print(f"\nConclusions: {analysis.get('conclusions', 'N/A')}")
    print(f"\nChanges applied: {len(applied)}")
    for c in applied:
        print(f"  {c['param']}: {c['old_value']} → {c['new_value']}")
    if not applied:
        print("  (none)")
    print(f"\nChangelog: {CHANGELOG}")
    print(f"Report analyzed: {report_path}")


if __name__ == "__main__":
    if "--report" in sys.argv:
        idx = sys.argv.index("--report")
        rp = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else None
        run_optimizer(rp)
    elif "--latest" in sys.argv:
        run_optimizer()
    else:
        run_optimizer()
