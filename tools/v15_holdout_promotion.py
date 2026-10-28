#!/usr/bin/env python3
"""v15_holdout_promotion - promotion must HOLD on held-out bars (USER 2026-10-27 23:0xZ: "make sure any future test stops calculating
greedily so forward tests and live trading can meet or beat the created expectations").

Measured before this was written (2026-10-27, FORWARD delta-log lines of run36/37 on s1/s2/s5/s6, each matched to the same
sym_side's latest in-sample promotion of that switch): 1,540 promotions summed +1,322.3 pp in sample and -205.3 pp on the unseen
bars after their board; only 8.2% were positive forward (4.2% above 0.48 pp), and an in-sample delta above the 0.48 pp noise floor
held forward no more often (8.3%, n 601) than one below it (8.1%, n 939). The greedy envelope is therefore not an expectation.

Rule (pure, engine injected so it is testable without NPZs):
  the board fits on bars ending at (last - holdout - confirm) days; at publication every switch whose FINAL value differs from the
  START value is scored leave-one-out on the CONFIRM window (the `confirm` days right after the fit window, never seen by the fit):
      hold  <=>  gain(final) - gain(final with that switch back at its start value) > floor
  switches that do not hold go back to their start value (pruned set). The pruned set's confirm-window gain is the board's
  EXPECTATION; the in-sample envelope is kept only as a labelled diagnostic. The gate's own OOS (last `holdout` days) is never
  touched by this choice.
A switch whose leave-one-out eval failed is kept (no evidence is not evidence of failure) and booked as `unscored`.
"""
from __future__ import annotations

ABSENT = object()


def changed_switches(final: dict, start: dict) -> list:
    """Switches whose value in `final` differs from `start` (a key missing on one side counts as a change), sorted."""
    keys = set(final) | set(start)
    return sorted(k for k in keys if final.get(k, ABSENT) != start.get(k, ABSENT))


def revert(final: dict, start: dict, switches) -> dict:
    """`final` with each switch in `switches` set back to its start value (removed when start does not carry it)."""
    out = dict(final)
    for k in switches:
        if k in start:
            out[k] = start[k]
        else:
            out.pop(k, None)
    return out


def _g(r):
    """Gain of one confirm-window result. The engine's `valid` flag carries the PUBLICATION floor (>= 10 trades); on a 7-day
    confirm window that floor is rarely met, so any result with >= 1 trade counts (measured 2026-10-28: UNH_LONG final set
    0 trades on its 7 sessions -> invalid -> every switch unscored). A 0-trade result is no evidence -> None."""
    try:
        if r is None or r.get("gain_pct") is None:
            return None
        if not r.get("valid", True) and int(r.get("trades") or 0) <= 0:
            return None
        return float(r["gain_pct"])
    except Exception:
        return None


def confirm_promotions(final: dict, start: dict, score, floor: float, max_switches: int = 200) -> dict:
    """score(named_sets: dict[name -> overrides]) -> dict[name -> result{gain_pct, valid, trades, ...}] on the CONFIRM window.
    Returns {kept, dropped, unscored, pruned (overrides), g_final, g_start, g_pruned, loo: {sw: d}, n_changed, capped}."""
    sw = changed_switches(final, start)
    capped = len(sw) > max_switches
    sw_eval = sw[:max_switches]
    sets = {"final": dict(final), "start": dict(start)}
    for k in sw_eval:
        sets["loo:" + k] = revert(final, start, [k])
    res = score(sets) or {}
    g_final, g_start = _g(res.get("final")), _g(res.get("start"))
    kept, dropped, unscored, loo = [], [], [], {}
    for k in sw:
        g_loo = _g(res.get("loo:" + k)) if k in sw_eval else None
        if g_final is None or g_loo is None:
            unscored.append(k)
            kept.append(k)
            continue
        d = g_final - g_loo
        loo[k] = round(d, 6)
        (kept if d > float(floor) else dropped).append(k)
    pruned = revert(final, start, dropped)
    g_pruned = g_final
    if dropped:
        g_pruned = _g((score({"pruned": pruned}) or {}).get("pruned"))
    return {"kept": kept, "dropped": dropped, "unscored": unscored, "pruned": pruned, "g_final": g_final, "g_start": g_start, "g_pruned": g_pruned,
            "loo": loo, "n_changed": len(sw), "capped": capped, "floor_pp": float(floor),
            "unconfirmable": g_final is None, "final_trades_confirm": int(((res.get("final") or {}).get("trades")) or 0)}


def expectation(progress: dict):
    """(expected gain %, basis) of a finished board: forward out-of-sample when the forward scorer ran, else the holdout-confirmed
    (pruned) set's confirm-window gain, else None. Never the greedy in-sample envelope."""
    fwd = progress.get("forward") if isinstance(progress.get("forward"), dict) else {}
    g = fwd.get("gain_pct") if fwd else None
    if g is None and isinstance(fwd.get("published_set"), dict):
        g = fwd["published_set"].get("gain_pct")
    if g is not None:
        return float(g), "forward_out_of_sample"
    hc = progress.get("holdout_confirm") if isinstance(progress.get("holdout_confirm"), dict) else {}
    if hc.get("g_pruned") is not None:
        return float(hc["g_pruned"]), f"holdout_confirm_{hc.get('confirm_days')}d"
    return None, "none"
