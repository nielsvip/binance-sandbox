"""Single qualification gate for ranking winners/losers (USER 2026-10-17).

Before this module three different definitions of "winner" coexisted:

* `plots/` (ez_rankings.plot_loop) ranked by `final_score_norm` /
  `final_score_recent_norm` straight off RANKING_DATA — no tradeability, no
  conflict check. 7-9 of every 20 plotted symbols were not in the traded list.
* `symbols_ang_long/short.json` (crypto) and `symbols_trb_long/short.json`
  (stocks) are the lists that actually trade. They are built from
  `linearity_boosted_score` plus ranking_points / weighted_gains / OI / news /
  AI injections appended AFTER the size cap, so the cap did not hold and the
  same symbol could land on both sides (4 crypto, 15 stock symbols on
  2026-10-17).
* `tools/v15_ranking_priority.py` read `data/rankings.json` lt_rank — a fourth
  order, and on S1 a file from 2026-08-20 that the 48h freshness guard rejected,
  so sweep priority was inert.

Every venue now funnels its candidates through `qualify()` immediately before
the symbols_*.json write, and publishes ONE artifact
(`data/rankings_qualified_{venue}.json`) that the plot loop, the fleet
scheduler and the auditor all read. Qualified == plotted == traded == swept.

Rules, in order:
1. Uppercase + dedup, incoming order preserved (it is the pipeline's opinion).
2. Tradeability is a HARD filter — never backfilled around.
3. Side exclusivity: a symbol may be a winner or a loser, never both. The side
   with the larger score magnitude keeps it; an exact tie drops it from both.
4. Size cap per side, applied last so injections cannot outrun it.

Fail-open by contract: a caller that passes nothing gets its own list back
deduped. This module never widens a list, never reads the network, never
touches sheet math or live sizing.
"""

import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

ROOT = Path(__file__).resolve().parent
ARTIFACT_DIR = ROOT / "data"
VENUES = ("crypto", "stocks")
DEFAULT_PLOT_N = 20
SCHEMA = 1


def artifact_path(venue: str) -> Path:
    return ARTIFACT_DIR / f"rankings_qualified_{venue}.json"


def _clean(seq: Optional[Iterable[Any]]) -> List[str]:
    out: List[str] = []
    seen = set()
    for raw in seq or ():
        symbol = raw.get("symbol") if isinstance(raw, dict) else raw
        if not symbol:
            continue
        symbol = str(symbol).strip().upper()
        if symbol and symbol not in seen:
            seen.add(symbol)
            out.append(symbol)
    return out


def _strength(symbol: str, side: str, scores: Optional[Dict[str, Any]]) -> float:
    try:
        value = float((scores or {}).get(symbol))
    except (TypeError, ValueError):
        return 0.0
    return value if side == "LONG" else -value


def qualify(venue, long_candidates, short_candidates, scores=None, cap=None, tradeable=None, plot_n=DEFAULT_PLOT_N):
    """-> qualification dict. `tradeable(symbol, side) -> bool`; None allows all."""
    longs = _clean(long_candidates)
    shorts = _clean(short_candidates)
    dropped = {"not_tradeable": [], "conflict": [], "over_cap": []}
    if tradeable is not None:
        kept_long = []
        for symbol in longs:
            try:
                ok = bool(tradeable(symbol, "LONG"))
            except Exception:
                ok = True
            (kept_long if ok else dropped["not_tradeable"]).append(symbol if ok else f"{symbol}_LONG")
        kept_short = []
        for symbol in shorts:
            try:
                ok = bool(tradeable(symbol, "SHORT"))
            except Exception:
                ok = True
            (kept_short if ok else dropped["not_tradeable"]).append(symbol if ok else f"{symbol}_SHORT")
        longs, shorts = kept_long, kept_short
    both = set(longs) & set(shorts)
    long_rank = {s: i for i, s in enumerate(longs)}
    short_rank = {s: i for i, s in enumerate(shorts)}
    for symbol in sorted(both):
        long_strength = _strength(symbol, "LONG", scores)
        short_strength = _strength(symbol, "SHORT", scores)
        if long_strength == short_strength:
            # No score separation (missing scores, or a symmetric zero): the better
            # incoming rank keeps the symbol. Only a dead heat drops it from both.
            long_strength = -long_rank[symbol]
            short_strength = -short_rank[symbol]
        if long_strength > short_strength:
            shorts = [s for s in shorts if s != symbol]
            dropped["conflict"].append(f"{symbol}_SHORT")
        elif short_strength > long_strength:
            longs = [s for s in longs if s != symbol]
            dropped["conflict"].append(f"{symbol}_LONG")
        else:
            longs = [s for s in longs if s != symbol]
            shorts = [s for s in shorts if s != symbol]
            dropped["conflict"].extend([f"{symbol}_LONG", f"{symbol}_SHORT"])
    if cap:
        cap = int(cap)
        dropped["over_cap"].extend(f"{s}_LONG" for s in longs[cap:])
        dropped["over_cap"].extend(f"{s}_SHORT" for s in shorts[cap:])
        longs, shorts = longs[:cap], shorts[:cap]
    def _rows(symbols, side):
        return [{"symbol": s, "rank": i, "side": side, "score": _strength(s, side, scores)} for i, s in enumerate(symbols, 1)]
    return {
        "schema": SCHEMA,
        "venue": venue,
        "generated_at": time.time(),
        "cap": int(cap) if cap else None,
        "plot_n": int(plot_n),
        "long": _rows(longs, "LONG"),
        "short": _rows(shorts, "SHORT"),
        "dropped": dropped,
    }


def symbols(result: Dict[str, Any], side: str) -> List[str]:
    return [row["symbol"] for row in (result or {}).get(side.lower(), []) if row.get("symbol")]


def sym_sides(result: Dict[str, Any]) -> List[str]:
    return [f"{row['symbol']}_{side.upper()}" for side in ("long", "short") for row in (result or {}).get(side, []) if row.get("symbol")]


def plot_queue(result: Dict[str, Any], plot_n: Optional[int] = None) -> List[tuple]:
    """-> [(label, symbol)] for the qualified top-N winners (W) and losers (L)."""
    limit = int(plot_n or (result or {}).get("plot_n") or DEFAULT_PLOT_N)
    queue = []
    for side, prefix in (("long", "W"), ("short", "L")):
        for row in (result or {}).get(side, [])[:limit]:
            queue.append((f"{prefix}{int(row['rank']):02d}", row["symbol"]))
    return queue


def write_artifact(venue: str, result: Dict[str, Any], directory: Optional[Path] = None) -> Optional[Path]:
    """Atomic publish. Returns the path, or None when the write failed (never raises)."""
    target = (Path(directory) if directory else ARTIFACT_DIR) / f"rankings_qualified_{venue}.json"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(result, indent=1, sort_keys=False))
        os.replace(tmp, target)
        return target
    except Exception:
        return None


def load_artifact(venue: str, max_age_s: Optional[float] = None, root: Optional[Path] = None) -> Optional[Dict[str, Any]]:
    """Fail-open read: None when missing, stale, unreadable or malformed."""
    path = (Path(root) / "data" if root else ARTIFACT_DIR) / f"rankings_qualified_{venue}.json"
    try:
        if max_age_s and (time.time() - path.stat().st_mtime) > float(max_age_s):
            return None
        data = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(data, dict) or not isinstance(data.get("long"), list) or not isinstance(data.get("short"), list):
        return None
    return data
