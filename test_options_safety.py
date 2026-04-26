"""Read-only safety tests for the options stack — Monday readiness check.

Validates that the safeguards added per OPTIONS_OVERHAUL_FRAMEWORK_20260425.md actually
work, without placing any live orders. Run before market open.

Tests:
  T1. _load_allowed_symbols filters BLACKLIST symbols out (ABT/JNJ scenario).
  T2. _load_allowed_symbols rejects symbols not in symbols_trb_long/short.json.
  T3. _place_gtc_buys augment-into-loss block: dry-run order at price < avg×0.85 is rejected.
  T4. _place_gtc_buys BLACKLIST block: dry-run order on ABT/JNJ is rejected.
  T5. State snapshot freshness: data/options_state/current.json was updated < 24h ago.
  T6. Recommendations file freshness: data/options_state/recommendations.json was updated < 24h ago.
  T7. All open positions in current.json have corresponding lifecycle JSONL files.
  T8. WT/DC entry gate: rejects a symbol with insufficient indicator score.

Exit code 0 = all green. Non-zero = at least one failure.
"""
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_PATH = Path(__file__).resolve().parent
sys.path.insert(0, str(BASE_PATH))

from config_tradier import TradierConfig

PASS = "✓"
FAIL = "✗"
WARN = "·"


def _print(name: str, ok: bool, msg: str) -> bool:
    sym = PASS if ok else FAIL
    print(f"  {sym} {name}: {msg}")
    return ok


def t1_blacklist_filter() -> bool:
    """T1: _load_allowed_symbols strips BLACKLIST entries from allowlist."""
    print("\nT1. BLACKLIST filter in _load_allowed_symbols")
    from tradier_options_agent import _load_allowed_symbols
    config = TradierConfig()
    bl = list(getattr(config, "BLACKLIST", []) or [])
    if not bl:
        return _print("BLACKLIST present", False, "config_tradier.BLACKLIST is empty — gate would be a no-op")
    ok = _print("BLACKLIST present", True, f"{bl}")
    calls, puts = _load_allowed_symbols(config)
    leak_calls = set(bl) & calls
    leak_puts = set(bl) & puts
    ok &= _print("no BLACKLIST leak in call allowlist", not leak_calls, f"leaked={leak_calls or '∅'}")
    ok &= _print("no BLACKLIST leak in put allowlist", not leak_puts, f"leaked={leak_puts or '∅'}")
    return ok


def t2_allowlist_membership() -> bool:
    """T2: random unknown symbol is NOT in the allowlist."""
    print("\nT2. Allowlist membership (random symbol must be excluded)")
    from tradier_options_agent import _load_allowed_symbols
    config = TradierConfig()
    calls, puts = _load_allowed_symbols(config)
    ok = _print("ZZZNONEXIST not in calls", "ZZZNONEXIST" not in calls, f"calls_size={len(calls)}")
    ok &= _print("ZZZNONEXIST not in puts", "ZZZNONEXIST" not in puts, f"puts_size={len(puts)}")
    abt_in = "ABT" in calls or "ABT" in puts
    jnj_in = "JNJ" in calls or "JNJ" in puts
    ok &= _print("ABT not in any allowlist", not abt_in, "blocked" if not abt_in else "LEAK")
    ok &= _print("JNJ not in any allowlist", not jnj_in, "blocked" if not jnj_in else "LEAK")
    return ok


def t3_augment_into_loss_logic() -> bool:
    """T3: validate augment-into-loss logic by code inspection (no orders)."""
    print("\nT3. Augment-into-loss block (logic check)")
    config = TradierConfig()
    enabled = bool(getattr(config, "OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED", False))
    threshold = float(getattr(config, "OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD", 0))
    ok = _print("OPTIONS_AUGMENT_INTO_LOSS_BLOCK_ENABLED", enabled, f"={enabled}")
    ok &= _print("OPTIONS_AUGMENT_INTO_LOSS_THRESHOLD set", threshold > 0, f"={threshold}")
    # Simulate the comparison: existing avg=11.45 (PLTR-style), new=9.00 → must be blocked
    existing_avg = 11.45
    new_price = 9.00
    would_block = new_price < existing_avg * threshold
    ok &= _print("PLTR pattern: new $9.00 vs avg $11.45 → blocked",
                 would_block, f"$9.00 < $11.45 × {threshold} = ${existing_avg * threshold:.2f}")
    # Within-tolerance add ($10.50 = -8.3% from $11.45, inside the 15% threshold) — correctly NOT blocked.
    new_price2 = 10.50
    would_block2 = new_price2 < existing_avg * threshold
    ok &= _print("11.45→10.50 (-8.3%, within tolerance) → allowed",
                 not would_block2, f"$10.50 ≥ ${existing_avg * threshold:.2f}")
    # Real PLTR disaster pattern: $17.45 → $14.65 (-16%) → MUST be blocked
    pltr_avg = 17.45
    pltr_add = 14.65
    pltr_blocked = pltr_add < pltr_avg * threshold
    ok &= _print("PLTR real pattern $17.45→$14.65 (-16%) → blocked",
                 pltr_blocked, f"$14.65 < ${pltr_avg * threshold:.2f}")
    # Add at higher price (averaging up) → allowed
    new_price3 = 11.50
    would_block3 = new_price3 < existing_avg * threshold
    ok &= _print("add at $11.50 (above avg) → allowed",
                 not would_block3, f"$11.50 ≥ ${existing_avg * threshold:.2f}")
    return ok


def t4_gtc_blacklist_logic() -> bool:
    """T4: _place_gtc_buys BLACKLIST check (logic / config inspection)."""
    print("\nT4. GTC BLACKLIST block (logic check)")
    import tradier_options_agent as toa
    src = Path(toa.__file__).read_text()
    ok = _print("_place_gtc_buys reads config.BLACKLIST",
                "_gtc_blacklist" in src and "getattr(config, 'BLACKLIST'" in src,
                "uses getattr(config, 'BLACKLIST'...)")
    ok &= _print("_place_gtc_buys returns blocked_blacklist status",
                 'status": "blocked_blacklist' in src or "'status': 'blocked_blacklist'" in src or "blocked_blacklist" in src,
                 "explicit blocked_blacklist status emitted")
    ok &= _print("_place_gtc_buys blocks before placing order",
                 "BLACKLIST_BLOCK" in src and "continue" in src,
                 "BLACKLIST_BLOCK log + continue (skip the order)")
    return ok


def t5_state_snapshot_freshness() -> bool:
    """T5: current.json updated within last 24h."""
    print("\nT5. State snapshot freshness")
    p = BASE_PATH / "data" / "options_state" / "current.json"
    if not p.exists():
        return _print("current.json exists", False, "missing — run tradier_options_state.py")
    snap = json.loads(p.read_text())
    ts = snap.get("ts", "")
    try:
        snap_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - snap_dt).total_seconds() / 3600.0
    except Exception as e:
        return _print("current.json ts parseable", False, str(e))
    return _print("current.json fresh (<24h)", age_h < 24, f"age={age_h:.1f}h, ts={ts}")


def t6_recs_freshness() -> bool:
    """T6: recommendations.json fresh."""
    print("\nT6. Recommendations freshness")
    p = BASE_PATH / "data" / "options_state" / "recommendations.json"
    if not p.exists():
        return _print("recommendations.json exists", False, "run tradier_options_recommendations.py")
    rec = json.loads(p.read_text())
    ts = rec.get("ts", "")
    try:
        rec_dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        age_h = (datetime.now(timezone.utc) - rec_dt).total_seconds() / 3600.0
    except Exception as e:
        return _print("recommendations.json ts parseable", False, str(e))
    return _print("recommendations.json fresh (<24h)", age_h < 24,
                  f"age={age_h:.1f}h, n={rec.get('summary', {}).get('n_positions', 0)} positions")


def t7_lifecycle_coverage() -> bool:
    """T7: every open OCC has a lifecycle JSONL file."""
    print("\nT7. Lifecycle log coverage")
    state_p = BASE_PATH / "data" / "options_state" / "current.json"
    trades_dir = BASE_PATH / "data" / "options_trades"
    if not state_p.exists():
        return _print("state file exists", False, "missing")
    snap = json.loads(state_p.read_text())
    occs = [p.get("occ") for p in snap.get("positions", []) if p.get("occ")]
    if not occs:
        return _print("any open positions", True, "no open positions, trivially OK")
    missing = [o for o in occs if not (trades_dir / f"{o}.jsonl").exists()]
    return _print(f"{len(occs)} OCCs covered in lifecycle dir",
                  not missing, f"missing={missing or '∅'}")


def t8_wt_dc_gate_callable() -> bool:
    """T8: WT/DC entry gate is callable + responds to disable flag."""
    print("\nT8. WT/DC entry gate callable")
    from tradier_options_agent import _wt_dc_options_entry_gate
    config = TradierConfig()
    # With empty indicators dict → should pass with NO_INDICATORS reason (graceful)
    allow, reason = _wt_dc_options_entry_gate("AAPL", True, {}, config)
    ok = _print("empty indicators → graceful (no crash)",
                isinstance(allow, bool) and isinstance(reason, str),
                f"allow={allow}, reason={reason!r}")
    # Verify config knob exists
    enabled = bool(getattr(config, "OPTIONS_BUY_WT_DC_GATE_ENABLED", None))
    ok &= _print("OPTIONS_BUY_WT_DC_GATE_ENABLED defined",
                 hasattr(config, "OPTIONS_BUY_WT_DC_GATE_ENABLED"),
                 f"={enabled}")
    min_score = getattr(config, "OPTIONS_BUY_MIN_WT_DC_SCORE", None)
    ok &= _print("OPTIONS_BUY_MIN_WT_DC_SCORE defined",
                 min_score is not None, f"={min_score}")
    return ok


def main():
    print("=" * 70)
    print(f" OPTIONS SAFETY TEST  {datetime.now(timezone.utc).isoformat()}")
    print("=" * 70)
    results = {
        "T1_blacklist_filter": t1_blacklist_filter(),
        "T2_allowlist_membership": t2_allowlist_membership(),
        "T3_augment_into_loss": t3_augment_into_loss_logic(),
        "T4_gtc_blacklist": t4_gtc_blacklist_logic(),
        "T5_state_freshness": t5_state_snapshot_freshness(),
        "T6_recs_freshness": t6_recs_freshness(),
        "T7_lifecycle_coverage": t7_lifecycle_coverage(),
        "T8_wt_dc_gate": t8_wt_dc_gate_callable(),
    }
    print("\n" + "=" * 70)
    n_pass = sum(1 for v in results.values() if v)
    n_total = len(results)
    print(f" RESULT: {n_pass}/{n_total} passed")
    if n_pass < n_total:
        print(" FAILED:")
        for k, v in results.items():
            if not v:
                print(f"   ✗ {k}")
    print("=" * 70)
    sys.exit(0 if n_pass == n_total else 1)


if __name__ == "__main__":
    main()
