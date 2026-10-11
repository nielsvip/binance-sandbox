"""ONE RISK ENTITY guard (USER 2026-10-11): no shadow/paper twin positions may exist
alongside live ones. Live keys are ONLY acct:SYM_SIDE; vec state lives in the twin
ledger (decisions/ownership metadata), never as _vec/_paper/_sim/_backtest positions
in positions files, trackers, tradeable_keys, or the owned registry.
Dependency-free: pure JSON scans, safe to run anywhere.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ACCTS = ("ang", "men", "fin", "inf", "flz")
BANNED = re.compile(r"_(vec|paper|sim|backtest)\b", re.IGNORECASE)
LIVE_KEY = re.compile(r"^[a-z0-9]+:[A-Z0-9]+_(LONG|SHORT)$")


def _load(p):
    try:
        return json.loads(Path(p).read_text())
    except Exception:
        return None


def test_no_shadow_position_keys():
    offenders = []
    for a in ACCTS:
        for side in ("long_positions.json", "short_positions.json"):
            d = _load(ROOT / a / side) or {}
            for k in d:
                if BANNED.search(k):
                    offenders.append(f"{a}/{side}:{k}")
    assert not offenders, f"shadow position keys found: {offenders[:10]}"


def test_no_shadow_tracker_or_tradeable_keys():
    offenders = []
    for a in ACCTS:
        t = _load(ROOT / a / "tracker.json") or {}
        for section in ("exit_candidates", "entry_candidates", "reentry_candidates"):
            for k in (t.get(section) or {}):
                if BANNED.search(k):
                    offenders.append(f"{a}/tracker:{section}:{k}")
    tk = _load(ROOT / "tradeable_keys.json") or []
    for k in tk:
        if isinstance(k, str) and BANNED.search(k):
            offenders.append(f"tradeable_keys:{k}")
    assert not offenders, f"shadow keys found: {offenders[:10]}"


def test_owned_registry_keys_are_live_form():
    checked = 0
    for p in (ROOT / "data").glob("vec_exact_owned_*.json"):
        d = _load(p) or {}
        for k in d:
            checked += 1
            assert LIVE_KEY.match(k), f"non-live key in twin owned registry {p.name}: {k}"
            assert not BANNED.search(k), f"shadow key in twin owned registry {p.name}: {k}"
    assert checked >= 0  # registry may legitimately be absent pre-first-twin-open
