# TIERED ALIGNMENT BACKTEST REPORT
Generated: 2026-03-14 20:03 UTC
Stoch: K=9 SK=5 SD=5 | Base TP=1.5%
Tier 1 (4/4 D+4h+1h aligned): 100% size, 1.5% TP
Tier 2 (3/4 D+4h aligned):     50% size, 1.05% TP
Tier 3 (2/4 D only):            25% size, 0.75% TP

## Aggregate: Tier Performance by RelVol Filter

| RV Filter | Tier                   |   Sharpe |    WR% |     PF |  MaxDD% |    Ret% |  Trades |  Syms |
|-----------|------------------------|----------|--------|--------|---------|---------|---------|-------|
| no filter | T1_D+4h+1h+15m         |  156.959 |  82.8% |  19.31 |  31.16% |  13.36% |      43 |    11 |
| no filter | T2_D+4h+15m            |  108.013 |  86.0% |  16.20 |  62.68% | -24.09% |      83 |    40 |
| no filter | T3_D+15m               |   36.793 |  87.8% |   4.96 |  35.13% |  32.11% |     196 |    40 |
| no filter | COMBINED               |   27.085 |  87.0% |   2.23 |  36.46% |   0.67% |     293 |    40 |
|           |                        |          |        |        |         |         |         |       |
|    RV≥1.0 | T1_D+4h+1h+15m         |   18.780 |  78.3% |   2.61 |  27.33% |  16.31% |      37 |    11 |
|    RV≥1.0 | T2_D+4h+15m            |  110.723 |  85.8% |  20.50 |  18.27% |  25.21% |      70 |    40 |
|    RV≥1.0 | T3_D+15m               |   74.640 |  89.5% |   9.82 |  28.48% |  25.55% |     149 |    40 |
|    RV≥1.0 | COMBINED               |   26.868 |  87.7% |   2.42 |  13.99% |  23.56% |     230 |    40 |
|           |                        |          |        |        |         |         |         |       |
|    RV≥1.3 | T1_D+4h+1h+15m         |   33.875 |  80.1% |   2.95 |  25.50% |  21.28% |      37 |     9 |
|    RV≥1.3 | T2_D+4h+15m            |  102.366 |  84.7% |  20.12 |  20.06% |  17.01% |      61 |    39 |
|    RV≥1.3 | T3_D+15m               |  133.100 |  90.6% |  26.67 |  21.74% |  26.07% |     124 |    40 |
|    RV≥1.3 | COMBINED               |   25.972 |  87.6% |   2.45 |  14.40% |  18.78% |     193 |    40 |
|           |                        |          |        |        |         |         |         |       |
|    RV≥1.5 | T1_D+4h+1h+15m         |   19.308 |  81.3% |   1.43 |  26.11% |  11.67% |      39 |     8 |
|    RV≥1.5 | T2_D+4h+15m            |  168.669 |  83.6% |  19.74 |  18.82% |  14.67% |      55 |    39 |
|    RV≥1.5 | T3_D+15m               |  149.368 |  90.7% |  27.03 |  19.33% |  22.55% |     106 |    40 |
|    RV≥1.5 | COMBINED               |   33.061 |  87.5% |   5.25 |  14.98% |  16.92% |     169 |    40 |
|           |                        |          |        |        |         |         |         |       |
|    RV≥2.0 | T1_D+4h+1h+15m         |   21.860 |  83.3% |   1.58 |  22.94% |   5.98% |      26 |     8 |
|    RV≥2.0 | T2_D+4h+15m            |  208.544 |  85.7% |  22.75 |  17.34% |  12.49% |      47 |    32 |
|    RV≥2.0 | T3_D+15m               |  147.985 |  87.7% |  26.45 |  18.07% |  16.35% |      76 |    39 |
|    RV≥2.0 | COMBINED               |   41.313 |  86.0% |  10.79 |  13.40% |  11.92% |     119 |    40 |
|           |                        |          |        |        |         |         |         |       |

## Best RelVol Threshold (by combined weighted Sharpe)

| RV Threshold | Avg Combined Sharpe |
|-------------|---------------------|
|         ≥2.0 |              41.313 |
|         ≥1.5 |              33.061 |
|         none |              27.085 |
|         ≥1.0 |              26.868 |
|         ≥1.3 |              25.972 |

## Value of Each Tier (no RV filter, delta vs T1 only)

- T1 only avg Sharpe:  156.959
- All tiers combined:  27.085  (delta: -129.874)

> **If combined > T1 alone**: tiered approach (allowing riskier trades with smaller size) adds portfolio value.
> **If combined < T1 alone**: better to only take 4/4 setups; lower tiers drag performance.

## Per-Symbol T1 vs Combined (no RV filter, top 20 by T1 Sharpe)

| Symbol | Side | T1 Sharpe | T1 Trades | Combined Sharpe | Combined Trades | +Delta |
|--------|------|-----------|-----------|-----------------|-----------------|--------|
| CHRUSDT        | SHORT |  1423.057 |         5 |         153.673 |              48 | -1269.384 |
| 1000FLOKIUSDT  | SHORT |   100.019 |        43 |          -5.564 |             918 | -105.583 |
| ACTUSDT        | SHORT |    89.112 |         7 |          33.134 |              92 | -55.978 |
| C98USDT        | LONG |    68.910 |        32 |           6.634 |             818 | -62.276 |
| BTCDOMUSDT     | LONG |    57.928 |        42 |          10.263 |            1197 | -47.665 |
| BBUSDT         | SHORT |    44.448 |        60 |           9.869 |            1133 | -34.579 |
| BATUSDT        | SHORT |    17.384 |        95 |           6.144 |            2780 | -11.240 |
| RIVERUSDT      | LONG |     9.795 |         6 |          29.102 |             101 | +19.307 |
| ARUSDT         | SHORT |    -2.958 |        68 |           6.646 |            1384 | +9.604 |
| BRETTUSDT      | SHORT |   -33.993 |        83 |          -0.355 |            1055 | +33.638 |
| BCHUSDC        | SHORT |   -47.151 |        37 |          -4.233 |             787 | +42.918 |
