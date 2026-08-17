# Local Advisory Generator — Backtest 20260427

**Generated:** 2026-04-27T02:53:09+00:00
**Scope:** 101/101 fin+ang symbols, 18.1d klines (~0.0495yr)

## CLAUDE.md compliance
- ≥48 syms × >1yr publication floor: **VIOLATED** (101 syms × 18.1d). Directional only.
- Sharpe per-trade only, no sqrt(N) annualization
- Open positions at end-of-test marked-to-market (exit_reason='EOT_MTM')
- Per-symbol Sharpe capped at +/-5.0, syms with <30 trades excluded from sym_sharpe

## ADVISORY (force_open + force_close + hold)
`pool_sharpe=-0.1152 | avg_gain_trade=-0.0988%/trade | gain_per_yr=-107.94%/yr | gain_sym_yr=-1.0687%/sym/yr | trades=54 | dd=9.77%`
- sym_sharpe (qualified syms=0): 0.0
- hit_rate: 38.89%
- accumulated gain: -5.34%

## BASELINE (random fixed-hold every 4h, no advisory rules)
`pool_sharpe=-0.04 | avg_gain_trade=-0.1171%/trade | gain_per_yr=-39702.31%/yr | gain_sym_yr=-393.0921%/sym/yr | trades=16772 | dd=100.0%`
- sym_sharpe (qualified syms=100): -0.0759
- hit_rate: 45.29%
- accumulated gain: -1963.38%

## DELTA (advisory − baseline)
- delta_pool_sharpe: -0.0752
- delta_avg_gain_trade: 0.0183%
- delta_hit_rate: -6.4pp
- delta_max_dd_pct: -90.23pp

## VERDICT
- **TRASH per CLAUDE.md feedback_sharpe_2_baseline.md** — advisory pool_sharpe < 1.0.
- **TRASH baseline per CLAUDE.md** — baseline pool_sharpe < 2.0; comparison is on a weak baseline.
- Sample size below 48-sym × 1-yr publication floor — NOT publishable, directional-only.