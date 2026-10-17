"""Ranking top-N priority for the v15 sweep queue (USER 2026-10-14, rewritten 2026-10-17).

The ranking loops (ez_rankings crypto, tradier_rankings stocks) publish
winners/losers. This module exposes them as a SCHEDULING signal: queue ORDER
(after the gain tier = BEST_WORST, before director-priority/attempts) and
recalculation FREQUENCY (members skip the M/L deferral window, like W, and are
due for a fresh board once a day).

USER 2026-10-17: "ang and trb ... need to be prioritized in the s1
v15_fleet_scheduler as they are the most rapidly changing according to markets
and need new settings for each sym_side every day."

WHY THIS WAS REWRITTEN — measured on S1 2026-10-17 02:4xZ, the whole hook was
inert (`ranking_sets()` -> 0 long / 0 short / 0 pairs on the live sandbox):

1. It read `data/rankings.json` + `data/tradier_rankings.json`, and
   `tools/ez_rankings_push_s1.sh` deliberately does NOT push them ("rankings.json
   not needed"). The copy under S1 `~/binance-sandbox/data/` was from 2026-08-20,
   58 days old, so the 48 h freshness guard discarded it every tick.
2. `data/tradier_rankings.json` does not exist at that path at all —
   tradier_rankings writes it to `data/tradier/` (TradierConfig.DATA_DIR), and
   as `{"rankings": [...], "info": {...}}`, not the `{SYM: {lt_rank}}` shape the
   parser expected. Stocks could never have been boosted.
3. rankings.json lt_rank is a different ordering from the lists that actually
   trade, so even a fresh file would have prioritised the wrong sym_sides.

Sources now, in order of authority — all three ARE pushed to S1 every 2 min:
1. `data/rankings_qualified_{crypto,stocks}.json` — the qualification gate's
   artifact (rankings_qualify.py): exactly what trades, in rank order, with
   per-symbol scores and side exclusivity already resolved.
2. `symbols_ang_long/short.json` + `symbols_trb_long/short.json` — the live
   lists themselves, used when the artifact is missing or stale.
3. `data/rankings.json` lt_rank — legacy, last resort.

Hard limits (BACKTEST_BIBLE section 14/19), unchanged:
- NEVER touches sheet math: no E/G/H delta, no baseline, no promotion.
- NEVER touches live sizing: the boost here only breaks scheduling ties.
- Fail-open: every source missing/stale/unreadable -> empty sets, the scheduler
  behaves exactly as before.
- Kill switch: V15_RANKING_PRIORITY=0 disables everything below.
"""

import json
import os
import time
from pathlib import Path

RANKING_TOP_N_DEFAULT = 25
RANKING_BOOST_DEFAULT = 1.1
RANKING_MAX_AGE_H_DEFAULT = 48.0
RANKING_REFRESH_H_DEFAULT = 24.0
_CACHE_TTL_S = 60.0

# Per venue: (artifact, long list, short list). Each venue resolves its own source INDEPENDENTLY —
# a fresh crypto artifact must never stop the stocks fallback from reading symbols_trb_*.json.
_VENUES = (
    ("crypto", "rankings_qualified_crypto.json", "symbols_ang_long.json", "symbols_ang_short.json"),
    ("stocks", "rankings_qualified_stocks.json", "symbols_trb_long.json", "symbols_trb_short.json"),
)
_QUALIFIED_FILES = tuple(v[1] for v in _VENUES)
_SYMBOL_FILES = tuple((f, side) for v in _VENUES for f, side in ((v[2], "LONG"), (v[3], "SHORT")))
_LEGACY_FILES = ("rankings.json", "tradier_rankings.json")

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


def refresh_window_s():
    """How old a ranking member's board may get before it is due again (USER: every day)."""
    try:
        return max(0.0, float(os.environ.get("V15_RANKING_REFRESH_H", RANKING_REFRESH_H_DEFAULT))) * 3600.0
    except (TypeError, ValueError):
        return RANKING_REFRESH_H_DEFAULT * 3600.0


def _fresh(path):
    """True = the file exists and is inside the freshness window (0 h disables the check)."""
    try:
        if not path.exists():
            return False
        age_limit = _max_age_s()
        return age_limit <= 0 or (time.time() - path.stat().st_mtime) <= age_limit
    except OSError:
        return False


def _read_json(path):
    try:
        return json.loads(path.read_text())
    except Exception:
        return None


def _from_qualified(root, top_n, fname):
    """Source 1 for one venue: its qualification artifact. -> (longs, shorts, hit)."""
    longs, shorts, hit = set(), set(), False
    path = root / "data" / fname
    if not _fresh(path):
        return longs, shorts, hit
    data = _read_json(path)
    if not isinstance(data, dict):
        return longs, shorts, hit
    for key, bucket in (("long", longs), ("short", shorts)):
        rows = data.get(key)
        if not isinstance(rows, list):
            continue
        ranked = [r for r in rows if isinstance(r, dict) and r.get("symbol")]
        ranked.sort(key=lambda r: float(r.get("rank") or 1e9))
        for row in ranked[:top_n]:
            bucket.add("%s_%s" % (str(row["symbol"]).upper(), key.upper()))
            hit = True
    return longs, shorts, hit


def _from_symbol_lists(root, top_n, long_file, short_file):
    """Source 2 for one venue: its live symbols_*.json lists (order IS the rank)."""
    longs, shorts, hit = set(), set(), False
    for fname, side in ((long_file, "LONG"), (short_file, "SHORT")):
        path = root / fname
        if not _fresh(path):
            continue
        data = _read_json(path)
        if not isinstance(data, list):
            continue
        bucket = longs if side == "LONG" else shorts
        for symbol in data[:top_n]:
            if symbol:
                bucket.add("%s_%s" % (str(symbol).upper(), side))
                hit = True
    return longs, shorts, hit


def _parse_file(path, top_n):
    """Source 3 (legacy): {SYM: {"lt_rank": n}}. Winners = lowest lt_rank -> LONG."""
    if not _fresh(path):
        return set(), set()
    data = _read_json(path)
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
    """Uncached read. -> {"long": frozenset, "short": frozenset, "source": str} of SYM_SIDE."""
    tn = top_n if top_n is not None else _top_n()
    r = Path(root) if root is not None else _root()
    longs, shorts, sources = set(), set(), []
    for venue, artifact, long_file, short_file in _VENUES:
        v_long, v_short, hit = _from_qualified(r, tn, artifact)
        label = "qualified"
        if not hit:
            v_long, v_short, hit = _from_symbol_lists(r, tn, long_file, short_file)
            label = "symbol_lists"
        if hit:
            longs |= v_long
            shorts |= v_short
            sources.append("%s:%s" % (venue, label))
    if not (longs or shorts):
        for fname in _LEGACY_FILES:
            l, s = _parse_file(r / "data" / fname, tn)
            longs |= l
            shorts |= s
        sources.append("legacy" if (longs or shorts) else "none")
    return {"long": frozenset(longs), "short": frozenset(shorts), "source": "+".join(sources) or "none"}


def ranking_sets():
    """Cached, env-gated, fail-open. -> {"long","short","pairs","boost","top_n","source"}."""
    if not _enabled():
        return {"long": frozenset(), "short": frozenset(), "pairs": frozenset(), "boost": 1.0, "top_n": _top_n(), "source": "disabled"}
    r = _root()
    tn = _top_n()
    watched = [r / "data" / f for f in _QUALIFIED_FILES] + [r / f for f, _ in _SYMBOL_FILES] + [r / "data" / f for f in _LEGACY_FILES]
    try:
        mtimes = tuple(p.stat().st_mtime if p.exists() else -1.0 for p in watched)
    except Exception:
        mtimes = ()
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
    out = {"long": sets["long"], "short": sets["short"], "pairs": pairs, "boost": boost, "top_n": tn, "source": sets["source"]}
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


if __name__ == "__main__":
    sets = ranking_sets()
    print("source=%s top_n=%s long=%d short=%d pairs=%d" % (sets["source"], sets["top_n"], len(sets["long"]), len(sets["short"]), len(sets["pairs"])))
    print("long :", ", ".join(sorted(sets["long"])[:12]))
    print("short:", ", ".join(sorted(sets["short"])[:12]))
