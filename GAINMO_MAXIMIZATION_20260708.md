# GAIN/MO MAXIMIZATION — Analysis & Suggestions (2026-07-08)

Synthesis of: central DB `test_results_central.db` (429,872 crypto + ~330k tradier rows, deduped),
`switch_priority.csv`, `ofat_report.md`, config.py + config_tradier.py audits, per_sym pipeline audit,
100.md Parts 1–16, and ~35 memory topic files. All Sharpe = pool_sharpe (per-trade, pooled). Tier-1 vec
numbers are labeled as such and are shortlist-grade only.

---

## 0. State of the evidence

- **No verified at-floor baseline exists for either system today.** OFAT coverage = 0/4335 (crypto) and
  0/3432 (tradier) cells; every tradier Tier-2 run since 2026-06-21 died rc=-9 (OOM/timeout); crypto
  `__baseline__` rows are n_syms=2 junk. Honest anchors: tradier faithful **pool_sharpe=−0.0696**
  (292 syms × 2.48yr, Tier-2, 2026-06-25); crypto ≈ −0.05 (Bible); historical tradier recipe
  **0.5823** (114 syms, WT_DC=45 + GR_MIN_IND=5 + GR_HTF_MIN_TFS=3); S2 iter41 0.6571 @ 114 syms
  is **[UNVERIFIED]**.
- **The churn law is the single strongest empirical fact in the DB** (both systems):

  Crypto (deduped runs, n_syms≥12, years>0.5):
  | trades/sym/yr | median pool_sharpe | median gain_per_yr |
  |---|---|---|
  | 30–100 | **1.134** | 379.8% |
  | 100–300 | 0.053 | 7,431% (fee-uncosted vec artifact) |
  | 1,000+ | 0.015–0.017 | ~7,450% (artifact) |

  Tradier (n_syms≥20, years>0.5, |gain|<10,000):
  | trades/sym/yr | median pool_sharpe | median gain_per_yr |
  |---|---|---|
  | 50–100 | 0.713 | 47.5% |
  | 100–200 | 0.535 | 53.8% |
  | 500–1000 | 0.229 | 171% |
  | 1000+ | 0.180 | 238% |

  Crypto: the 30–100 band is the ONLY pool_sharpe>1 regime. Tradier: monotonic trade-off; practical
  knee ≈ 100–200 trades/sym/yr.

- **Crypto frontier is bimodal.** Latest combo validator (57 syms × 1.33yr, Tier-1 vec): nothing between
  pool_sharpe 0.34 and 1.01. The pool_sharpe>1 family is exactly one recipe:
  `L_dc_x4h + {k15|mfi15|dcpos15}_lt50-60 + rsi1h_lt40 + wt_4h, h64–128` →
  **pool_sharpe 1.01–1.14 | gain_per_yr 320–384% (~27–32%/mo) | ~4,000–4,200 trades | 57 syms | 1.33yr
  [Tier-1 vec — needs Tier-2]**. `rsi1h_lt40` appears in all 5 winners and no churn combo.
- **Tradier frontier** (34 syms × 2.63yr, Tier-1 vec, 2026-07-03): sharpe corner =
  `bb15_lt30`-anchored dip entries at h8 (pool_sharpe 0.50–0.73 @ 45–70%/yr); gain corner =
  `above_sma5pct+sma200up_D` trend combos at h32–64 (400–730%/yr @ pool_sharpe 0.12–0.15).
  **No config has ever shown pool_sharpe≥0.5 at n_syms≥100 in the entire DB.**
- Switch-testing program: only 6 of ~2,800 switches have any measured delta; the only material one is
  `R1_DC_LOW4_3M_EMERGENCY_ENABLED=False → Δ+0.0649` (16-sym diagnostic; conflicts with the 05-29 user
  mandate). The entire ABLATION_DISABLE_* matrix (which strategy actually pays) has never run.

---

## 1. DO-FIRST — unblock measurement (nothing below can be *proven* until these)

1. **Fix tradier Tier-2 rc=-9** (OOM/timeout kills every ≥100-sym run since Jun 21): batch symbols
   (proven pattern from the 2026-06-10 OOM fix), lower worker RAM, or shard+merge. This is THE blocking
   issue for stocks.
2. **Re-establish honest at-floor baselines** (48+ crypto, 100+ stocks, current live config) so every
   suggestion below has a real denominator. OFAT runner is dead (runner_alive=0) — restart it under the
   global test lock.
3. **Repair result plumbing**: central DB double-ingestion (every file loaded under 2 path prefixes) and
   the broken `symbol_results.run_id` join; dedupe + backfill run_id at ingest.
4. **Per_sym last-mile bug**: Mac `data/hourly_reconfig/{flz,fin,inf}/active_config.json` are ~26 days
   stale (only `per_sym_active_config.json` is rsynced S1→Mac). Add those dirs to the sync or stop
   S1-side writers pretending they reach live.

## 2. CRYPTO — baseline gain/mo

1. **Tier-2-validate the winning combo family** (`dc_x4h + 15m-pullback<50-60 + rsi1h_lt40 + wt_4h`,
   h64–128). If it holds even half its Tier-1 number at 48+ syms, it is the best documented path to
   >20%/mo at pool_sharpe>0.5. Cheapest sub-test: wire **RSI_1h<40 as an entry gate** on the existing
   baseline and A/B it — it is the single discriminating term.
2. **Engineer the trade count into the 30–100/sym/yr band.** Current OFAT-era baselines sit ~900–1,200
   trades/sym/yr. Concrete config levers (all currently churn-positive, from the config audit):
   - Three zero cooldowns: `REENTRY_COOLDOWN_S=0`, `REENTRY_MIN_GAP_MINUTES=0`,
     `SCALP_V3_REENTRY_COOLDOWN_S=0`; `EZ_REENTRY_PRICE_CROSS_PCT=0.0` (no price improvement) and
     20 fires/tick.
   - `REENTRY_CHURN_GUARD_ENABLED=False` (the purpose-built guard, never relocated to the queue consumer).
   - `TRADES_PER_SYM_PER_DAY_MAX=50` (loosened from 8 for a bug tradier already fixed properly).
   - 8 concurrently-enabled force-openers (WT_3M_FORCE_OPEN, MOMENTUM_SMA_WATCHDOG, OBLIGATORY_SMA200,
     WATCHDOG_DC, WT3M_ESCALATE ladder, BREAKOUT_DC1H bypass, GOLDEN_RULE, TRADEABLE_KEYS_MANDATORY) —
     several user-mandated "never disable", so tune their *parameters* (rate caps, HH-cross requirement,
     cooldowns) rather than masters, or get explicit sign-off per the LOCKED_FILES 06-25 held list.
   - `MAX_AUGMENTS_PER_POSITION=999999` + `REENTRY_MANDATORY=True` + escalate ladder = compounding churn.
   - **Add a live min-hold**: the strongest anti-churn lever exists only in the vec engine
     (`V8Q_MIN_HOLD_BARS=250`); live has no counterpart. A/B a modest live min-hold (e.g. 1–4h,
     technical-exit-exempt) at Tier-2.
3. **Symbol book (per_sym gate, config-independent)**: exclude the proven drag set — ZECUSDC, LRCUSDT,
   RSRUSDT, CHRUSDT, SANDUSDT, THETAUSDT, 1000BONKUSDC, DOGEUSDC, ATOMUSDT, COMPUSDT, BTCDOMUSDT,
   TRXUSDT (median negative across 300–440 runs each; BTCDOM corroborates the 05-26 systemic-killer
   finding). Overweight the consistent winners: SKL, NKN, COTI, CELR, SXP, BAT, STORJ, RVN, KNC, YFI,
   ZEN, GTC (median sym_sharpe 0.41–0.56, 76–100% of runs positive — mid-cap legacy USDT alts, not majors).
4. **Run the ABLATION_DISABLE_* matrix** (never tested, all priority-100): one Tier-2 arm per strategy
   family answers "which opener/augmenter actually pays" — directly the gain/mo question.
5. **Apply the mandate fix**: `RATIO_MULTIPLIER` is 4.0 in config.py vs STATE-OF-AFFAIRS 3.0 (justified
   by a 12-sym sub-floor test) — revert or re-prove at floor.
6. **Old measured-but-never-applied levers worth one Tier-2 arm each**: momentum gate on DC-4h entries
   (+30% sharpe, −31% trades in its own test), RSI-50-line SHORT entry (PF 4.64 / WR 76% pre-QA),
   B12/B14 re-ablation with B16 strict.

## 3. TRADIER — baseline gain/mo

1. **Resolve the uncommitted-config limbo (highest-leverage single action).** The proven 0.58-recipe
   values (WT_DC=45, GR_MIN_IND=5, GR_HTF_MIN_TFS=3) exist ONLY as uncommitted edits — HEAD still holds
   the disproven flood values (20/2/1, the pool_sharpe-0.10 config). But the same working tree carries
   ~10 unexplained risk-loosening flips that must be triaged hunk-by-hunk per the 07-07 pattern before
   any commit/deploy: `TRADIER_MIN_HOLD_MINUTES` 4320→42 (100× cut of the 72h rule),
   `NOLOSS_BB1H_GATE_ENABLED` False→True (loss-close outside the R1/R2/HEDGE_FAILED trio, bypasses
   min-hold), `DC_LOW4/DC_LOW_STOP_ENABLED` False→True (armed stop-losses in a no-stop-loss system,
   also silently changes the backtest baseline), `ALL_TF_AGAINST_CLOSE_MIN_TFS` 5→2,
   `WRONG_SIDE_ABS_KILL=True` (its own header says a 114-sym sweep found Sharpe −2.03).
2. **Promote MINERVINI and CLENOW from paper (trc) to real money (trb)** once the running live A/B
   confirms: they are the two biggest validated gain/mo adders (+0.4806 pool_sharpe / +25.2%/mo and
   +0.1815 / +8.5%/mo, 2026-06-02 faithful vec, 33–37 syms [DIAGNOSTIC <100]) yet
   `MINERVINI_ENABLED=False`/`CLENOW_ENABLED=False` on trb — the $70k account isn't getting the edge.
   Precondition: a floor-size (100+ sym) Tier-2 confirm after fix §1.1.
3. **Decide the sharpe/gain operating point explicitly.** The documented tension: strict gates =
   pool_sharpe 0.58 @ ~8.7%/mo vs loose gates = 0.10–0.24 @ ~14–15%/mo (2026-06-03/26 tests). The churn
   table says the knee is ~100–200 trades/sym/yr (pool_sharpe ~0.54 @ ~54%/yr in-DB medians). Target the
   knee, not either extreme; the GR grid's sharpe-preserving zone (Tier-2, 20 syms, DIAGNOSTIC) was
   MIN_IND 3–4 × TFS 2–3 with ind≥6 collapsing — reconcile that with the 5/3 recipe at floor.
4. **Tier-2-validate the two vec frontier families at 100+ syms**: `bb15_lt30` dip-entry h8 combos
   (pool_sharpe 0.50–0.73 @ 45–70%/yr, 34 syms Tier-1) as the sharpe anchor; `above_sma5pct+sma200up_D`
   h32–64 (400–730%/yr @ 0.12–0.15) as the gain overlay. The blend (dip entries + trend filter) is the
   obvious first composite arm.
5. **Wire conviction sizing on stocks** (cap 40×: sharpe +34%, gain/DD 69→207 in the 06-01 money-metric
   study) — computed but has NO live consumer. Default-OFF switch + paper A/B first.
6. **Per-symbol book**: overweight miners/nuclear/momentum-semis — SNDK, AEM, MU, ZIM, WMT (best
   gain+sharpe combos), OKLO, SMR, CDE, AG, HL, PAAS, PLTR, AVGO, UUUU, GDX*(gain only)*; exclude
   FDX, ALB, COIN, MRVL, CHRD, MSFT*(gain-poor)*, COP, UBER, NKE, DVN, TTD, DIS, and the ETH stock-list
   leak. (symbol_results medians over 100–738 runs each.)
7. **R1 on stocks**: the only measured switch delta in the whole audit is R1=False → Δ+0.0649, AND the
   07-08 memory shows stocks-R1 is structurally non-functional anyway (31,429 fires → 6 submitted).
   Either fix the queue_trade_action path and A/B it honestly, or disable it deliberately — the current
   state is the worst of both.
8. **MTF Phase E–K winner** (sym_sharpe +0.29 both sides, 293 stocks × 2.13yr, at-floor) was never wired
   live — a validated, floor-size, unapplied entry-filter improvement.

## 4. PER_SYM — both systems

1. **Respect phase order (Bible §7)**: the whole S1 grinder (FOTEST campaign, reopt loop, vec pass,
   hourly reconfigs) is doing Phase-2 per-key tuning on a NEGATIVE cross-sym baseline — it optimizes
   noise. Until Phase-1 pool_sharpe>0 at floor: keep per_sym for **gating and sizing only** (what
   `persym_final_book.json` does well), pause per-key param tuning.
2. **Switch the winner-pick objective to gain/mo under constraints** — the user's stated goal, and a
   one-line sort-key change at each site (engines already emit `gain_per_yr`):
   `per_sym_7d_agent.py:316`, `per_sym_20d_agent_stocks.py:311`, `tradier_hourly_reconfig.py:291`,
   `reopt_loop.py:537`. New objective: `max(gain_per_mo)` s.t. `pool_sharpe>0` (or >0.25) AND
   `trades≥30` AND beats b&h — i.e. the `per_sym_trb_profiles.py` pattern, and the exact formula already
   canonical in `apply_gainmo_gate_and_mult.py` (`acc_gain_pct/(years*12)`, median over DB rows).
3. **Make Tier-2 the only gate-writer.** Vec writers still stamp live gates despite the proven vec lie
   (89% of overrides no-op); the reopt "correction pass" exists only to undo vec's wrong disables. Keep
   vec as shortlist, never as a live gate.
4. **Raise the joke floors**: `tradier_hourly_reconfig.py` MIN_TRADES_FOR_OPINION=**2**; 7d windows with
   ~6-trade opinions; FOTEST baselines are 5-week n_syms=1. Floor every live-feeding decision at
   trades≥30 minimum and label everything else [DIAGNOSTIC].
5. **Restore the staging path**: `_pending_per_sym_active_config.json` is empty because campaign/reopt/vec
   write the live config directly — the promote-gate + human-review mandate is bypassed. Route writers
   through pending → `persym_floor_gate.py` → promote.
6. **Overfit control**: ~40–100 override params per (sym,side) against a 30-trade floor guarantees
   curve-fit. Cap per-key overrides to the handful with measured cross-sym effect (entry threshold,
   side-enable, size_mult) — seek plateaus, not per-key peaks.
7. Verify the 2026-07-06 crypto/stocks contamination fix is holding (it is per the audit: rate_filter
   routes tradier to `per_sym_active_config_stocks.json`) and that the crypto file actually contains
   crypto keys again.

## 5. Interpretation traps (keep enforcing)

Tier-1 vec = shortlist feel only (~85% parity; doesn't model force-open/R1/daemon churn — churn A/Bs
MUST be Tier-2). All-identical sweep rows = unwired knob, never "param doesn't matter". Gains must be
per-trade sums (compounded-leverage per_sym gains were lies). Sub-floor/single-sym results never
promote. /history/ ledger omits fills+commission — true PnL = exchange income. Central DB "what was
tested" only via raw_json until the join is fixed. Pre-2026-04-30 absolute Sharpe values are suspect
(deltas mostly valid).

---

*Sources: S1 test_results_central.db + ofat_report.md + switch_priority.csv (2026-07-08 snapshots);
config.py/config_tradier.py working trees (both LOCKED, read-only); per_sym pipeline audit; 100.md;
memory files. No code, config, or server state was modified.*
