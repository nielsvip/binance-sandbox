# USDC Breadth Audit — 20260426

Source: `https://fapi.binance.com/fapi/v1/exchangeInfo` + `/fapi/v1/ticker/24hr` (public, no auth).
Tradeable set: `tradeable_keys.json` (sides stripped — symbol-level audit).

## Summary

- Total Binance USDC perpetuals (TRADING): **38**
- In fin tradeable_keys: **13**  /  Missing: **25**
- In ang tradeable_keys: **18**  /  Missing: **20**
- USDT-shadow leaks (fin → could be swapped to USDC): **0**
- USDT-shadow leaks (ang → could be swapped to USDC): **0**

## Top-30 missing-from-fin USDC perps (by 24h volume)

| # | Symbol | Base | Onboard | 24h Quote Vol |
|---|--------|------|---------|---------------|
| 1 | ETHUSDC | ETH | 2024-01-03 | $1.27B |
| 2 | SOLUSDC | SOL | 2024-01-03 | $149.50M |
| 3 | XRPUSDC | XRP | 2024-01-03 | $38.80M |
| 4 | DOGEUSDC | DOGE | 2024-01-18 | $26.70M |
| 5 | ORDIUSDC | ORDI | 2024-02-22 | $17.46M |
| 6 | SUIUSDC | SUI | 2024-02-08 | $8.01M |
| 7 | PENGUUSDC | PENGU | 2025-07-23 | $6.81M |
| 8 | AVAXUSDC | AVAX | 2024-03-20 | $6.74M |
| 9 | LINKUSDC | LINK | 2024-02-16 | $5.52M |
| 10 | ENAUSDC | ENA | 2024-05-02 | $3.73M |
| 11 | FILUSDC | FIL | 2024-04-18 | $3.20M |
| 12 | LTCUSDC | LTC | 2024-04-11 | $2.56M |
| 13 | ARBUSDC | ARB | 2024-04-18 | $2.35M |
| 14 | BIOUSDC | BIO | 2025-08-25 | $2.04M |
| 15 | IPUSDC | IP | 2025-03-05 | $1.95M |
| 16 | NEARUSDC | NEAR | 2024-04-11 | $1.77M |
| 17 | 1000BONKUSDC | 1000BONK | 2024-05-02 | $1.76M |
| 18 | TIAUSDC | TIA | 2024-04-25 | $1.22M |
| 19 | HBARUSDC | HBAR | 2025-03-07 | $1.19M |
| 20 | 1000SHIBUSDC | 1000SHIB | 2024-03-28 | $843.89K |
| 21 | WLFIUSDC | WLFI | 2025-09-08 | $712.36K |
| 22 | BOMEUSDC | BOME | 2024-04-25 | $689.65K |
| 23 | PNUTUSDC | PNUT | 2025-03-07 | $649.73K |
| 24 | NEOUSDC | NEO | 2024-04-18 | $539.23K |
| 25 | KAITOUSDC | KAITO | 2025-03-05 | $369.04K |

## Top-30 missing-from-ang USDC perps (by 24h volume)

| # | Symbol | Base | Onboard | 24h Quote Vol |
|---|--------|------|---------|---------------|
| 1 | ETHUSDC | ETH | 2024-01-03 | $1.27B |
| 2 | SOLUSDC | SOL | 2024-01-03 | $149.50M |
| 3 | XRPUSDC | XRP | 2024-01-03 | $38.80M |
| 4 | ORDIUSDC | ORDI | 2024-02-22 | $17.46M |
| 5 | 1000PEPEUSDC | 1000PEPE | 2024-03-07 | $16.53M |
| 6 | SUIUSDC | SUI | 2024-02-08 | $8.01M |
| 7 | LINKUSDC | LINK | 2024-02-16 | $5.52M |
| 8 | FILUSDC | FIL | 2024-04-18 | $3.20M |
| 9 | LTCUSDC | LTC | 2024-04-11 | $2.56M |
| 10 | BIOUSDC | BIO | 2025-08-25 | $2.04M |
| 11 | IPUSDC | IP | 2025-03-05 | $1.95M |
| 12 | NEARUSDC | NEAR | 2024-04-11 | $1.77M |
| 13 | TIAUSDC | TIA | 2024-04-25 | $1.22M |
| 14 | HBARUSDC | HBAR | 2025-03-07 | $1.19M |
| 15 | 1000SHIBUSDC | 1000SHIB | 2024-03-28 | $843.89K |
| 16 | WLFIUSDC | WLFI | 2025-09-08 | $712.36K |
| 17 | BOMEUSDC | BOME | 2024-04-25 | $689.65K |
| 18 | PNUTUSDC | PNUT | 2025-03-07 | $649.73K |
| 19 | NEOUSDC | NEO | 2024-04-18 | $539.23K |
| 20 | KAITOUSDC | KAITO | 2025-03-05 | $369.04K |

## USDT-shadow pairs in tradeable_keys with USDC equivalents (fee leak)

Per `feedback_usdc_preferred_no_fees.md`: USDC quote pairs have lower/zero fees on Binance perps. The pairs below trade on USDT in tradeable_keys but have a Binance USDC perp available.

### fin USDT-shadow leaks (sorted by USDC equiv 24h volume)

_None._

### ang USDT-shadow leaks (sorted by USDC equiv 24h volume)

_None._

## Notes

- Symbol membership is checked at the BASE pair level (sides stripped); a `_LONG`-only listing for a symbol still counts as 'present'. Side-coverage gap analysis is out of scope here.
- This is RESEARCH ONLY. `symbols.json` and `tradeable_keys.json` are SACRED per CLAUDE.md — no auto-add. User decides which to onboard.
