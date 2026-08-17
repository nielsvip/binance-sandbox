#!/bin/bash
# Install/reload the Mac paper-options runner and its two paper-report emails.
# This script never enables live trading; the order lock remains false.
set -euo pipefail

BASE="/Users/niels/Documents/binance"
AGENTS="$HOME/Library/LaunchAgents"
UID_VALUE="$(id -u)"
mkdir -p "$AGENTS"

unload_if_loaded() {
    local label="$1"
    local target="gui/$UID_VALUE/$label"
    if launchctl print "$target" >/dev/null 2>&1; then
        echo "Stopping $label..."
        # The shadow supervisor is intentionally persistent. Kill only this
        # named paper job before bootout so launchctl does not wait forever for
        # an old process to exit cleanly.
        launchctl kill SIGKILL "$target" >/dev/null 2>&1 || true
        launchctl bootout "$target" >/dev/null 2>&1 || true
    fi
}

bootstrap_plist() {
    local label="$1"
    local plist_path="$2"
    echo "Registering $label..."
    if ! launchctl bootstrap "gui/$UID_VALUE" "$plist_path"; then
        echo "Failed to bootstrap $label ($plist_path)" >&2
        return 1
    fi
}

for plist in \
    options_paper_email_morning.plist \
    options_paper_email_afternoon.plist; do
    label=$(/usr/libexec/PlistBuddy -c 'Print :Label' "$BASE/$plist")
    cp "$BASE/$plist" "$AGENTS/$plist"
    unload_if_loaded "$label"
    bootstrap_plist "$label" "$AGENTS/$plist"
done

SHADOW_LABEL="com.niels.options-shadow"
cp "$BASE/com.niels.options-shadow-runner.plist" "$AGENTS/com.niels.options-shadow.plist"
unload_if_loaded "$SHADOW_LABEL"
bootstrap_plist "$SHADOW_LABEL" "$AGENTS/com.niels.options-shadow.plist"
echo "Installed paper-only options runner and weekday morning/afternoon reports."
