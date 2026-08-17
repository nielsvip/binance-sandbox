#!/usr/bin/env python3
"""Freeze evidence for the discarded ladder-grind daemon; never restart it."""
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path


RULES = (
    ("MISSING_NPZ", (r"no npz", r"npz.*missing", r"skipped_no_npz")),
    (
        "DATA_SPLIT_OR_CORRUPT",
        (r"one.bar jump", r"split", r"corrupt", r"137\\.2%", r"303\\.1%"),
    ),
    (
        "MISSING_REQUIRED_LR_HTF",
        (
            r"lrL_pct_b_(?:4h|D)",
            r"lrL_slope_(?:4h|D)",
            r"missing required",
        ),
    ),
    (
        "THIN_OR_UNUSABLE_HTF",
        (r"thin", r"unusable", r"coverage", r"stoch_k_(?:1h|4h|D)"),
    ),
    ("RUNNER_ERROR", (r"traceback", r"error", r"failed", r"returncode")),
)


def classify(text: str) -> str:
    lowered = text.lower()
    for label, patterns in RULES:
        if any(re.search(pattern.lower(), lowered) for pattern in patterns):
            return label
    return "SUCCESS_OR_UNCLASSIFIED"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--state",
        type=Path,
        default=Path("data/handle_priority/grind_state.json"),
    )
    parser.add_argument(
        "--logs", type=Path, default=Path("/home/niels/logs/ladder_grind")
    )
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    state = json.loads(args.state.read_text())
    counts: Counter[str] = Counter()
    examples: dict[str, list[str]] = {}
    log_paths = sorted(args.logs.glob("*.log"))
    for path in log_paths:
        label = classify(path.read_text(errors="replace"))
        counts[label] += 1
        examples.setdefault(label, [])
        if len(examples[label]) < 5:
            examples[label].append(path.name)
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "verdict": "STOPPED_SUPERSEDED_DO_NOT_RESTART",
        "superseded_by": "MATRIX_ONLY_MANDATE_20260728.md",
        "last_state": state,
        "process_expected": False,
        "cron_expected": False,
        "logs_scanned": len(log_paths),
        "log_classifications": dict(sorted(counts.items())),
        "examples": examples,
        "interpretation": (
            "The final pass was not a healthy 24/7 campaign: no results were "
            "accepted, and the due work resolved to data/contract failures or "
            "missing NPZ. Historical standalone artifacts remain evidence only."
        ),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    temp = args.output.with_suffix(args.output.suffix + ".tmp")
    temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    temp.replace(args.output)
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
