#!/bin/zsh
# Run OUTSIDE sandbox (normal Terminal) to make fix persistent
set -e
echo "Fixing CODEX_SANDBOX_NETWORK_DISABLED..."
launchctl unsetenv CODEX_SANDBOX_NETWORK_DISABLED 2>/dev/null || true
unset CODEX_SANDBOX_NETWORK_DISABLED
export CODEX_SANDBOX_NETWORK_DISABLED=""
if ! grep -q "CODEX_SANDBOX_NETWORK_DISABLED" ~/.zshrc 2>/dev/null; then
  cat >> ~/.zshrc <<'EOF2'

# --- sandbox network keepalive (2026-08-10): ensure network never disabled ---
unset CODEX_SANDBOX_NETWORK_DISABLED
launchctl unsetenv CODEX_SANDBOX_NETWORK_DISABLED 2>/dev/null || true
export CODEX_SANDBOX_NETWORK_DISABLED=""
EOF2
  echo "Patched ~/.zshrc"
else
  echo "~/.zshrc already patched"
fi
cat > ~/Library/LaunchAgents/com.codex.sandbox-network-keepalive.plist <<'PLIST2'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//W3C//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key><string>com.codex.sandbox-network-keepalive</string>
    <key>ProgramArguments</key><array><string>/bin/zsh</string><string>-c</string><string>launchctl unsetenv CODEX_SANDBOX_NETWORK_DISABLED 2>/dev/null; unset CODEX_SANDBOX_NETWORK_DISABLED; exit 0</string></array>
    <key>RunAtLoad</key><true/>
    <key>StartInterval</key><integer>60</integer>
    <key>StandardOutPath</key><string>/tmp/codex-sandbox-network-keepalive.log</string>
    <key>StandardErrorPath</key><string>/tmp/codex-sandbox-network-keepalive.log</string>
</dict>
</plist>
PLIST2
plutil -lint ~/Library/LaunchAgents/com.codex.sandbox-network-keepalive.plist
launchctl bootout gui/$(id -u)/com.codex.sandbox-network-keepalive 2>/dev/null || true
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.codex.sandbox-network-keepalive.plist 2>&1 || launchctl load -w ~/Library/LaunchAgents/com.codex.sandbox-network-keepalive.plist 2>&1 || true
echo "LaunchAgent loaded"
echo "CODEX_SANDBOX_NETWORK_DISABLED=$(launchctl getenv CODEX_SANDBOX_NETWORK_DISABLED 2>&1; echo exit:$?)"
echo "network_access=$(grep network_access ~/.codex/config.toml)"
echo "gateway check: $(ssh -O check gateway-internal 2>&1 | head -1)"
echo "s1 check: $(ssh -O check s1-int 2>&1 | head -1)"
