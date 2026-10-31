#!/usr/bin/env python3
"""Read-only admission checks and stable host assignment for paper workloads."""

import hashlib
import math
import numbers
import os
import re
import shutil
from pathlib import Path


HOSTS_BY_VENUE = {"crypto": ("s1", "s2"), "stocks": ("s5", "s6")}
DEFAULT_THRESHOLDS = {
    "mem_available_bytes": 8 * 1024**3,
    "disk_free_bytes": 8 * 1024**3,
    "load1_per_cpu": 0.80,
    "cpu_psi_avg10": 10.0,
}
OBSERVED_FIELDS = (
    "mem_available_bytes",
    "disk_free_bytes",
    "load1",
    "ncpu",
    "cpu_psi_avg10",
)


def stable_host(venue, symbol, side, arm=None):
    """Choose a stable host; optional arm identity is deliberately not hashed."""
    venue = str(venue).strip().lower()
    side = str(side).strip().upper()
    symbol = str(symbol).strip().upper()
    if venue not in HOSTS_BY_VENUE:
        raise ValueError(f"unsupported venue: {venue}")
    if not symbol or side not in ("LONG", "SHORT"):
        raise ValueError("symbol and LONG/SHORT side are required")
    del arm
    key = f"{venue}\0{symbol}\0{side}".encode("utf-8")
    digest = hashlib.sha256(key).digest()
    hosts = HOSTS_BY_VENUE[venue]
    return hosts[int.from_bytes(digest[:8], "big") % len(hosts)]


def _read_mem_available(proc_root):
    for line in (Path(proc_root) / "meminfo").read_text().splitlines():
        if line.startswith("MemAvailable:"):
            parts = line.split()
            if len(parts) >= 2 and parts[1].isdigit():
                return int(parts[1]) * 1024
            break
    return None


def _read_cpu_psi_avg10(proc_root):
    for line in (Path(proc_root) / "pressure" / "cpu").read_text().splitlines():
        if line.startswith("some "):
            for part in line.split()[1:]:
                if part.startswith("avg10="):
                    return float(part.split("=", 1)[1])
    return None


def collect_observed(data_path, proc_root="/proc"):
    """Collect only local resource readings; unavailable values remain None."""
    observed = {field: None for field in OBSERVED_FIELDS}
    try:
        observed["mem_available_bytes"] = _read_mem_available(proc_root)
    except (OSError, ValueError):
        pass
    try:
        observed["disk_free_bytes"] = shutil.disk_usage(data_path).free
    except OSError:
        pass
    try:
        observed["load1"] = float((Path(proc_root) / "loadavg").read_text().split()[0])
    except (OSError, ValueError, IndexError):
        pass
    try:
        observed["ncpu"] = os.cpu_count()
    except OSError:
        pass
    try:
        observed["cpu_psi_avg10"] = _read_cpu_psi_avg10(proc_root)
    except (OSError, ValueError):
        pass
    return observed


def decide_admission(venue, host, observed, thresholds=None):
    """Return a fail-closed decision without signaling or changing any process."""
    configured_thresholds = dict(DEFAULT_THRESHOLDS)
    if thresholds is not None:
        try:
            configured_thresholds.update(dict(thresholds))
        except (TypeError, ValueError):
            configured_thresholds["_invalid_threshold_input"] = None
    venue = str(venue).strip().lower()
    host = str(host).strip().lower()
    observed_copy = {field: observed.get(field) for field in OBSERVED_FIELDS}
    reasons = []
    if venue not in HOSTS_BY_VENUE:
        reasons.append("unsupported_venue")
    elif host not in HOSTS_BY_VENUE[venue]:
        reasons.append("host_not_allowed_for_venue")

    for field in OBSERVED_FIELDS:
        value = observed_copy[field]
        if value is None:
            reasons.append(f"missing_{field}")
        elif isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value) or value < 0:
            reasons.append(f"invalid_{field}")
            observed_copy[field] = None
    if observed_copy["ncpu"] == 0:
        reasons.append("invalid_ncpu")
        observed_copy["ncpu"] = None

    valid_thresholds = {}
    for key in DEFAULT_THRESHOLDS:
        value = configured_thresholds.get(key)
        if isinstance(value, bool) or not isinstance(value, numbers.Real) or not math.isfinite(value) or value < 0:
            reasons.append(f"invalid_threshold_{key}")
        else:
            valid_thresholds[key] = value
    for key in configured_thresholds.keys() - DEFAULT_THRESHOLDS.keys():
        reasons.append(f"invalid_threshold_{key}")

    mem = observed_copy["mem_available_bytes"]
    if mem is not None and "mem_available_bytes" in valid_thresholds and mem < valid_thresholds["mem_available_bytes"]:
        reasons.append("mem_available_below_reserve")
    disk = observed_copy["disk_free_bytes"]
    if disk is not None and "disk_free_bytes" in valid_thresholds and disk < valid_thresholds["disk_free_bytes"]:
        reasons.append("disk_free_below_reserve")
    load1, ncpu = observed_copy["load1"], observed_copy["ncpu"]
    if load1 is not None and ncpu is not None and "load1_per_cpu" in valid_thresholds and load1 > ncpu * valid_thresholds["load1_per_cpu"]:
        reasons.append("load1_above_limit")
    psi = observed_copy["cpu_psi_avg10"]
    if psi is not None and "cpu_psi_avg10" in valid_thresholds and psi > valid_thresholds["cpu_psi_avg10"]:
        reasons.append("cpu_psi_avg10_above_limit")
    return {"admit": not reasons, "reasons": reasons, "observed": observed_copy}


def render_paper_service(exec_start, description="Paper intake capture"):
    """Render an intake-only unit; 25% CPU/512M do not budget a future live adapter."""
    exec_start = str(exec_start)
    description = str(description)
    if not exec_start.strip() or any(c in exec_start for c in "\r\n"):
        raise ValueError("exec_start must be one non-empty line")
    if any(c in description for c in "\r\n"):
        raise ValueError("description must be one line")
    if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", exec_start + description):
        raise ValueError("control characters are not allowed")
    return (
        "# Intake capture only; these limits are not a budget for a live adapter.\n"
        "[Unit]\n"
        f"Description={description}\n"
        "After=network.target\n\n"
        "[Service]\n"
        "Type=simple\n"
        f"ExecStart={exec_start}\n"
        "Nice=19\n"
        "CPUWeight=1\n"
        "CPUQuota=25%\n"
        "MemoryHigh=256M\n"
        "MemoryMax=512M\n"
        "IOWeight=1\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )
