#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""local_advisory_generator.py — fast 1-min advisory writer for all 5 crypto accounts.

Bypasses the slow hourly cloud routine for safety + speed.

Inputs:
  ~/binance-agent-handoff/tradeable_refresh.json  (3-min cadence, scored MTF + S/R per account)
  data/opinion_causality_report.json              (reliable_traders[] from Bitget causality study)
  Redis positions:{acct}                          (current open positions, gain%)
  Redis latest_market_data                        (per-symbol MTF fields for held positions outside top-25)
  tradeable_keys.json                             (USDC-preferred allowlist; only emit for keys here)

Outputs (idempotent — only writes if semantic change vs existing local entries):
  ~/binance-agent-handoff/fin_advisories.json
  ~/binance-agent-handoff/ang_advisories.json
  ~/binance-agent-handoff/inf_advisories.json
  ~/binance-agent-handoff/flz_advisories.json
  ~/binance-agent-handoff/men_advisories.json

Conflict avoidance: cloud advisories carry generator='fin-hourly-supervisor'. We never overwrite those
unless they have expired. We only manage entries with generator='local_advisory_generator'.

Cron: */1 * * * *    (~5s wallclock budget per run; hard ceiling 60s)
"""
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone, timedelta
from pathlib import Path

BASE = Path("/Users/niels/Documents/binance")
HANDOFF_REPO = Path.home() / "binance-agent-handoff"
TRADEABLE_REFRESH = HANDOFF_REPO / "tradeable_refresh.json"
CAUSALITY_REPORT = BASE / "data" / "opinion_causality_report.json"
TRADEABLE_KEYS_FILE = BASE / "tradeable_keys.json"
LOG_PATH = Path.home() / "logs" / "local_advisory_generator.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

ACCOUNTS = ("fin", "ang", "inf", "flz", "men")
GENERATOR_NAME = "local_advisory_generator"
SCHEMA_VERSION = 1
TF_ORDER = ("3m", "15m", "1h", "4h", "D")

ENTRY_SIZE_USD = 50.0
ENTRY_EXPIRES_MIN = 30
CLOSE_EXPIRES_MIN = 10
HOLD_EXPIRES_MIN = 5

ENTRY_SCORE_MIN = 75.0
ENTRY_MTF_MIN = 4
ENTRY_NEAR_DIST_MAX_PCT = 1.5

# 2026-04-30 BLEED KILL: hedge-storm of 134 force-hedges/day on inf bled real money
# (8 hedges sitting at -0.5% to -5.59% per hedge_safety_alerts). The previous -0.5%
# threshold + 4-TF gate fired on minor wobbles, opening hedges that themselves bled.
# Trigger now requires SEVERE losers (-3%) + still-against MTF, not normal noise.
# Override at runtime via env: INF_AGENT_HEDGE_DISABLED=1 (full kill) or
# INF_AGENT_LOSER_THRESHOLD=-2.0 (custom).
LOSER_GAIN_THRESHOLD = float(os.environ.get("INF_AGENT_LOSER_THRESHOLD", "-3.0"))
SR_BREAK_GAIN_THRESHOLD = float(os.environ.get("INF_AGENT_SR_BREAK_THRESHOLD", "-1.5"))
INF_AGENT_HEDGE_DISABLED = os.environ.get("INF_AGENT_HEDGE_DISABLED", "0") == "1"
HOLD_MIN_MTF_ALIGN = 3
HOLD_MIN_GAIN_PCT = 0.0

DEADLINE_SEC = 55.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()],
)
log = logging.getLogger("local_advisory")


def _utc_now():
    return datetime.now(timezone.utc)


def _utc_now_iso():
    return _utc_now().isoformat(timespec="seconds")


def _iso_plus(minutes):
    return (_utc_now() + timedelta(minutes=minutes)).isoformat(timespec="seconds")


def _parse_iso(s):
    if not s:
        return None
    try:
        s2 = s.replace("Z", "+00:00") if s.endswith("Z") else s
        return datetime.fromisoformat(s2)
    except Exception:
        return None


def _is_active(adv):
    if not isinstance(adv, dict):
        return False
    exp = _parse_iso(adv.get("expires_at_utc"))
    if exp is None:
        return False
    return _utc_now() < exp


def _redis_client():
    import redis
    return redis.Redis(host="localhost", port=6379, db=0, decode_responses=False)


def _redis_get_json(key):
    import pickle
    try:
        r = _redis_client()
        raw = r.get(key)
    except Exception as e:
        log.warning("redis get %s failed: %s", key, e)
        return None
    if raw is None:
        return None
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        try:
            return pickle.loads(raw)
        except Exception:
            return None


def _load_tradeable_refresh():
    try:
        return json.loads(TRADEABLE_REFRESH.read_text())
    except Exception as e:
        log.error("tradeable_refresh read failed: %s", e)
        return None


def _load_causality():
    try:
        return json.loads(CAUSALITY_REPORT.read_text())
    except Exception:
        return None


def _load_tradeable_keys():
    try:
        return set(json.loads(TRADEABLE_KEYS_FILE.read_text()))
    except Exception as e:
        log.error("tradeable_keys read failed: %s", e)
        return set()


def _account_allowed_keys(all_keys, acct):
    prefix = f"{acct}:"
    return {k[len(prefix):] for k in all_keys if k.startswith(prefix)}


def _positions_for(acct):
    payload = _redis_get_json(f"positions:{acct}")
    if not isinstance(payload, dict):
        return {}
    inner = payload.get("positions") if "positions" in payload else payload
    if not isinstance(inner, dict):
        return {}
    out = {}
    prefix = f"{acct}:"
    for full_key, p in inner.items():
        if not isinstance(p, dict):
            continue
        if full_key.startswith(prefix):
            short_key = full_key[len(prefix):]
        else:
            short_key = full_key
        try:
            qty = float(p.get("positionAmt") or 0)
        except (TypeError, ValueError):
            qty = 0.0
        if abs(qty) <= 0:
            continue
        out[short_key] = p
    return out


def _wt_aligned(fields, tf, side):
    w1 = fields.get(f"wt1_{tf}")
    w2 = fields.get(f"wt2_{tf}")
    if w1 is None or w2 is None:
        return None
    if side == "LONG":
        return w1 > w2
    return w1 < w2


def _wt_alignment_count(fields, side):
    n = 0
    seen = 0
    per_tf = {}
    for tf in TF_ORDER:
        a = _wt_aligned(fields, tf, side)
        per_tf[tf] = a
        if a is None:
            continue
        seen += 1
        if a:
            n += 1
    return n, seen, per_tf


def _build_lookup(refresh_acct):
    lookup = {}
    for c in (refresh_acct.get("candidates") or []):
        lookup[c["key"]] = c
    for fs in (refresh_acct.get("fresh_setups") or []):
        lookup.setdefault(fs["key"], fs)
    return lookup


def _opposite(side):
    return "SHORT" if side == "LONG" else "LONG"


def _mtf_against_count(refresh_lookup, market_data, symbol, held_side):
    opp = _opposite(held_side)
    opp_key = f"{symbol}_{opp}"
    cand = refresh_lookup.get(opp_key)
    if cand is not None:
        return cand.get("mtf_align"), cand.get("mtf_seen"), cand.get("mtf_per_tf") or {}
    fields = market_data.get(symbol)
    if not isinstance(fields, dict):
        return None, None, {}
    n, seen, per_tf = _wt_alignment_count(fields, opp)
    return n, seen, per_tf


def _mtf_with_count(refresh_lookup, market_data, symbol, held_side):
    same_key = f"{symbol}_{held_side}"
    cand = refresh_lookup.get(same_key)
    if cand is not None:
        return cand.get("mtf_align"), cand.get("mtf_seen"), cand.get("mtf_per_tf") or {}
    fields = market_data.get(symbol)
    if not isinstance(fields, dict):
        return None, None, {}
    n, seen, per_tf = _wt_alignment_count(fields, held_side)
    return n, seen, per_tf


def _sr_break_against(market_data, symbol, side, current_price):
    fields = market_data.get(symbol)
    if not isinstance(fields, dict) or not current_price:
        return False, None
    try:
        price = float(current_price)
    except (TypeError, ValueError):
        return False, None
    if side == "SHORT":
        lvl = fields.get("dc_high_4h")
        if lvl is not None and price >= float(lvl):
            return True, "dc_high_4h"
    else:
        lvl = fields.get("dc_low_4h")
        if lvl is not None and price <= float(lvl):
            return True, "dc_low_4h"
    return False, None


def _build_force_hedge(symbol, side, reason, mtf_against_n, mtf_against_seen, mtf_per_tf, gain, level=None):
    """For LOSING positions only. NEVER closes at a loss. Triggers same-symbol hedge instead."""
    return {
        "action": "force_hedge",
        "scope": "position",
        "reason": reason,
        "thesis": {
            "setup": "advisory_loss_hedge",
            "mtf_against": f"{mtf_against_n}/{mtf_against_seen}",
            "mtf_per_tf_against": mtf_per_tf,
            "current_gain_pct": gain,
            "broken_level": level,
        },
        "expires_at_utc": _iso_plus(CLOSE_EXPIRES_MIN),
        "iteration_id": int(time.time() / 60),
        "generator": GENERATOR_NAME,
    }


def _build_force_close_profit(symbol, side, reason, mtf_against_n, mtf_against_seen, mtf_per_tf, gain, level=None):
    """For PROFITABLE positions only. Take profit on technical reversal."""
    return {
        "action": "force_close",
        "scope": "position",
        "reason": reason,
        "thesis": {
            "setup": "advisory_profit_take",
            "mtf_against": f"{mtf_against_n}/{mtf_against_seen}",
            "mtf_per_tf_against": mtf_per_tf,
            "current_gain_pct": gain,
            "broken_level": level,
        },
        "expires_at_utc": _iso_plus(CLOSE_EXPIRES_MIN),
        "iteration_id": int(time.time() / 60),
        "generator": GENERATOR_NAME,
    }


def _build_hold(symbol, side, mtf_with_n, mtf_with_seen, gain):
    return {
        "action": "hold",
        "scope": "position",
        "reason": "MTF_SUPPORTIVE_RIDE_WINNER",
        "thesis": {
            "setup": "winner_ride",
            "mtf_with": f"{mtf_with_n}/{mtf_with_seen}",
            "current_gain_pct": gain,
        },
        "expires_at_utc": _iso_plus(HOLD_EXPIRES_MIN),
        "iteration_id": int(time.time() / 60),
        "generator": GENERATOR_NAME,
    }


def _build_force_open(fs, causality_bonus):
    thesis = {
        "setup": "MTF+SR_FRESH_SETUP",
        "score": fs.get("score"),
        "mtf_align": f"{fs.get('mtf_align')}/{fs.get('mtf_seen')}",
        "mtf_per_tf": fs.get("mtf_per_tf"),
        "near_level": fs.get("near_level"),
        "distance_pct": fs.get("distance_pct"),
        "supportive": fs.get("supportive"),
        "invalidation": fs.get("near_level"),
        "expected_hold_bars": 12,
    }
    if causality_bonus:
        thesis["causality_bonus"] = causality_bonus
    return {
        "action": "force_open",
        "scope": "position",
        "size_override_usd": ENTRY_SIZE_USD,
        "reason": "MTF_SR_FRESH_SETUP",
        "thesis": thesis,
        "expires_at_utc": _iso_plus(ENTRY_EXPIRES_MIN),
        "iteration_id": int(time.time() / 60),
        "generator": GENERATOR_NAME,
    }


def _semantic_eq(a, b):
    if not isinstance(a, dict) or not isinstance(b, dict):
        return False
    keys = ("action", "scope", "reason", "size_override_usd")
    for k in keys:
        if a.get(k) != b.get(k):
            return False
    th_a = a.get("thesis") or {}
    th_b = b.get("thesis") or {}
    for k in ("setup", "mtf_align", "mtf_with", "mtf_against", "near_level", "broken_level"):
        if th_a.get(k) != th_b.get(k):
            return False
    return True


def _load_existing(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _merge_and_decide_write(existing, new_advisories):
    if not isinstance(existing, dict):
        existing = {"schema_version": SCHEMA_VERSION, "advisories": {}}
    cur = existing.get("advisories") or {}
    out = {}
    changed = False
    for key, adv in cur.items():
        gen = (adv or {}).get("generator")
        if gen and gen != GENERATOR_NAME:
            if _is_active(adv):
                out[key] = adv
            else:
                changed = True
        elif gen == GENERATOR_NAME:
            if key not in new_advisories:
                changed = True
            else:
                pass
        else:
            if _is_active(adv):
                out[key] = adv
            else:
                changed = True
    for key, adv in new_advisories.items():
        existing_adv = cur.get(key)
        if existing_adv and (existing_adv.get("generator") not in (None, GENERATOR_NAME)) and _is_active(existing_adv):
            continue
        if existing_adv and _semantic_eq(existing_adv, adv) and _is_active(existing_adv):
            out[key] = existing_adv
            continue
        out[key] = adv
        changed = True
    return out, changed


def _write_atomic(path, doc):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w") as f:
        json.dump(doc, f, indent=2, sort_keys=True, default=str)
    os.replace(tmp, path)


def _generate_for_account(acct, refresh, market_data, allowed_keys, causality_traders_active):
    refresh_acct = (refresh.get("accounts") or {}).get(acct) or {}
    candidates = refresh_acct.get("candidates") or []
    fresh_setups = refresh_acct.get("fresh_setups") or []
    refresh_lookup = _build_lookup(refresh_acct)
    positions = _positions_for(acct)
    advisories = {}
    for short_key, p in positions.items():
        if short_key not in allowed_keys:
            continue
        symbol = p.get("symbol")
        side = p.get("position_side")
        if not symbol or side not in ("LONG", "SHORT"):
            continue
        try:
            gain = float(p.get("gain") if p.get("gain") is not None else (p.get("gain_pct") or 0))
        except (TypeError, ValueError):
            gain = 0.0
        try:
            current_price = float(p.get("mark_price") or p.get("current_price") or 0)
        except (TypeError, ValueError):
            current_price = 0.0
        against_n, against_seen, against_per_tf = _mtf_against_count(refresh_lookup, market_data, symbol, side)
        with_n, with_seen, _with_per_tf = _mtf_with_count(refresh_lookup, market_data, symbol, side)
        # BLEED KILL 2026-04-30: kill switch + raised thresholds. Force-hedge only on SEVERE losers
        # to stop the 134/day fee-bleed that ate real $.
        if not INF_AGENT_HEDGE_DISABLED:
            if gain < LOSER_GAIN_THRESHOLD and against_n is not None and against_seen and against_n >= 4 and (with_n or 0) <= 1:
                advisories[short_key] = _build_force_hedge(symbol, side, "HEDGE_MTF_AGAINST_LOSER", against_n, against_seen, against_per_tf, gain)
                continue
            if gain < SR_BREAK_GAIN_THRESHOLD:
                broken, lvl = _sr_break_against(market_data, symbol, side, current_price)
                if broken:
                    advisories[short_key] = _build_force_hedge(symbol, side, "HEDGE_SR_BREAK", against_n, against_seen, against_per_tf, gain, level=lvl)
                    continue
        if gain > 0 and against_n is not None and against_seen and against_n >= 4 and (with_n or 0) <= 1:
            advisories[short_key] = _build_force_close_profit(symbol, side, "PROFIT_TAKE_MTF_REVERSE", against_n, against_seen, against_per_tf, gain)
            continue
        if gain >= HOLD_MIN_GAIN_PCT and with_n is not None and with_n >= HOLD_MIN_MTF_ALIGN:
            advisories[short_key] = _build_hold(symbol, side, with_n, with_seen, gain)
            continue
    held_keys = set(positions.keys())
    for fs in fresh_setups:
        key = fs.get("key")
        if not key or key in held_keys or key in advisories:
            continue
        if key not in allowed_keys:
            continue
        score = fs.get("score") or 0
        mtf_align = fs.get("mtf_align") or 0
        dist = fs.get("distance_pct")
        supportive = fs.get("supportive")
        if score < ENTRY_SCORE_MIN or mtf_align < ENTRY_MTF_MIN:
            continue
        if dist is None or dist > ENTRY_NEAR_DIST_MAX_PCT or not supportive:
            continue
        causality_bonus = None
        if causality_traders_active:
            causality_bonus = "reliable_traders_active_4h_edge"
        advisories[key] = _build_force_open(fs, causality_bonus)
    return advisories


def _causality_active(report):
    if not isinstance(report, dict):
        return False
    verdict = (report.get("verdict") or {})
    rt = verdict.get("reliable_traders") or []
    return len(rt) > 0


def main():
    t0 = time.time()
    refresh = _load_tradeable_refresh()
    if refresh is None:
        log.error("no tradeable_refresh.json; aborting")
        return 2
    causality = _load_causality()
    causality_active = _causality_active(causality)
    market_data = _redis_get_json("latest_market_data") or {}
    all_keys = _load_tradeable_keys()
    if not all_keys:
        log.error("no tradeable_keys.json; aborting")
        return 2
    written = {}
    counts = {}
    for acct in ACCOUNTS:
        if time.time() - t0 > DEADLINE_SEC:
            log.error("deadline exceeded before %s", acct)
            break
        allowed = _account_allowed_keys(all_keys, acct)
        new_advs = _generate_for_account(acct, refresh, market_data, allowed, causality_active)
        path = HANDOFF_REPO / f"{acct}_advisories.json"
        existing = _load_existing(path)
        merged, changed = _merge_and_decide_write(existing, new_advs)
        counts[acct] = {"new_local": len(new_advs), "total_after_merge": len(merged), "changed": changed}
        if not changed:
            written[acct] = "skipped_no_change"
            continue
        doc = {
            "schema_version": SCHEMA_VERSION,
            "generated_at_utc": _utc_now_iso(),
            "generator": GENERATOR_NAME,
            "advisories": merged,
        }
        _write_atomic(path, doc)
        written[acct] = "wrote"
    log.info("done elapsed=%.2fs counts=%s written=%s causality_active=%s", time.time() - t0, counts, written, causality_active)
    return 0


if __name__ == "__main__":
    sys.exit(main())
