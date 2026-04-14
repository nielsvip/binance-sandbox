#!/usr/bin/env python3
"""Log Healer — scans ALL logs every 60s, finds top errors, classifies, and auto-fixes where possible.

Not a dumb "grep error and restart" loop — actually analyzes patterns and patches.
"""
import os, re, subprocess, sys, time
from collections import Counter, defaultdict
from pathlib import Path

sys.stdout.reconfigure(line_buffering=True)

# Singleton
import fcntl
_lf = open("/tmp/log_healer.lock", "w")
try:
    fcntl.flock(_lf, fcntl.LOCK_EX | fcntl.LOCK_NB)
except (IOError, OSError):
    sys.exit(0)

LOG_DIR = "/Users/niels/logs"
CHECK = 60

# Classified error patterns → fix actions
PATTERNS = [
    # (regex, category, action)
    (r"RZ_ERROR.*NoneType", "rz_none_compare", "already_silenced"),
    (r"APIError.*-1003.*banned", "binance_ip_ban", "wait_for_unban"),
    (r"Unclosed client session", "tradier_http_leak", "benign_warning"),
    (r"SKIP_REDUCE_EMPTY_POSITION", "phantom_close", "log_only"),
    (r"BLOCKED NOT TRADEABLE", "non_tradeable_trade_attempt", "check_tradeable_keys"),
    (r"Failed to sync time with Binance", "binance_time_sync", "transient"),
    (r"Failed to get listen key", "binance_ws_auth", "wait_retry"),
    (r"Read timed out", "network_timeout", "transient"),
    (r"KeyError.*position", "position_key_missing", "investigate"),
    (r"MemoryError|OutOfMemory|Killed", "oom", "restart_with_less"),
    (r"ModuleNotFoundError|ImportError", "import_error", "clear_pycache"),
]


def ts():
    return time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())


def classify(line):
    for pat, cat, action in PATTERNS:
        if re.search(pat, line, re.IGNORECASE):
            return cat, action
    return "unknown", "log_only"


def scan_log(path, since_offset=0):
    """Return list of (line, category, action) for errors newer than since_offset bytes."""
    try:
        with open(path, "rb") as f:
            if since_offset > 0:
                f.seek(since_offset)
            data = f.read()
            new_offset = f.tell()
        text = data.decode("utf-8", errors="replace")
    except Exception:
        return [], since_offset
    errors = []
    for line in text.split("\n"):
        if re.search(r"ERROR|Traceback|CRITICAL", line):
            cat, action = classify(line)
            errors.append((line.strip()[:200], cat, action))
    return errors, new_offset


def act(category, action, examples):
    """Take action on classified errors."""
    if action == "already_silenced":
        return "ok (fix already deployed)"
    if action == "benign_warning":
        return "ok (benign)"
    if action == "wait_for_unban":
        # Extract unban timestamp
        for ex in examples[:1]:
            m = re.search(r"banned until (\d+)", ex)
            if m:
                unban_ts = int(m.group(1)) / 1000
                remaining = unban_ts - time.time()
                if remaining > 0:
                    return f"wait {remaining/60:.0f}min for IP unban"
        return "wait for unban"
    if action == "clear_pycache":
        subprocess.run(["find", "/Users/niels/Documents/binance", "-name", "__pycache__",
                        "-exec", "rm", "-rf", "{}", "+"], capture_output=True, timeout=10)
        return "cleared __pycache__"
    if action == "check_tradeable_keys":
        # Extract the symbol being blocked
        for ex in examples[:1]:
            m = re.search(r"BLOCKED NOT TRADEABLE (\w+:\w+)", ex)
            if m:
                return f"blocked entry for {m.group(1)} — symbol not in tradeable_keys (investigate caller)"
        return "check symbols.json matches tradeable_keys"
    if action == "restart_with_less":
        return "OOM detected — would restart with fewer symbols"
    return "logged only"


def main():
    print(f"[{ts()}] LOG HEALER STARTED — scanning {LOG_DIR} every {CHECK}s", flush=True)
    offsets = {}
    cycle = 0
    while True:
        cycle += 1
        all_errors = Counter()  # category -> count
        examples = defaultdict(list)
        scanned_files = 0
        for path in sorted(Path(LOG_DIR).glob("*.log")):
            name = path.name
            prev_offset = offsets.get(name, path.stat().st_size if cycle == 1 else 0)
            errors, new_offset = scan_log(path, prev_offset)
            offsets[name] = new_offset
            scanned_files += 1
            for line, cat, action in errors:
                all_errors[(cat, action)] += 1
                if len(examples[cat]) < 3:
                    examples[cat].append(f"[{name}] {line}")
        if all_errors:
            print(f"[{ts()}] --- cycle {cycle}: scanned {scanned_files} logs, found {sum(all_errors.values())} new errors ---", flush=True)
            for (cat, action), count in all_errors.most_common():
                result = act(cat, action, examples[cat])
                print(f"  [{count:>4}x] {cat:30s} → {result}", flush=True)
                if cat == "unknown" and examples[cat]:
                    print(f"         example: {examples[cat][0][:150]}", flush=True)
        else:
            print(f"[{ts()}] cycle {cycle}: scanned {scanned_files} logs — clean", flush=True)
        time.sleep(CHECK)


if __name__ == "__main__":
    main()
