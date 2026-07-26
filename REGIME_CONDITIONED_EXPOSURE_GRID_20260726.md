# Causal regime-conditioned exposure grid — 2026-07-26

All six features are present in all 20 frozen NPZs. The classifier uses only completed 1h/4h/D values and fixed global thresholds. It consumes one pre-final-frozen entry family; no blended overlay or final-fold selection is allowed.

| policy | regimes | adverse scale/gap/cap | neutral | supportive |
|---|---:|---|---|---|
| binary_gentle | 2 | 0.875/1/6.0x | 0.875/1/6.0x | 1.125/0/8.0x |
| binary_balanced | 2 | 0.750/2/4.0x | 0.750/2/4.0x | 1.250/0/8.0x |
| ternary_gentle | 3 | 0.875/1/6.0x | 1.000/1/6.0x | 1.125/0/8.0x |
| ternary_balanced | 3 | 0.750/2/4.0x | 1.000/1/6.0x | 1.250/0/8.0x |
| ternary_strong | 3 | 0.625/3/3.0x | 1.000/1/6.0x | 1.375/0/8.0x |

Each cell is multiplier scale / minimum completed-1h entry gap / absolute multiplier cap. Scales and density are monotonic from adverse to supportive, and every cap is at or below 8x.
