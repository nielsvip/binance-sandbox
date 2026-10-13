"""PRIOR POLICY + TIERS (USER 2026-10-12): fleet causal priors prune the yellow matrix, never the naked eval;
tiers decide the FOCUS budget. Pure-function pins for v15_pilot.prior_row_policy / symside_tier."""

import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
import v15_pilot as P


def _prior():
    return {
        "switch_cand": {
            "DEAD_SW=True": {"verdict": "DEAD", "n_sym": 40, "pos_sym": 0, "mean_delta": 0.0},
            "HARM_SW=2": {"verdict": "HARMFUL", "n_sym": 25, "pos_sym": 0, "neg_sym": 20, "mean_delta": -6.1},
            "GOOD_SW=1h": {"verdict": "USEFUL", "n_sym": 50, "pos_sym": 20, "mean_delta": 0.4, "sum_pos_delta": 30.0},
            "THIN_SW=3": {"verdict": "DEAD", "n_sym": 4, "pos_sym": 0},
            "NEUT_SW=5": {"verdict": "NEUTRAL", "n_sym": 30, "pos_sym": 2},
        },
        "filter_opt": {
            "F_DEAD=OFF": {"verdict": "USELESS", "n_sym": 30, "pos_sym": 1, "sum_pos_delta": 0.1},
            "F_GOOD=1h": {"verdict": "USEFUL", "n_sym": 30, "pos_sym": 12, "sum_pos_delta": 40.0},
            "F_UNKNOWN=D": {"verdict": "INSUFFICIENT", "n_sym": 2, "pos_sym": 1},
        },
    }


HDRS = ["F_DEAD=OFF", "F_GOOD=1h", "F_UNKNOWN=D", "F_NOEV=15m"]


def _policy(sw, cand, rr=5, seq=1, every=25, strict=False, exempt=False, hdrs=HDRS):
    return P.prior_row_policy(_prior(), "ENTRY_X", sw, cand, hdrs, rr, seq, every, 10, exempt, strict=strict)


def test_dead_row_is_naked_only_and_books_every_yellow():
    mode, kept, skipped, meta = _policy("DEAD_SW", "True", rr=5, seq=1, every=25)
    assert mode == "naked_only" and kept == [] and skipped == HDRS
    assert meta["why"].startswith("PRIOR_DEAD")


def test_harmful_row_pruned_but_thin_evidence_is_not():
    assert _policy("HARM_SW", "2")[0] == "naked_only"
    mode, kept, skipped, _ = _policy("THIN_SW", "3")
    assert mode in ("full", "trimmed") and "F_GOOD=1h" in kept  # n_sym 4 < min_n 10 -> row never pruned


def test_useful_row_keeps_matrix_but_trims_useless_cells():
    mode, kept, skipped, meta = _policy("GOOD_SW", "1h")
    assert mode == "trimmed"
    assert "F_DEAD=OFF" in skipped
    assert kept == ["F_GOOD=1h", "F_UNKNOWN=D", "F_NOEV=15m"]  # unevidenced cells always kept (fail-open)


def test_rotation_restores_full_matrix_once_per_cycle():
    # find a (rr, seq) hitting the rotation slot for this tab and confirm the pruned row runs full there
    hit = [rr for rr in range(3, 80) if P.inert_row_sampled(rr, "ENTRY_X", 7, 25)]
    assert hit and len(hit) <= 4
    mode, kept, skipped, meta = _policy("DEAD_SW", "True", rr=hit[0], seq=7, every=25)
    assert mode == "rotate" and kept == HDRS and skipped == []
    miss = next(rr for rr in range(3, 80) if rr not in hit)
    assert _policy("DEAD_SW", "True", rr=miss, seq=7, every=25)[0] == "naked_only"


def test_exempt_and_no_prior_never_prune():
    assert _policy("DEAD_SW", "True", exempt=True)[0] == "full"
    assert P.prior_row_policy({}, "T", "DEAD_SW", "True", HDRS, 5, 1, 25, 10, False)[0] == "full"
    assert P.prior_row_policy(_prior(), "T", "DEAD_SW", "True", [], 5, 1, 25, 10, False)[0] == "full"


def test_strict_tier_only_keeps_useful_rows():
    assert _policy("NEUT_SW", "5", strict=False)[0] in ("full", "trimmed")
    mode, kept, skipped, meta = _policy("NEUT_SW", "5", strict=True)
    assert mode == "naked_only" and skipped == HDRS
    assert _policy("GOOD_SW", "1h", strict=True)[0] == "trimmed"


def test_symside_tier_rules(monkeypatch):
    monkeypatch.delenv("V15_TIER_FORCE", raising=False)
    assert P.symside_tier(12.0, 8.0, 40)[0] == "WINNER"
    assert P.symside_tier(2.0, 40.0, 40)[0] == "WINNER"  # big mover: the opportunity is there
    # USER 2026-10-13 BH-side priority: bh here is SIDE-CORRECT (this side's own B&H). bh <= -BIG_MOVER
    # means B&H made its money on the OPPOSITE side -> this side is calculated but quick, never WINNER.
    assert P.symside_tier(-3.0, -40.0, 40)[0] == "LOSER"  # opposing big mover, no own gain: quick
    tier, meta = P.symside_tier(57.0, -100.0, 219)  # ONEUSDT_SHORT shape: real own gain vs a 2x pump
    assert tier == "MEDIOCRE" and meta["bh_opposing"] is True and meta["focus_s"] < P._TIER_FOCUS_S["WINNER"]
    tier, meta = P.symside_tier(124.0, 213.0, 444)  # ONEUSDT_LONG shape: BH-aligned side gets the time
    assert tier == "WINNER" and not meta.get("bh_opposing")
    assert P.symside_tier(1.0, 0.5, 40)[0] == "LOST_CAUSE"
    assert P.symside_tier(1.0, 0.5, 40, prior_best=9.0)[0] == "MEDIOCRE"  # earlier evidence of real gain lifts it
    assert P.symside_tier(-2.0, 6.0, 40)[0] == "LOSER"
    assert P.symside_tier(15.0, 6.0, 4)[0] == "LOSER"  # below the sample floor
    assert P.symside_tier(4.0, 6.0, 40)[0] == "MEDIOCRE"
    tier, meta = P.symside_tier(4.0, 6.0, 40, force="LOST_CAUSE")
    assert tier == "LOST_CAUSE" and meta["focus_s"] == P._TIER_FOCUS_S["LOST_CAUSE"]
    assert P._TIER_FOCUS_S["WINNER"] > P._TIER_FOCUS_S["MEDIOCRE"] > P._TIER_FOCUS_S["LOSER"] >= P._TIER_FOCUS_S["LOST_CAUSE"]


def test_prior_skip_and_rank_helpers():
    assert P._prior_skip({"verdict": "DEAD", "n_sym": 10}, 10)
    assert not P._prior_skip({"verdict": "DEAD", "n_sym": 9}, 10)
    assert not P._prior_skip({"verdict": "USEFUL", "n_sym": 99}, 10)
    assert not P._prior_skip(None, 10)
    assert P._prior_rank({"pos_sym": 3, "sum_pos_delta": 2.5}) == (3, 2.5)
    assert P._prior_rank(None) == (0, 0.0)
