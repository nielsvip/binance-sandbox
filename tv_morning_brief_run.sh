#!/bin/bash
# Runs at 13:25 UTC (9:25 AM ET) weekdays via launchd.
# Calls morning_brief MCP on TradingView, filters to trb symbols,
# writes data/tv_morning_brief.json for the newsletter at 13:35 UTC.

cd /Users/niels/Documents/binance || exit 1

LOGFILE="/tmp/tv_morning_brief.log"
CLAUDE="/Users/niels/.local/bin/claude"

echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — tv_morning_brief_run starting" >> "$LOGFILE"

"$CLAUDE" --print \
  --allowedTools "mcp__tradingview__morning_brief,mcp__tradingview__data_get_study_values,mcp__tradingview__quote_get,mcp__tradingview__tv_health_check,Read,Write" \
  -p "You are running a pre-market bias scan. Do not ask questions — execute and write the output file.

STEP 1 — Read the tradeable symbol lists:
- Read /Users/niels/Documents/binance/symbols_trb_long.json  (list of LONG-side symbols)
- Read /Users/niels/Documents/binance/symbols_trb_short.json (list of SHORT-side symbols)

STEP 2 — Health check TradingView, then call morning_brief:
- Call tv_health_check first. If it fails, write the JSON with an error note and exit.
- Call morning_brief with rules_path='/Users/niels/Documents/binance/rules.json'
- morning_brief returns indicator data for symbols on the active TV watchlist.

STEP 3 — Score each symbol returned by morning_brief:
Only process symbols that appear in symbols_trb_long.json OR symbols_trb_short.json.
For each qualifying symbol, determine its side (long = in trb_long list, short = in trb_short list).

LONG setup criteria (score 1 point each, max 5):
  1. WT bullish on 1h TF
  2. WT bullish on 4h TF
  3. WT bullish on D TF
  4. Stoch K < 80 on 15m or 1h (not overbought)
  5. MFI >= 40 on 15m or 1h (flow confirming)
A symbol qualifies as a LONG setup if it scores >= 2 AND at least 1 of criteria 1-3 is met.

SHORT setup criteria (score 1 point each, max 5):
  1. WT bearish on 1h TF
  2. WT bearish on 4h TF
  3. WT bearish on D TF
  4. Stoch K > 20 on 15m or 1h (not oversold)
  5. MFI <= 60 on 15m or 1h (flow confirming)
A symbol qualifies as a SHORT setup if it scores >= 2 AND at least 1 of criteria 1-3 is met.

Count wt_tfs_aligned = number of WT TFs (1h, 4h, D) that are aligned with the setup direction.

Write a brief note per symbol, e.g. 'WT 1h+4h bullish, stoch K 52, MFI 61' or 'WT D bearish, stoch K 78'.

STEP 4 — Compute market_bias:
long_setups = count of qualifying LONG setups
short_setups = count of qualifying SHORT setups
If long_setups > short_setups * 1.1 → market_bias = 'LONG'
If short_setups > long_setups * 1.1 → market_bias = 'SHORT'
Otherwise → market_bias = 'NEUTRAL'

STEP 5 — Write /Users/niels/Documents/binance/data/tv_morning_brief.json with this EXACT schema:
{
  \"generated_at\": \"<ISO8601 UTC timestamp, e.g. 2026-04-23T13:25:00Z>\",
  \"symbols_long_count\": <total symbols in trb_long list>,
  \"symbols_short_count\": <total symbols in trb_short list>,
  \"tv_watchlist_coverage\": <how many trb symbols morning_brief returned data for>,
  \"summary\": \"<N long setups, M short setups from K trb symbols scanned>\",
  \"market_bias\": \"<LONG|SHORT|NEUTRAL>\",
  \"top_longs\": [<qualifying LONG symbols sorted by score desc, max 15>],
  \"top_shorts\": [<qualifying SHORT symbols sorted by score desc, max 15>],
  \"bias\": {
    \"<SYMBOL>\": {
      \"side\": \"<long|short>\",
      \"signal\": \"<LONG|SHORT|NEUTRAL>\",
      \"strength\": <0-5 score>,
      \"wt_tfs_aligned\": <int>,
      \"note\": \"<brief note>\"
    }
  }
}

Only include symbols in the bias dict if they scored >= 1 (at least 1 WT TF aligned).
If morning_brief returned no data (TV not open), write the file with empty bias and a summary like '0 long setups, 0 short setups — TradingView not available'." \
  >> "$LOGFILE" 2>&1

EXIT_CODE=$?
echo "$(date -u '+%Y-%m-%d %H:%M:%S UTC') — tv_morning_brief_run done (exit $EXIT_CODE)" >> "$LOGFILE"
exit $EXIT_CODE
