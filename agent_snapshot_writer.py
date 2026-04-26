#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""agent_snapshot_writer.py — dehydrate local trading state into JSON for remote routines.

Reads ONLY local Redis + data/decisions/*.jsonl + symbols_*.json. NEVER calls Binance/Tradier APIs.
Writes /Users/niels/binance-agent-handoff/snapshot.json and pushes to GitHub.

Run cadence: every 5 min via cron. Idempotent — skips push if snapshot bytes unchanged.

The snapshot has a curated indicator subset (not the full 125-field dict per symbol) to keep
push payload small and routine prompts focused.
"""
import json
import logging
import os
import pickle
import platform
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path


def _detect_base():
    for cand in (Path("/Users/niels/Documents/binance"), Path("/home/niels/binance"), Path("/home/niels/binance-sandbox")):
        if cand.exists():
            return cand
    return Path(__file__).resolve().parent


BASE = _detect_base()
HANDOFF_REPO = Path.home() / "binance-agent-handoff"
SNAPSHOT_PATH = HANDOFF_REPO / "snapshot.json"
DECISIONS_DIR = BASE / "data" / "decisions"
LOG_PATH = Path.home() / "logs" / "agent_snapshot_writer.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
SCHEMA_VERSION = 1
RECENT_DECISIONS_PER_ACCOUNT = 50
INDICATOR_FIELDS = [
    "current_price",
    "0market_sentiment_score",
    "0sentiment_classification",
    "0ranking_points",
    "0sentiment_rank",
    "wt1_3m", "wt2_3m", "wt_cross_3m",
    "wt1_15m", "wt2_15m", "wt_cross_15m",
    "wt1_1h", "wt2_1h", "wt_cross_1h",
    "wt1_4h", "wt2_4h", "wt_cross_4h",
    "wt1_D", "wt2_D", "wt_cross_D",
    "stoch_k_5m", "stoch_d_5m",
    "stoch_k_15m", "stoch_d_15m",
    "stoch_k_1h", "stoch_d_1h",
    "rsi_15m", "mfi_15m",
    "adx_1h", "atr_1h",
    "dc_high_4h", "dc_low_4h",
    "bb_high_1h", "bb_low_1h", "bb_high_4h", "bb_low_4h",
    "sma200_1h", "sma200_D",
    "ha_5m", "ha_15m",
]
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("agent_snapshot_writer")


def _utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _redis_client():
    import redis
    return redis.Redis(host="localhost", port=6379, db=0, decode_responses=False)


def _decode_redis_value(raw):
    if raw is None:
        return None
    if isinstance(raw, bytes):
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            try:
                return pickle.loads(raw)
            except Exception:
                return None
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw
    return raw


def _read_redis_json(r, key):
    try:
        return _decode_redis_value(r.get(key))
    except Exception as e:
        log.warning("redis read %s failed: %s", key, e)
        return None


def _trim_position(p):
    if not isinstance(p, dict):
        return p
    keep = {"symbol", "position_side", "positionAmt", "entry_price", "mark_price", "gain", "max_gain", "prev_gain", "max_quantity", "is_hedge", "opened_at", "entry_time", "last_augmentation_time", "last_reduction_time", "augmentation_count", "reduction_count", "unrealized_pnl"}
    return {k: v for k, v in p.items() if k in keep}


def _load_positions(r):
    out = {"tradier": {}, "crypto": {}}
    for acct in ("trb", "trc"):
        d = _read_redis_json(r, f"tradier:positions:{acct}")
        if d is None:
            continue
        positions = d.get("positions") if isinstance(d, dict) else None
        if not isinstance(positions, dict):
            continue
        out["tradier"][acct] = {k: _trim_position(v) for k, v in positions.items()}
    for acct in ("ang", "inf", "flz", "men", "fin"):
        d = _read_redis_json(r, f"positions:{acct}")
        if d is None:
            continue
        positions = d.get("positions") if isinstance(d, dict) else None
        if not isinstance(positions, dict):
            continue
        out["crypto"][acct] = {k: _trim_position(v) for k, v in positions.items()}
    return out


def _load_indicators(r):
    raw = _read_redis_json(r, "tradier_indicators_latest")
    if not isinstance(raw, dict):
        return {}
    trimmed = {}
    for sym, fields in raw.items():
        if not isinstance(fields, dict):
            continue
        trimmed[sym] = {k: fields[k] for k in INDICATOR_FIELDS if k in fields}
        ts = fields.get("1m_updated_at") or fields.get("updated_at")
        if ts:
            trimmed[sym]["updated_at"] = ts
    return trimmed


def _load_sentiment(r):
    out = {}
    for label, key in (("stocks", "news_sentiment_stocks"), ("crypto", "news_sentiment_crypto"), ("meta", "news_sentiment_meta")):
        v = _read_redis_json(r, key)
        if v is not None:
            out[label] = v
    return out


def _today_yyyymmdd():
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _yesterday_yyyymmdd():
    return (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y%m%d")


def _load_recent_decisions():
    out = {}
    today = _today_yyyymmdd()
    yesterday = _yesterday_yyyymmdd()
    for acct in ("trb", "trc", "ang", "inf", "flz", "men", "fin"):
        rows = []
        for ymd in (yesterday, today):
            f = DECISIONS_DIR / f"decisions_{acct}_{ymd}.jsonl"
            if not f.exists():
                continue
            try:
                with f.open() as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            rows.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
            except Exception as e:
                log.warning("decisions read %s failed: %s", f, e)
        if rows:
            out[acct] = rows[-RECENT_DECISIONS_PER_ACCOUNT:]
    return out


def _load_allowlists():
    out = {}
    for acct in ("trb", "trc"):
        for side in ("long", "short"):
            f = BASE / f"symbols_{acct}_{side}.json"
            if not f.exists():
                continue
            try:
                out[f"{acct}_{side}"] = json.loads(f.read_text())
            except Exception as e:
                log.warning("allowlist read %s failed: %s", f, e)
    for acct in ("fin", "men", "ang"):
        f = BASE / f"symbols_{acct}.json"
        if not f.exists():
            continue
        try:
            out[acct] = json.loads(f.read_text())
        except Exception as e:
            log.warning("crypto allowlist read %s failed: %s", f, e)
    tk_path = BASE / "tradeable_keys.json"
    if tk_path.exists():
        try:
            tk_all = json.loads(tk_path.read_text())
            for acct in ("fin", "ang", "inf", "flz", "men"):
                out[f"tradeable_{acct}"] = [k for k in tk_all if k.startswith(f"{acct}:")]
        except Exception as e:
            log.warning("tradeable_keys read failed: %s", e)
    return out


def _config_summary():
    import re
    out = {}
    for fname, vars_ in (
        ("config_tradier.py", ["WRONG_SIDE_ABS_KILL_ENABLED", "WRONG_SIDE_MIN_AGE_MIN", "WRONG_SIDE_WT_TFS_REQUIRED", "TRADIER_ENTRY_SCORE_THRESHOLD", "PARTIAL_PROFIT_LOCK_ENABLED", "PARTIAL_PROFIT_LOCK_ACCOUNTS_TRADIER", "ATR_TRAIL_ENABLED", "OPTIONS_DENYLIST"]),
        ("config.py", ["STRICT_NO_LOSS_ACCOUNTS", "HEDGE_ACCOUNTS", "MIN_GAIN_TO_BUY_AGGRESSIVELY", "RATIO_MULTIPLIER", "PARTIAL_PROFIT_LOCK_ENABLED"]),
    ):
        path = BASE / fname
        if not path.exists():
            continue
        try:
            text = path.read_text()
            for var in vars_:
                pat = re.compile(r"^\s*" + re.escape(var) + r"\s*[:=]")
                for line in text.splitlines():
                    if pat.match(line):
                        out[var] = line.strip().split("#", 1)[0].rstrip()
                        break
        except Exception as e:
            log.warning("config read %s failed: %s", fname, e)
    return out


def _conviction_signals():
    cdir = BASE / "data" / "stock_traders"
    if not cdir.exists():
        return []
    files = sorted(cdir.glob("*_conviction.json"), reverse=True)[:1]
    if not files:
        return []
    try:
        rows = json.loads(files[0].read_text())
        if isinstance(rows, list):
            return rows[:25]
    except Exception as e:
        log.warning("conviction read %s failed: %s", files[0], e)
    return []


def _market_meta(r):
    raw = _read_redis_json(r, "latest_market_data")
    if not isinstance(raw, dict):
        return {}
    return {
        "btc_price": raw.get("BTCUSDT", {}).get("mark_price") if isinstance(raw.get("BTCUSDT"), dict) else None,
        "eth_price": raw.get("ETHUSDT", {}).get("mark_price") if isinstance(raw.get("ETHUSDT"), dict) else None,
        "symbols_count": len(raw),
    }


def _load_local_handoff_file(name):
    p = HANDOFF_REPO / name
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except Exception as e:
        log.warning("handoff read %s failed: %s", name, e)
        return None


def _load_causality_verdict():
    path = BASE / "data" / "opinion_causality_report.json"
    if not path.exists():
        return None
    try:
        rep = json.loads(path.read_text())
    except Exception as e:
        log.warning("causality report read failed: %s", e)
        return None
    if not isinstance(rep, dict):
        return None
    return {
        "generated_at_utc": rep.get("generated_at_utc"),
        "verdict": rep.get("verdict"),
        "per_trader_top20": rep.get("per_trader_top20"),
        "per_symbol_top30_by_volume": rep.get("per_symbol_top30_by_volume"),
    }


def build_snapshot():
    r = _redis_client()
    positions = _load_positions(r)
    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": _utc_now_iso(),
        "generator": "agent_snapshot_writer.py",
        "stale": False,
        "agent_focus_accounts": ["fin", "ang"],
        "positions": positions,
        "positions_count": {
            "tradier": {acct: len(p) for acct, p in positions["tradier"].items()},
            "crypto": {acct: len(p) for acct, p in positions["crypto"].items()},
        },
        "indicators_tradier": _load_indicators(r),
        "news_sentiment": _load_sentiment(r),
        "market_meta": _market_meta(r),
        "decisions_recent": _load_recent_decisions(),
        "allowlists": _load_allowlists(),
        "conviction_signals": _conviction_signals(),
        "config_summary": _config_summary(),
        "tradeable_refresh": _load_local_handoff_file("tradeable_refresh.json"),
        "tv_enrichment": _load_local_handoff_file("tv_enrichment.json"),
        "causality_verdict": _load_causality_verdict(),
    }


def write_and_push(snap):
    HANDOFF_REPO.mkdir(parents=True, exist_ok=True)
    new_bytes = json.dumps(snap, indent=2, default=str, sort_keys=True).encode("utf-8")
    if SNAPSHOT_PATH.exists():
        old_bytes = SNAPSHOT_PATH.read_bytes()
        if old_bytes == new_bytes:
            log.info("snapshot unchanged, skipping push (%d bytes)", len(new_bytes))
            return False
    SNAPSHOT_PATH.write_bytes(new_bytes)
    log.info("wrote snapshot (%d bytes)", len(new_bytes))
    git_env = os.environ.copy()
    git_env["GIT_SSH_COMMAND"] = f"ssh -i {Path.home()}/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git_env["HOME"] = str(Path.home())
    cmds = [
        ["git", "-C", str(HANDOFF_REPO), "add", "snapshot.json"],
        ["git", "-C", str(HANDOFF_REPO), "commit", "-m", f"snapshot {snap['generated_at_utc']}"],
        ["git", "-C", str(HANDOFF_REPO), "push", "origin", "main"],
    ]
    for cmd in cmds:
        rv = subprocess.run(cmd, env=git_env, capture_output=True, text=True, timeout=60)
        if rv.returncode != 0:
            if "nothing to commit" in (rv.stdout + rv.stderr):
                log.info("nothing to commit, skip push")
                return False
            log.error("git failed: %s\nstdout=%s\nstderr=%s", " ".join(cmd), rv.stdout, rv.stderr)
            return False
    log.info("pushed")
    return True


def main():
    try:
        snap = build_snapshot()
    except Exception as e:
        log.exception("build_snapshot failed: %s", e)
        sys.exit(1)
    write_and_push(snap)


if __name__ == "__main__":
    main()
