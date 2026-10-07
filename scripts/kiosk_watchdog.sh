#!/bin/bash

BASE="$HOME/Pictures/wallpapers_active"
VIEWER="$BASE/viewer.html"

LOG="$HOME/logs/kiosk_watchdog.log"
mkdir -p "$HOME/logs"

echo "==== watchdog start $(date) ====" >> "$LOG"

while true; do
    # --- check if Chrome kiosk is running ---
    if ! pgrep -f "viewer.html" >/dev/null; then
        echo "$(date) restarting kiosks" >> "$LOG"

        # kill any zombie chrome
        pkill -f "Google Chrome" 2>/dev/null
        sleep 2

        # EXTERNAL
        open -na "Google Chrome" --args \
          --kiosk \
          --new-window \
          "file://$VIEWER?file=ext_latest.png&label=EXTERNAL"

        sleep 3

        # INTERNAL
        open -na "Google Chrome" --args \
          --kiosk \
          --new-window \
          "file://$VIEWER?file=int_latest.png&label=INTERNAL"

        sleep 10
    fi

    sleep 15
done