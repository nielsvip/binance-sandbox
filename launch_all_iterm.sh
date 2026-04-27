#!/bin/bash
# Launch ALL services (infra + crypto + stocks) in named iTerm tabs
# Tab titles set via iTerm proprietary escape + precmd hook

CD="/Users/niels/Documents/binance"
PY="/opt/anaconda3/envs/binance_env/bin/python -u"
LOG="/Users/niels/logs"

# Function: launch one script in a new tab with a visible name
launch_tab() {
    local window_id="$1"
    local tab_name="$2"
    local cmd="$3"
    local is_first="$4"

    # The trick: set PROMPT_COMMAND to keep refreshing the title
    # Derive log name from tab name
    local log_name=$(echo "$tab_name" | tr '[:upper:]' '[:lower:]')
    local full_cmd="echo -ne '\\\\033]1;${tab_name}\\\\007'; echo -ne '\\\\033]0;${tab_name}\\\\007'; cd ${CD} && ${cmd} 2>&1 | tee -a ${LOG}/${log_name}_app.log"

    if [ "$is_first" = "first" ]; then
        osascript -e "
tell application \"iTerm\"
    tell window id ${window_id}
        tell current session
            write text \"${full_cmd}\"
        end tell
    end tell
end tell" 2>/dev/null
    else
        osascript -e "
tell application \"iTerm\"
    tell window id ${window_id}
        set newTab to (create tab with default profile)
        tell current session of newTab
            write text \"${full_cmd}\"
        end tell
    end tell
end tell" 2>/dev/null
    fi
}

echo "[$(date)] Launching all services..."

# Create Infrastructure window
INFRA_WIN=$(osascript -e '
tell application "iTerm"
    set w to (create window with default profile)
    return id of w
end tell' 2>/dev/null)
echo "Infra window: $INFRA_WIN"

launch_tab "$INFRA_WIN" "PRICES" "$PY ez_prices.py" "first"
launch_tab "$INFRA_WIN" "PRICES_WS" "$PY ez_prices_ws.py"
launch_tab "$INFRA_WIN" "KLINES" "$PY ez_klines.py"
launch_tab "$INFRA_WIN" "INDICATORS" "$PY ez_indicators.py"
launch_tab "$INFRA_WIN" "MARKET_DATA" "$PY ez_market_data.py"
launch_tab "$INFRA_WIN" "RANKINGS" "$PY ez_rankings.py"
launch_tab "$INFRA_WIN" "CROSSES" "$PY ez_crosses.py"
launch_tab "$INFRA_WIN" "SHARE_IND" "$PY ez_share_ind.py"
launch_tab "$INFRA_WIN" "IND_MERGER" "$PY ez_indicators_merger.py"
launch_tab "$INFRA_WIN" "MARK_PRICES" "$PY ez_mark_prices.py"
launch_tab "$INFRA_WIN" "NEWS_SCANNER" "$PY ez_news_scanner.py"
launch_tab "$INFRA_WIN" "ORDERBOOK" "$PY ez_orderbook.py"

echo "[$(date)] Infra launched (12 tabs). Waiting 30s for fresh data..."
sleep 30

# Create Crypto Trading window
CRYPTO_WIN=$(osascript -e '
tell application "iTerm"
    set w to (create window with default profile)
    return id of w
end tell' 2>/dev/null)
echo "Crypto window: $CRYPTO_WIN"

launch_tab "$CRYPTO_WIN" "MANAGE_ANG" "$PY ez_manage.py --account ang" "first"
launch_tab "$CRYPTO_WIN" "MANAGE_INF" "$PY ez_manage.py --account inf"
launch_tab "$CRYPTO_WIN" "MANAGE_FLZ" "$PY ez_manage.py --account flz"
launch_tab "$CRYPTO_WIN" "MANAGE_MEN" "$PY ez_manage.py --account men"
launch_tab "$CRYPTO_WIN" "MANAGE_FIN" "$PY ez_manage.py --account fin"
launch_tab "$CRYPTO_WIN" "QUICK_ANG" "$PY ez_positions_quick.py --account ang"
launch_tab "$CRYPTO_WIN" "QUICK_INF" "$PY ez_positions_quick.py --account inf"

echo "[$(date)] Crypto launched (7 tabs)."

# Create Tradier/Stocks window
STOCK_WIN=$(osascript -e '
tell application "iTerm"
    set w to (create window with default profile)
    return id of w
end tell' 2>/dev/null)
echo "Stock window: $STOCK_WIN"

launch_tab "$STOCK_WIN" "TRADIER_PRICES" "$PY tradier_prices.py" "first"
launch_tab "$STOCK_WIN" "TRADIER_IND" "$PY tradier_indicators.py"
launch_tab "$STOCK_WIN" "TRADIER_RANK" "$PY tradier_rankings.py"
launch_tab "$STOCK_WIN" "TRADIER_POS" "$PY tradier_positions.py --accounts tra trb trc"
launch_tab "$STOCK_WIN" "TRADIER_TRB" "$PY tradier_manage.py --accounts trb"
launch_tab "$STOCK_WIN" "TRADIER_TRC" "$PY tradier_manage.py --accounts trc"

echo "[$(date)] Stocks launched (6 tabs). ALL DONE."
echo ""
echo "Windows:"
echo "  1. Infrastructure (11 tabs)"
echo "  2. Crypto Trading (7 tabs)"
echo "  3. Stock Trading (6 tabs)"
echo "  Total: 24 services"
