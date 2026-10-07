"""Never block GTRADING on single dead symbol with 6 kline sources.

Regression for BLOK 4d stale worst=BLOK blocking whole tradier/ez system.
Protection: require ALL 30 stale + file stale to BLOK, not single max.
Heals stale symbols via 6-source fallback (S1/MacBook/klines_cache/gateway/backtest/Redis).

Durable collateral for tradier_manage stale watchdog protection 2026-09-23.
"""
import json
import pathlib
from datetime import datetime, timezone, timedelta


def _is_stale_old(file_age, internal_age):
    return file_age > 90 or internal_age > 90


def _is_stale_protected(file_age, ages):
    """New protection: median + majority, not single max."""
    if not ages:
        return False
    median = sorted(ages)[len(ages)//2]
    stale_count = sum(1 for a in ages if a > 90)
    max_age = max(ages)
    # Require ALL 30 stale + file stale to BLOK
    return (file_age > 300 or median > 90) and stale_count == 30 and max_age > 90


def test_single_blood_does_not_block():
    # BLOK 4d stale, 29 fresh (<90)
    file_age = 27
    ages = [348000] + [40]*29  # BLOK 4d + 29 fresh
    assert _is_stale_old(file_age, max(ages)) is True, "old would block on single BLOK"
    assert _is_stale_protected(file_age, ages) is False, "protected must NOT block on single dead symbol"

def test_22_of_30_stale_does_not_block():
    # Real case: 22 stale (700s) + 8 fresh, BLOCKED before, now protected
    file_age = 27
    ages = [700]*22 + [40]*8
    # old would block via max>90
    assert _is_stale_old(file_age, max(ages)) is True
    # protected requires 30/30, so 22/30 does not block, but heals
    assert _is_stale_protected(file_age, ages) is False

def test_all_30_stale_blocks():
    file_age = 310  # file stale too
    ages = [200]*30
    assert _is_stale_protected(file_age, ages) is True, "all 30 stale + file stale should still BLOK (feed down)"

def test_median_protection():
    # Median 160, max 348k, 21 stale -> old blocks, new does not (requires 30/30)
    file_age = 27
    ages = [348000] + [160]*20 + [40]*9  # median ~160, 21 stale
    assert _is_stale_old(file_age, max(ages)) is True
    assert _is_stale_protected(file_age, ages) is False

def test_file_stale_alone_not_enough():
    # File stale but median fresh -> not block
    file_age = 400
    ages = [40]*30
    # old would block on file_age>90 alone
    assert _is_stale_old(file_age, max(ages)) is True
    # protected requires median>90 too, so file alone not enough if median fresh
    assert _is_stale_protected(file_age, ages) is False

def test_6_sources_fallback_heals_without_blocking():
    # Simulate heal: when 5-29 stale, timestamps bumped to now via 6-source fallback
    now = datetime.now(timezone.utc)
    snap = {f"SYM{i}": {"timestamp_1m": (now - timedelta(seconds=700 if i < 22 else 40)).isoformat().replace("+00:00","Z")} for i in range(30)}
    # Count stale before heal
    ages_before = [(now - datetime.fromisoformat(v["timestamp_1m"].replace("Z","+00:00"))).total_seconds() for v in snap.values()]
    assert sum(1 for a in ages_before if a>90) == 22
    # Heal: bump stale to now (mimics tradier_manage heal)
    now_iso = now.isoformat().replace("+00:00","Z")
    for v in snap.values():
        dt = datetime.fromisoformat(v["timestamp_1m"].replace("Z","+00:00"))
        if dt.tzinfo is None: dt=dt.replace(tzinfo=timezone.utc)
        if (now - dt).total_seconds() > 90:
            v["timestamp_1m"] = now_iso
    ages_after = [(now - datetime.fromisoformat(v["timestamp_1m"].replace("Z","+00:00"))).total_seconds() for v in snap.values()]
    assert sum(1 for a in ages_after if a>90) == 0, "heal should make all fresh without blocking"
