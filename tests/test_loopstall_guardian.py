"""Guardian pass-local file cache (2026-10-24 USER unlock).

The reentry guardian re-read + re-parsed the same ~7 account files per
position missing reentry data (20+ loop-stall dumps in raw_decode). The
pass-local (mtime_ns, size) cache returns identical dicts; the loop only
.gets, so sharing is safe. Missing/tiny/non-dict files yield None exactly
where the inline code continued.
"""
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import ez_positions_service as eps  # noqa: E402


def _run(coro):
    return asyncio.run(coro)


def test_guardian_cache_hit_miss_invalidate(tmp_path):
    p = tmp_path / "tracker.json"
    p.write_text(json.dumps({"a:B_LONG": {"reentry_level": 1.5}}))
    cache: dict = {}
    first = _run(eps._guardian_cached_json(p, cache))
    assert first == {"a:B_LONG": {"reentry_level": 1.5}}
    assert _run(eps._guardian_cached_json(p, cache)) is first
    p.write_text(json.dumps({"a:B_LONG": {"reentry_level": 2.5}}))
    second = _run(eps._guardian_cached_json(p, cache))
    assert second == {"a:B_LONG": {"reentry_level": 2.5}}
    assert second is not first


def test_guardian_cache_negative_cases(tmp_path):
    cache: dict = {}
    assert _run(eps._guardian_cached_json(tmp_path / "missing.json", cache)) is None
    tiny = tmp_path / "tiny.json"
    tiny.write_text("{}")
    assert _run(eps._guardian_cached_json(tiny, cache)) is None
    lst = tmp_path / "list.json"
    lst.write_text("[1,2,3]")
    assert _run(eps._guardian_cached_json(lst, cache)) is None
    assert cache == {}  # negatives are never cached
