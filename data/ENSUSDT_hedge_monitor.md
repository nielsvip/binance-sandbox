# ENSUSDT Hedge Monitor Log
Started: 2026-05-12T03:30:00Z
Monitoring: fin:ENSUSDT_SHORT (hedge for fin:ENSUSDT_LONG)

---

## Cycle 1 at 2026-05-12T03:30:00Z

- Last SHORT event: 2026-05-12T03:21:10 AUGMENT qty=1.5 price=7.2420 reason=QUICK_REDUCE_REDUCE_k_1m:86/d_1m:92/k_3m:100/d_3m:94_SIMPLE_TP_0.00%_wt=declining_bc112STAGNATION_REDUCE
- Last LONG event: 2026-05-11T00:38:22 AUGMENT qty=1.4 price=7.4444 reason=QUICK_PARABOLIC_EXIT_LONG_k15=99_px>7.312000
- Current SHORT size: 8.5 units at mark $7.23, gain approximately -13.33%
- Current wt state (from last event indicators): wt1_3m=N/A wt1_15m=null wt1_1h=N/A k_3m=100.0 (prior events: k_3m was oscillating 5-100)
- Hedge logic firing? YES — REPEATED FIRING. Key observations:
  - UNIVERSAL_NOLOSS_GATE blocking REDUCE every ~60s because gain=-13.33% (SHORT is losing, price rose)
  - OBLIGATORY_HEDGE fires each time → execute_same_symbol_hedge → result=False (cannot open same-symbol hedge)
  - HEDGE_FAILED_FALLBACK_CLOSE triggers → reduces qty=1.0 per cycle via STEP3_ROUTE
  - But then: NEWBORN_PROTECT blocks further reduce for 900s after position gets re-augmented
  - Two AUGMENT events at 03:20:12 (qty=6.0) and 03:21:10 (qty=1.5) have reason "QUICK_REDUCE_REDUCE...STAGNATION_REDUCE" — this appears to be the STAGNATION logic adding to SHORT instead of closing it
  - WT direction: wt_against=0/2 (3m=False 1h=False) per log — WT is NOT bullish, it's bearish/neutral → SHORT hedge should NOT be closing (WT hasn't flipped bullish yet)
  - LONG has MAKER_CLOSE_FLOOR_CLAMP active: trying to close LONG at $7.494 but market is $7.23 — stuck below floor

- FLAG_USER: CRITICAL ANOMALY — The SHORT hedge is at -13.33% loss (price moved UP to $7.23 from entries ~$6.2-6.4). The STAGNATION_REDUCE logic AUGMENTED the SHORT position by 6.0+1.5 units at 03:20-03:21 UTC instead of reducing it. HEDGE_FAILED_FALLBACK_CLOSE fires every ~60s but only reduces 1 unit each time from a now-8.5-unit position. NEWBORN_PROTECT then blocks for 900s. This is a spiral — hedge is growing not closing. WT has NOT flipped bullish (wt_against=0/2 both False), so the WT-flip close trigger hasn't fired. But the SHORT is hemorrhaging at -13.33% loss while stuck in NEWBORN_PROTECT window.

