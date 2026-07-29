#!/usr/bin/env python3
"""expand_tier2_sweepset.py — close the parity gap honestly: add the trading-logic
LIVE_ONLY params to the Tier-2 sweep set so they are actually tested.

WHY this is faithful, not a reimplementation lie: backtest_v8_engine.py `import ez_manage`
and runs the REAL process_position / check_entry_candidates / execute_now, and it applies
V8_OVERRIDE_FILE into config (module attr + class attr + dataclass default). So a param the
live entry/exit code reads via config.X IS exercised at runtime — perturbing it through the
override changes the REAL decision. The token-classifier tagged these LIVE_ONLY only because
the token isn't literally in the engine file; functionally they ARE in the Tier-2 path.

What this does, per mode manifest:
  * for each LIVE_ONLY param that is TRADING-LOGIC (used in a live conditional / decision
    keyword) and numeric/bool -> set sweepable=True, sweep_tier="ENGINE_SCREEN_LIVECALL",
    test_values=derived grid, parity_via_tier2_livecall=True.
  * INFRA LIVE_ONLY (api/ws/redis/webhook/log/...) left untouched (legitimately out of scope).
  * DEAD left untouched.

NO-LIES: this only widens the SEARCH SPACE. The OFAT Tier-2 runner records trades + delta
per cell, which EMPIRICALLY proves whether each param affects the real run. Params that show
zero effect across all their values are inert in the backtest context (need live-only state,
e.g. order book / hedge tracker) -> flagged by the monitor, not claimed as tested-good.
"""
import json
import re
import sys
from pathlib import Path

from sweep_value_semantics import executable_values, validate_test_values

BASE = Path(__file__).resolve().parent
LIVE = {
    "crypto": ["ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
               "ez_indicators.py", "ez_prices.py", "ez_rankings.py", "ez_reentry.py",
               "ez_klines.py", "ez_market_data.py", "wt_composite.py", "utils.py",
               "wt_dc_delta.py"],
    "tradier": ["tradier_manage.py", "tradier_indicators.py", "tradier_positions.py",
                "tradier_prices.py", "tradier_rankings.py", "tradier_api.py",
                "tradier_hourly_reconfig.py", "utils.py", "wt_composite.py",
                "wt_dc_delta.py", "wt_dc_entry_scorer.py", "wt_dc_exit_scorer.py",
                "local_extremes_scorer.py"],
}
INFRA = re.compile(r"API|_WS|WEBHOOK|REDIS|LOG|EMAIL|ALERT|SUPERVISOR|INTERVAL|TIMEOUT|RETRY|"
                   r"CACHE|DISK|PORT|URL|HEARTBEAT|POLL|SLEEP|RATE_LIMIT|_BAN$|_BAN_|^BAN_|GATEWAY|SOCKET|"
                   r"RECONNECT|DESKTOP|NOTIFY|DASHBOARD|CONFIRMATION_THRESHOLD|ZERO_CONF|SYNC|"
                   r"BACKUP|SNAPSHOT|MONITOR|WATCHDOG|PROCESS|THREAD|QUEUE_|_PATH|_DIR|_FILE|SYMBOLS_",
                   re.I)
LOGIC = re.compile(r"ENTRY|EXIT|GATE|SCORE|SIZE|GAIN|LOSS|STOP|TRAIL|HEDGE|REENTRY|REDUCE|"
                   r"AUGMENT|LONG|SHORT|WT|DC_|STOCH|RSI|MFI|ADX|BB_|EMA|SMA|MACD|ATR|FUNDING|"
                   r"ZONE|ALIGN|MOMENTUM|BREAKOUT|THRESHOLD|MIN_|MAX_|_PCT|RATIO|DIRECTION|"
                   r"NOLOSS|TFS|VEL|PEAK|BIAS|CONFIRM|BUDGET|CONVICTION|OBLIGATORY|"
                   r"LR_BAND|BAND_SLOPE|HARVEST|_FRAC|SLOPE|REGIME|DEPTH|RATCHET|READD", re.I)


def derive(name, default):
    if isinstance(default, bool):
        return [True, False]
    if isinstance(default, (int, float)) and not isinstance(default, bool):
        v = float(default)
        if v == 0:
            grid = [0, 1, 2] if ("MIN" in name.upper() or "TFS" in name.upper()) else [0.0, 0.5, 1.0]
        else:
            grid = sorted({round(v * m, 6) for m in (0.5, 0.75, 1.0, 1.25, 1.5)})
        return [int(x) if isinstance(default, int) and float(x).is_integer() else x for x in grid]
    return None


def main(mode):
    mp = BASE / f"data/param_sweep_manifest_{mode}.json"
    man = json.loads(mp.read_text())
    params = man["params"]
    added = 0
    for name, v in params.items():
        if v.get("sweep_tier") != "LIVE_ONLY" or v.get("sweepable"):
            continue
        # keyword classify (fast): trading-logic AND not pure-infra
        if not LOGIC.search(name) or (INFRA.search(name) and not LOGIC.search(name)):
            continue
        if INFRA.search(name) and not re.search(r"LONG|SHORT|HEDGE|ENTRY|EXIT|GATE|STOP|REENTRY|AUGMENT", name, re.I):
            continue
        proposed = v.get("test_values") or derive(name, v.get("default"))
        validation = validate_test_values(name, v.get("default"), proposed)
        grid = executable_values(validation)
        v["range_validation"] = validation
        if not grid:
            v["sweepable"] = False
            v["sweep_quarantine"] = validation["status"]
            continue
        v["test_values"] = grid
        v["sweepable"] = True
        v["sweep_tier"] = "ENGINE_SCREEN_LIVECALL"
        v["parity_via_tier2_livecall"] = True
        added += 1
    man["tier2_livecall_added"] = added
    man["sweepable_after_expand"] = sum(1 for x in params.values() if x.get("sweepable"))
    mp.write_text(json.dumps(man, indent=2))
    print(f"{mode}: +{added} trading-logic LIVE_ONLY -> Tier-2 sweepable | "
          f"total sweepable now {man['sweepable_after_expand']}")


if __name__ == "__main__":
    for m in (sys.argv[1:] or ["crypto", "tradier"]):
        main(m)
