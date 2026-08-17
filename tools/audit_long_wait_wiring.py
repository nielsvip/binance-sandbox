#!/usr/bin/env python3
"""Audit the phantom LONG_WAIT_ENABLED inventory row and removed score path."""
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def audit_repo(root: Path = ROOT) -> dict:
    config = (root / "config_tradier.py").read_text()
    manage = (root / "tradier_manage.py").read_text()
    history = (root / "backtest_results_20260331.csv").read_text(errors="replace")
    old_result = subprocess.run(
        ["git", "show", "f83bc7b9:tradier_manage.py"],
        cwd=root, text=True, capture_output=True, check=False,
    )
    evidence = root / "tools" / "evidence" / "long_wait_f83bc7b9_excerpt.txt"
    old = (
        old_result.stdout
        if old_result.returncode == 0
        else evidence.read_text(errors="replace")
    )
    declared = "LONG_WAIT_ENABLED:" in config
    read = "getattr(config, 'LONG_WAIT_ENABLED'" in manage
    active_reason = "LONG_WAIT:" in manage or "LONG_WEAK_BUY:" in manage
    old_reasons = all(
        value in old
        for value in (
            'reasons.append("Bounce_15m_Low")',
            'reasons.append("Bounce_5m_Low")',
            'reasons.append("4h_Deep_Value")',
            'reasons.append("1h_Turn_Up")',
        )
    )
    historical_fills = history.count("LONG_WAIT:")
    return {
        "inventory_switch": "LONG_WAIT_ENABLED",
        "switch_declared": declared,
        "switch_read": read,
        "active_reason_present": active_reason,
        "removed_source_commit": "f83bc7b9",
        "removed_source_evidence": (
            "git_show" if old_result.returncode == 0 else str(evidence)
        ),
        "removed_score_reasons_proven": old_reasons,
        "historical_fill_rows": historical_fills,
        "classification": (
            "PHANTOM_SWITCH_REMOVED_HISTORICAL_SCORE_PATH"
            if not declared and not read and not active_reason
            and old_reasons and historical_fills > 0
            else "REQUIRES_FRESH_MANUAL_AUDIT"
        ),
    }


if __name__ == "__main__":
    print(json.dumps(audit_repo(), indent=2, sort_keys=True))
