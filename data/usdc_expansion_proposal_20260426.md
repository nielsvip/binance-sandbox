# USDC Expansion Proposal — 20260426

**Status:** PROPOSAL ONLY. Not applied. User reviews + runs `usdc_expansion_apply.sh`.
**Source audit:** `data/usdc_breadth_audit_20260426.md` (Binance fapi, 38 USDC perps total).
**CLAUDE.md guards:** `feedback_tradeable_keys_sacred.md`, `feedback_positions_untouchable.md`, `feedback_usdc_preferred_no_fees.md`. APPENDS only — no key removal, no position mutation.

## Why USDC

- Binance USDC perps charge **lower / waived** maker+taker fees vs USDT — direct PnL uplift on a 2,000x trade-volume goal.
- Audit confirmed **0 USDT-shadow leaks** in fin/ang (no swap-and-replace needed); this is purely net-new breadth.
- All proposed pairs onboarded ≥ 6 months ago — sufficient klines history for backtests.

## Liquidity (24h quote vol — proxy for spread quality)

Pulled by the audit script directly from `fapi/v1/ticker/24hr`. The audit ran 20260426. All ≥ $369K/24h.

## Per-account proposal

### fin — top-15 (paper / high-freq, breadth-friendly)

| # | Pair | 24h Quote Vol | Klines OK? | Fit |
|---|------|---------------|------------|------|
| 1 | ETHUSDC | $1.27B | YES | Tier-1 majors must be in fin |
| 2 | SOLUSDC | $149.50M | YES | Tier-1 majors |
| 3 | XRPUSDC | $38.80M | YES | High-vol L1 |
| 4 | ORDIUSDC | $17.46M | YES | BRC-20 leader, fits fin breadth |
| 5 | SUIUSDC | $8.01M | YES | Active L1 |
| 6 | PENGUUSDC | $6.81M | YES | Memecoin volatility — V3 fodder |
| 7 | LINKUSDC | $5.52M | YES | DeFi blue-chip |
| 8 | ENAUSDC | $3.73M | YES | Ethena, high-beta |
| 9 | FILUSDC | $3.20M | YES | Storage leader |
| 10 | LTCUSDC | $2.56M | YES | Established |
| 11 | ARBUSDC | $2.35M | YES | L2 leader |
| 12 | BIOUSDC | $2.04M | **NO** | Klines fetch needed pre-backtest |
| 13 | IPUSDC | $1.95M | YES | New IP narrative |
| 14 | NEARUSDC | $1.77M | YES | L1 |
| 15 | TIAUSDC | $1.22M | YES | Modular DA |

### ang — top-15 (similar breadth profile to fin)

| # | Pair | 24h Quote Vol | Klines OK? | Fit |
|---|------|---------------|------------|------|
| 1 | ETHUSDC | $1.27B | YES | Major missing |
| 2 | SOLUSDC | $149.50M | YES | Major missing |
| 3 | XRPUSDC | $38.80M | YES | High-vol L1 |
| 4 | ORDIUSDC | $17.46M | YES | BRC-20 |
| 5 | 1000PEPEUSDC | $16.53M | YES | Memecoin ang already trades 1000PEPEUSDT |
| 6 | SUIUSDC | $8.01M | YES | L1 |
| 7 | LINKUSDC | $5.52M | YES | DeFi |
| 8 | FILUSDC | $3.20M | YES | Storage |
| 9 | LTCUSDC | $2.56M | YES | Established |
| 10 | BIOUSDC | $2.04M | **NO** | Klines fetch needed |
| 11 | IPUSDC | $1.95M | YES | New narrative |
| 12 | NEARUSDC | $1.77M | YES | L1 |
| 13 | TIAUSDC | $1.22M | YES | Modular |
| 14 | HBARUSDC | $1.19M | YES | Enterprise |
| 15 | 1000SHIBUSDC | $843.89K | YES | Memecoin |

### inf — top-10 (SCALP_V3 active, breadth carefully — onboard only 8 candidates available)

inf already holds 17/25 of the audit's top USDC perps, so only 8 net-new candidates exist. Listing all 8 (target 10 not reachable from this audit batch).

| # | Pair | 24h Quote Vol | Klines OK? | Fit |
|---|------|---------------|------------|------|
| 1 | ORDIUSDC | $17.46M | YES | BRC-20, V3 volatility |
| 2 | LINKUSDC | $5.52M | YES | DeFi, V3 candidate |
| 3 | BIOUSDC | $2.04M | **NO** | Klines fetch needed |
| 4 | IPUSDC | $1.95M | YES | New narrative |
| 5 | WLFIUSDC | $712.36K | **NO** | Klines fetch needed |
| 6 | PNUTUSDC | $649.73K | YES | Memecoin |
| 7 | NEOUSDC | $539.23K | YES | Established |
| 8 | KAITOUSDC | $369.04K | YES | New |

### flz — top-5 (smaller account, conservative add)

| # | Pair | 24h Quote Vol | Klines OK? | Fit |
|---|------|---------------|------------|------|
| 1 | XRPUSDC | $38.80M | YES | High-vol L1 |
| 2 | DOGEUSDC | $26.70M | YES | Memecoin major |
| 3 | ORDIUSDC | $17.46M | YES | BRC-20 |
| 4 | SUIUSDC | $8.01M | YES | L1 |
| 5 | PENGUUSDC | $6.81M | YES | High-vol memecoin |

### men — top-5

| # | Pair | 24h Quote Vol | Klines OK? | Fit |
|---|------|---------------|------------|------|
| 1 | ETHUSDC | $1.27B | YES | Tier-1 |
| 2 | SOLUSDC | $149.50M | YES | Tier-1 |
| 3 | XRPUSDC | $38.80M | YES | L1 |
| 4 | ORDIUSDC | $17.46M | YES | BRC-20 |
| 5 | PENGUUSDC | $6.81M | YES | High-vol memecoin |

## Counts

| Account | Net-new pairs | LONG keys | SHORT keys | Total tradeable_keys lines |
|---------|---------------|-----------|------------|----------------------------|
| fin     | 15            | 15        | 15         | 30                         |
| ang     | 15            | 15        | 15         | 30                         |
| inf     | 8             | 8         | 8          | 16                         |
| flz     | 5             | 5         | 5          | 10                         |
| men     | 5             | 5         | 5          | 10                         |
| **Sum** | **48 (with overlap)** | **48** | **48** | **96**                     |

Distinct symbols across all accounts: see apply script — `symbols.json` only gets each unique pair appended once.

## Klines flag (must fetch BEFORE backtests)

Missing klines (per `klines_cache/` audit):

- `BIOUSDC` — 1h+4h present, but **15m / 3m / D missing** → blocks backtests
- `WLFIUSDC` — same gap

Fetch via existing `fetch_klines.py` / `ez_klines.py` workflow before any sweep validation. Apply script will still include them in allowlists (no harm — live just won't see them until klines available), but **BACKTESTS WILL UNDER-COUNT until klines exist**.

## Files modified by apply script

1. `tradeable_keys.json` — append 96 new `{acct}:{SYMBOL}_{SIDE}` entries
2. `symbols.json` — append distinct net-new pairs (deduped)
3. `symbols_fin.json`, `symbols_flz.json`, `symbols_men.json` — append per-account lists
4. `symbols_ang_long.json`, `symbols_ang_short.json` — append per-side
5. `symbols_inf_long.json`, `symbols_inf_short.json` — append per-side

All 8 files backed up first to `backups/usdc_expansion_<TS>/` before modification.

## Sacred-positions guarantee

- `symbols.json` count growing means new pairs ENTER the tradeable universe; no existing entry is removed.
- No `entry_price`, `max_gain`, `opened_at`, or `positionAmt` is ever touched. The apply script touches NO position dict files (`*positions*.json`).
- Per CLAUDE.md: positions are created only by `add_new_symbols.py`. This proposal explicitly does NOT create positions — only allowlists.
