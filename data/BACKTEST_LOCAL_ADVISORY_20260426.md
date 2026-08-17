# Local Advisory Generator — Backtest 20260426

**Generated:** 2026-04-26T23:36:10+00:00
**Scope:** 84/84 fin+ang symbols, 17.9d klines (~0.0491yr)

## CLAUDE.md compliance
- ≥48 syms × >1yr publication floor: **VIOLATED** (84 syms × 17.9d). Directional only.
- Sharpe per-trade only, no sqrt(N) annualization
- Open positions at end-of-test marked-to-market (exit_reason='EOT_MTM')
- Per-symbol Sharpe capped at +/-5.0, syms with <30 trades excluded from sym_sharpe

## ADVISORY (force_open + force_close + hold)
`pool_sharpe=-0.1163 | avg_gain_trade=-0.1113%/trade | gain_per_yr=-95.2%/yr | gain_sym_yr=-1.1333%/sym/yr | trades=42 | dd=7.19%`
- sym_sharpe (qualified syms=0): 0.0
- hit_rate: 40.48%
- accumulated gain: -4.68%

## BASELINE (random fixed-hold every 4h, no advisory rules)
`pool_sharpe=-0.0386 | avg_gain_trade=-0.1178%/trade | gain_per_yr=-32943.42%/yr | gain_sym_yr=-392.1836%/sym/yr | trades=13731 | dd=100.0%`
- sym_sharpe (qualified syms=83): -0.0712
- hit_rate: 45.52%
- accumulated gain: -1617.86%

## DELTA (advisory − baseline)
- delta_pool_sharpe: -0.0777
- delta_avg_gain_trade: 0.0065%
- delta_hit_rate: -5.04pp
- delta_max_dd_pct: -92.81pp

## VERDICT
- **TRASH per CLAUDE.md feedback_sharpe_2_baseline.md** — advisory pool_sharpe < 1.0.
- **TRASH baseline per CLAUDE.md** — baseline pool_sharpe < 2.0; comparison is on a weak baseline.
- Sample size below 48-sym × 1-yr publication floor — NOT publishable, directional-only.