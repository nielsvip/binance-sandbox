"""HAIKU_WINNER winner-augment — live-parity twin of HaikuOverseer.manage_winners().

LANE w2-haiku. ONE pure per-bar core step() consumed by v12_quick_engine's
position loop (single call site in the LIVE-PARITY AUGMENT block); per-position
state lives on pos['haiku'], no numpy needed, no side effects.

LIVE SOURCE (ez_manage.py):
- HaikuOverseer consts :56721-56728 — AUGMENT_GAIN_THRESHOLD 3.0,
  REDUCE_GAIN_THRESHOLD 2.5, AUGMENT_FRACTION 0.10, MIN_POSITION_VALUE 5.0,
  AUGMENT_INTERVAL 60, POLL_INTERVAL 5.
- manage_winners() :57015-57178 — per-position per-poll state machine:
  gain<=0 skip (:57031-57033); entry/mark/amt<=0 skip (:57041-57042);
  value<$5 skip (:57043-57044); stale-gain guard |computed-file|>0.5pp skip
  (:57045-57060); gain<3.0 skip (:57061-57066); AUGMENT leg (gain>=3.0 AND
  gain>=config.MIN_GAIN 3.0, :57068-57128): reenter total_augmented_qty if
  reduced else min(amt*0.10, amt*0.4), $1 min notional, 60s cooldown, reasons
  HAIKU_REENTER_{gain:.1f}pct (:57087) / HAIKU_WINNER_AUG_{gain:.1f}pct
  (:57113); REDUCE leg (elif gain<2.5, managed, not reduced, total>0,
  :57143-57165): cut total_augmented_qty, $1 min notional, NO cooldown,
  reason HAIKU_REDUCE_{gain:.1f}pct<2.5 (:57158).
- run() :57337-57353 — manage_winners every AUGMENT_INTERVAL (60s).
- startup :58426-58434 — overseer starts UNCONDITIONALLY (no config gate);
  CRYPTO_ACCOUNTS only (:57018).
- config.py:128 — MIN_GAIN 3.0 (second augment gate).

VEC MAPPING (documented deltas, all conservative):
- Stale-gain guard vacuous: vec gain IS (mark-entry)/entry by construction
  (no file snapshot to go stale).
- 60s cooldown -> bars via cooldown_bars() (ceil, min 1).
- gain_pct is vs blended avg_price (live gain is vs blended avg; the engine
  passes its live_pnl_pct).
- Crypto only (MODE=='tradier' -> inert; live iterates CRYPTO_ACCOUNTS).
- Master HAIKU_WINNER_ENABLED default False = neutral until promotion; live
  runs ungated (startup has no switch). Set True to reproduce live.
- scan_decisions()/call_haiku() AI-reversal path NOT twinned (non-deterministic
  live API calls; same exclusion as vec_paths/haiku_winner.py).
- vec_paths/haiku_winner.py drift NOT copied: its WT15-crossover augment
  condition and 1.0 reduce default appear nowhere in live manage_winners().

E1 (live data/decisions/*.jsonl, 280 files): 20,477 HAIKU_AUGMENT rows;
ALGOUSDT_LONG 423, XLMUSDT_LONG 134; HAIKU_REDUCE 0 rows in window.
Walk engine had 0 functional vec refs (only dead AUTO_WIRED/QuickConfig keys).
"""
from __future__ import annotations


def params(config):
    """Resolve every threshold the live block reads, with live defaults."""
    enabled = bool(getattr(config, "HAIKU_WINNER_ENABLED", False))
    aug_thr = float(getattr(config, "HAIKU_AUGMENT_GAIN_THRESHOLD", 3.0))
    red_thr = float(getattr(config, "HAIKU_REDUCE_GAIN_THRESHOLD", 2.5))
    frac = float(getattr(config, "HAIKU_AUGMENT_FRACTION", 0.10))
    min_gain = float(getattr(config, "HAIKU_MIN_GAIN", 3.0))
    min_value = float(getattr(config, "HAIKU_MIN_POSITION_VALUE", 5.0))
    interval_s = float(getattr(config, "HAIKU_AUGMENT_INTERVAL_S", 60.0))
    return enabled, aug_thr, red_thr, frac, min_gain, min_value, interval_s


def cooldown_bars(config, bar_minutes: float) -> int:
    """HAIKU_AUGMENT_INTERVAL_S -> bars on the sim base TF (min 1 bar)."""
    try:
        cd_sec = float(getattr(config, "HAIKU_AUGMENT_INTERVAL_S", 60.0))
    except (TypeError, ValueError):
        cd_sec = 60.0
    if cd_sec <= 0:
        return 1
    bm = max(float(bar_minutes or 1.0), 1e-9)
    bars = int(-(-cd_sec // (60.0 * bm)))  # ceil
    return max(bars, 1)


def step(config, is_long: bool, px: float, entry_px: float, gain_pct: float,
         pos_qty: float, state, bar_i: int, bar_minutes: float):
    """PURE per-bar winner-augment decision + state transition.

    Args:
        config: QuickConfig (HAIKU_* keys).
        is_long: side (kept for signature parity; live gain is pre-signed).
        px: current mark price. entry_px: blended avg entry.
        gain_pct: side-adjusted gain vs entry_px (engine live_pnl_pct).
        pos_qty: current position qty. state: pos['haiku'] dict or None.
        bar_i: current bar index. bar_minutes: sim base TF minutes.

    Returns (action, qty, reason, new_state); action in {'AUGMENT', 'REDUCE',
    None}. new_state is the FULL replacement for pos['haiku'] — the engine
    commits it ONLY when it executes the action (live: state set after a
    successful order); on None the engine keeps the old state.
    """
    enabled, aug_thr, red_thr, frac, min_gain, min_value, _ = params(config)
    st = dict(state or {})
    try:
        total = float(st.get("total_augmented_qty", 0.0) or 0.0)
    except (TypeError, ValueError):
        total = 0.0
    reduced = bool(st.get("reduced", False))
    if not enabled:
        return None, 0.0, "", st
    if str(getattr(config, "MODE", "crypto") or "crypto") == "tradier":
        return None, 0.0, "", st  # live: CRYPTO_ACCOUNTS only
    if not (gain_pct > 0.0):  # live: `if gain <= 0: continue`
        return None, 0.0, "", st
    if not (entry_px > 0.0 and px > 0.0 and pos_qty > 0.0):
        return None, 0.0, "", st
    if pos_qty * px < min_value:
        return None, 0.0, "", st
    # Stale-gain guard: vacuous in vec (gain IS computed) — see docstring.
    if gain_pct >= aug_thr and gain_pct >= min_gain:
        # AUGMENT leg — live checks the 60s cooldown BEFORE sizing, both branches.
        try:
            last_bar = int(st.get("last_aug_bar", -10 ** 9))
        except (TypeError, ValueError):
            last_bar = -10 ** 9
        if (int(bar_i) - last_bar) < cooldown_bars(config, bar_minutes):
            return None, 0.0, "", st
        if reduced:
            if total <= 0.0:
                # Impossible live (reduced is set only with total>0, total never
                # resets); live would `continue` on the $1 notional check.
                return None, 0.0, "", st
            if total * px < 1.0:
                return None, 0.0, "", st
            new = dict(st)
            new["reduced"] = False
            new["last_aug_bar"] = int(bar_i)
            return "AUGMENT", float(total), f"HAIKU_REENTER_{gain_pct:.1f}pct", new
        qty = min(pos_qty * frac, pos_qty * 0.4)
        if qty * px < 1.0:
            return None, 0.0, "", st
        new = dict(st)
        new["total_augmented_qty"] = total + float(qty)
        new["last_aug_bar"] = int(bar_i)
        return "AUGMENT", float(qty), f"HAIKU_WINNER_AUG_{gain_pct:.1f}pct", new
    elif gain_pct < red_thr and total > 0.0 and not reduced:
        # REDUCE leg — live `elif`, NO cooldown.
        if total * px < 1.0:
            return None, 0.0, "", st
        new = dict(st)
        new["reduced"] = True
        return "REDUCE", float(total), f"HAIKU_REDUCE_{gain_pct:.1f}pct<{red_thr}", new
    return None, 0.0, "", st
