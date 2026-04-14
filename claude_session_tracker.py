#!/usr/bin/env python3
"""Claude Code Session Tracker & Reopener

On crash/reboot: detects which sessions were active by finding JSONL files
that were being written to (clustered modification times near crash moment).
No cron needed — file timestamps ARE the tracker.

Usage:
    python3 claude_session_tracker.py reopen     # Close stale windows + reopen crashed sessions
    python3 claude_session_tracker.py list        # Show what would be reopened
    python3 claude_session_tracker.py auto        # LaunchAgent: same as reopen (with startup delay)
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(os.path.expanduser("~/.claude/projects/-Users-niels-Documents-binance"))
WORKING_DIR = "/Users/niels/Documents/binance"
REOPEN_DELAY = 3
MAX_INTERNAL_GAP = 1800  # 30 min — if gap between consecutive files > this, cluster ends


def get_first_user_message(jsonl_path):
    """Extract first real user message from a JSONL conversation file."""
    try:
        with open(jsonl_path) as f:
            for line in f:
                try:
                    d = json.loads(line)
                    if d.get("type") == "user":
                        msg = d.get("message", {})
                        if isinstance(msg, dict):
                            content = msg.get("content", "")
                            if isinstance(content, list):
                                for c in content:
                                    if isinstance(c, dict) and c.get("type") == "text":
                                        text = c["text"].strip()
                                        if not text.startswith("<task-notification") and not text.startswith("<local-command") and not text.startswith("#"):
                                            return text
                            elif isinstance(content, str):
                                return content.strip()
                except json.JSONDecodeError:
                    continue
    except Exception:
        pass
    return ""


def detect_crash_cluster():
    """Find sessions that were active at crash time using JSONL file modification timestamps.

    Active sessions write to their JSONL files frequently. On crash, they all stop
    at roughly the same moment. Find files whose mtime clusters near the most recent file.
    """
    if not PROJECT_DIR.exists():
        return []
    # Get all JSONL files with their modification times
    files = []
    for f in PROJECT_DIR.glob("*.jsonl"):
        try:
            files.append((f.stat().st_mtime, f))
        except OSError:
            continue
    if not files:
        return []
    files.sort(reverse=True)
    # Walk files newest→oldest; stop when gap between consecutive files > MAX_INTERNAL_GAP
    cluster = [files[0]]
    for i in range(1, len(files)):
        gap = files[i - 1][0] - files[i][0]
        if gap > MAX_INTERNAL_GAP:
            break
        cluster.append(files[i])
    # Build session info
    newest_mtime = cluster[0][0]
    sessions = []
    for mtime, f in cluster:
        first_msg = get_first_user_message(f)
        sessions.append({
            "session_id": f.stem,
            "mtime": mtime,
            "age_seconds": newest_mtime - mtime,
            "first_message": first_msg[:120] if first_msg else "(empty)",
            "size_kb": round(f.stat().st_size / 1024, 1),
        })
    return sessions


def get_running_session_ids():
    """Get session IDs of currently running claude processes."""
    try:
        result = subprocess.run(["ps", "aux"], capture_output=True, text=True)
        ids = set()
        for line in result.stdout.splitlines():
            if "--resume" not in line or "grep" in line:
                continue
            parts = line.split()
            for i, p in enumerate(parts):
                if p == "--resume" and i + 1 < len(parts):
                    uid = parts[i + 1]
                    if len(uid) == 36 and uid.count("-") == 4:
                        ids.add(uid)
        return ids
    except Exception:
        return set()


def close_all_terminal_windows():
    """Close ALL Terminal windows to prevent duplicates."""
    print("Closing all Terminal windows...")
    subprocess.run(["osascript", "-e", '''
tell application "Terminal"
    if (count of windows) > 0 then
        close every window
    end if
end tell
'''], capture_output=True)
    time.sleep(2)


def reopen_sessions(startup_delay=0):
    """Detect crash cluster, close stale windows, reopen active sessions."""
    if startup_delay:
        print(f"Waiting {startup_delay}s for system to settle...")
        time.sleep(startup_delay)
    # Skip sessions that are already running
    already_running = get_running_session_ids()
    sessions = detect_crash_cluster()
    if not sessions:
        print("No crash cluster detected — nothing to reopen.")
        return
    # Filter out already-running sessions
    to_open = [s for s in sessions if s["session_id"] not in already_running]
    if not to_open:
        print(f"All {len(sessions)} sessions already running — nothing to do.")
        return
    close_all_terminal_windows()
    print(f"Reopening {len(to_open)} sessions (from crash cluster of {len(sessions)})...")
    for i, s in enumerate(to_open):
        sid = s["session_id"]
        label = s.get("first_message", "")[:70].replace('"', '\\"').replace("'", "\\'").replace("\n", " ")
        subprocess.run(["osascript", "-e", f'''
tell application "Terminal"
    activate
    do script "cd {WORKING_DIR} && claude --resume {sid}"
end tell
'''], capture_output=True)
        print(f"  [{i+1}/{len(to_open)}] {sid[:8]}... | {label}")
        if i < len(to_open) - 1:
            time.sleep(REOPEN_DELAY)
    print(f"\nDone! Reopened {len(to_open)} conversations.")


def list_sessions():
    """Show what the crash cluster detection finds."""
    sessions = detect_crash_cluster()
    already_running = get_running_session_ids()
    if not sessions:
        print("No crash cluster detected.")
        return
    newest = max(s["mtime"] for s in sessions)
    print(f"Crash cluster: {len(sessions)} sessions (gap threshold {MAX_INTERNAL_GAP}s)")
    print(f"Newest file: {datetime.fromtimestamp(newest, tz=timezone.utc).strftime('%Y-%m-%d %H:%M:%S UTC')}\n")
    for s in sessions:
        running = "RUN" if s["session_id"] in already_running else "   "
        age = s["age_seconds"]
        print(f"  [{running}] {s['session_id'][:8]}... | {age:5.0f}s gap | {s['size_kb']:8.1f}KB | {s['first_message'][:65]}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "list"
    if cmd == "reopen":
        reopen_sessions()
    elif cmd == "list":
        list_sessions()
    elif cmd == "auto":
        reopen_sessions(startup_delay=5)
    else:
        print(__doc__)
        sys.exit(1)
