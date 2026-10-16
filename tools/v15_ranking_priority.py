"""Ranking top-N priority for the v15 sweep queue (USER 2026-10-14).

The ranking loops (ez_rankings crypto, tradier_rankings stocks) publish
winners/losers with per-symbol scores. This module exposes the top-N
winners (-> LONG) and top-N losers (-> SHORT) per venue as a SCHEDULING
signal: queue ORDER (after the gain tier = BEST_WORST, before
director-priority/attempts) and recalculation FREQUENCY (top-N skips the
M/L deferral window, like W).

Hard limits (BACKTEST_BIBLE section 14/19):
- NEVER touches sheet math: no E/G/H delta, no baseline, no promotion.
- NEVER touches live sizing: the boost here only breaks scheduling ties.
- Fail-open: missing/stale/unreadable ranking files -> empty sets, the
  scheduler behaves exactly as before.
- Kill switch: V15_RANKING_PRIORITY=0 disables everything below.

File formats (written by ez_rankings.save_rankings_json, shared by both
venues): {SYMBOL: {"lt_rank": int, ...}} where lt_rank 1 = best.
`data/rankings.json` = crypto, `data/tradier_rankings.json` = stocks.
"""

import json
import os
import time
from pathlib import Path

RANKING_TOP_N_DEFAULT = 25
RANKING_BOOST_DEFAULT = 1.1
RANKING_MAX_AGE_H_DEFAULT = 48.0
_CACHE_TTL_S = 60.0

_CACHE = {"at": 0.0, "key": None, "sets": None}


def _root():
    r = os.environ.get("V15_RANKING_ROOT")
    if r:
        return Path(r)
    return Path(__file__).resolve().parent.parent


def _top_n():
    try:
        return max(1, int(os.environ.get("RANKING_TOP_N", RANKING_TOP_N_DEFAULT)))
    except (TypeError, ValueError):
        return RANKING_TOP_N_DEFAULT


def _enabled():
    return os.environ.get("V15_RANKING_PRIORITY", "1") != "0"


def _max_age_s():
    try:
        return max(0.0, float(os.environ.get("RANKING_MAX_AGE_H", RANKING_MAX_AGE_H_DEFAULT))) * 3600.0
    except (TypeError, ValueError):
        return RANKING_MAX_AGE_H_DEFAULT * 3600.0


def _parse_file(path, top_n):
    """-> (long_sides, short_sides). Winners = lowest lt_rank -> LONG."""
    try:
        if not path.exists():
            return set(), set()
        if _max_age_s() > 0 and (time.time() - path.stat().st_mtime) > _max_age_s():
            return set(), set()
        data = json.loads(path.read_text())
    except Exception:
        return set(), set()
    if isinstance(data, dict):
        items = [(s, e) for s, e in data.items() if isinstance(e, dict) and isinstance(e.get("lt_rank"), (int, float))]
    elif isinstance(data, list):
        items = [(e.get("symbol"), e) for e in data if isinstance(e, dict) and e.get("symbol") and isinstance(e.get("lt_rank"), (int, float))]
    else:
        return set(), set()
    if not items:
        return set(), set()
    items.sort(key=lambda kv: float(kv[1]["lt_rank"]))
    syms = [str(s).upper() for s, _ in items]
    longs = {"%s_LONG" % s for s in syms[:top_n]}
    shorts = {"%s_SHORT" % s for s in syms[-top_n:]}
    return longs, shorts


def load_ranking_sets(root=None, top_n=None):
    """Uncached read. -> {"long": frozenset, "short": frozenset} of SYM_SIDE."""
    tn = top_n if top_n is not None else _top_n()
    r = Path(root) if root is not None else _root()
    longs, shorts = set(), set()
    for fname in ("rankings.json", "tradier_rankings.json"):
        l, s = _parse_file(r / "data" / fname, tn)
        longs |= l
        shorts |= s
    return {"long": frozenset(longs), "short": frozenset(shorts)}


def ranking_sets():
    """Cached, env-gated, fail-open. -> {"long","short","pairs","boost","top_n"}."""
    if not _enabled():
        return {"long": frozenset(), "short": frozenset(), "pairs": frozenset(), "boost": 1.0, "top_n": _top_n()}
    r = _root()
    tn = _top_n()
    try:
        mtimes = tuple((r / "data" / f).stat().st_mtime if (r / "data" / f).exists() else -1.0 for f in ("rankings.json", "tradier_rankings.json"))
    except Exception:
        mtimes = (-1.0, -1.0)
    key = (str(r), tn, mtimes)
    now = time.time()
    if _CACHE["sets"] is not None and _CACHE["key"] == key and (now - _CACHE["at"]) < _CACHE_TTL_S:
        return _CACHE["sets"]
    sets = load_ranking_sets(r, tn)
    pairs = frozenset(s.rsplit("_", 1)[0] for s in sets["long"] | sets["short"])
    try:
        boost = float(os.environ.get("RANKING_BOOST_MULT", RANKING_BOOST_DEFAULT))
    except (TypeError, ValueError):
        boost = RANKING_BOOST_DEFAULT
    out = {"long": sets["long"], "short": sets["short"], "pairs": pairs, "boost": boost, "top_n": tn}
    _CACHE.update(at=now, key=key, sets=out)
    return out


def is_ranking_side(sym_side):
    """True = this SYM_SIDE is a ranking top-N winner-long or loser-short."""
    try:
        s = ranking_sets()
        ss = str(sym_side).upper()
        return ss in s["long"] or ss in s["short"]
    except Exception:
        return False


def is_ranking_sym(base_sym):
    """True = either side of this base symbol is ranking top-N."""
    try:
        return str(base_sym).upper() in ranking_sets()["pairs"]
    except Exception:
        return False


def ranking_boost(sym_side):
    """Small scheduling/sizing multiplier: boost for members, 1.0 else."""
    try:
        return float(ranking_sets()["boost"]) if is_ranking_side(sym_side) else 1.0
    except Exception:
        return 1.0


def reset_cache():
    _CACHE.update(at=0.0, key=None, sets=None)
