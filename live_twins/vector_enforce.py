"""vector_enforce — USER 2026-10-21 vector-copy-trading law. THE SWITCHES AND NOTHING ELSE.

#1  NO TRADE exits unless due to a CROSS (board cross exit, channel break, regime flip
    confirmed by the switches, board-lane intent, owed exit, or the exchange backstop).
#2  NO trade enters or exits except at a 15m bar change (first VECTOR_BAR_WINDOW_S seconds
    after :00/:15/:30/:45 UTC). The vector evaluates on closed bars; mid-bar decisions wait.
#3  NO trade not confirmed by the switch calculations may EVER execute. A violating family is
    IMMEDIATELY refused, DISABLED in the kill registry, and FLAGGED — it can NEVER act again
    until manually restored (tools/vector_violations.py restore).

Kill scope is the reason FAMILY, venue-wide, entries and exits. Every NEW kill also files
a retrace work order (data/vector_retrace_queue.jsonl, BIBLE §126.1): an agent vectorizes
it (switch + shared scalar/vec core, both venues) or notes non-vectorizable (stays killed).
Kill switches:
VECTOR_ENFORCE_ENABLED (default True; False restores pre-law behavior for everything below).
Never touched here: exchange STOP_MARKET backstops (not decisions), broker-reconcile bookkeeping,
correctness (bad key/price/dup), infra freshness, locks/dedup/cooldowns, loss halts.
"""

import json
import os
import re
import time
from pathlib import Path

ROOT = Path(os.environ.get("BASE_PATH") or Path(__file__).resolve().parents[1])
KILLS = ROOT / "data" / "vector_kills.json"
VIOLATIONS = ROOT / "data" / "vector_violations.jsonl"
RETRACE_QUEUE = ROOT / "data" / "vector_retrace_queue.jsonl"
RESOLUTIONS = ROOT / "data" / "vector_retrace_resolutions.jsonl"
MONITOR_LOG = ROOT / "data" / "vector_family_monitor.jsonl"
# USER 2026-10-21: families under parity watch — every check() verdict is logged
# for the monitor until a final verdict lands (tools/vector_family_monitor.py).
MONITORED_FAMILIES = frozenset(
    {"VEL_EXIT", "EXIT", "TECHNICAL", "REDUCE_K", "RATIO", "BBKC", "BB_RECOVERY"}
)
BAR_S = 900

# Exit reason families that ARE a cross (board cross exits, breaks, flips), board-lane
# intents, owed exits, and the backstop. Everything else that closes/reduces is a violation.
CROSS_PATTERNS = (
    "CROSS",
    "FLIP",
    "BREAK",
    "BREACH",
    "LH_LL",
    "REVERSE",
    "RE-CROSS",
    "RECROSS",
    "DC_LOW",
    "DC_HIGH",
    "STOP",
    "VEC_DRIVEN",
    "RECONCILE",
    "EXIT_OWED",
    "OBLIGATORY",
    "FORCED",
    "GUARANTEE",
    "RECONCILED",
)
BOOKKEEPING = ("BROKER_RECONCILED",)

_KILLS = {"loaded": 0.0, "killed": {}}


def enabled(cfg_mod=None) -> bool:
    try:
        import config as _c

        mod = cfg_mod if cfg_mod is not None else _c.Config
        v = getattr(mod, "VECTOR_ENFORCE_ENABLED", True)
        return (
            v
            if isinstance(v, bool)
            else str(v).strip().lower() not in ("0", "false", "no", "off")
        )
    except Exception:
        return True


def bar_window_s() -> float:
    try:
        import config as _c

        return float(getattr(_c.Config, "VECTOR_BAR_WINDOW_S", 180.0))
    except Exception:
        return 180.0


def in_bar_window(now=None) -> bool:
    try:
        t = now if now is not None else time.time()
        return (t % BAR_S) < max(1.0, bar_window_s())
    except Exception:
        return True


def _load_kills() -> dict:
    try:
        if KILLS.exists() and KILLS.stat().st_mtime > _KILLS["loaded"]:
            _KILLS["killed"] = json.loads(KILLS.read_text() or "{}")
            _KILLS["loaded"] = KILLS.stat().st_mtime
    except Exception:
        pass
    return _KILLS["killed"]


def _family_of(reason: str) -> str:
    """USER 2026-10-21 retrace: QUICK_* is a DISPATCHER category (the whole twin lane),
    never a killable family — the family is the embedded rec token (SCALP_REDUCE, ...).
    A bare QUICK_* with no rec token maps to QUICK_UNATTRIBUTED (refused+flagged, never
    killed — there is nothing attributable to disable)."""
    full = str(reason or "").upper()
    if not full:
        return "UNKNOWN"
    for lead in ("CLOSE ", "REDUCE ", "STRONG_REDUCE ", "OPEN ", "AUGMENT "):
        if full.startswith(lead):
            full = full[len(lead):]
            break
    # USER 2026-10-22 parity restores: faithful-twin reasons whose head names a
    # killed parent but whose body names the modeled member. BB_BAND_EXIT_LIVE
    # is the shared-core twin of the booked BB_EXIT_AT_LOSS_TF /
    # BB_PROFIT_TAKE_TF legs; E_1_WT_DELTA_EXIT is the booked e1_wt_delta leg.
    if full.startswith("BB_BAND_EXIT_LIVE_BB_LOSS"):
        return "BB_LOSS"
    if full.startswith("BB_BAND_EXIT_LIVE_BB_TAKE"):
        return "BB_TAKE"
    if full.startswith("E_1_WT_DELTA_EXIT"):
        return "E_1"
    # USER 2026-10-23 hawk E3: E_3_STRUCTURE_EXIT_{n}TF_{tfs} (both live lanes
    # + grey-wire booking) is its own narrow family. The old tradier
    # E3_STRUCT_EXIT_{n}of3 cross-analogue shape maps to E3_ANALOGUE (never
    # listed — a stale live process firing it stays refused).
    if full.startswith("E_3_STRUCTURE_EXIT"):
        return "E3"
    if full.startswith("E3_STRUCT_EXIT"):
        return "E3_ANALOGUE"
    if re.fullmatch(
        r"(?:QUICK_)?BB_RECOVERY_EXIT_TRADIER_(?:LONG|SHORT)(?:_PX\d+(?:\.\d+)?_K\d+(?:\.\d+)?)?",
        full,
    ):
        return "BB_RECOVERY_SWITCHED"
    # Bottom/top live reasons are prefixed with BT_ by ez_manage, while the
    # vector books the same switch-backed masks without that wrapper. Keep each
    # family narrow so the historical broad BT kill cannot swallow a modeled
    # member or accidentally authorize a sibling.
    for _bt_family in (
        "RSI2_XTREME_EXIT",
        "STOCH_XTREME_EXIT",
        "WICK_REJECT_EXIT",
        # This exact crypto switch reason is booked by v12 with the matching
        # mid-band/K mask and fee-positive close gate. Keep the old broad
        # BB_RECOVERY kill active for every other reason in that family.
        "BB_RECOVERY_SWITCHED",
    ):
        if full.startswith(f"BT_{_bt_family}"):
            return _bt_family
    # USER 2026-10-22 parity restore: narrow hopeless family — the generic
    # fallback would map every DC_* reason (DC_BREACH_*, DC_BB_D_BREAK_*,
    # DC_15M_HOLD_OVERRIDE, ...) to "DC". Only the hopeless exit is restored.
    if full.startswith("DC_HOPELESS"):
        return "DC_HOPELESS"
    # USER 2026-10-24 retrace DELTA/DC_BASIS TODOs: narrow DC_BASIS family for
    # the switch-gated 3m-DC legs (shared twin
    # vec_decisions/process_position_crypto__dc_basis_3m_reduce, v12-walked).
    # DC_BASIS_PROFIT_EXIT (no vec twin) is gated default-OFF by its own
    # switch and stays dead unless a board promotes it with a twin.
    if full.startswith("DC_BASIS"):
        return "DC_BASIS"
    # USER 2026-10-22 PPL re-augment switch (2026-10-23: trigger modes
    # DC_RECLAIM/WT_CROSS/WT_SLOWDOWN share this prefix): narrow family for
    # the switch-gated re-augment ONLY. The broad "PPL" kill (standing-order
    # PPL_TP, operator-open question) must keep refusing PPL_TP_*/PPL_SL_*.
    if full.startswith("PPL_REAUGMENT"):
        return "PPL_REAUGMENT"
    # USER 2026-10-23 hawk PROFIT_TARGET: the switch-gated pct take-profit
    # (v12 leg + both live twins, default OFF, 25 per-sym backtest holds) is
    # its own narrow family. The broad "PROFIT" kill keeps refusing
    # PROFIT_LOCK / PROFIT_TAKE / PROFIT_TP_EXHAUSTION shapes.
    if full.startswith("PROFIT_TARGET"):
        return "PROFIT_TARGET"
    if full.startswith("QUICK_"):
        rest = full[len("QUICK_") :]
        for act in ("REDUCE_", "CLOSE_", "OPEN_", "AUGMENT_", "QUICK_"):
            if rest.startswith(act):
                rest = rest[len(act) :]
                break
        tok = [t for t in rest.replace("|", "_").replace(" ", "_").split("_") if t]
        if len(tok) >= 2:
            return "_".join(tok[:2])[:40]
        return "QUICK_UNATTRIBUTED"
    for known in (
        "BB_TAKE",
        "BB_LOSS",
        "STOCH_CROSS_3M_EXIT",
        "SCALP_REDUCE",
        "STRONG_REDUCE",
        "VEL_EXIT",
        "MAKER_PROFIT_EXIT",
        "DAYTRADE_TARGET",
        "WRONG_SIDE",
        "CYCLE_TP_TIERED",
        "ALL_TF_AGAINST",
        "GAIN_EROSION",
        "DC_BREACH",
        "BREAKOUT_LEASH",
        "LH_LL",
        "R3_HTF",
        "GOLDEN_RULE",
        "VEC_DRIVEN",
        "BOARD_CONVERGE",
        "RECONCILE",
        "EXIT_OWED",
        "HARD_STOP",
        "TRAILING",
        "FIXED_STOP",
        "OBLIGATORY",
        "BROKER_RECONCILED",
    ):
        if (
            full == known
            or full.startswith(known + "_")
            or full.startswith(known + " ")
        ):
            return known
    return (full.split("_")[0].split(" ")[0].split("|")[0].split(":")[0] or "UNKNOWN")[
        :40
    ]


def family_of(reason: str) -> str:
    return _family_of(reason)


# Vector-modeled, switch-gated exit families (shared vec_decisions core, pilot-tested
# switches like BB_PROFIT_TAKE_TF — 174 boards promote it). Lawful when their switch says
# so: they skip the kill (even a stale kill entry) but keep the bar window.
# NOT vectorizable = NOT here. Modeled is NOT enough: the LIVE TWIN must be faithful
# (same bar-completion rule + same downstream vetoes) — BIBLE §126.1 step 4 refined
# 2026-10-21 by measurement (see VEL_EXIT note below).
# VEL_EXIT deliberately NOT here (re-gated 2026-10-21 — my own 10-20 exemption was
# wrong, corrected by bar replay, not by rollback): the vector leg exists
# (v12:13177-84, OR'd into _all_exit :13587) and the switches agree (boards True),
# yet live fired where the vector would not on 4 of 5 replayed bars:
# (A) forming-bar velocity — live ez_indicators.py:1758 computes wt1[-1]-wt1[-4] over
# klines whose last row is the still-forming 4h candle (mark-patched by
# with_mark_price :432); NPZ wt_velocity_4h is completed-bar-only (stepwise-flat per
# 4h window, proven 10-21). Mid-window reversals sign-flip: EGLD live -9.92 vs NPZ
# +2.31, RLC live -14.45 vs NPZ +6.67. (B) missing structural veto — the vector ANDs
# STRUCTURAL_EXIT_GATE (boards True) over _all_exit twice (:13606-30, :13643-73,
# twin of wt_dc_delta.structural_exit_permitted); structural_exit_permitted is
# called NOWHERE in live, so WLD (long rising) and ENS (short falling) fired live
# while the vector held. FIDELITY LANDED 2026-10-21, re-exempted below: NEW
# wt_velocity_4h_closed (forming bar excluded, twin prefers it with fallback) and
# STRUCTURAL veto wired per-sym into the live VEL lane (ez_manage.py GATES-A).
# Replay proof (real NPZ bars, new twin): twin==vector on all 5 fire bars —
# EGLD F, RLC F, WLD STRUCT_VETO_HOLD, XRP T, ENS STRUCT_VETO_HOLD.
# EXIT (2026-10-21 backlog audit): vector-BOOKED lane-D labeled closes
# (v12:20720+, NOT exit_sig-suppressed). Twin pair shares predicate + MIN_TFS
# helpers; live now reads completed-bar velocities (wt_velocity_{1h,4h,D}_closed,
# fallback for old snapshots). Replay proof: twin==vector 3/3 (1000LUNC T/T,
# EDU T/T, SOL F/F suppression-match on closed 1h). Booking gates symmetric or
# safe: per-sym ENABLED/MIN_TFS/hold/NOLOSS both sides, confirms OFF, HTF veto
# crypto-inert, daytrade priority identical. Residual: none known for lane-D.
# TECHNICAL (2026-10-21 backlog audit): vector-BOOKED labeled TECHNICAL_EXIT dc_*
# crosses (loop 22579-22696, cross + held_bars>=min_hold). Live twin restored to
# faithful: technical_dc_exit_crossing (fresh cross per spec, same buffers) +
# grid-faithful hold (ceil(MIN_HOLD_BARS_BEFORE_EXIT*180/900) 15m bars from
# opened_at) wired in the ez_manage.py lane per-sym. Replay proof: DOGE no-cross
# suppression MATCH; XMR fresh cross (hold mirrors vector rule on both sides, so
# both branches agree whatever the age). Residual: exit_sig composition (vector
# also needs exit_sig[i]; shared with VEL) + lifecycle alignment — both watched
# by the family monitor. TECHNICAL_STOP reasons already flow via STOP_PROTECTION.
VEC_SWITCH_EXIT_FAMILIES = frozenset(
    {
        "BB_TAKE",
        "BB_LOSS",
        "STOCH_CROSS_3M_EXIT",
        "VEL_EXIT",
        "EXIT",
        "TECHNICAL",
        # USER 2026-10-22 parity restore: E_1_WT_DELTA leg booked in v12
        # (e1_wt_delta_exit OR'd into _all_exit) + faithful twin both venues
        # (ez E_1 lane / tradier TWIN_SIZING_REDUCE) + 96 boards hold the
        # switch. Killed 10-22 13:28Z FOR an E_1 reason (false positive).
        "E_1",
        # USER 2026-10-22 parity restore: WT_PERCENTILE leg booked in v12
        # (wt_percentile_exit OR'd into _all_exit) + twin==vector replay proof
        # (test_wt_percentile_twin_matches_vector, 216-pt grid incl. 15m
        # turn-confirm) + 45 stocks boards hold the switch. 647 10-22 trb/trc
        # refusals were UNATTRIBUTED (family never killed by operator order).
        "WT",
        # USER 2026-10-22 parity restore: MTF_GR_WT_EXIT leg booked in v12
        # (crypto compound walk block + tradier walk arm, both labeled,
        # trigger-independent) via the shared predicate
        # (vec_decisions.mtf_compound_exits.gr_wt_exit_fires) with a venue TF
        # split (ez 15m/1h/4h/D, tradier 1h/4h/D/W). Booking is UNVETOED on
        # both venues by construction, so neither live lane carries a
        # structural veto (an ez veto was added then reverted same session
        # when the compound path proved unvetoed; the tradier inline veto was
        # removed with it). Killed 10-22 20:47Z on fin:KSMUSDT_LONG (live
        # re-fire of a 39h-held position). Bar window kept.
        # 10-22 round 2: tradier DC + BB_REJECT walk arms wired (shared
        # timestamp-windowed DC step + shared BB step, tradier TF resolution,
        # live-default gates) — 16 stocks syms hold COMPOUND+DC+BB live, so
        # the compound is now fully booked on both venues (crypto: trail/DC/
        # BB/GR; tradier: trail/DC/BB/GR). Measured on MINAUSDT tradier SHORT:
        # all-off 0 MTF rows / 773 trades; all-on DC 14 + BB 5 + GR 10.
        "MTF",
        # USER 2026-10-23 hawk E3: E_3_STRUCTURE_EXIT leg booked in v12
        # (grey-wire e3_structure, mode==2 gated, 695 rows on 1000BONKUSDC
        # SHORT at mode 2) + faithful peak-structure twins both venues
        # (tradier W2 block + crypto E_3 block; the cross analogue agreed
        # only ~45% and the trough extras fired live-only — both removed).
        # Killed 10-23 on trb:ARM_SHORT (cross-analogue fire the vector never
        # books). Bar window kept (full CLOSE).
        "E3",
        # USER 2026-10-23 hawk PROFIT_TARGET: switch-gated pct take-profit
        # leg booked in v12 (walk block gain>=pct, CLOSE PROFIT_TARGET rows
        # on CDE SHORT when ON, none when OFF) + faithful twins both venues
        # (tradier G-exit block + crypto twin, same predicate, default OFF).
        # 25 per-sym backtest holds (sqlite, e.g. CDE_SHORT pct 3.2 fired
        # live at g4.30). Killed 10-23 on trc:CDE_SHORT under the broad
        # PROFIT kill (family fallback). Broad PROFIT kill stays: PROFIT_LOCK
        # / PROFIT_TAKE / PROFIT_TP_EXHAUSTION still refused. Bar window kept
        # (full CLOSE).
        "PROFIT_TARGET",
        # Shared bottom/top reversal masks are emitted by both live and v12;
        # exact BT_ reason wrappers map to these narrow switch families above.
        "RSI2_XTREME_EXIT",
        "STOCH_XTREME_EXIT",
        "WICK_REJECT_EXIT",
        # CYCLE_TP_TIERED is emitted by the same shared twin_sizing_reduce helper
        # in live and v12; its REDUCE is permitted only after the simulator's net-fee gate.
        "CYCLE_TP_TIERED",
        # USER 2026-10-22 parity restore: ALL_TF_AGAINST_CLOSE booked in v12
        # (walk block with labeled reason, trigger-independent, 17 rows on
        # KSMUSDT_LONG at min 4) via the shared twin
        # (vec_decisions.process_position_crypto__all_tf_against, 4-TF count).
        # The live lane counted a 5th 3m vote that exists in no NPZ, so it
        # fired where the vector holds — the lane now counts only the 4
        # vector-visible TFs (boards measured the 4-TF behavior). Crypto-only
        # (tradier has no decision site). Bar window kept.
        "ALL_TF_AGAINST",
        # USER 2026-10-22 OPERATOR ORDER ("ratio is L/S, make sure that is
        # on"): the L/S ratio rebalance (RATIO_MULTIPLIER amplifier,
        # sentiment-driven 10-90 band) decides AND acts — REDUCE/CLOSE/OW
        # overweight arms flow (de-risk toward target). NO vector model
        # (portfolio cross-position state, unbookable in a single-sym walk),
        # so this is an operator override of the vec-only law, not a parity
        # proof. RATIO opens/augments stay stood down in code (an open is
        # never vector-dictated) even though the family listing would pass
        # them — the stand-down, not the listing, is the guard there.
        "RATIO",
        # USER 2026-10-22 parity restore: GR_HTF_DIRECT_EXIT leg booked in v12
        # (gr_htf_exit OR'd into _all_exit) via the shared twin
        # (vec_decisions.gr_htf_direct_entry.gr_direct_exit_mask: score>=min
        # with 2-bar confirm, gain/age staged, trend fail-open, structural via
        # the engine global veto). The ez lane fired single-bar unguarded, so
        # the tradier guard chain (gain/age>0.3/30m, 2-bar confirm, opposite
        # HTF trend, structural veto) is now ported to the ez lane verbatim.
        # Killed 10-22 14:00Z on trb:CRWD_LONG. Bar window kept.
        "GR",
        # USER 2026-10-22 parity restore: DC_HOPELESS entry-outside-dc_4h exit
        # booked in v12 (grey-wire walk, labeled reason, trigger-independent)
        # via the shared twin (vec_decisions.grey_wire_exits.dc_hopeless) +
        # live lanes both venues (ez lane + tradier TWIN-REENTRY-STAGED). The
        # 10-22 13:33Z trb:BNO_LONG fire was board-authorized (live store
        # DC_HOPELESS_EXIT_ENABLED True). Narrow family (not "DC"): other
        # DC_* reasons (DC_BREACH_*, DC_BB_D_BREAK_*, DC_15M_HOLD_OVERRIDE)
        # stay refused. Bar window kept.
        "DC_HOPELESS",
        # USER 2026-10-24 retrace: DC_BASIS 3m-DC legs are vector-modeled
        # (shared twin above + v12 walk) and per-sym gated on
        # DC_BASIS_3M_REDUCE_ENABLED (boards True; SUB15M hard block still
        # forces False while LTF off). PROFIT_EXIT leg guarded default-OFF by
        # DC_BASIS_PROFIT_EXIT_ENABLED (no twin). Bar window kept.
        "DC_BASIS",
        # USER 2026-10-22 parity restore: DAYTRADE_TARGET dc-channel exits
        # booked in v12 (ledger-proven: 479 DAYTRADE_TARGET closes on
        # KSMUSDT_LONG with its board set) via the shared twin
        # (vec_decisions.dc_channel_exits.daytrade_dc_exit) + live lanes both
        # venues (ez grey rewire + tradier daytrade wing). Killed 10-22
        # 13:31Z on trb:LRCX_SHORT (+10.32% winner refused exit). Known
        # operator-decided residual: live NOLOSS holds losses the vector
        # exits (documented in-lane). Bar window kept.
        "DAYTRADE_TARGET",
    }
)

# Families that may be refused+flagged but NEVER killed (dispatcher categories).
NEVER_KILL_FAMILIES = frozenset({"QUICK", "QUICK_UNATTRIBUTED", "OVERSIZE", "NON",
    # USER 2026-10-22 total stocks parity: WT exits still REFUSE (law effect kept,
    # verdict UNATTRIBUTED_REFUSED_NO_KILL, still flagged) but the family is never
    # (re)killed, so board-authorized WT ENTRIES flow. A kill was self-sustaining:
    # every refused WT exit re-armed it within seconds (restore at 17:3x re-killed
    # 17:50:18), blocking 200 verified OPENs/day as collateral. Reversible: drop WT here + disable.
    "WT"})


def is_killed(reason: str) -> bool:
    try:
        return family_of(reason) in _load_kills()
    except Exception:
        return False


def _flag(action, position_key, reason, verdict):
    try:
        VIOLATIONS.parent.mkdir(parents=True, exist_ok=True)
        with open(VIOLATIONS, "a") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "action": action,
                        "position_key": position_key,
                        "reason": str(reason)[:160],
                        "family": family_of(reason),
                        "verdict": verdict,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _queue_retrace(fam: str, action, position_key, reason) -> None:
    """BIBLE §126.1 standing agent job: every newly killed family gets a retrace work
    order — an agent session picks it up and either vectorizes (switch + shared
    scalar/vec core + wire both venues) or records non-vectorizable (note + stays
    killed, switch default OFF). The live process never auto-wires: that would bypass
    LOCKED_FILES + the version-gate production path. One TODO row per family."""
    try:
        RETRACE_QUEUE.parent.mkdir(parents=True, exist_ok=True)
        try:
            existing = RETRACE_QUEUE.read_text().splitlines()
        except OSError:
            existing = []
        for line in existing:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("family") == fam and d.get("status") == "TODO":
                return
        with open(RETRACE_QUEUE, "a") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "family": fam,
                        "status": "TODO",
                        "example_action": str(action)[:40],
                        "example_position_key": str(position_key)[:60],
                        "example_reason": str(reason)[:160],
                        "retrace": (
                            "1 locate emitting branch; 2 find route bypassing "
                            "existing gates; 3 gate ROUTE per-sym on EXISTING "
                            "switch (default-neutral); 4 if vector-modeled + "
                            "switch-gated, RESTORE + list in "
                            "VEC_SWITCH_EXIT_FAMILIES, do not re-kill; else "
                            "switch default OFF, note + keep disabled. Resolve "
                            "via: tools/vector_violations.py resolve FAMILY "
                            "--note TEXT --verdict vectorized|non_vectorizable"
                        ),
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def disable_family(reason: str, why: str) -> str:
    fam = family_of(reason)
    try:
        kills = _load_kills()
        kills[fam] = {"disabled_at": time.time(), "why": str(why)[:200]}
        KILLS.write_text(json.dumps(kills, indent=1))
        _KILLS["loaded"] = time.time()
    except Exception:
        pass
    return fam


def _is_vec_reason(reason) -> bool:
    """USER 2026-10-21 vec-decided supremacy: the in-script twin tag or a VEC_DRIVEN reason.
    Such orders already passed the vector's own gates — live must not second-guess them.
    """
    r = str(reason or "")
    return "|VEC_EXACT" in r or r.upper().startswith("VEC_DRIVEN_")


def _is_board_confirmed(reason) -> bool:
    """The switches spoke: vec-driven/board-converge/vec-exact reasons and owed exits
    (promise ledger). These skip the kill AND the bar window — the vector's own cadence
    (~27min/lane) cannot fit 180s windows; tonight's stale-dictates decision stands."""
    r = str(reason or "")
    u = r.upper()
    if (
        "|VEC_EXACT" in r
        or u.startswith("VEC_DRIVEN_")
        or u.startswith("BOARD_CONVERGE_")
    ):
        return True
    return any(t in u for t in ("EXIT_OWED", "OBLIGATORY", "GUARANTEE", "FORCED"))


def _is_stop_reason(reason) -> bool:
    """Price protection is never deferred to the next bar."""
    u = str(reason or "").upper()
    return "STOP" in u or "TRAILING" in u or "BACKSTOP" in u


def _is_emergency(reason) -> bool:
    """USER 2026-10-22: law #1 already keeps the exchange backstop, and an account-protecting exit is the same class.
    Measured this day: a `LIQUIDATION_PROTECT` close matches no CROSS pattern and is not board-confirmed, so the
    enforcer REFUSED it and KILLED the LIQUIDATION family for good — an emergency exit must never be refused and must
    never cost its family a kill. Token list shared with live_twins.vec_driven.EMERGENCY_TOKENS, plus the broker's
    own reconcile/quarantine wording. Allow-only: it can never block an exit."""
    u = str(reason or "").upper()
    try:
        from live_twins.vec_driven import EMERGENCY_TOKENS as _et
        if any(t in u for t in _et):
            return True
    except Exception:
        pass
    return any(t in u for t in ("LIQUIDATION", "EMERGENCY", "MARGIN_KILL", "BALANCE_FLOOR", "MANUAL", "QUARANTINE", "DELIST", "KILL_SWITCH"))


def _log_monitor(family, action, position_key, reason, verdict) -> None:
    """USER 2026-10-21 family watch: one compact row per check() verdict so the
    monitor can score live-vs-vector agreement until a final verdict lands."""
    try:
        if family not in MONITORED_FAMILIES:
            return
        MONITOR_LOG.parent.mkdir(parents=True, exist_ok=True)
        with open(MONITOR_LOG, "a", encoding="utf-8") as fh:
            fh.write(
                json.dumps(
                    {
                        "ts": time.time(),
                        "family": family,
                        "action": str(action)[:20],
                        "position_key": str(position_key)[:60],
                        "reason": str(reason)[:160],
                        "verdict": verdict,
                    }
                )
                + "\n"
            )
    except Exception:
        pass


def _is_scaling_act(action) -> bool:
    """USER 2026-10-22: "reduce and augment orders on live trading should not be gated by vector as l/s ratio
    rebalancing uses it and it is not vectorizable but essential".

    Law #2's bar window exists because the vector evaluates on CLOSED bars, so a mid-bar signal must wait. That
    reasoning covers entries and full exits. It does not cover POSITION SCALING: the portfolio L/S rebalancer is
    a risk function, not a bar signal, it has no vector model and it is essential, and a 180 s window on a 900 s
    bar refused it 80 % of the time (measured 2026-10-22: RATIO_REDUCE / RATIO_AUGMENT -> OFF_BAR_WINDOW).
    A partial REDUCE or an AUGMENT therefore skips the window. OPEN and full CLOSE keep it.
    """
    a = str(action or "").upper()
    if "OPEN" in a or "ENTRY" in a:
        return False
    return ("REDUCE" in a and "CLOSE" not in a) or "AUGMENT" in a


def _check_inner(action, position_key, reason, cfg_mod=None):
    """(allowed: bool, verdict: str). USER 2026-10-21 vector-copy law, restored: a
    non-cross, non-board-confirmed exit is REFUSED, its family DISABLED + FLAGGED
    (verdict VIOLATION_FAMILY_KILLED) + QUEUED for agent retrace (BIBLE §126.1: the
    kill files a vector_retrace_queue.jsonl work order — switch + vectorized core,
    or a non-vectorizable note) — it can NEVER act again until manually restored.
    QUICK_* is a dispatcher category, never a killable family (sub-rec is the family);
    vector-modeled switch exits with a FAITHFUL live twin (BB_TAKE/...) skip the kill
    even when a stale kill entry exists, but keep the bar window; stops skip the
    window; board-confirmed orders skip both."""
    act = str(action or "").upper()
    try:
        if not enabled(cfg_mod):
            return True, "ENFORCE_OFF"
        is_exit = "CLOSE" in act or "REDUCE" in act
        is_entry = (
            "OPEN" in act
            or "AUGMENT" in act
            or "ENTRY" in act
            or act == "BUY"
            or "HEDGE" in act
            or "REENTRY" in act
        ) and not is_exit
        if is_exit:
            up = str(reason or "").upper()
            if any(b in up for b in BOOKKEEPING):
                return True, "BOOKKEEPING"
            if _is_emergency(reason):
                return True, "EMERGENCY_EXIT"
            if _is_board_confirmed(reason):
                return True, "BOARD_CONFIRMED"
            if _is_stop_reason(reason):
                return True, "STOP_PROTECTION"
            fam = family_of(reason)
            if fam in VEC_SWITCH_EXIT_FAMILIES:
                if not (_is_scaling_act(act) or in_bar_window()):
                    return False, "OFF_BAR_WINDOW"
                return True, "VEC_SWITCH_EXIT"
            if fam == "RATIO" and "REDUCE" in act and "CLOSE" not in act:
                # USER 2026-10-21 (+18:0x ruling, "augment too"): the portfolio L/S
                # rebalancer has no vector model, but trims (REDUCE) stay allowed;
                # CLOSE falls through to the kill below. Bar window kept.
                if not (_is_scaling_act(act) or in_bar_window()):
                    return False, "OFF_BAR_WINDOW"
                return True, "RATIO_TRIM_ALLOWED"
            if fam == "OVERSIZE" and "REDUCE" in act and "CLOSE" not in act:
                # USER 2026-10-22 parity restore (USER 2026-10-17 mandate:
                # trim platform excess over cap even at a loss):
                # oversize_trim.decide() only ever emits partial trims down to
                # the cap (never to flat); 283 boards hold OVERSIZE_TRIM_ENABLED.
                # Same class as RATIO trims: allowed, bar window kept.
                if not (_is_scaling_act(act) or in_bar_window()):
                    return False, "OFF_BAR_WINDOW"
                return True, "OVERSIZE_TRIM_ALLOWED"
            if "NON_TRADEABLE" in up:
                # USER 2026-10-22 parity restore (USER 2026-10-06 mandate: exit
                # non-tradeable symbols at first wt1_15m against + keep closed):
                # protective exit, same class as stops — never refused, never
                # killed, no bar window.
                return True, "NON_TRADEABLE_EXIT_ALLOWED"
            if is_killed(reason):
                return False, f"KILLED_FAMILY_{family_of(reason)}"
            if any(p in up for p in CROSS_PATTERNS):
                if not (_is_scaling_act(act) or in_bar_window()):
                    return False, "OFF_BAR_WINDOW"
                return True, "CROSS_CONFIRMED"
            if fam in NEVER_KILL_FAMILIES:
                _flag(action, position_key, reason, "UNATTRIBUTED_REFUSED_NO_KILL")
                return False, "UNATTRIBUTED_REFUSED_NO_KILL"
            fam = disable_family(reason, f"non-cross exit attempted: {action} {reason}")
            _flag(action, position_key, reason, "VIOLATION_FAMILY_KILLED")
            _queue_retrace(fam, action, position_key, reason)
            return False, "VIOLATION_FAMILY_KILLED"
        if _is_vec_reason(reason):
            return True, "VEC_DECIDED"
        if (
            family_of(reason) == "PPL_REAUGMENT"
            and "AUGMENT" in act
            and "OPEN" not in act
        ):
            # USER 2026-10-22 PPL re-augment switch (backtest decides per
            # sym_side, default OFF/inert): the vector books the leg
            # (PPL_REAUGMENT_DC_RECLAIM walk block) and both live lanes gate
            # on the same switch, so the augment flows like any fitted twin
            # leg. Skip even a "PPL" kill entry (different family, standing
            # order stays stood down). Scaling skips the bar window.
            if not (_is_scaling_act(act) or in_bar_window()):
                return False, "OFF_BAR_WINDOW"
            return True, "PPL_REAUGMENT_ALLOWED"
        if family_of(reason) == "RATIO" and "AUGMENT" in act and "OPEN" not in act:
            # USER 2026-10-21 "augment too": with-trend adds stay allowed (with or
            # without a RATIO kill entry); OPEN/CLOSE stay killed. Window kept.
            if not (_is_scaling_act(act) or in_bar_window()):
                return False, "OFF_BAR_WINDOW"
            return True, "RATIO_TRIM_ALLOWED"
        if is_killed(reason):
            return False, f"KILLED_FAMILY_{family_of(reason)}"
        if family_of(reason) == "OBLIGATORY" and str(position_key or "").startswith("trb:"):
            # USER 2026-10-23 exact parity: sector-hedge entries are not board switches (absent from every
            # TEMPLATE tab; measured live opens CRWD/LDOS). Refuse + flag, never kill: crypto
            # OBLIGATORY_OPEN_SMA200 shares the family name and stays untouched.
            _flag(action, position_key, reason, "NONBOARD_ENTRY_REFUSED_NO_KILL")
            return False, "NONBOARD_ENTRY_REFUSED_NO_KILL"
        if is_entry:
            if not (_is_scaling_act(act) or in_bar_window()):
                return False, "OFF_BAR_WINDOW"
        return True, "ENTRY_TIMING_OK"
    except Exception:
        return True, "ENFORCE_ERROR_FAILOPEN"


def check(action, position_key, reason, cfg_mod=None):
    """Public gate (same signature as before): verdict + family-watch logging."""
    ok, verdict = _check_inner(action, position_key, reason, cfg_mod)
    try:
        _log_monitor(family_of(reason), action, position_key, reason, verdict)
    except Exception:
        pass
    return ok, verdict
