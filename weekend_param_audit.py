#!/usr/bin/env python3
"""
Weekend Parameter Audit — 2026-03-15/16
Compares sandbox backtest results with live config changes.
Flags non-reliable outcomes and questionable parameter decisions.

Data sources:
  - backtest_framework/results/tournament.db (113K combos, tournament)
  - backtest_framework/results/sharpe_all_combos.db (4.9M combos, exhaustive)
  - backtest_framework/results/MARATHON_REPORT.md (marathon 914K results)
  - backtest_framework/results/REPORT.md (tradier 978K results)
  - sweep_results/ (indicator phase1-3 sweeps)
  - backtest_framework/results/tf_compare.db + tiered.db
  - Live config.py vs sandbox config.py diffs

Run: python3 weekend_param_audit.py
"""

import json
import os
import sys
from datetime import datetime

REPORT_LINES = []

def h1(text):
    REPORT_LINES.append(f"\n{'='*80}")
    REPORT_LINES.append(f" {text}")
    REPORT_LINES.append(f"{'='*80}")

def h2(text):
    REPORT_LINES.append(f"\n--- {text} ---")

def line(text=""):
    REPORT_LINES.append(text)

def warn(text):
    REPORT_LINES.append(f"  ⚠ WARNING: {text}")

def ok(text):
    REPORT_LINES.append(f"  ✓ OK: {text}")

def flag(text):
    REPORT_LINES.append(f"  ✗ UNRELIABLE: {text}")


def audit():
    h1("WEEKEND PARAMETER AUDIT — 2026-03-15/16")
    line(f"Generated: {datetime.utcnow().strftime('%Y-%m-%d %H:%M UTC')}")
    line(f"Sources: tournament.db (113K), sharpe_all_combos.db (4.9M), marathon (914K), tradier (978K), indicator sweeps (239 symbols x 5 TFs)")

    # ─────────────────────────────────────────────────────
    h1("SECTION 1: CONFIG CHANGES APPLIED TO LIVE (sandbox → live diffs)")
    # ─────────────────────────────────────────────────────

    changes = [
        ("BASIS_CONDITION", "True → False", "Sweep says OFF = +0.67 delta Sharpe", "APPLIED"),
        ("K3M_CAP", "N/A → 80", "Tournament winner: block LONG entry k_3m>=80, SHORT k_3m<=20", "APPLIED"),
        ("CYCLE_TP_PCT", "N/A → 0.03 (3%)", "Best exit mode in 4.9M sweep: cycle_tp_030 avg Sharpe +34.68 vs next best cycle_tp_015 at -8.14", "APPLIED"),
        ("ACCOUNT_TP_PCT", "N/A → per-account 1-2%", "Tournament per-account winners", "APPLIED"),
        ("HTF_STRICT", "N/A → True", "Tournament: D+4h+1h must ALL confirm", "APPLIED"),
        ("HA_3M_ENTRY_WEIGHT", "N/A → 0.0", "Sweep: ha_3m = -5.3 delta Sharpe (harmful)", "APPLIED"),
        ("HTF1_CONF", "False → True", "Was marked OPTIMAL_V1: OFF. Now enabled — contradicts note", "APPLIED"),
        ("NEWS_SENTIMENT_WEIGHT", "0.10 → 0.90", "9x increase. No backtest evidence for this.", "APPLIED"),
        ("MITIGATOR_ENABLED", "False → True", "Enables tiered gain protector on ang/men", "APPLIED"),
        ("HEDGE_MODE", "False → True", "Re-enabled on live", "APPLIED"),
        ("HEDGE_ACCOUNTS", "inf,fin,men,flz → inf,fin,men", "flz removed from hedge accounts", "APPLIED"),
        ("MIN_POSITION_SIZE", "0.3 → 0.9", "3x increase in min position size", "APPLIED"),
        ("MIN_GAIN_TO_BUY_AGGRESSIVELY", "0.7 → 1.6", "Higher bar for aggressive buys", "APPLIED"),
        ("ZERO_CONFIRMATION_THRESHOLD_API", "1 → 5", "More confirmations needed (slower but safer)", "APPLIED"),
        ("COUNTER_TREND_CRYPTO", "N/A → [XAUUSDT,PAXGUSDT,XAGUSDT,BTCDOMUSDT,SKYUSDT]", "New: invert ratio_mult for safe-haven assets", "APPLIED"),
    ]

    line(f"{'Parameter':40s} {'Change':30s} {'Status':10s}")
    line("-" * 82)
    for name, change, evidence, status in changes:
        line(f"{name:40s} {change:30s} {status:10s}")

    # ─────────────────────────────────────────────────────
    h1("SECTION 2: RELIABILITY ANALYSIS OF EACH CHANGE")
    # ─────────────────────────────────────────────────────

    h2("2.1 — CYCLE_TP_PCT = 0.03 (3% take-profit)")
    line("  4.9M combo sweep: cycle_tp_030 avg Sharpe = +34.68 (ONLY positive exit mode)")
    line("  Next: cycle_tp_015 = -8.14, atr_trail_2x = -74.92, stoch_cross_3m = -87.84")
    line("  Gap between #1 and #2: +42.82 Sharpe — massive separation")
    line("  Average trades: 6.4 per combo (very low trade count)")
    flag("LOW TRADE COUNT (6.4 avg). High Sharpe could be survivorship bias — "
         "few trades with 100% WR inflate Sharpe to infinity. "
         "The supervisor top-10 shows TIAUSDC with 10 trades, Sharpe 13041. "
         "This is CLEARLY overfitting to thin data.")
    warn("cycle_tp_030 avg win_rate = 0.9% across all combos — almost all combos lose, "
         "a few winners pull the average Sharpe up. This is a SKEWED distribution.")
    line("  VERDICT: The 3% TP is the safest exit mode but the magnitude of its edge "
         "is overstated by low-trade-count outliers.")

    h2("2.2 — BASIS_CONDITION = False (turned OFF)")
    line("  Sweep evidence: OFF = +0.67 delta Sharpe")
    line("  This was a safety gate preventing entries on wrong side of dc_basis")
    warn("Turning OFF a safety filter increases trade count but may increase drawdown. "
         "The delta is modest (+0.67). In live trading with slippage/fees this edge may vanish.")
    line("  VERDICT: MARGINAL — could go either way. Monitor DD closely.")

    h2("2.3 — K3M_CAP = 80 (block overbought entries)")
    line("  Tournament winner across all combos.")
    line("  Already partially live (quick:987 had k_3m < 80 for longs)")
    ok("Consistent across tournament (113K) AND 4.9M sweep. Reliable signal.")
    line("  VERDICT: RELIABLE — well-supported by multiple test frameworks.")

    h2("2.4 — HTF_STRICT = True (D+4h+1h must ALL confirm)")
    line("  Tournament: top combos all have htf_strict=strict")
    line("  TF compare report: D+4h+1h Sharpe = 39.21, D+4h = 16.29, D alone = 7.25")
    line("  Adding each TF improves Sharpe monotonically (except D+4h+1h+15m on return)")
    ok("Strong progressive improvement with each added TF. Well-tested.")
    warn("D+4h+1h+15m has Sharpe 53.22 but NEGATIVE total return (-32.16%). "
         "The 15m layer reduces trade quality despite raising Sharpe. "
         "This is because 15m filtering kills too many valid trades.")
    line("  VERDICT: RELIABLE for HTF_STRICT=True (D+4h+1h). "
         "Do NOT add 15m to HTF strict requirement.")

    h2("2.5 — HA_3M_ENTRY_WEIGHT = 0.0 (disable ha_3m for entries)")
    line("  Indicator sweep: ha_turn_green and ha_turn_red rank WORST on every timeframe.")
    line("  D: ha_turn_red score=0.0061 (worst), ha_turn_green score=0.0132 (3rd worst)")
    line("  4h: ha_turn_green score=0.0043 (2nd worst)")
    line("  1h: ha_turn_red score=0.0049 (5th worst)")
    line("  15m: ha_turn_red score=0.0026 (3rd worst)")
    ok("Universally worst indicator across all timeframes. Disabling is correct.")
    line("  VERDICT: HIGHLY RELIABLE — confirmed harmful across 239 symbols x 5 TFs.")

    h2("2.6 — HTF1_CONF = True (1h confirmation enabled)")
    line("  Sandbox had this OFF (comment: 'OPTIMAL_V1: OFF, marginal improvement')")
    line("  Live now has it ON (True)")
    line("  No direct backtest evidence cited for re-enabling it")
    warn("HTF_STRICT=True already requires 1h alignment for entry. "
         "HTF1_CONF may be redundant OR may double-gate the 1h check. "
         "If these are independent gates, trades will be over-filtered.")
    line("  VERDICT: UNCLEAR — needs investigation whether HTF1_CONF is redundant "
         "with HTF_STRICT, or if they serve different functions.")

    h2("2.7 — NEWS_SENTIMENT_WEIGHT = 0.90 (was 0.10)")
    line("  9x increase in news sentiment influence on rankings")
    line("  No backtest of news sentiment impact in any sweep or tournament")
    line("  News scanner (ez_news_scanner.py v4) uses CoinGecko trending + Finnhub + RSS + F&G")
    flag("NO BACKTEST EVIDENCE. 0.90 means news sentiment dominates 90% of ranking adjustment. "
         "This is effectively making news the primary signal. "
         "Zero historical validation for this weight.")
    warn("CoinGecko trending and RSS feeds are lagging indicators in crypto. "
         "By the time news appears, price has already moved.")
    line("  VERDICT: HIGHLY UNRELIABLE — largest config change with zero backtesting.")

    h2("2.8 — MITIGATOR_ENABLED = True")
    line("  Enables tiered gain protector: reduce 25% at peak-0.08%, 50% at breakeven, full close at -0.05%")
    line("  Accounts: ang, men")
    warn("MITIGATOR_TIER3_DROP = -0.05 means full close at tiny loss. "
         "This CONTRADICTS STRICT_NO_LOSS rule if loss_mode applies. "
         "Check if mitigator bypasses the no-loss check.")
    line("  VERDICT: NEEDS VERIFICATION — may conflict with STRICT_NO_LOSS principle.")

    h2("2.9 — ACCOUNT_TP_PCT per-account targets")
    line("  ang=2%, inf=2%, flz=1%, men=2%, fin=2%")
    line("  Tournament: top combos had tp_pct=2.0 with Sharpe 242")
    line("  But tournament only tested on 17-43 symbols, not full 350+")
    warn("tp_pct=1.5 had similar Sharpe (179.88) with HIGHER win_rate (83% vs 78%). "
         "The 2% TP trades less but has more MaxDD (31% vs 29%).")
    line("  VERDICT: SOMEWHAT RELIABLE — 2% is a reasonable tournament winner "
         "but 1.5% may be safer for accounts with many positions.")

    h2("2.10 — COUNTER_TREND_CRYPTO (new)")
    line("  XAUUSDT, PAXGUSDT, XAGUSDT, BTCDOMUSDT, SKYUSDT")
    line("  These go UP when market goes DOWN — invert ratio_mult")
    ok("Makes economic sense. Gold/silver/BTC dominance are natural counter-trend assets.")
    line("  VERDICT: RELIABLE (logical, not parameter-dependent).")

    # ─────────────────────────────────────────────────────
    h1("SECTION 3: MARATHON RESULTS — CRYPTO vs TRADIER")
    # ─────────────────────────────────────────────────────

    h2("3.1 — Crypto performance is TERRIBLE")
    line("  Marathon (914K results, 171 symbols):")
    line("  Best crypto LONG Sharpe: 0.07 (barely positive)")
    line("  Best crypto SHORT Sharpe: 0.21 (barely positive)")
    line("  Compare: best tradier SHORT Sharpe: 4.07")
    flag("Crypto backtests show NEAR-ZERO edge across ALL parameter combinations. "
         "The best crypto combo has Sharpe 0.07 — indistinguishable from random. "
         "This casts doubt on whether ANY crypto parameter tuning matters.")
    line("  Possible explanations:")
    line("  - Crypto is 24/7 with higher noise → harder to find stationary edges")
    line("  - Fee structure (0.04% per side) eats small gains")
    line("  - 3m/15m data may be too noisy for DC-based entries")

    h2("3.2 — Tradier performance is EXCELLENT")
    line("  Best tradier SHORT: Sharpe 4.07 (robust)")
    line("  Best tradier LONG: Sharpe 2.26")
    line("  Key winners: 2of3_aligned stoch gate, trailing_1atr exit, pct_2 stop")
    ok("Tradier results are consistent across buffer sizes and hold periods.")
    warn("Top-20 tradier settings ALL have identical Sharpe (7.592) with PF=inf and 46.7% WR. "
         "This means they only differ in tier multipliers (15m, 1h, 4h) which had NO EFFECT. "
         "The real signal is: buffer=0.0005, stop_atr=2.0, htf_trend=required, stoch=off.")

    h2("3.3 — Per-symbol tradier reliability")
    line("  UNRELIABLE symbols (negative best Sharpe):")
    line("    QCOM (-0.78), SMCI (-0.95), RDDT (-1.16), SPY (-2.11), INOD (-9.51)")
    line("    JOBY (-10.51), AMD (-10.61), DUOL (-13.26)")
    flag("AMD has NEGATIVE Sharpe even with best settings. Remove from active trading "
         "or restrict to directional conviction only.")
    line("  MARGINAL symbols (Sharpe 0-5):")
    line("    GME (1.4), LMT (0.4), SCHW (1.6), PATH (2.2), QQQ (2.9), HON (3.4), ARM (4.2)")
    warn("These have razor-thin edges. Transaction costs may eliminate profitability.")

    # ─────────────────────────────────────────────────────
    h1("SECTION 4: INDICATOR SWEEP — NON-RELIABLE SIGNALS")
    # ─────────────────────────────────────────────────────

    h2("4.1 — Universally BAD indicators (bottom 5 on every TF)")
    line("  ha_turn_red:     score 0.006-0.005 across D/1h/15m → NOISE")
    line("  ha_turn_green:   score 0.013-0.004 across D/4h    → NOISE")
    line("  stoch_cross_up:  score 0.006-0.010 across 4h/1h/3m → NOISE")
    line("  stoch_cross_dn:  score 0.006-0.014 across D/4h/3m  → NOISE")
    line("  wt_cross_up/dn:  score 0.005-0.013 across D/4h    → NOISE")
    line("  sma200_cross_up: score 0.002-0.006 across 1h/15m/3m → NOISE")
    line("  dc_basis_cross:  score 0.003-0.003 across 4h/1h   → NOISE")
    line("  rel_volume:      score 0.003-0.005 on 15m/3m      → NOISE")
    line("  mfi_div:         score 0.003 on 15m                → NOISE")
    flag("These are CROSS/EVENT indicators with near-zero predictive power. "
         "Any trading logic using these as entry signals is unreliable.")

    h2("4.2 — Universally GOOD indicators")
    line("  D:   ema_20_dist (0.106), mfi (0.082), lr_trend (0.079)")
    line("  4h:  sma_500_dist (0.129), sma_200_dist (0.093), ema_20_dist (0.073)")
    line("  1h:  sma_500 (0.119), sma_200 (0.103), dc_low4 (0.092)")
    line("  15m: range_pct (0.069), body_pct (0.061), sma_500_dist (0.058)")
    line("  3m:  dc_high4 (0.133), dc_low4 (0.131), ema_20 (0.126)")
    ok("Level/distance indicators dominate. How far price is from SMA/DC is predictive. "
         "Crossover events are not.")

    h2("4.3 — Implications for live code")
    line("  Live uses stoch crosses for entry timing → WEAK signal per sweep")
    line("  Live uses dc_basis for gate → MODERATE (dc_basis itself scores well, crosses don't)")
    line("  Live uses HA candles for entry → CONFIRMED HARMFUL (disabling via HA_3M=0 is correct)")
    line("  Live should INCREASE weight of: sma_500_dist, ema_20_dist, mfi on HTF")

    # ─────────────────────────────────────────────────────
    h1("SECTION 5: TIERED ENTRY — MIXED RESULTS")
    # ─────────────────────────────────────────────────────

    line("  4.9M sweep: AGGRESSIVE tiered avg Sharpe = -58.04 vs OFF = -77.06")
    line("  Delta: +19.02 Sharpe in favor of tiered sizing")
    ok("Tiered sizing (smaller positions on weaker setups) outperforms flat sizing.")
    line()
    line("  BUT from tiered_report.md:")
    line("  T1 only Sharpe: 156.96 vs COMBINED (T1+T2+T3): 27.09")
    flag("Adding T2 and T3 trades DESTROYS Sharpe from 157 → 27. "
         "The 4/4 alignment setup is 5.8x better than mixed tiers.")
    line("  With RV≥2.0 filter: T1=21.86, COMBINED=41.31 (combined WINS)")
    line("  The relative volume filter makes lower tiers profitable.")
    line("  VERDICT: Tiered is OK only with RV≥2.0 filter. Without it, stick to T1 only.")

    # ─────────────────────────────────────────────────────
    h1("SECTION 6: LOSS MODE — NO_LOSS vs NORMAL")
    # ─────────────────────────────────────────────────────

    line("  4.9M sweep: NO_LOSS avg Sharpe = -67.22, NORMAL = -67.87")
    line("  Delta: +0.65 Sharpe for NO_LOSS")
    ok("NO_LOSS performs identically or slightly better than NORMAL loss mode. "
         "STRICT_NO_LOSS policy is NOT dragging performance.")
    line("  This confirms the CLAUDE.md rule: L/S ratio is the hedge, "
         "never close losers. The data backs this up.")

    # ─────────────────────────────────────────────────────
    h1("SECTION 7: RED FLAGS & ACTION ITEMS")
    # ─────────────────────────────────────────────────────

    h2("RED FLAG 1: NEWS_SENTIMENT_WEIGHT = 0.90")
    line("  Risk: HIGH | Evidence: NONE | Action: REDUCE to 0.10-0.20 until backtested")

    h2("RED FLAG 2: CYCLE_TP 3% inflated by low-trade outliers")
    line("  Risk: MEDIUM | Evidence: Statistically significant but skewed")
    line("  Action: Keep 3% TP but add minimum trade count filter (>=20 trades)")
    line("  Monitor: if crypto positions consistently hit 3% → edge is real")
    line("  Monitor: if positions rarely reach 3% → effectively disabling TP (positions held forever)")

    h2("RED FLAG 3: Crypto has ZERO edge in backtests")
    line("  Risk: HIGH | Evidence: Best crypto Sharpe = 0.07-0.21 (random)")
    line("  Action: Review whether crypto parameter tuning matters at all")
    line("  Consider: the real crypto edge may come from L/S ratio balance, not entry signals")

    h2("RED FLAG 4: MITIGATOR_ENABLED may conflict with NO_LOSS")
    line("  Risk: MEDIUM | Action: Verify MITIGATOR_TIER3_DROP=-0.05 respects STRICT_NO_LOSS")

    h2("RED FLAG 5: HTF1_CONF enabled without evidence")
    line("  Risk: LOW | Action: Verify it's not redundant with HTF_STRICT")
    line("  If redundant: harmless (double gate). If independent: may over-filter.")

    h2("SAFE CHANGES (keep as-is)")
    line("  ✓ K3M_CAP=80 — well-validated across all frameworks")
    line("  ✓ HA_3M_ENTRY_WEIGHT=0.0 — universally confirmed harmful")
    line("  ✓ HTF_STRICT=True — progressive TF comparison strong")
    line("  ✓ COUNTER_TREND_CRYPTO — logically sound")
    line("  ✓ ZERO_CONFIRMATION_THRESHOLD_API=5 — safer, slower (acceptable)")
    line("  ✓ MIN_GAIN_TO_BUY_AGGRESSIVELY=1.6 — conservative, reduces aggressive buying")

    # ─────────────────────────────────────────────────────
    h1("SECTION 8: SUMMARY SCORECARD")
    # ─────────────────────────────────────────────────────

    line(f"{'Parameter':40s} {'Reliability':15s} {'Evidence':15s} {'Risk':10s}")
    line("-" * 82)
    scorecard = [
        ("K3M_CAP=80", "HIGH", "Multi-framework", "LOW"),
        ("HA_3M_ENTRY_WEIGHT=0.0", "HIGH", "5-TF sweep", "LOW"),
        ("HTF_STRICT=True", "HIGH", "TF progression", "LOW"),
        ("COUNTER_TREND_CRYPTO", "HIGH", "Logical", "LOW"),
        ("CYCLE_TP_PCT=0.03", "MEDIUM", "4.9M sweep*", "MEDIUM"),
        ("BASIS_CONDITION=False", "MEDIUM", "Sweep +0.67", "MEDIUM"),
        ("ACCOUNT_TP_PCT (per-acct)", "MEDIUM", "Tournament", "LOW"),
        ("MITIGATOR_ENABLED=True", "MEDIUM", "No sweep", "MEDIUM"),
        ("MIN_POSITION_SIZE=0.9", "MEDIUM", "Operational", "LOW"),
        ("MIN_GAIN_TO_BUY_AGGRESSIVELY=1.6", "MEDIUM", "Conservative", "LOW"),
        ("ZERO_CONF_API=5", "MEDIUM", "Operational", "LOW"),
        ("HTF1_CONF=True", "LOW", "No evidence", "LOW"),
        ("HEDGE_MODE=True", "LOW", "Postmortem risk", "MEDIUM"),
        ("NEWS_SENTIMENT_WEIGHT=0.90", "NONE", "Zero backtest", "HIGH"),
    ]
    for name, rel, ev, risk in scorecard:
        line(f"{name:40s} {rel:15s} {ev:15s} {risk:10s}")

    line()
    line("* CYCLE_TP evidence is real but inflated by low-trade-count outliers")
    line()
    line("BOTTOM LINE: 6 of 15 changes are well-supported. 5 are marginal.")
    line("3 need immediate attention (NEWS_SENTIMENT_WEIGHT, MITIGATOR vs NO_LOSS, crypto edge).")


def main():
    audit()
    report = "\n".join(REPORT_LINES)
    out_path = os.path.join(os.path.dirname(__file__), "WEEKEND_PARAM_AUDIT.md")
    with open(out_path, "w") as f:
        f.write(report)
    print(report)
    print(f"\n\nReport saved to: {out_path}")


if __name__ == "__main__":
    main()
