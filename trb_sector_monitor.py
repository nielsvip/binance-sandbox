import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path("/Users/niels/Documents/binance")
TRB_LONG_FILE = BASE_PATH / "trb" / "long_positions.json"
TRB_SHORT_FILE = BASE_PATH / "trb" / "short_positions.json"
PER_SYM_CFG_FILE = BASE_PATH / "data" / "hourly_reconfig" / "per_sym_active_config.json"
TRB_ACTIVE_CFG_FILE = BASE_PATH / "data" / "hourly_reconfig" / "trb" / "active_config.json"
DECISIONS_DIR = BASE_PATH / "data" / "decisions"

KEY_OVERRIDES = [
    "PEAK_GIVEBACK_ENABLED",
    "PEAK_GIVEBACK_DROP_PCT",
    "STDEV_BREAKOUT_ENABLED",
    "STDEV_BOUNCE_ENABLED",
    "USE_PROCESS_POSITION_EXIT_GATES",
    "TRADIER_WT_DC_ENTRY_THRESHOLD",
    "TRADIER_ENTRY_MIN_ALIGNMENT",
    "TRADIER_MIN_EXIT_TF_AGAINST_TRADIER",
]


def _load_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _file_age_hours(path):
    try:
        mtime = os.path.getmtime(path)
        return (datetime.now(timezone.utc).timestamp() - mtime) / 3600
    except Exception:
        return None


def _file_mtime_str(path):
    try:
        mtime = os.path.getmtime(path)
        return datetime.fromtimestamp(mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return "unknown"


def _resolve_config_source(symbol, side, trb_cfg, per_sym_cfg):
    pk = f"{symbol}_{side}"
    if pk in trb_cfg and trb_cfg[pk].get("overrides"):
        return "trb/active_config", trb_cfg[pk].get("overrides", {})
    if pk in per_sym_cfg and per_sym_cfg[pk].get("overrides"):
        return "per_sym_active_config", per_sym_cfg[pk].get("overrides", {})
    if pk in trb_cfg:
        tag = trb_cfg[pk].get("winning_tag", "baseline")
        if tag != "baseline":
            return f"trb/active_config ({tag})", trb_cfg[pk].get("overrides", {})
        return "trb/active_config (baseline)", {}
    if pk in per_sym_cfg:
        tag = per_sym_cfg[pk].get("winning_tag", "baseline")
        if tag != "baseline":
            return f"per_sym ({tag})", per_sym_cfg[pk].get("overrides", {})
        return "per_sym (baseline)", {}
    return "basic_defaults", {}


def _format_overrides(overrides):
    if not overrides:
        return "none"
    parts = []
    for k in KEY_OVERRIDES:
        if k in overrides:
            parts.append(f"{k}={overrides[k]}")
    extras = {k: v for k, v in overrides.items() if k not in KEY_OVERRIDES}
    for k, v in extras.items():
        parts.append(f"{k}={v}")
    return ", ".join(parts) if parts else "none"


def _load_positions():
    long_pos = _load_json(TRB_LONG_FILE)
    short_pos = _load_json(TRB_SHORT_FILE)
    positions = []
    for pk, p in long_pos.items():
        positions.append({"pk": pk, "symbol": p.get("symbol", pk), "side": "LONG", "gain": p.get("gain", 0.0), "entry_price": p.get("entry_price", 0.0), "mark_price": p.get("mark_price", 0.0), "positionAmt": p.get("positionAmt", 0.0), "entry_time": p.get("entry_time", "")})
    for pk, p in short_pos.items():
        positions.append({"pk": pk, "symbol": p.get("symbol", pk), "side": "SHORT", "gain": p.get("gain", 0.0), "entry_price": p.get("entry_price", 0.0), "mark_price": p.get("mark_price", 0.0), "positionAmt": p.get("positionAmt", 0.0), "entry_time": p.get("entry_time", "")})
    positions.sort(key=lambda x: x["gain"])
    return positions


def _load_recent_trades(n=20):
    from datetime import timedelta
    today = datetime.now(timezone.utc)
    yesterday = today - timedelta(days=1)
    date_strs = [today.strftime("%Y%m%d"), yesterday.strftime("%Y%m%d")]
    trades = []
    for date_str in date_strs:
        path = DECISIONS_DIR / f"decisions_trb_{date_str}.jsonl"
        if not path.exists():
            continue
        try:
            with open(path) as f:
                lines = f.readlines()
            for line in lines:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                    action = d.get("action", "")
                    if not action:
                        continue
                    pk = d.get("position_key", "")
                    symbol = pk.split(":")[-1].replace("_LONG", "").replace("_SHORT", "") if ":" in pk else pk
                    side = "LONG" if pk.endswith("_LONG") else "SHORT" if pk.endswith("_SHORT") else "?"
                    gain = d.get("trade", {}).get("gain_pct", None)
                    ts = d.get("timestamp", "")
                    reason = d.get("reason_text", "")
                    trades.append({"ts": ts, "symbol": symbol, "side": side, "action": action, "gain": gain, "reason": reason, "pk": pk})
                except Exception:
                    continue
        except Exception:
            continue
    trades.sort(key=lambda x: x["ts"], reverse=True)
    return trades[:n]


def _trade_config_source(trade_ts, symbol, side, per_sym_cfg, per_sym_mtime):
    pk = f"{symbol}_{side}"
    if pk not in per_sym_cfg:
        return "basic_defaults"
    try:
        trade_epoch = datetime.fromisoformat(trade_ts.replace("Z", "+00:00")).timestamp()
        if per_sym_mtime and per_sym_mtime <= trade_epoch:
            return "per_sym_active_config"
    except Exception:
        pass
    return "per_sym_active_config"


def print_section(title):
    print()
    print("=" * 72)
    print(f"  {title}")
    print("=" * 72)


def main():
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"\nTRB MONITOR  {now_str}")

    trb_cfg = _load_json(TRB_ACTIVE_CFG_FILE) if TRB_ACTIVE_CFG_FILE.exists() else {}
    per_sym_cfg = _load_json(PER_SYM_CFG_FILE) if PER_SYM_CFG_FILE.exists() else {}
    try:
        per_sym_mtime = os.path.getmtime(PER_SYM_CFG_FILE) if PER_SYM_CFG_FILE.exists() else None
    except Exception:
        per_sym_mtime = None

    print_section("7D AGENT — trb/active_config.json")
    if TRB_ACTIVE_CFG_FILE.exists():
        age_h = _file_age_hours(TRB_ACTIVE_CFG_FILE)
        mtime_str = _file_mtime_str(TRB_ACTIVE_CFG_FILE)
        stale_flag = "  [7D STALE]" if age_h is not None and age_h > 2 else ""
        print(f"  Last modified : {mtime_str}  ({age_h:.1f}h ago){stale_flag}")
        syms_with_overrides = {k: v for k, v in trb_cfg.items() if v.get("overrides")}
        print(f"  Keys in file  : {len(trb_cfg)}")
        print(f"  With overrides: {len(syms_with_overrides)}")
        if syms_with_overrides:
            print()
            print(f"  {'SYM_SIDE':<22} {'TAG':<16} {'OVERRIDES'}")
            print(f"  {'-'*22} {'-'*16} {'-'*32}")
            for pk, v in sorted(syms_with_overrides.items()):
                tag = v.get("winning_tag", "?")
                ovr = _format_overrides(v.get("overrides", {}))
                print(f"  {pk:<22} {tag:<16} {ovr}")
    else:
        print("  [MISSING] trb/active_config.json not found — 7D agent has not written output")

    print_section("LIVE POSITIONS")
    positions = _load_positions()
    if not positions:
        print("  No open positions found.")
    else:
        col_w = [8, 6, 8, 26, 0]
        hdr = f"  {'SYMBOL':<8} {'SIDE':<6} {'GAIN%':>7}  {'CONFIG_SOURCE':<26}  KEY_OVERRIDES"
        print(hdr)
        print(f"  {'-'*8} {'-'*6} {'-'*7}  {'-'*26}  {'-'*38}")
        for pos in positions:
            symbol = pos["symbol"]
            side = pos["side"]
            gain = pos["gain"]
            cfg_source, overrides = _resolve_config_source(symbol, side, trb_cfg, per_sym_cfg)
            ovr_str = _format_overrides(overrides)
            gain_str = f"{gain:+.2f}%"
            print(f"  {symbol:<8} {side:<6} {gain_str:>7}  {cfg_source:<26}  {ovr_str}")

    print_section("RECENT TRADES (last 20, today)")
    trades = _load_recent_trades(20)
    if not trades:
        print("  No trades found for today.")
    else:
        print(f"  {'TIME':<19} {'SYM':<8} {'SIDE':<5} {'ACTION':<12} {'GAIN%':>7}  {'CFG_SOURCE':<22}  REASON")
        print(f"  {'-'*19} {'-'*8} {'-'*5} {'-'*12} {'-'*7}  {'-'*22}  {'-'*30}")
        for t in trades:
            ts_short = t["ts"][:19] if t["ts"] else "?"
            symbol = t["symbol"][:8]
            side = t["side"][:5]
            action_raw = t["action"]
            action_clean = action_raw.encode("ascii", "ignore").decode().strip().replace("CLOSE", "CLOSE").replace("OPEN", "OPEN").replace("AUGMENT", "AUGMENT").replace("REDUCE", "REDUCE")
            if "CLOSE" in action_raw:
                action_clean = "CLOSE"
            elif "OPEN" in action_raw:
                action_clean = "OPEN"
            elif "AUGMENT" in action_raw:
                action_clean = "AUGMENT"
            elif "REDUCE" in action_raw:
                action_clean = "REDUCE"
            else:
                action_clean = action_raw[:12]
            gain = t["gain"]
            gain_str = f"{gain:+.2f}%" if gain is not None else "   n/a"
            cfg_src = _trade_config_source(t["ts"], t["symbol"], t["side"], per_sym_cfg, per_sym_mtime)
            reason = t["reason"][:50] if t["reason"] else ""
            print(f"  {ts_short:<19} {symbol:<8} {side:<5} {action_clean:<12} {gain_str:>7}  {cfg_src:<22}  {reason}")

    print()


if __name__ == "__main__":
    main()
