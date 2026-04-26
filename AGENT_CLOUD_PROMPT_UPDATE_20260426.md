# Cloud routine prompt update — extend to ang + use new data sources

The remote `fin-hourly-supervisor` Claude routine needs its prompt updated to:
1. Generate advisories for **both fin and ang** (was: fin only)
2. Read the new `tradeable_refresh` (3-min cadence) and `tv_enrichment` blocks in `snapshot.json`
3. Anchor decisions on **MTF WT alignment + S/R proximity** (already in tradeable_refresh)
4. Keep both accounts **out of loss** via technical force_close advisories

## Changes to make in the cloud routine prompt

### Output format (was: one file → now: two files)

The routine must now write **two** Gmail drafts (one per account) **and/or** push two files to the handoff repo:

| Account | Subject prefix | Filename |
|---|---|---|
| fin | `[FIN_ADVISORIES_v1]` | `fin_advisories.json` |
| ang | `[ANG_ADVISORIES_v1]` | `ang_advisories.json` |

Same schema as today; one file per account.

### Inputs to read from snapshot.json

```
snapshot.positions.crypto.fin           — current fin positions
snapshot.positions.crypto.ang           — current ang positions  (NEW)
snapshot.allowlists.fin                 — fin symbol allowlist
snapshot.allowlists.ang                 — ang symbol allowlist  (NEW; from symbols_ang.json if present, else from tradeable_refresh)
snapshot.allowlists.tradeable_fin       — fin tradeable_keys
snapshot.allowlists.tradeable_ang       — ang tradeable_keys
snapshot.tradeable_refresh.accounts.{fin,ang}.fresh_setups   — top fresh setups, refreshed every 3 min  (NEW)
snapshot.tradeable_refresh.accounts.{fin,ang}.candidates     — top scored, sorted, refreshed every 3 min  (NEW)
snapshot.tv_enrichment.symbols          — TV MCP MTF state (when status=ok); ignore if status!=ok  (NEW)
snapshot.decisions_recent.{fin,ang}     — last 50 decisions per account
snapshot.market_meta                    — BTC/ETH price + symbols_count
snapshot.news_sentiment                 — crypto/stocks/meta
snapshot.config_summary                 — relevant live config values
```

### Decision rules

**Per fin or ang position currently held:**

1. **Out-of-loss safety (mandatory check first):**
   - If position gain < -0.5% AND `tradeable_refresh` shows MTF alignment ≤ 1/5 in position's favor (i.e. 4/5 against) → emit `action: "force_close", reason: "MTF_AGAINST_LOSER"`
   - If position gain < 0% AND price is at/through `dc_high_4h` (for SHORT) or `dc_low_4h` (for LONG) → emit `action: "force_close", reason: "SR_BREAK_AGAINST"`
   - Else if gain ≥ 0% AND MTF aligned (≥3/5 supportive) → emit `action: "hold"` (suppress scripted exits that would close at small gain)

2. **Entry candidates (no position open on key):**
   - Only emit `action: "force_open"` for keys in `tradeable_refresh.fresh_setups` (score ≥ 70 AND mtf_align ≥ 4)
   - AND `near_level` exists with `distance_pct ≤ 1.5` AND `supportive: true`
   - Size: $50–$200 USD (`size_override_usd`)
   - `expires_at_utc`: 90 minutes
   - Include thesis: setup, tf_alignment summary, invalidation level (the S/R level), expected_hold_bars

3. **Block bad entries:**
   - For any key the scripted system might enter that has `mtf_align < 2` against direction → emit `action: "block_entry"`

### Cadence

- Routine runs hourly today; this is fine for strategic decisions.
- The fast 3-min `tradeable_refresh.json` covers between-iteration awareness.
- If you want sub-hourly cadence, change the schedule to every 30min (more cost; not required).

### TV enrichment

- Read `snapshot.tv_enrichment.status`; only consume `symbols` when `status == "ok"`.
- When paused, fall back to `tradeable_refresh` (which uses Redis-cached MTF — same data quality, just no Pine indicators).
- Never block on TV enrichment.

### USDC pair enforcement (CRITICAL — fee impact)

**When both USDT and USDC variants exist for a coin, always emit advisories on the USDC key.** Binance charges zero fees on USDC pairs; on a high-freq account like fin/ang, USDT fees (~0.04% taker) can erase the edge.

- Lookup rule: for any coin C where `{account}:{C}USDC_{SIDE}` exists in `snapshot.allowlists.tradeable_{account}`, use `{C}USDC_{SIDE}`. Otherwise use the USDT key (only valid if USDC has no listing for that coin).
- Reference data (news_sentiment, Bitget trader copy CSVs) may carry USDT names — that's their feed, not actionable. Translate to USDC for any trade decision.
- **Never emit an advisory for a key not in tradeable_{account}.** That key won't trade.

### Iteration ID

Increment `iteration_id` per advisory; lets local consumer dedupe.

### What stays the same

- Same `advisory_object` schema (action, scope, reason, thesis, expires_at_utc, iteration_id, size_override_usd).
- Same kill switch: omit a key from `advisories` to clear; or set `expires_at_utc` in the past.
- ang and fin have identical schemas — just two files.

## Local pieces already done

- `fin_advisory_consumer.py` — multi-account, supports `fin_advisories.json` AND `ang_advisories.json`.
- `ez_manage.py:19637` hook — extended to `account_key in ("fin","ang")`.
- `agent_inbox_poller.py` — maps `[ANG_ADVISORIES_v1]` → `ang_advisories.json`.
- `agent_snapshot_writer.py` — inlines `tradeable_refresh` + `tv_enrichment` into snapshot, includes ang allowlist.
- `tradeable_refresh_loop.py` — cron `*/3` writes `tradeable_refresh.json`.
- `tv_enrichment.json` — placeholder, `status: paused` until interactive runner is started.
- `ang_advisories.json` — placeholder, empty `advisories: {}`.
- All synced MB ↔ S1 ↔ S2; md5 parity verified.

## Action for user

Open the `fin-hourly-supervisor` routine in Claude (or wherever it's defined), update the prompt to reflect the rules above, and re-save. Local pipeline will pick up `ang_advisories.json` on the routine's next iteration (poller next run within 5 min).
