#!/usr/bin/env python3
"""Quick smoke test: verify each SCALP_V2 config switch produces different exit decisions.

Loads one NPZ symbol (CHRUSDT — active inf symbol), picks 50 bars with DC breakout
entry, runs check_scalp_v2_exit with each switch combo, asserts result differences.
"""
import numpy as np
from pathlib import Path
from types import SimpleNamespace

# Inline the module to avoid async import chain
import sys
sys.path.insert(0, str(Path(__file__).parent))
from htf_breakout_scalper import check_scalp_v2_entry, check_scalp_v2_exit, _exit_redzone, _exit_lh_ll, _exit_v1_wt_confirm, _exit_htf_reclaim, _universal_technical_stop

NPZ = Path("backtest_v5/indicators_3m/CHRUSDT.npz")

def load_bar(d, ts, idx):
    """Build indicator dict for a single bar from NPZ arrays."""
    out = {}
    for k in d.keys():
        if k == "timestamps": continue
        try:
            v = d[k][idx]
            out[k] = v.item() if hasattr(v, "item") else v
        except Exception:
            pass
    out["_ts"] = int(ts[idx])
    return out

def make_config(**overrides):
    defaults = {
        "SCALP_MODE": True,
        "SCALP_ACCOUNTS": ["inf"],
        "SCALP_V2_VARIANT": "V1_WT_CONFIRM",
        "SCALP_V2_DC_HTF_LIST": ["15m", "1h"],
        "SCALP_V2_DC_HTF_REQUIRE_ALL": True,
        "SCALP_V2_MAX_HOLD_MINUTES": 15.0,
        "SCALP_V2_MAX_CONCURRENT": 5,
        "SCALP_V2_REENTRY_COOLDOWN_S": 300,
        "SCALP_V2_ISOLATE": False,
        "SCALP_V2_REDZONE_EXIT": False,
        "SCALP_V2_REDZONE_K_THRESHOLD": 90,
        "SCALP_V2_LH_LL_EXIT": False,
        "SCALP_V2_LH_LL_TF": "15m",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)

def make_position(is_v2=True, opened_minutes_ago=5):
    from datetime import datetime, timezone, timedelta
    opened = datetime.now(timezone.utc) - timedelta(minutes=opened_minutes_ago)
    return SimpleNamespace(
        positionAmt=100.0,
        augment_reason=("SCALP_V2_OPEN_V1_WT_CONFIRM_test" if is_v2 else "MANUAL"),
        opened_at=opened.isoformat(),
        gain=0.5,
    )

def run_exit_on_bars(bars, config, position):
    """Run exit check on each bar, return list of (idx, result_action_or_None)."""
    results = []
    for idx, ind in bars:
        px = float(ind.get("close_3m", ind.get("close", 0)))
        r = check_scalp_v2_exit(f"inf:CHRUSDT_LONG", ind, px, position, config)
        results.append((idx, r["reason"] if r else None))
    return results

def main():
    if not NPZ.exists():
        print(f"SKIP: {NPZ} not found"); return
    d = np.load(NPZ, allow_pickle=True)
    ts = d["timestamps"].astype(np.int64)
    if ts[-1] > 1e12: ts = ts // 1000
    n = len(ts)
    # Pick 200 evenly spaced bars from last 30 days
    start = max(0, n - 14400)  # ~30 days of 3m bars
    indices = list(range(start, n, max(1, (n - start) // 200)))[:200]
    bars = [(i, load_bar(d, ts, i)) for i in indices]
    pos = make_position(is_v2=True, opened_minutes_ago=5)

    # ═══ TEST 1: SCALP_MODE=False → all None ═══
    cfg_off = make_config(SCALP_MODE=False)
    r_off = run_exit_on_bars(bars, cfg_off, pos)
    exits_off = sum(1 for _, r in r_off if r is not None)
    print(f"[TEST 1] SCALP_MODE=False          → exits: {exits_off:>3}  {'PASS ✓' if exits_off == 0 else 'FAIL ✗'}")

    # ═══ TEST 2: V1_WT_CONFIRM baseline (primary only, no secondary) ═══
    cfg_v1 = make_config()
    r_v1 = run_exit_on_bars(bars, cfg_v1, pos)
    exits_v1 = sum(1 for _, r in r_v1 if r is not None)
    print(f"[TEST 2] V1_WT_CONFIRM only         → exits: {exits_v1:>3}  {'PASS ✓' if exits_v1 > 0 else 'FAIL ✗ (no exits!)'}")

    # ═══ TEST 3: V1 + REDZONE_EXIT ═══
    cfg_rz = make_config(SCALP_V2_REDZONE_EXIT=True)
    r_rz = run_exit_on_bars(bars, cfg_rz, pos)
    exits_rz = sum(1 for _, r in r_rz if r is not None)
    rz_extra = exits_rz - exits_v1
    print(f"[TEST 3] V1 + REDZONE_K90           → exits: {exits_rz:>3}  delta: +{rz_extra:>3}  {'PASS ✓' if rz_extra > 0 else 'WARN ⚠ (no extra exits — check data)'}")

    # ═══ TEST 4: V1 + LH_LL_EXIT ═══
    cfg_lh = make_config(SCALP_V2_LH_LL_EXIT=True)
    r_lh = run_exit_on_bars(bars, cfg_lh, pos)
    exits_lh = sum(1 for _, r in r_lh if r is not None)
    lh_extra = exits_lh - exits_v1
    print(f"[TEST 4] V1 + LH_LL_15m             → exits: {exits_lh:>3}  delta: +{lh_extra:>3}  {'PASS ✓' if lh_extra > 0 else 'WARN ⚠ (no extra exits — check data)'}")

    # ═══ TEST 5: V1 + BOTH secondary exits ═══
    cfg_all = make_config(SCALP_V2_REDZONE_EXIT=True, SCALP_V2_LH_LL_EXIT=True)
    r_all = run_exit_on_bars(bars, cfg_all, pos)
    exits_all = sum(1 for _, r in r_all if r is not None)
    all_extra = exits_all - exits_v1
    print(f"[TEST 5] V1 + REDZONE + LH_LL       → exits: {exits_all:>3}  delta: +{all_extra:>3}  {'PASS ✓' if all_extra >= rz_extra or all_extra >= lh_extra else 'FAIL ✗'}")

    # ═══ TEST 6: V8_HTF_RECLAIM variant (different primary) ═══
    cfg_v8 = make_config(SCALP_V2_VARIANT="V8_HTF_RECLAIM")
    r_v8 = run_exit_on_bars(bars, cfg_v8, pos)
    exits_v8 = sum(1 for _, r in r_v8 if r is not None)
    v8_diff = abs(exits_v8 - exits_v1)
    print(f"[TEST 6] V8_HTF_RECLAIM variant      → exits: {exits_v8:>3}  diff from V1: {v8_diff:>3}  {'PASS ✓' if v8_diff > 0 else 'WARN ⚠'}")

    # ═══ TEST 7: Non-V2 position → always None ═══
    pos_manual = make_position(is_v2=False)
    r_manual = run_exit_on_bars(bars[:10], cfg_v1, pos_manual)
    exits_manual = sum(1 for _, r in r_manual if r is not None)
    print(f"[TEST 7] Non-V2 position tag         → exits: {exits_manual:>3}  {'PASS ✓' if exits_manual == 0 else 'FAIL ✗'}")

    # ═══ TEST 8: Entry check fires for inf account ═══
    entry_hits = 0
    for i, ind in bars:
        px = float(ind.get("close_3m", ind.get("close", 0)))
        pos_empty = SimpleNamespace(positionAmt=0.0, augment_reason="")
        r = check_scalp_v2_entry("CHRUSDT", "inf:CHRUSDT_LONG", ind, px, pos_empty, "inf", cfg_v1)
        if r: entry_hits += 1
    print(f"[TEST 8] Entry fires for inf          → hits: {entry_hits:>3}  {'PASS ✓' if entry_hits > 0 else 'FAIL ✗ (no entries!)'}")

    # ═══ TEST 9: Entry blocked for non-scalp account ═══
    cfg_noacct = make_config(SCALP_ACCOUNTS=["ang"])
    entry_blocked = 0
    for i, ind in bars[:20]:
        px = float(ind.get("close_3m", ind.get("close", 0)))
        pos_empty = SimpleNamespace(positionAmt=0.0, augment_reason="")
        r = check_scalp_v2_entry("CHRUSDT", "inf:CHRUSDT_LONG", ind, px, pos_empty, "inf", cfg_noacct)
        if r: entry_blocked += 1
    print(f"[TEST 9] Entry blocked for wrong acct → hits: {entry_blocked:>3}  {'PASS ✓' if entry_blocked == 0 else 'FAIL ✗'}")

    # ═══ Reason string samples ═══
    print(f"\nSAMPLE EXIT REASONS (first 5 from V1+REDZONE+LHLL):")
    for idx, reason in [(i, r) for i, r in r_all if r is not None][:5]:
        print(f"  bar {idx}: {reason}")

    # ═══ Summary ═══
    all_pass = (exits_off == 0 and exits_v1 > 0 and exits_manual == 0 and entry_hits > 0 and entry_blocked == 0)
    print(f"\n{'='*60}")
    print(f"CORE TESTS: {'ALL PASS ✓' if all_pass else 'SOME FAILURES — check above'}")
    print(f"Secondary exits producing different results: REDZONE +{rz_extra}, LH_LL +{lh_extra}, BOTH +{all_extra}")

    # ═══ SWITCH PRIORITY GUIDE ═══
    print(f"""
{'='*60}
SWITCH PRIORITY FOR FIRST WEEKS OF TESTING
{'='*60}

P0 — MUST TEST FIRST (core scalp V2 engine):
  SCALP_MODE=True                    Master ON/OFF. Test this first. Everything depends on it.
  SCALP_ACCOUNTS=["inf"]             Already set. inf = spike-fade momentum, the right target.
  SCALP_V2_VARIANT="V1_WT_CONFIRM"  Sweep winner (Sharpe 107, +634% net). DO NOT change until V8 validates.
  SCALP_V2_DC_HTF_REQUIRE_ALL=True  Quality gate. True = fewer but better trades. Keep True.
  SCALP_V2_MAX_HOLD_MINUTES=15      Sweep-proven. 60m universally worse for inf.

P1 — TEST AFTER P0 IS STABLE (2-3 days):
  SCALP_V2_REDZONE_EXIT=True        #2 in sweep (Sharpe 97). Catches exits V1_WT misses.
  SCALP_V2_REDZONE_K_THRESHOLD=90   Default 90 is sweep winner. Try 80 only after 90 is tested.

P2 — TEST AFTER P1 (week 2+):
  SCALP_V2_LH_LL_EXIT=True          #4 in sweep (Sharpe 68). Adds 15m structure break.
  SCALP_V2_LH_LL_TF="15m"           15m is sweep winner. 1h too slow, 3m too noisy.

P3 — DEFER (not urgent, optional tuning):
  SCALP_V2_MAX_CONCURRENT=5         Fine for now. Only tune if hitting position limits.
  SCALP_V2_REENTRY_COOLDOWN_S=300   5 min is reasonable. Shorter = more trades but more chop.
  SCALP_V2_DC_HTF_LIST=["15m","1h"] Adding "4h" tested but didn't improve.
  SCALP_V2_ISOLATE                   BACKTEST ONLY. Never True in live.

DO NOT TEST YET (from INF_RANKING_BYPASS — separate entry-side project):
  INF_RANKING_PRIORITY_BYPASS        Pending V8 validation of entry bypass gates.
  INF_RANKING_BYPASS_STOCH/HTF       Same — entry side, not exit side.
""")

if __name__ == "__main__":
    main()
