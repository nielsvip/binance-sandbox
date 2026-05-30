# 🔒 BASELINE_LOCK — HTF-regime strategy baselines + per_sym configs (2026-05-30)
**Future changes MUST NOT silently destroy these. Read before editing the cores, the per_sym config, or the live wiring.**

## What is locked
The strategy = two parity-proven vectorized cores, used IDENTICALLY by backtest, per_sym, and live:
- `vec_decisions/htf_regime_scale.py` — staircase LONG (Rule A %-ladder + winners-bonus, Rule B struct exit, expansion gate).
- `vec_decisions/short_elevator.py` — elevator SHORT (vol-expansion breakdown + rally-fade, fast cover, no avg-down).
- `vec_decisions/htf_regime_decision.py` — the TANDEM glue (one decision path for all three consumers).

**SYNCHRONICITY GUARANTEE (proven 2026-05-30):** live calls `desired_weight_vec`/`short_weight_vec` on a trailing
~600-bar window each tick; windowed-vec == full-history vec to **0/200 bars**. So live == backtest == per_sym BY
CONSTRUCTION. NEVER reimplement these in scalar/hand-rolled live code — that reintroduces drift (measured 0.4%).
Any change to a core MUST keep windowed==full parity (tools test: `_parity_window.py`).

## The 4 all-sym 4yr baselines (NET, no-lookahead) — the floor every change must not drop below
| pool | n | baseline pool_sharpe / gain | per_sym-optimized pool_sharpe / gain |
|---|---|---|---|
| crypto LONG | 50 | 0.111 / 41% | 0.197 / 153% |
| stocks LONG | 28 | 0.063 / 10% | 0.196 / 192% |
| crypto SHORT | 33 | 0.518 / 66% | 0.518 / 73% |
| stocks SHORT | 39 | 0.475 / 16% | 0.475 / 25% |
Snapshot: `data/_diagnostic/persym_optimized.json` (per-key baseline+best), `PERSYM_OPT_PROGRESS.txt`.

## Per_sym configs (the live source of truth)
`data/_diagnostic/htf_regime_persym.json` — 150 keys (78 LONG + 72 SHORT), each with its winning variant + knobs:
LONG: pct_entry, per_pct_mult, size_cap, gain_bonus_k (winners-bonus), struct_exit_tf, expansion. SHORT: regime_tf,
vol_k, rsi_cover, size_cap, struct_exit_tf. Universe = `tradeable_keys.json` + `symbols_trb_long/short` (NOT the
dynamic per-account lists). Regenerate with `tools/persym_optimize.py` (establishes baselines THEN per_sym best).

## RULES so a future change can never destroy the baselines
1. **NEVER hand-write scalar live logic for this strategy.** Live = the vec cores on a rolling window. Drift = banned.
2. **Any edit to a core** → re-run `tools/persym_optimize.py` + the windowed-vec parity test. If any pool's median
   pool_sharpe drops below the baseline above, the change is REJECTED (it destroyed a baseline).
3. **per_sym configs are gated**: a key trades under this strategy ONLY if it's in `htf_regime_persym.json` AND
   `config.HTF_REGIME_ENABLED=True`. Removing a key from the config = that key stops (safe). The master switch
   defaults OFF — flipping it on is the only go-live action.
4. **NO martingale, ever.** Rule A scales into WINNERS (gain-since-entry bonus), never adds to losers. Rule B exits
   on lower-low+lower-high. Short never averages down.
5. **maxDD cap 60%** is baked into the per_sym selection — never relax it without a DD re-check.
6. **Mean-reversion / range_mode is OFF** (tested 4 ways, loses). Do not enable without new proof.
7. **Backups before any core/config edit** (per CLAUDE.md). Backup of the pre-strategy live system:
   `backups/PRE_HTF_REGIME_GOLIVE_20260530_060853_UTC/`.

## Live wiring (gated, default-OFF)
config.py / config_tradier.py: `HTF_REGIME_ENABLED=False` (master kill) + `HTF_REGIME_LEDGER_PATH`. Live reads the
per_sym config, computes target weight via the glue on a rolling window, routes size changes through execute_now.
SCALP_REDUCE_ENABLED=False (stops the legacy profit-protect reduces that cut winners). Flip HTF_REGIME_ENABLED=True
+ restart to go live; set False to instantly revert to the prior system.
