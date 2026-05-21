#!/usr/bin/env python3
"""zec_supervisor_agent.py — Sonnet 4.6 daemon supervising flz:ZECUSDC.

USER MANDATE 2026-05-21: monitor live ZECUSDC on flz, adapt strategy overrides,
and close bad entries autonomously (capped 1/hr). Uses the existing
fin_advisory_consumer schema for close authority (ez_manage already polls
~/binance-agent-handoff/flz_advisories.json every 30s).

Per cycle (every ZEC_SUPERVISOR_POLL_INTERVAL_SEC, default 300s):
  1. Gather snapshot: last ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN of /history/flz/ZECUSDC_LONG.jsonl,
     today's /decisions/decisions_flz_<today>.jsonl ZEC entries, last few 15m bars from
     klines_cache_backtest/ZECUSDC_15m.json, current overrides for ZECUSDC.
  2. Call Sonnet 4.6 with cached system prompt + fresh snapshot.
  3. Parse strict-JSON response: {action, close_reason?, overrides_patch?, thesis?}
  4. If action=close: rate-limit 1/hr (ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX);
     write force_close advisory. Loss-cases auto-downgraded to force_hedge by ez_manage.
  5. If action=adapt_overrides: merge patch into active_config_7d.json[ZECUSDC].overrides.
  6. If action=hold: no-op.
  7. Append decision to data/zec_supervisor/log.jsonl.

Safety:
  - All closes route through fin_advisory_consumer → ez_manage execute_now (loss → hedge downgrade).
  - Override patches limited to a whitelist (ZEC_SUPERVISOR_OVERRIDE_WHITELIST below).
  - One-close-per-hour cap. No close authority means the strategy adaption is the dominant lever.
  - Reason tag 'AGENT_AUTONOMOUS_CLOSE' / 'ZEC_SUPERVISOR_CLOSE' is in UNIVERSAL_NOLOSS_GATE_BYPASS_REASONS.

Env: ANTHROPIC_API_KEY required.
"""
from __future__ import annotations
import argparse, json, os, sys, time, traceback
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import config as _cfg_mod

CFG = _cfg_mod.Config()

import shutil, subprocess
# USER MANDATE 2026-05-21: NO API key — use Claude Code CLI subscription auth.
# The `claude` CLI without --bare uses OAuth/keychain (subscription), not API key.
CLAUDE_BIN = shutil.which("claude") or "/Users/niels/.local/bin/claude"
if not Path(CLAUDE_BIN).exists():
    print(f"ERROR: claude CLI not found at {CLAUDE_BIN}", file=sys.stderr)
    sys.exit(2)
MODEL = getattr(CFG, "ZEC_SUPERVISOR_MODEL", "claude-sonnet-4-6")

HIST_PATH = ROOT / "data" / "history" / "flz" / "ZECUSDC_LONG.jsonl"
HIST_SHORT_PATH = ROOT / "data" / "history" / "flz" / "ZECUSDC_SHORT.jsonl"
DEC_DIR = ROOT / "data" / "decisions"
KLINES_PATH = ROOT / "klines_cache" / "ZECUSDC_15m.json"  # live klines, not stale backtest tail
ACTIVE_7D = ROOT / "data" / "hourly_reconfig" / "flz" / "active_config_7d.json"
ADVISORY_PATH = Path.home() / "binance-agent-handoff" / "flz_advisories.json"
LOG_DIR = ROOT / "data" / "zec_supervisor"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "log.jsonl"
CLOSE_LEDGER = LOG_DIR / "close_ledger.jsonl"

# Whitelist of override knobs Sonnet is allowed to patch. Anything outside this set is rejected.
ZEC_SUPERVISOR_OVERRIDE_WHITELIST = {
    "MICRO_SCALP_USDC_MAKER_ENABLED", "MICRO_SCALP_GAIN_THRESHOLD_PCT",
    "DC_BB_D_BREAK_REVERSE_ENABLED",
    "BREAKEVEN_GAIN_EROSION_ENABLED", "BREAKEVEN_GAIN_EROSION_MIN_GAIN",
    "WT_15M_VEL_SLOW_GAIN_BAND_PCT", "WT_15M_VEL_SLOW_GAIN_FLOOR_PCT",
    "WT_VEL_DECEL_RATIO", "R1_NEWBORN_WINDOW_MIN", "R2_TF_LIST",
    "WT_3M_FORCE_OPEN_ENABLED", "RZ_BASELINE_BOUNCE_SHORT_ENABLED",
    "MIN_GAIN", "MIN_GAIN_TO_BUY_AGGRESSIVELY",
    "DUP_GUARD_GAIN_MULTIPLIER",
    "HEDGE_MODE", "OBLIGATORY_HEDGE_ENABLED",
    "WT_EXHAUST_EXIT_ENABLED", "WT_EXHAUST_EXIT_MIN_GAIN_PCT",
}

SYSTEM_PROMPT = """You are the autonomous supervisor for flz:ZECUSDC_LONG on a live crypto futures account.

CONTEXT:
- ZEC has been the best-performing crypto of the year but the system loses trend rides to micro-scalp closes.
- Live strategy has many gates and exits; some fire too eagerly and kill +5-20% upside on partial pullbacks.
- A 5x sizing multiplier is now active on flz:ZECUSDC_LONG (only). Hedging exists if you choose force_hedge instead.

YOUR AUTHORITY:
- action='close': forces a position close (loss-cases auto-downgrade to hedge in the gate). Rate-limited 1/hr.
- action='adapt_overrides': patch ZECUSDC override knobs. Whitelist below.
- action='hold': do nothing.

OVERRIDE KNOB WHITELIST (only these may appear in overrides_patch):
- MICRO_SCALP_USDC_MAKER_ENABLED (bool): closes at any decel past tiny gain. Default True. Often suspect.
- MICRO_SCALP_GAIN_THRESHOLD_PCT (float): raise to require larger gain before scalping.
- DC_BB_D_BREAK_REVERSE_ENABLED (bool): early reverse on D break. Diagnostic shows it leaves +5% avg on table.
- BREAKEVEN_GAIN_EROSION_ENABLED (bool): currently False; legacy.
- WT_15M_VEL_SLOW_GAIN_BAND_PCT (float): R2 exit band.
- WT_15M_VEL_SLOW_GAIN_FLOOR_PCT (float): R2 floor; never close below this.
- WT_VEL_DECEL_RATIO (float): R2 deceleration ratio.
- R1_NEWBORN_WINDOW_MIN (float): newborn DC4 stop window.
- WT_3M_FORCE_OPEN_ENABLED (bool): currently False; always-open on 3m WT signal.
- MIN_GAIN (float): augment gain floor.
- MIN_GAIN_TO_BUY_AGGRESSIVELY (float): aggression floor.
- DUP_GUARD_GAIN_MULTIPLIER (float): dup-open gain gate.
- HEDGE_MODE (bool), OBLIGATORY_HEDGE_ENABLED (bool).
- WT_EXHAUST_EXIT_ENABLED (bool), WT_EXHAUST_EXIT_MIN_GAIN_PCT (float).

OUTPUT FORMAT (STRICT JSON, no markdown, no prose):
{
  "action": "hold" | "close" | "adapt_overrides",
  "close_reason": "...",       // required iff action=close, <120 chars
  "close_action": "force_close" | "force_hedge",  // optional; default force_close
  "overrides_patch": {KNOB: VAL, ...},  // required iff action=adapt_overrides
  "thesis": "..."               // 1-3 sentence explanation, <300 chars
}

DECISION RULES:
1. NEVER recommend close at a loss (current gain%<0). The downgrade-to-hedge handler is your fallback — only use 'close_action':'force_hedge' if losing.
2. PREFER adapt_overrides over close. Closes are rate-limited and expensive (slippage, miss reentry).
3. If position is profitable AND showing signs of major reversal (WT_D flip + DC4 break + sentiment crash): 'close'.
4. If recent history shows the SAME exit reason firing 3+ times on positive moves: 'adapt_overrides' to disable that reason.
5. If no signal: 'hold' with thesis explaining what you're watching.
6. NEVER patch a knob outside the whitelist."""


def _now_utc():
    return datetime.now(timezone.utc)


def _read_jsonl_tail(path: Path, since_ts: float, max_rows: int = 50) -> list[dict]:
    if not path.exists():
        return []
    out = []
    try:
        with path.open() as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                ts_str = e.get("ts") or e.get("timestamp") or ""
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00")).timestamp()
                except Exception:
                    continue
                if ts >= since_ts:
                    out.append(e)
    except Exception as exc:
        print(f"[read_jsonl_tail] {path.name}: {exc}", file=sys.stderr)
    return out[-max_rows:]


def _today_decisions_zec() -> list[dict]:
    day = _now_utc().strftime("%Y%m%d")
    p = DEC_DIR / f"decisions_flz_{day}.jsonl"
    if not p.exists():
        return []
    out = []
    try:
        with p.open() as f:
            for line in f:
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if "ZECUSDC" in (e.get("position_key", "") or ""):
                    out.append(e)
    except Exception as exc:
        print(f"[decisions] {exc}", file=sys.stderr)
    return out[-30:]


def _recent_klines(n: int = 10) -> list[dict]:
    if not KLINES_PATH.exists():
        return []
    try:
        with KLINES_PATH.open() as f:
            data = json.load(f)
        return data[-n:]
    except Exception:
        return []


def _current_overrides() -> dict:
    if not ACTIVE_7D.exists():
        return {}
    try:
        d = json.loads(ACTIVE_7D.read_text())
        z = d.get("ZECUSDC", {})
        return z.get("overrides", {}) or {}
    except Exception:
        return {}


def _build_snapshot() -> dict:
    lookback_sec = int(getattr(CFG, "ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN", 30)) * 60
    since_ts = _now_utc().timestamp() - lookback_sec
    return {
        "ts_utc": _now_utc().isoformat(),
        "history_long": _read_jsonl_tail(HIST_PATH, since_ts),
        "history_short": _read_jsonl_tail(HIST_SHORT_PATH, since_ts),
        "decisions_today": _today_decisions_zec(),
        "recent_15m_bars": _recent_klines(10),
        "current_overrides": _current_overrides(),
        "lookback_min": int(getattr(CFG, "ZEC_SUPERVISOR_HISTORY_LOOKBACK_MIN", 30)),
    }


def _close_count_last_hour() -> int:
    if not CLOSE_LEDGER.exists():
        return 0
    cutoff = _now_utc().timestamp() - 3600
    n = 0
    try:
        with CLOSE_LEDGER.open() as f:
            for line in f:
                try:
                    e = json.loads(line)
                    if e.get("ts", 0) >= cutoff:
                        n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["hold", "close", "adapt_overrides"]},
        "close_reason": {"type": "string", "maxLength": 200},
        "close_action": {"type": "string", "enum": ["force_close", "force_hedge"]},
        "overrides_patch": {"type": "object"},
        "thesis": {"type": "string", "maxLength": 400},
    },
    "required": ["action"],
}


def call_sonnet(snapshot: dict) -> dict:
    """Invoke `claude -p` subprocess (uses subscription auth via OAuth/keychain)."""
    user_msg = (
        "Decide an action for flz:ZECUSDC_LONG based on this snapshot. "
        "Respond with strict JSON matching the schema — nothing else.\n\n"
        f"SNAPSHOT:\n{json.dumps(snapshot, default=str)}"
    )
    try:
        # cwd=/tmp avoids the binance CLAUDE.md auto-load (~32k tokens). --system-prompt
        # replaces the default base prompt entirely. Net per-call: ~few k tokens instead of 40k+.
        proc = subprocess.run(
            [
                CLAUDE_BIN,
                "-p",
                user_msg,
                "--model", MODEL,
                "--system-prompt", SYSTEM_PROMPT,
                "--output-format", "json",
                "--json-schema", json.dumps(DECISION_SCHEMA),
                "--disallowedTools", "Bash,Edit,Write,WebFetch,WebSearch,Agent",
                "--exclude-dynamic-system-prompt-sections",
            ],
            capture_output=True,
            text=True,
            timeout=180,
            cwd="/tmp",
        )
    except subprocess.TimeoutExpired:
        return {"action": "hold", "thesis": "CLI_TIMEOUT_180s"}
    except Exception as e:
        return {"action": "hold", "thesis": f"CLI_LAUNCH_FAIL: {e}"}
    if proc.returncode != 0:
        return {"action": "hold", "thesis": f"CLI_RC={proc.returncode} stderr[:200]={proc.stderr[:200]!r}"}
    raw = proc.stdout.strip()
    # When --json-schema is used, claude routes the response into outer['structured_output']
    # rather than outer['result']. Fall back through both, plus inline markdown JSON.
    try:
        outer = json.loads(raw)
    except Exception:
        outer = {}
    if isinstance(outer, dict):
        so = outer.get("structured_output")
        if isinstance(so, dict) and "action" in so:
            return so
        text = outer.get("result", "") or raw
    else:
        text = raw
    text = (text or "").strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()
    if not text:
        return {"action": "hold", "thesis": f"EMPTY_RESPONSE rc={proc.returncode}"}
    try:
        d = json.loads(text)
        if not isinstance(d, dict) or "action" not in d:
            return {"action": "hold", "thesis": f"PARSE_NO_ACTION: raw[:200]={text[:200]!r}"}
        return d
    except Exception as e:
        return {"action": "hold", "thesis": f"PARSE_FAIL: {e}; raw[:200]={text[:200]!r}"}


def write_close_advisory(close_reason: str, action: str = "force_close") -> bool:
    ADVISORY_PATH.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = {"advisories": {}}
        if ADVISORY_PATH.exists():
            try:
                existing = json.loads(ADVISORY_PATH.read_text())
            except Exception:
                pass
        existing.setdefault("advisories", {})
        existing["advisories"]["ZECUSDC_LONG"] = {
            "action": action,
            "scope": "position",
            "reason": ("ZEC_SUPERVISOR_CLOSE: " + close_reason)[:240],
            "thesis": {"source": "zec_supervisor_agent", "agent": "sonnet-4-6"},
            "expires_at_utc": (_now_utc() + timedelta(seconds=180)).isoformat(),
            "iteration_id": int(_now_utc().timestamp()),
            "generator": "zec_supervisor_agent",
        }
        existing["schema_version"] = 1
        existing["generated_at_utc"] = _now_utc().isoformat()
        tmp = ADVISORY_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(existing, indent=2))
        os.replace(tmp, ADVISORY_PATH)
        with CLOSE_LEDGER.open("a") as f:
            f.write(json.dumps({"ts": _now_utc().timestamp(), "reason": close_reason, "action": action}) + "\n")
        return True
    except Exception as e:
        print(f"[write_close_advisory] FAILED: {e}", file=sys.stderr)
        return False


def apply_overrides_patch(patch: dict) -> tuple[int, list[str]]:
    """Returns (n_applied, rejected_keys)."""
    if not ACTIVE_7D.exists():
        return 0, ["ACTIVE_7D_MISSING"]
    rejected = [k for k in patch if k not in ZEC_SUPERVISOR_OVERRIDE_WHITELIST]
    accepted = {k: v for k, v in patch.items() if k in ZEC_SUPERVISOR_OVERRIDE_WHITELIST}
    if not accepted:
        return 0, rejected
    try:
        active = json.loads(ACTIVE_7D.read_text())
        zec = active.setdefault("ZECUSDC", {})
        ov = zec.setdefault("overrides", {})
        n = 0
        for k, v in accepted.items():
            if ov.get(k) != v:
                ov[k] = v
                n += 1
        zec["updated_at"] = _now_utc().isoformat()
        zec.setdefault("_meta_supervisor", [])
        if isinstance(zec["_meta_supervisor"], list):
            zec["_meta_supervisor"].append({"ts": _now_utc().isoformat(), "applied": accepted, "n": n})
            zec["_meta_supervisor"] = zec["_meta_supervisor"][-20:]
        tmp = ACTIVE_7D.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(active, indent=2))
        os.replace(tmp, ACTIVE_7D)
        return n, rejected
    except Exception as e:
        print(f"[apply_overrides_patch] FAILED: {e}", file=sys.stderr)
        return 0, rejected + ["WRITE_FAILED"]


def log_decision(snapshot_summary: dict, decision: dict, applied: dict | None = None):
    rec = {
        "ts": _now_utc().isoformat(),
        "model": MODEL,
        "snapshot_summary": snapshot_summary,
        "decision": decision,
        "applied": applied or {},
    }
    try:
        with LOG_PATH.open("a") as f:
            f.write(json.dumps(rec, default=str) + "\n")
    except Exception as e:
        print(f"[log] {e}", file=sys.stderr)


def cycle() -> dict:
    if not bool(getattr(CFG, "ZEC_SUPERVISOR_ENABLED", True)):
        return {"action": "disabled"}
    snap = _build_snapshot()
    summary = {
        "n_long_events": len(snap.get("history_long", [])),
        "n_short_events": len(snap.get("history_short", [])),
        "n_decisions_today": len(snap.get("decisions_today", [])),
        "n_overrides": len(snap.get("current_overrides", {})),
        "last_15m_close": (snap.get("recent_15m_bars") or [{}])[-1].get("close"),
    }
    try:
        decision = call_sonnet(snap)
    except Exception as e:
        decision = {"action": "hold", "thesis": f"LLM_ERROR: {e}"}
    applied: dict = {}
    action = decision.get("action", "hold")
    if action == "close":
        cap = int(getattr(CFG, "ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX", 1))
        recent = _close_count_last_hour()
        if recent >= cap:
            applied["close"] = {"skipped": True, "reason": f"rate_limit_{recent}>={cap}/hr"}
        else:
            close_reason = decision.get("close_reason", "no_reason")
            close_action = decision.get("close_action", "force_close")
            if close_action not in ("force_close", "force_hedge"):
                close_action = "force_close"
            ok = write_close_advisory(close_reason, action=close_action)
            applied["close"] = {"written": ok, "advisory_action": close_action, "reason": close_reason[:120]}
    elif action == "adapt_overrides":
        patch = decision.get("overrides_patch", {}) or {}
        if isinstance(patch, dict):
            n, rejected = apply_overrides_patch(patch)
            applied["overrides"] = {"n_applied": n, "rejected": rejected, "patch": patch}
        else:
            applied["overrides"] = {"error": "patch_not_dict"}
    log_decision(summary, decision, applied)
    return {"summary": summary, "decision": decision, "applied": applied}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--daemon", action="store_true")
    args = ap.parse_args()
    if not (args.once or args.daemon):
        ap.error("specify --once or --daemon")
    interval = int(getattr(CFG, "ZEC_SUPERVISOR_POLL_INTERVAL_SEC", 300))
    print(f"[zec_supervisor] start model={MODEL} interval={interval}s cap={getattr(CFG,'ZEC_SUPERVISOR_AUTONOMOUS_CLOSE_PER_HOUR_MAX',1)}/hr")
    while True:
        try:
            t0 = time.time()
            r = cycle()
            elapsed = time.time() - t0
            d = r.get("decision", {})
            ap = r.get("applied", {})
            print(f"[{datetime.now().strftime('%H:%M:%S')}] action={d.get('action')} thesis={(d.get('thesis') or '')[:90]} applied={list(ap.keys())} t={elapsed:.1f}s")
            if args.once:
                return 0
            time.sleep(max(10, interval))
        except KeyboardInterrupt:
            return 0
        except Exception:
            traceback.print_exc()
            time.sleep(60)


if __name__ == "__main__":
    sys.exit(main())
