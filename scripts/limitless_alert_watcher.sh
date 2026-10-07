#!/bin/bash
# limitless_alert_watcher.sh — Watch for Limitless trading edges and alert
#
# Syncs alerts.json from server every 10s, plays sound + shows notification
# when new edges are found.
#
# Usage: bash scripts/limitless_alert_watcher.sh

SERVER="s1-int"
REMOTE_DIR="/home/niels/_binance_PAUSED_UNTIL_WED18/data/poly/limitless_trader"
LOCAL_DIR="/Users/niels/Documents/binance/data/poly/limitless_trader"
ALERTS="$LOCAL_DIR/alerts.json"
LAST_TS=""

mkdir -p "$LOCAL_DIR"
echo "🔔 Limitless Edge Watcher — monitoring for >10% edges every 10s..."
echo "   Will play sound + show macOS notification when edges found."
echo ""

while true; do
    # Sync alerts file
    rsync -az "$SERVER:$REMOTE_DIR/alerts.json" "$LOCAL_DIR/" 2>/dev/null
    rsync -az "$SERVER:$REMOTE_DIR/stats.json" "$LOCAL_DIR/" 2>/dev/null

    if [ -f "$ALERTS" ]; then
        # Check if new alert
        CURRENT_TS=$(python3 -c "import json; print(json.load(open('$ALERTS')).get('ts',''))" 2>/dev/null)
        COUNT=$(python3 -c "import json; print(json.load(open('$ALERTS')).get('count',0))" 2>/dev/null)

        if [ "$CURRENT_TS" != "$LAST_TS" ] && [ "$COUNT" -gt 0 ] 2>/dev/null; then
            LAST_TS="$CURRENT_TS"

            # Parse and display
            python3 -c "
import json
d = json.load(open('$ALERTS'))
edges = d.get('edges', [])
if not edges:
    exit()
print()
print('═' * 70)
print(f'  🚨 {len(edges)} EDGE(S) FOUND — {d[\"ts\"][:19]}')
print('═' * 70)
for e in edges:
    print(f'  BUY {e[\"side\"]:>3s} {e[\"ticker\"]:>5s} | edge={e[\"edge_pct\"]:+.1f}% | entry=\${e[\"entry_price\"]:.3f} | {e[\"mins_left\"]:.0f}min left')
    print(f'    strike=\${e[\"strike\"]:,.2f} spot=\${e[\"spot\"]:,.2f}')
    print(f'    → {e[\"url\"]}')
    print()
print('═' * 70)
" 2>/dev/null

            # macOS notification
            FIRST=$(python3 -c "
import json
e = json.load(open('$ALERTS')).get('edges', [{}])[0]
print(f'BUY {e.get(\"side\",\"?\")} {e.get(\"ticker\",\"?\")} edge={e.get(\"edge_pct\",0):+.1f}% — {e.get(\"mins_left\",0):.0f}min left')
" 2>/dev/null)
            osascript -e "display notification \"$FIRST\" with title \"Limitless Edge\" sound name \"Glass\"" 2>/dev/null

            # Also play alert sound
            afplay /System/Library/Sounds/Glass.aiff 2>/dev/null &
        fi
    fi

    sleep 10
done
