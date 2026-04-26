# tv_enrichment_runner — on-demand TradingView enrichment for fin/ang agent

## Purpose

The remote `fin-hourly-supervisor` cloud routine has no path to local TradingView Desktop. This runner uses an **interactive Claude Code session** with the `tradingview` MCP server to:

1. Pull MTF state (1m/5m/15m/1h/4h/D) for the top fresh setups in `tradeable_refresh.json`
2. Read S/R levels from any custom Pine indicators visible (Profiler, etc.)
3. Capture quote_get for current price/spread/volume
4. Optional: compare with successful traders' published setups (chartlist comparison)

Output: `~/binance-agent-handoff/tv_enrichment.json` — read by `agent_snapshot_writer.py` and pushed to the cloud routine.

## Why not a cron job

TradingView MCP requires a live Claude Code session (CDP attached to TV Desktop). Cron-spawned Python can't reach it. TV is also brittle (`api_available: false` happens often) — running this on autopilot wastes cycles.

## Kill switch

- **Manual pause**: `touch ~/binance-agent-handoff/.tv_enrichment_paused` — runner exits without overwriting the file
- **Auto-pause**: if `mcp__tradingview__tv_health_check` returns `api_available: false`, runner writes `{"status": "paused", ...}` and exits
- **TTL**: cloud routine ignores `tv_enrichment.json` when `generated_at_utc + ttl_sec < now`, so a stale file is harmless

## Recommended invocation

In an interactive Claude Code session:

```
/loop 15m  Run the tv_enrichment_runner: read /Users/niels/binance-agent-handoff/tradeable_refresh.json,
take the top 5 fresh setups for fin and ang. For each, set TV to BINANCE:{SYMBOL}.P, capture
{quote_get, chart_get_state, data_get_study_values} on each of {3m,15m,1h,4h,D}.
Aggregate into tv_enrichment.json with schema {symbols: {BINANCE:BTCUSDT.P: {mtf: {3m:{wt1,wt2,...},...}, levels:{...}, quote:{...}}}}.
If tv_health_check api_available=false, write status=paused and exit.
On 3 consecutive failures, touch .tv_enrichment_paused so subsequent runs no-op.
```

## Runs as part of "go" pipeline

Until manually started by the user, `tv_enrichment.json` shows `status: paused` and the cloud routine falls back to Redis indicators in the snapshot (which already cover MTF WT + DC + BB + SMA200 — the heavy lifting).

TV is the cherry on top, not the foundation.
