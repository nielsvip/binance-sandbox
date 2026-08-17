# Causal vector lifecycle priority results — 2026-08-01

These are research receipts from the canonical V7 Tradier NPZ bundle on S1.
They do not overwrite scalar cells, exact V8 cells, or live configuration.
Every run used zero stock commission, 5 bps one-way slippage, a $2,000 B&H
benchmark unit, and at most $16,000 strategy capacity.

The corrected runner forbids an exit and reentry on the same bar and forbids
the exit bar's high/low from arming a reclaim. Higher-timeframe future-source
counts are zero. Different recipes that collapsed to the same observable
behavior were quarantined; workbook adapters retain one least-inactive recipe
per behavior.

| key | gain/mo | B&H/mo | x B&H | delta/mo | account DD | TIM | closes | evaluated | logical | unique behavior | quarantined |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| MU_LONG | 178.1961% | 20.3937% | 8.738x | +157.8024 | 87.90% | 99.39% | 10 | 8,874 | 8,826 | 598 | 8,228 |
| NVDA_LONG | 38.2744% | 4.2384% | 9.030x | +34.0360 | 40.56% | 99.84% | 15 | 8,658 | 8,610 | 787 | 7,823 |
| VT_LONG | 13.9232% | 1.4901% | 9.344x | +12.4331 | 19.90% | 99.74% | 7 | 8,874 | 8,826 | 267 | 8,559 |
| TTD_SHORT (Pareto) | 30.2929% | 2.8390% | 10.670x | +27.4539 | 70.23% | 63.44% | 148 | 43,314 | 43,218 | 5,449 | 37,769 |
| ACN_SHORT | 19.9646% | 1.8392% | 10.855x | +18.1254 | 46.99% | 98.78% | 98 | 44,130 | 44,034 | 1,759 | 42,275 |
| LAC_SHORT | 20.6019% | 2.1067% | 9.779x | +18.4952 | 40.02% | 37.69% | 95 | 43,304 | 43,208 | 1,143 | 42,065 |

## Producing recipes

- **MU_LONG** — DC 5m N40/B10/R0 entry+augment; WT/DC exit
  threshold 45, min conditions 5, K75, DC0.8; WT 15m delta8/P30
  reentry; entry units 6, augment units 1.
- **NVDA_LONG** — DC 5m N5/B10/R0 entry+augment; WT full exit on D,
  delta8/P10; DC 5m N5/B10/R0 reentry; entry units 4, augment units 2.
  The canonical adapter removes a configured reduce when it produces zero
  reductions.
- **VT_LONG** — DC 1h N5/B10/R10 entry; WT 5m delta3/P30 augment;
  D Donchian opposite-break N30 exit; DC 15m N5/B100/R0 reentry; entry
  units 6, augment units 2.
- **TTD_SHORT Pareto** — DC 5m N5/B10/R10 entry; WT 5m delta0/P10
  augment; WT full exit on 15m delta16/P75; no reduce; mandatory reclaim;
  entry and augment units 1. Recipe `lcb-62735c3111cdee4cc8a2`. This row was
  selected over the raw maximum-gain row because it has 70.23% rather than
  92.36% account drawdown and also meets the 50–80% TIM target.
- **ACN_SHORT** — DC 5m N20/B10/R40 entry; WT 15m delta3/P30 augment;
  4h Donchian opposite-break N30 exit; WT 4h delta0/P10 25% reduce;
  WT 15m delta8/P30 reentry; entry units 8, augment units 2.
- **LAC_SHORT** — DC 15m N10/B100/R0 entry; WT 15m delta16/P0 augment;
  WT/DC exit threshold60, min conditions4, K85, DC0.8; no reduce;
  mandatory reclaim; entry units 8, augment units 2.

## Data and evidence boundaries

- Canonical NPZ directory:
  `data/matrix_npz/canonical_v7_20260801_pilots` on S1.
- All seven rebuilt pilot NPZs passed the ladder data contract with zero
  future HTF timestamps.
- MU/NVDA/TTD/ACN/LAC disclose 84.5–91.8% bounded 15m-derived 5m execution
  bars over the full window. This is expected historical interpolation.
- VT discloses one 69.5-day source gap. MSTR begins in March 2026 and is
  handled by its separate campaign.
- Source runner SHA-256:
  `de20ce46dfefc4364cd170078aa51c16bc81840c38f0ba09cfc8eec7f0444a98`.
- Matrix and exact-engine completion credit remain false. Port 5077 may show
  these only in its separate causal vector table; exact chart trade markers
  remain exact-ledger-only.
