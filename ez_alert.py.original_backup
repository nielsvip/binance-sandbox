"""Desktop alerts for emergency exit fires (R1/R2/HEDGE_FAILED).

osascript native notification (macOS only) + JSONL append to data/sweep_alerts/bad_exits.jsonl.
Failures are silent — alerts must NEVER block trading.
"""
import json
import os
import subprocess
from datetime import datetime, timezone

ALERT_LOG = "data/sweep_alerts/bad_exits.jsonl"


def _osascript_notify(title, subtitle, body):
    try:
        msg = f'display notification "{body}" with title "{title}" subtitle "{subtitle}"'
        subprocess.Popen(["osascript", "-e", msg], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def alert_bad_exit(account, position_key, gain, reason, entry_signal, entry_ts, current_price):
    rec = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "account": account or "?",
        "position_key": position_key,
        "gain_pct": round(float(gain), 4),
        "exit_reason": reason,
        "entry_signal": str(entry_signal)[:120],
        "entry_ts": str(entry_ts)[:30],
        "current_price": float(current_price) if current_price else 0.0,
    }
    try:
        os.makedirs(os.path.dirname(ALERT_LOG), exist_ok=True)
        with open(ALERT_LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
    except Exception:
        pass
    try:
        _osascript_notify(title=f"BAD EXIT — {position_key}", subtitle=f"{account or '?'} | {reason[:40]}", body=f"g={gain:.2f}% | entry={str(entry_signal)[:40]}")
    except Exception:
        pass
