"""paper_forward_install_variants.py — generate one LaunchAgent plist per variant.

Reads paper_forward_variants.json and (re)writes ~/Library/LaunchAgents/com.niels.paper-forward-<id>.plist.
Then prints the launchctl commands to load each one.

This is idempotent: running it again overwrites the plists with the current spec.
Variants removed from the JSON are detected and printed for unload.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SPEC = BASE / "paper_forward_variants.json"
LAUNCH_DIR = Path.home() / "Library" / "LaunchAgents"
LOG_DIR = BASE / "logs"
PYTHON = "/opt/anaconda3/envs/binance_env/bin/python"
RUNNER = BASE / "paper_forward_runner.py"
LABEL_PREFIX = "com.niels.paper-forward-"


def _plist_xml(label: str, variant_id: str, side_mode: str, env: dict) -> str:
    env_lines = [
        f"        <key>PAPER_FWD_VARIANT</key>\n        <string>{variant_id}</string>",
        f"        <key>PAPER_FWD_SIDE_MODE</key>\n        <string>{side_mode}</string>",
        f"        <key>PYTHONUNBUFFERED</key>\n        <string>1</string>",
    ]
    for k, v in env.items():
        if k.startswith("_"):
            continue
        env_lines.append(f"        <key>{k}</key>\n        <string>{v}</string>")
    env_block = "\n".join(env_lines)
    out_path = LOG_DIR / f"paper_forward_{variant_id}.out"
    err_path = LOG_DIR / f"paper_forward_{variant_id}.err"
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>{label}</string>
    <key>ProgramArguments</key>
    <array>
        <string>{PYTHON}</string>
        <string>-u</string>
        <string>{RUNNER}</string>
    </array>
    <key>WorkingDirectory</key>
    <string>{BASE}</string>
    <key>EnvironmentVariables</key>
    <dict>
{env_block}
    </dict>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>ThrottleInterval</key>
    <integer>30</integer>
    <key>StandardOutPath</key>
    <string>{out_path}</string>
    <key>StandardErrorPath</key>
    <string>{err_path}</string>
</dict>
</plist>
"""


def main() -> int:
    if not SPEC.exists():
        print(f"ERROR: spec not found: {SPEC}", file=sys.stderr)
        return 2
    spec = json.loads(SPEC.read_text())
    variants = spec.get("variants", [])
    if not variants:
        print("no variants in spec", file=sys.stderr)
        return 1
    LAUNCH_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    written = []
    seen_labels = set()
    for v in variants:
        vid = v.get("id")
        if not vid:
            print(f"WARN: variant missing id: {v}", file=sys.stderr)
            continue
        side_mode = (v.get("side_mode") or "BOTH").upper()
        env = v.get("env", {}) or {}
        label = f"{LABEL_PREFIX}{vid}"
        seen_labels.add(label)
        plist_path = LAUNCH_DIR / f"{label}.plist"
        plist_path.write_text(_plist_xml(label, vid, side_mode, env))
        written.append((label, plist_path))
        print(f"wrote {plist_path}")

    # Detect orphans (existing paper-forward plists not in the spec)
    orphans = []
    for p in LAUNCH_DIR.glob("com.niels.paper-forward-*.plist"):
        label = p.stem
        # Skip the legacy single-runner plist if it ever exists, and the report job.
        if label.endswith("-report"):
            continue
        if label not in seen_labels:
            orphans.append((label, p))

    print()
    print("=" * 78)
    print("To start (or restart) all variants:")
    print()
    for label, _ in written:
        print(f"  launchctl unload ~/Library/LaunchAgents/{label}.plist 2>/dev/null; launchctl load -w ~/Library/LaunchAgents/{label}.plist")
    if orphans:
        print()
        print("Orphans (in JSON spec was removed — unload + delete):")
        for label, p in orphans:
            print(f"  launchctl unload ~/Library/LaunchAgents/{label}.plist 2>/dev/null; rm '{p}'")
    print()
    print("To check status:")
    print("  launchctl list | grep paper-forward")
    print("To inspect a variant's heartbeat:")
    print(f"  cat data/paper_forward/<variant_id>/HEARTBEAT.json | jq")
    return 0


if __name__ == "__main__":
    sys.exit(main())
