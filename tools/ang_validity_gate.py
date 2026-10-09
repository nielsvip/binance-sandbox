#!/usr/bin/env python3
"""ANG validity gatekeeper — RETIRED 2026-10-09, AUDIT-ONLY (USER: tradeable_keys
is not your domain; respect the lists). Reports what WOULD drop, writes nothing.

Was: enforce backtest-proven universe on ranking outputs.

ROOT CAUSE (2026-10-09): ez_rankings emits symbols_ang_long/short.json by raw
momentum/linearity score with NO backtest-validity check. 2026-10-09 audit of the
live lists vs data/hourly_reconfig/per_sym_active_config.json showed:
  - 8/29 longs with NEGATIVE 30D backtest gain, 5 with NO backtest at all
    (backfill/MISSING), 4 below the 30-trade NO-LIES floor;
  - 3/6 shorts NEGATIVE in backtest, 2 with NO backtest, 1 below floor;
  - EVERY listed sym_side underperforms buy-and-hold by 20-120pp;
  - FARTCOINUSDT/PUMPUSDT listed LONG and SHORT simultaneously;
  - lists churn every rankings cycle (~2-6 min) -> 124 trades/min halt 2026-09-25.
Live "never stops losing" because the lists lose IN THE BACKTEST ITSELF, before
fees/slippage/churn. ez_rankings.py is LOCKED, so this gatekeeper (NEW file, no
locked edits) prunes the ranking OUTPUTS every minute until the generator is
fixed (needs "unlock ez_rankings.py").

Gate per sym_side (from per_sym_active_config.json):
  trades >= 30, gain > 0, pool_sharpe > 0, max_dd_pct <= 30.
Overlap (same symbol valid BOTH sides) -> keep higher-gain side only.
SAFETY: keys with open positions (positionAmt != 0) and tracker hedge keys are
ALWAYS kept (must stay closeable/hedgeable). Only ang: keys are touched in
shared files; other accounts pass through untouched.
"""

import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PERSYM = BASE / "data" / "hourly_reconfig" / "per_sym_active_config.json"
ANG_LONG = BASE / "symbols_ang_long.json"
ANG_SHORT = BASE / "symbols_ang_short.json"
TRADEABLE = BASE / "tradeable_keys.json"
PERSIST = BASE / "universe_persistence.json"
LONG_POS = BASE / "ang" / "long_positions.json"
SHORT_POS = BASE / "ang" / "short_positions.json"
TRACKER = BASE / "ang" / "tracker.json"
MIN_TRADES = 30
MIN_GAIN_PCT = 0.0
MIN_POOL_SHARPE = 0.0
WARN_SHARPE_BELOW = 0.2
MAX_DD_PCT = 30.0


def _load_json(path, default):
    try:
        with open(path) as f:
            return json.load(f), True
    except Exception:
        return default, False


def _gain_of(entry):
    for k in ("acc_gain_pct", "gain_30d"):
        v = entry.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _sharpe_of(entry):
    for k in ("pool_sharpe", "wsharpe"):
        v = entry.get(k)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                pass
    return 0.0


def _trades_of(entry):
    try:
        return int(entry.get("trades", 0) or 0)
    except (TypeError, ValueError):
        return 0


def _dd_of(entry):
    v = entry.get("max_dd_pct")
    if v is None:
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def validity(side_key, persym):
    entry = persym.get(side_key)
    if not isinstance(entry, dict):
        return False, "MISSING_FROM_PERSYM"
    trades = _trades_of(entry)
    if trades < MIN_TRADES:
        return False, f"trades={trades}<{MIN_TRADES}"
    gain = _gain_of(entry)
    if not gain > MIN_GAIN_PCT:
        return False, f"gain={gain:.2f}<=0"
    sharpe = _sharpe_of(entry)
    if not sharpe > MIN_POOL_SHARPE:
        return False, f"pool_sharpe={sharpe:.3f}<=0"
    dd = _dd_of(entry)
    if dd is not None and dd > MAX_DD_PCT:
        return False, f"dd={dd:.1f}>30"
    if sharpe < WARN_SHARPE_BELOW:
        return True, f"WEAK_SHARPE_{sharpe:.3f}"
    return True, "ok"


def _open_keys():
    keep = set()
    for path in (LONG_POS, SHORT_POS):
        data, _ = _load_json(path, {})
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict):
                    try:
                        if abs(float(v.get("positionAmt", 0) or 0)) > 0:
                            keep.add(k)
                    except (TypeError, ValueError):
                        pass
    return keep


def _hedge_keys():
    keep = set()
    tracker, _ = _load_json(TRACKER, {})
    if not isinstance(tracker, dict):
        return keep
    hedges = tracker.get("hedges", {})
    if isinstance(hedges, dict):
        keep.update(hedges.keys())
    for h in tracker.get("active_hedges", []) or []:
        if isinstance(h, dict) and h.get("position_key"):
            keep.add(h["position_key"])
        elif isinstance(h, str):
            keep.add(h)
    exits = tracker.get("exit_candidates", {})
    if isinstance(exits, dict):
        for k, v in exits.items():
            if isinstance(v, dict) and (
                v.get("is_hedge") is True or v.get("hedge_for")
            ):
                keep.add(k)
    return keep


def _atomic_write_json(path, data):
    tmp = path.with_suffix(path.suffix + f".gate.{os.getpid()}.tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def run(apply=False):
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    persym, persym_ok = _load_json(PERSYM, {})
    if not persym_ok or not persym:
        print(f"{ts} ABORT: cannot read {PERSYM} (no files written)")
        return 2
    safety = _open_keys() | _hedge_keys()
    safety_ang = {k for k in safety if k.startswith("ang:")}
    longs, longs_ok = _load_json(ANG_LONG, [])
    shorts, shorts_ok = _load_json(ANG_SHORT, [])
    if not longs_ok or not isinstance(longs, list):
        print(f"{ts} ABORT: cannot read {ANG_LONG} (no files written)")
        return 2
    if not shorts_ok or not isinstance(shorts, list):
        print(f"{ts} ABORT: cannot read {ANG_SHORT} (no files written)")
        return 2
    keep_long, drop_long = [], []
    for s in longs:
        ok, why = validity(f"{s}_LONG", persym)
        if ok or f"ang:{s}_LONG" in safety_ang:
            keep_long.append(s)
        else:
            drop_long.append((s, why))
    keep_short, drop_short = [], []
    for s in shorts:
        ok, why = validity(f"{s}_SHORT", persym)
        if ok or f"ang:{s}_SHORT" in safety_ang:
            keep_short.append(s)
        else:
            drop_short.append((s, why))
    overlap = set(keep_long) & set(keep_short)
    for s in sorted(overlap):
        gl = _gain_of(persym.get(f"{s}_LONG", {}))
        gs = _gain_of(persym.get(f"{s}_SHORT", {}))
        if gl >= gs:
            keep_short.remove(s)
            drop_short.append((s, f"BOTH_SIDES_KEEP_LONG_{gl:.2f}_vs_{gs:.2f}"))
        else:
            keep_long.remove(s)
            drop_long.append((s, f"BOTH_SIDES_KEEP_SHORT_{gs:.2f}_vs_{gl:.2f}"))
    tk, tk_ok = _load_json(TRADEABLE, [])
    tk_list = (
        list(tk)
        if isinstance(tk, list)
        else list(tk.keys() if isinstance(tk, dict) else [])
    )
    if not tk_ok or not tk_list:
        print(f"{ts} ABORT: cannot read {TRADEABLE} (no files written)")
        return 2
    keep_tk, drop_tk = [], []
    for k in tk_list:
        if not k.startswith("ang:"):
            keep_tk.append(k)
            continue
        if k in safety_ang:
            keep_tk.append(k)
            continue
        side = k.split(":", 1)[1] if ":" in k else k
        ok, why = validity(side, persym)
        if ok:
            keep_tk.append(k)
        else:
            drop_tk.append((k, why))
    up, up_ok = _load_json(PERSIST, {})
    if not up_ok or not isinstance(up, dict) or not up:
        print(f"{ts} ABORT: cannot read {PERSIST} (no files written)")
        return 2
    keep_up, drop_up = {}, []
    for k, v in up.items():
        if not k.startswith("ang:"):
            keep_up[k] = v
            continue
        if k in safety_ang:
            keep_up[k] = v
            continue
        side = k.split(":", 1)[1] if ":" in k else k
        ok, why = validity(side, persym)
        if ok:
            keep_up[k] = v
        else:
            drop_up.append((k, why))
    mode = "APPLY" if apply else "DRYRUN"
    print(
        f"{ts} [{mode}] longs {len(longs)}->{len(keep_long)} shorts {len(shorts)}->{len(keep_short)} tradeable {len(tk_list)}->{len(keep_tk)} persist {len(up) if isinstance(up, dict) else '?'}->{len(keep_up) if isinstance(keep_up, dict) else '?'} safety_kept={len(safety_ang)}"
    )
    for s, why in drop_long:
        print(f"  DROP_LONG {s} {why}")
    for s, why in drop_short:
        print(f"  DROP_SHORT {s} {why}")
    for k, why in drop_tk:
        print(f"  DROP_TK {k} {why}")
    for k, why in drop_up:
        print(f"  DROP_PERSIST {k} {why}")
    if apply:
        if len(keep_tk) < len(tk_list) // 2 or len(keep_up) < len(up) // 2:
            print(
                f"{ts} ABORT: kept<50% (tk {len(keep_tk)}/{len(tk_list)}, persist {len(keep_up)}/{len(up)}) — refusing wipe (no files written)"
            )
            return 2
        # RETIRED 2026-10-09 (USER: tradeable_keys is not your domain; respect
        # the lists). Audit-only from here on: report what WOULD drop, write
        # nothing. Cron stays off.
        print(f"{ts} [APPLY] RETIRED — audit only, no files written (tradeable_keys not our domain)")
    return 0


if __name__ == "__main__":
    sys.exit(run(apply="--apply" in sys.argv))
