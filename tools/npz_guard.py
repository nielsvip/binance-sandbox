#!/usr/bin/env python3
"""Guard against overwriting 917d NPZs with truncated 77d data."""
from __future__ import annotations

import numpy as np
from pathlib import Path


def should_allow_overwrite(existing_npz: Path, candidate_span_days: float, candidate_dt: float, candidate_keys: int, candidate_n: int | None = None, candidate_ts_span_days: float | None = None) -> tuple[bool, str]:
    """Return (allowed, reason). Blocks truncated overwrites."""
    if not existing_npz.exists():
        return True, "no existing — allow"
    try:
        d = np.load(existing_npz, allow_pickle=True)
    except Exception as e:
        return True, f"existing unreadable {e} — allow"
    ts = d["timestamps"].astype(np.int64) if "timestamps" in d.files else d.get("timestamp_15m", np.array([0])).astype(np.int64)
    if len(ts) < 2:
        return True, "existing too short — allow"
    if ts[-1] > 1e11:
        ts = ts // 1000
    existing_span = (float(ts[-1]) - float(ts[0])) / 86400
    existing_dt = float(np.median(np.diff(ts.astype(float)))) if len(ts) > 1 else 900
    existing_keys = len(d.files)
    # NPZFIX 2026-10-01: block overwrites that lose bars/history even when the candidate's D-derived span looks fine
    # (a tradier rebuild with ~80d of 15m bars replaced 154 stock NPZs of 730d at 21:41-21:44Z).
    if candidate_n is not None and len(ts) > 2000 and candidate_n < 0.8 * len(ts):
        return False, f"BLOCK bar-loss overwrite: existing {len(ts)} bars vs candidate {candidate_n}"
    if candidate_ts_span_days is not None and existing_span >= 200 and candidate_ts_span_days < 0.8 * existing_span:
        return False, f"BLOCK history-loss overwrite: existing {existing_span:.0f}d vs candidate timestamps span {candidate_ts_span_days:.0f}d"
    has_w = "wt1_W" in d.files
    # Existing is good 365d+ canonical
    is_existing_good = existing_span >= 365 and 600 < existing_dt < 1200 and existing_keys >= 800 and has_w
    is_candidate_bad = candidate_span_days < 300 or candidate_keys < 800 or not (600 < candidate_dt < 1200)
    is_candidate_shorter = candidate_span_days < existing_span * 0.80
    if is_existing_good and (is_candidate_bad or is_candidate_shorter):
        return False, f"BLOCK truncated overwrite: existing {existing_span:.1f}d dt{int(existing_dt)} keys{existing_keys} hasW{has_w} vs candidate {candidate_span_days:.1f}d dt{int(candidate_dt)} keys{candidate_keys}"
    return True, "allow"


def assert_safe_or_backup(existing_npz: Path, candidate_span_days: float, candidate_dt: float, candidate_keys: int, backup_dir: Path | None = None) -> None:
    allowed, reason = should_allow_overwrite(existing_npz, candidate_span_days, candidate_dt, candidate_keys)
    if not allowed:
        raise ValueError(reason)
