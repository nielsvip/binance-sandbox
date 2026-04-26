"""Scoring + mutation engine for tradier_options_shadow_runner variants.

Called by tradier_options_shadow_compare.py at EOD. Three responsibilities:

1. **Score each variant** for the day:
   - Primary: directional accuracy of BUY proposals (call symbols up + put symbols down by EOD).
   - Secondary: overlap with live's profitable opens (variant agreed with a live winner).
   - Tertiary: avg analyzer score (proposal quality proxy).

2. **Score the live config** with the same scorer so variants are comparable to the running
   production system. Live's "proposal set" = OCCs that opened today (RECONCILE_NEW events).

3. **Mutate variants** that have lost to live for >= MUTATION_LOSS_STREAK consecutive days.
   Mutation = step ONE knob within bounds. Variant identity stable; mutation history logged.

Outputs (consumed by the compare report):
- data/options_shadow/scores_<YYYYMMDD>.json     — per-variant + live scores for one day
- data/options_shadow/score_history.json         — rolling per-variant score history
- data/options_shadow/variants.json              — mutated overrides for tomorrow
- data/options_shadow/mutation_log.jsonl         — append-only mutation events
- data/options_shadow/suggestions_<YYYYMMDD>.md  — human-readable EOD suggestions
"""
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any

BASE_PATH = Path(__file__).resolve().parent
SHADOW_DIR = BASE_PATH / "data" / "options_shadow"
STATE_DIR = BASE_PATH / "data" / "options_state"

# Mutation policy
MUTATION_LOSS_STREAK = 3            # mutate after N consecutive day losses vs live
MUTATION_HISTORY_DAYS = 14          # keep last 14 days of scores per variant
SUGGESTION_WIN_STREAK = 3           # graduate variant overrides to live after N consecutive wins
PROTECT_LIVE = "live"               # the variant named "live" is never mutated

# Auto-graduation policy (owner directive 2026-04-26 — auto-promote winners)
AUTO_GRADUATE_ENABLED = True        # if False, only suggestions, no auto-write
GRADUATE_COOLDOWN_DAYS = 7          # max 1 graduation per N days (system-wide)
MAX_ACTIVE_OVERRIDES = 3            # max simultaneously-active overrides on live config
GRADUATE_MIN_PROPOSALS = 5          # variant must have proposed ≥ N items per winning day

# Knob bounds + step (used by mutator). All ranges INCLUSIVE.
KNOB_BOUNDS: Dict[str, Dict[str, Any]] = {
    "OPTIONS_BUY_MIN_WT_DC_SCORE":  {"min": 50.0,  "max": 90.0,  "step": 5.0,  "type": float},
    "OPTIONS_BUY_MIN_DTE":          {"min": 30,    "max": 90,    "step": 10,   "type": int},
    "OPTIONS_BUY_MIN_ABS_DELTA":    {"min": 0.25,  "max": 0.55,  "step": 0.05, "type": float},
    "OPTIONS_BUY_MAX_OTM_PCT":      {"min": 1.0,   "max": 5.0,   "step": 0.5,  "type": float},
    "OPTIONS_MAX_PER_SYMBOL":       {"min": 0.10,  "max": 0.25,  "step": 0.05, "type": float},
    "OPTIONS_MAX_PER_SECTOR":       {"min": 0.20,  "max": 0.45,  "step": 0.05, "type": float},
}

# Directional move thresholds (symbol must move at least this much to count as a "hit")
HIT_THRESHOLD_PCT = 0.5             # underlying moved ≥ 0.5% in proposal direction = hit


def _read_jsonl(path: Path) -> List[dict]:
    if not path.exists():
        return []
    out: List[dict] = []
    with path.open() as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                pass
    return out


def _atomic_write_json(path: Path, payload: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str))
    tmp.replace(path)


def _append_jsonl(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as f:
        f.write(json.dumps(record, default=str) + "\n")


def _collect_proposals(records: List[dict]) -> List[Tuple[str, str, str]]:
    """Return list of (symbol, side, ts) for every distinct BUY proposal across the day.
    Distinct = same (symbol, side) collapses to first-seen ts."""
    seen: Dict[Tuple[str, str], str] = {}
    for rec in records:
        for d in rec.get("decisions", []) or []:
            if d.get("action") != "BUY":
                continue
            sym = (d.get("symbol") or "").upper()
            side = d.get("option_type", "")  # "call"/"put"
            if not sym or side not in ("call", "put"):
                continue
            key = (sym, side)
            if key not in seen:
                seen[key] = rec.get("ts") or d.get("ts", "")
        for o in rec.get("opportunities", []) or []:
            sym = (o.get("symbol") or "").upper()
            side = o.get("type", "")
            if not sym or side not in ("call", "put"):
                continue
            key = (sym, side)
            if key not in seen:
                seen[key] = rec.get("ts", "")
    return [(s, sd, ts) for (s, sd), ts in seen.items()]


def _live_proposals_for_date(date_str: str) -> List[Tuple[str, str, str]]:
    """Live's 'proposals' = OCCs that opened today (RECONCILE_NEW events).
    Returns (symbol, side, ts) tuples mirroring _collect_proposals shape."""
    sf = STATE_DIR / f"{date_str}.jsonl"
    if not sf.exists():
        return []
    seen: Dict[Tuple[str, str], str] = {}
    # Walk per-tick records and emit (symbol, side) for new positions
    prev_occs: set = set()
    for rec in _read_jsonl(sf):
        cur_occs = {p.get("occ") for p in rec.get("positions", []) or [] if p.get("occ")}
        new = cur_occs - prev_occs
        prev_occs = cur_occs
        if not new:
            continue
        ts = rec.get("ts", "")
        for occ in new:
            for p in rec.get("positions", []) or []:
                if p.get("occ") == occ:
                    sym = (p.get("symbol") or "").upper()
                    side = p.get("option_type", "")
                    if sym and side in ("call", "put"):
                        seen.setdefault((sym, side), ts)
                    break
    return [(s, sd, ts) for (s, sd), ts in seen.items()]


def _score_proposals(proposals: List[Tuple[str, str, str]],
                     eod_quotes: Dict[str, Dict[str, float]]) -> dict:
    """Score = directional hit rate (calls up, puts down) using EOD vs proposal-time price.

    For now we use EOD price vs DAY-OPEN price as a simplification — proposal-time
    quotes would be more accurate but require historical intraday quotes.
    """
    n_total = len(proposals)
    if n_total == 0:
        return {"n_proposals": 0, "n_hits": 0, "n_misses": 0, "n_skipped": 0,
                "hit_rate": 0.0, "avg_move_pct_in_dir": 0.0}
    hits = 0
    misses = 0
    skipped = 0
    move_sum = 0.0
    move_n = 0
    for sym, side, _ts in proposals:
        q = eod_quotes.get(sym)
        if not q or "open" not in q or "close" not in q or q["open"] == 0:
            skipped += 1
            continue
        move_pct = (q["close"] - q["open"]) / q["open"] * 100.0
        # Directional sign: +move favors call, -move favors put
        in_dir = move_pct if side == "call" else -move_pct
        if in_dir >= HIT_THRESHOLD_PCT:
            hits += 1
            move_sum += in_dir
            move_n += 1
        elif in_dir <= -HIT_THRESHOLD_PCT:
            misses += 1
            move_sum += in_dir
            move_n += 1
        else:
            move_sum += in_dir
            move_n += 1
    n_scored = hits + misses
    return {
        "n_proposals": n_total,
        "n_hits": hits,
        "n_misses": misses,
        "n_skipped": skipped,
        "hit_rate": round(hits / max(1, n_scored), 4),
        "avg_move_pct_in_dir": round(move_sum / max(1, move_n), 3),
    }


async def fetch_eod_quotes(symbols: List[str]) -> Dict[str, Dict[str, float]]:
    """Get one-day OHLC for each underlying. Uses TradierAPIClient.get_history with daily interval."""
    if not symbols:
        return {}
    sys.path.insert(0, str(BASE_PATH))
    from config_tradier import TradierConfig
    from tradier_api import TradierAPIClient
    config = TradierConfig()
    client = TradierAPIClient(config=config, account_key="trb")
    setattr(client, "_account_key", "trb")
    out: Dict[str, Dict[str, float]] = {}
    today = datetime.now(timezone.utc).date().isoformat()
    try:
        for sym in symbols:
            try:
                bars = await client.get_history(symbol=sym, start=today, end=today, interval="daily")
                if bars:
                    b = bars[-1]
                    out[sym] = {
                        "open": float(b.get("open", 0) or 0),
                        "high": float(b.get("high", 0) or 0),
                        "low": float(b.get("low", 0) or 0),
                        "close": float(b.get("close", 0) or 0),
                    }
            except Exception:
                pass
    finally:
        await client.close()
    return out


def _load_score_history() -> Dict[str, List[dict]]:
    p = SHADOW_DIR / "score_history.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {}


def _save_score_history(history: Dict[str, List[dict]]) -> None:
    _atomic_write_json(SHADOW_DIR / "score_history.json", history)


def _update_history(history: Dict[str, List[dict]], date_str: str,
                    per_variant_scores: Dict[str, dict],
                    live_score: dict) -> Dict[str, List[dict]]:
    """Append today's scores (vs live delta) and trim to MUTATION_HISTORY_DAYS."""
    live_hr = live_score.get("hit_rate", 0.0)
    for name, sc in per_variant_scores.items():
        if name == PROTECT_LIVE:
            continue
        delta = sc.get("hit_rate", 0.0) - live_hr
        history.setdefault(name, []).append({
            "date": date_str,
            "hit_rate": sc.get("hit_rate"),
            "n_proposals": sc.get("n_proposals"),
            "live_hit_rate": live_hr,
            "delta_vs_live": round(delta, 4),
            "won_vs_live": delta > 0,
        })
        # Trim
        history[name] = history[name][-MUTATION_HISTORY_DAYS:]
    return history


def _streak(history_list: List[dict], won: bool) -> int:
    streak = 0
    for entry in reversed(history_list):
        if bool(entry.get("won_vs_live")) == won:
            streak += 1
        else:
            break
    return streak


def _mutate_one_knob(overrides: Dict[str, Any]) -> Tuple[Dict[str, Any], dict]:
    """Pick one knob (existing override OR new knob) and step it ±1 step within bounds.
    Returns (new_overrides, mutation_event_dict)."""
    candidates = list(KNOB_BOUNDS.keys())
    knob = random.choice(candidates)
    bounds = KNOB_BOUNDS[knob]
    cur = overrides.get(knob)
    if cur is None:
        # Variant didn't override this knob — start at midrange
        mid = (bounds["min"] + bounds["max"]) / 2
        new_val = bounds["type"](mid)
        direction = "init"
    else:
        # Step ±1 step in random direction, clamped
        sign = random.choice([-1, 1])
        new_val_raw = cur + sign * bounds["step"]
        new_val_raw = max(bounds["min"], min(bounds["max"], new_val_raw))
        new_val = bounds["type"](new_val_raw)
        direction = "up" if sign > 0 else "down"
    new_overrides = dict(overrides)
    new_overrides[knob] = new_val
    event = {
        "knob": knob,
        "old_value": cur,
        "new_value": new_val,
        "direction": direction,
    }
    return new_overrides, event


def _mutate_variants(variants: Dict[str, Dict[str, Any]],
                     history: Dict[str, List[dict]],
                     date_str: str) -> Tuple[Dict[str, Dict[str, Any]], List[dict]]:
    """For each non-live variant with ≥ MUTATION_LOSS_STREAK consecutive losses, mutate one knob.

    Returns (new_variants_dict, list_of_mutation_events)."""
    new_variants = dict(variants)
    events: List[dict] = []
    for name in list(variants.keys()):
        if name == PROTECT_LIVE:
            continue
        h = history.get(name, [])
        loss_streak = _streak(h, won=False)
        if loss_streak < MUTATION_LOSS_STREAK:
            continue
        cur_overrides = variants.get(name, {}) or {}
        new_overrides, ev = _mutate_one_knob(cur_overrides)
        ev.update({
            "ts": datetime.now(timezone.utc).isoformat(),
            "variant": name,
            "loss_streak_before": loss_streak,
            "date_triggered": date_str,
            "old_overrides": cur_overrides,
            "new_overrides": new_overrides,
        })
        new_variants[name] = new_overrides
        events.append(ev)
        _append_jsonl(SHADOW_DIR / "mutation_log.jsonl", ev)
    return new_variants, events


def _load_active_overrides() -> dict:
    p = SHADOW_DIR / "live_overrides_active.json"
    if not p.exists():
        return {"ts_last_updated": None, "active_overrides": {}}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"ts_last_updated": None, "active_overrides": {}}


def _save_active_overrides(payload: dict) -> None:
    _atomic_write_json(SHADOW_DIR / "live_overrides_active.json", payload)


def _last_graduation_age_days() -> Optional[float]:
    """Return days since last GRADUATE event in mutation_log.jsonl, or None if never."""
    p = SHADOW_DIR / "mutation_log.jsonl"
    if not p.exists():
        return None
    last_ts = None
    for rec in _read_jsonl(p):
        if rec.get("event") == "GRADUATE":
            last_ts = rec.get("ts")
    if not last_ts:
        return None
    try:
        last_dt = datetime.fromisoformat(last_ts.replace("Z", "+00:00"))
        return (datetime.now(timezone.utc) - last_dt).total_seconds() / 86400.0
    except Exception:
        return None


def _auto_graduate(variants: Dict[str, Dict[str, Any]],
                   history: Dict[str, List[dict]],
                   per_variant_scores: Dict[str, dict],
                   date_str: str) -> List[dict]:
    """Auto-promote variant overrides to live when they beat live for SUGGESTION_WIN_STREAK days.

    Safeguards:
    - AUTO_GRADUATE_ENABLED master switch.
    - GRADUATE_COOLDOWN_DAYS — at most one graduation system-wide per N days.
    - MAX_ACTIVE_OVERRIDES — caps how many overrides can be live at once.
    - GRADUATE_MIN_PROPOSALS — each winning day must have ≥ N proposals (avoid lucky tiny samples).
    - Each graduation logs both forward (apply) and rollback values for easy revert.
    - Resets variant's win-streak history after graduation (must re-win to graduate again).

    Returns list of graduation events (also logged to mutation_log.jsonl)."""
    if not AUTO_GRADUATE_ENABLED:
        return []
    cooldown_age = _last_graduation_age_days()
    if cooldown_age is not None and cooldown_age < GRADUATE_COOLDOWN_DAYS:
        return []
    active = _load_active_overrides()
    active_overrides = dict(active.get("active_overrides") or {})
    if len(active_overrides) >= MAX_ACTIVE_OVERRIDES:
        return []
    events: List[dict] = []
    candidates: List[Tuple[str, int, float, dict]] = []
    for name, h in history.items():
        if name == PROTECT_LIVE:
            continue
        win_streak = _streak(h, True)
        if win_streak < SUGGESTION_WIN_STREAK:
            continue
        # Each winning day must have ≥ GRADUATE_MIN_PROPOSALS to count
        recent = h[-win_streak:]
        if any((e.get("n_proposals") or 0) < GRADUATE_MIN_PROPOSALS for e in recent):
            continue
        avg_delta = sum(e["delta_vs_live"] for e in recent) / max(1, len(recent))
        ovr = variants.get(name, {})
        if not ovr:
            continue  # variant has no overrides to graduate (it IS live)
        candidates.append((name, win_streak, avg_delta, ovr))
    # Best candidate first (largest avg delta)
    candidates.sort(key=lambda x: -x[2])
    for name, ws, avg_d, ovr in candidates:
        if len(active_overrides) >= MAX_ACTIVE_OVERRIDES:
            break
        # Pick the override knob(s) — for now graduate ALL of this variant's overrides
        for knob, val in ovr.items():
            if knob in active_overrides:
                # Knob already active from another variant — skip (would conflict)
                continue
            # Sanity check the new value against bounds
            if knob in KNOB_BOUNDS:
                b = KNOB_BOUNDS[knob]
                if not (b["min"] <= val <= b["max"]):
                    continue
            rollback_val = None
            try:
                from config_tradier import TradierConfig as _TC
                rollback_val = getattr(_TC(), knob, None)
            except Exception:
                pass
            active_overrides[knob] = {
                "value": val,
                "graduated_from": name,
                "graduated_ts": datetime.now(timezone.utc).isoformat(),
                "win_streak_at_graduation": ws,
                "avg_delta_vs_live": round(avg_d, 4),
                "rollback_value": rollback_val,
            }
            ev = {
                "ts": datetime.now(timezone.utc).isoformat(),
                "event": "GRADUATE",
                "variant": name,
                "knob": knob,
                "new_value": val,
                "rollback_value": rollback_val,
                "win_streak": ws,
                "avg_delta_vs_live": round(avg_d, 4),
                "date_triggered": date_str,
            }
            events.append(ev)
            _append_jsonl(SHADOW_DIR / "mutation_log.jsonl", ev)
        # Reset this variant's history so it must earn the streak again
        if name in history:
            history[name] = []
    if events:
        active["active_overrides"] = active_overrides
        active["ts_last_updated"] = datetime.now(timezone.utc).isoformat()
        _save_active_overrides(active)
        # Persist the trimmed history so reset takes effect tomorrow
        _save_score_history(history)
    return events


def _emit_suggestions(date_str: str, per_variant_scores: Dict[str, dict],
                      live_score: dict, history: Dict[str, List[dict]],
                      mutation_events: List[dict],
                      variants: Dict[str, Dict[str, Any]],
                      graduation_events: Optional[List[dict]] = None,
                      active_overrides: Optional[dict] = None) -> Path:
    out_path = SHADOW_DIR / f"suggestions_{date_str}.md"
    lines: List[str] = []
    lines.append(f"# Options shadow EOD report — {date_str}\n")
    lines.append(f"_Generated {datetime.now(timezone.utc).isoformat()}_\n")
    # Live
    lines.append("## Live config performance\n")
    lines.append(f"- proposals: {live_score.get('n_proposals', 0)}  "
                 f"hits: {live_score.get('n_hits', 0)}  "
                 f"misses: {live_score.get('n_misses', 0)}  "
                 f"skipped: {live_score.get('n_skipped', 0)}\n")
    lines.append(f"- hit rate: **{live_score.get('hit_rate', 0)*100:.1f}%**  "
                 f"avg in-direction move: **{live_score.get('avg_move_pct_in_dir', 0):+.2f}%**\n")
    # Per variant
    lines.append("\n## Variant scores (sorted by hit rate desc)\n")
    rows = sorted(per_variant_scores.items(),
                  key=lambda kv: -kv[1].get("hit_rate", 0))
    lines.append("| variant | hit_rate | n_proposals | hits | misses | Δ vs live | win streak | loss streak | overrides |")
    lines.append("|---|---|---|---|---|---|---|---|---|")
    for name, sc in rows:
        h = history.get(name, [])
        win_streak = _streak(h, True)
        loss_streak = _streak(h, False)
        delta = sc.get("hit_rate", 0) - live_score.get("hit_rate", 0)
        ovr = variants.get(name, {})
        ovr_str = ", ".join(f"{k}={v}" for k, v in ovr.items()) or "—"
        lines.append(f"| {name} | {sc.get('hit_rate',0)*100:.1f}% | "
                     f"{sc.get('n_proposals',0)} | {sc.get('n_hits',0)} | {sc.get('n_misses',0)} | "
                     f"{delta*100:+.1f}pp | {win_streak} | {loss_streak} | {ovr_str} |")
    # Mutations applied tonight
    lines.append("\n## Mutations applied for tomorrow\n")
    if not mutation_events:
        lines.append("_No mutations — no variant has lost to live for {} consecutive days._".format(MUTATION_LOSS_STREAK))
    else:
        for ev in mutation_events:
            lines.append(f"- **{ev['variant']}** (loss streak: {ev['loss_streak_before']}): "
                         f"{ev['knob']} {ev['old_value']} → {ev['new_value']} ({ev['direction']})")
    # Auto-graduations applied tonight
    lines.append("\n## Auto-graduations applied to live config tonight\n")
    if not graduation_events:
        lines.append("_No graduations — no variant met all criteria (win streak ≥ {}, ≥ {} proposals/day, "
                     "cooldown clean, < {} active overrides)._".format(
                         SUGGESTION_WIN_STREAK, GRADUATE_MIN_PROPOSALS, MAX_ACTIVE_OVERRIDES))
    else:
        lines.append("**The following overrides were AUTO-WRITTEN to `data/options_shadow/live_overrides_active.json` "
                     "and will be applied at next live agent restart:**\n")
        for ev in graduation_events:
            rb = ev.get("rollback_value")
            rb_str = f" (rollback: {rb})" if rb is not None else ""
            lines.append(f"- **GRADUATED** `{ev['knob']}` ← {ev['new_value']}{rb_str} "
                         f"from variant **{ev['variant']}** "
                         f"(won {ev['win_streak']} days, avg +{ev['avg_delta_vs_live']*100:.1f}pp vs live)")
        lines.append("\nTo manually revert: edit or delete `data/options_shadow/live_overrides_active.json`.")
    # Currently-active overrides
    lines.append("\n## Currently active runtime overrides on live config\n")
    if not active_overrides:
        lines.append("_None — live config running on stock `config_tradier.py` defaults._")
    else:
        lines.append("| knob | value | rollback | from variant | graduated |")
        lines.append("|---|---|---|---|---|")
        for k, info in active_overrides.items():
            if isinstance(info, dict):
                lines.append(f"| `{k}` | {info.get('value')} | {info.get('rollback_value')} | "
                             f"{info.get('graduated_from','?')} | {info.get('graduated_ts','?')[:10]} |")
            else:
                lines.append(f"| `{k}` | {info} | — | manual | — |")
    # Pending suggestion candidates (not yet graduated, e.g. cooldown active)
    lines.append("\n## Pending suggestion candidates (won {}+ days but blocked by cooldown / cap)\n".format(SUGGESTION_WIN_STREAK))
    candidates = []
    for name, h in history.items():
        if name == PROTECT_LIVE:
            continue
        win_streak = _streak(h, True)
        if win_streak >= SUGGESTION_WIN_STREAK:
            avg_delta = sum(e["delta_vs_live"] for e in h[-win_streak:]) / win_streak
            candidates.append((name, win_streak, avg_delta, variants.get(name, {})))
    if not candidates:
        lines.append("_None._")
    else:
        for name, ws, avg_d, ovr in sorted(candidates, key=lambda x: -x[2]):
            ovr_str = ", ".join(f"{k}={v}" for k, v in ovr.items()) or "—"
            lines.append(f"- **{name}**: won {ws} days running, avg +{avg_d*100:.1f}pp vs live. "
                         f"Pending: `{ovr_str}` (will graduate when cooldown clears)")
    out_path.write_text("\n".join(lines) + "\n")
    return out_path


async def score_and_mutate(date_str: str,
                           per_variant_proposals: Dict[str, List[Tuple[str, str, str]]],
                           variants: Dict[str, Dict[str, Any]],
                           variants_path: Path) -> dict:
    """Main entry point called by compare.py. Returns full report dict."""
    # 1. Collect every unique underlying needed for EOD quotes
    all_syms: set = set()
    for proposals in per_variant_proposals.values():
        for sym, _side, _ts in proposals:
            all_syms.add(sym)
    live_proposals = _live_proposals_for_date(date_str)
    for sym, _side, _ts in live_proposals:
        all_syms.add(sym)
    # 2. Fetch EOD quotes (one batched call per symbol)
    eod_quotes = await fetch_eod_quotes(sorted(all_syms))
    # 3. Score each variant + live
    per_variant_scores = {}
    for name, proposals in per_variant_proposals.items():
        per_variant_scores[name] = _score_proposals(proposals, eod_quotes)
    live_score = _score_proposals(live_proposals, eod_quotes)
    # 4. Update history
    history = _load_score_history()
    history = _update_history(history, date_str, per_variant_scores, live_score)
    _save_score_history(history)
    # 5. Mutate underperforming variants for tomorrow
    new_variants, mutation_events = _mutate_variants(variants, history, date_str)
    if mutation_events:
        _atomic_write_json(variants_path, new_variants)
    # 6. Auto-graduate variants that beat live for SUGGESTION_WIN_STREAK days (owner directive)
    graduation_events = _auto_graduate(variants, history, per_variant_scores, date_str)
    active_overrides = (_load_active_overrides() or {}).get("active_overrides") or {}
    # 7. Emit human-readable suggestions
    sugg_path = _emit_suggestions(date_str, per_variant_scores, live_score,
                                  history, mutation_events, variants,
                                  graduation_events=graduation_events,
                                  active_overrides=active_overrides)
    # 8. Save scores file
    scores_payload = {
        "date": date_str,
        "ts": datetime.now(timezone.utc).isoformat(),
        "live_score": live_score,
        "per_variant_scores": per_variant_scores,
        "n_eod_quotes": len(eod_quotes),
        "n_mutations": len(mutation_events),
        "mutations": mutation_events,
        "n_graduations": len(graduation_events),
        "graduations": graduation_events,
        "active_overrides": active_overrides,
        "suggestions_path": str(sugg_path),
    }
    _atomic_write_json(SHADOW_DIR / f"scores_{date_str}.json", scores_payload)
    return scores_payload
