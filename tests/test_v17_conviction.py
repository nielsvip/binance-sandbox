"""V17 CONVICTION (USER 2026-10-14) — pure scoring, combination with the tier, live drawdown clamp, kill switch, ledger dedupe,
and the live wiring markers (ez_manage OPEN/REENTRY boost, tradier 6x SPS ceiling)."""
import importlib
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
import v17_conviction as C  # noqa: E402

NOW = 1_800_000_000.0


def _closes(vals, notional=50.0, start=NOW - 20 * 86400):
    return [{"ts_epoch": start + i * 3600, "net_pct": v, "usd": notional * v / 100, "notional": notional} for i, v in enumerate(vals)]


def test_shrinkage_small_samples_near_neutral():
    s1, n1 = C.weighted_shrunk(_closes([2.0]))
    s30, n30 = C.weighted_shrunk(_closes([2.0] * 30))
    assert n1 == pytest.approx(1.0) and s1 == pytest.approx(2.0 / 11)
    assert s30 == pytest.approx(2.0 * 30 / 40)
    assert C.weighted_shrunk([]) == (0.0, 0.0)


def test_dust_churn_cannot_outvote_real_trades():
    real = _closes([-1.0] * 6, notional=100.0)
    dust = _closes([3.0] * 700, notional=3.0, start=NOW - 10 * 86400)
    lv = C.live_stats(real + dust, NOW, dust_usd=5.0)
    assert lv["n30"] == 6 and lv["L"] < 0


def test_lifecycle_dedupe_keeps_last_relog():
    recs = []
    for i in range(5):  # same position re-logged every minute while the close retried
        ts = NOW + 60 * i
        recs.append({"ts_epoch": ts, "net_pct": 0.4 + i * 0.01, "life": C.lifecycle_key("men", ts, 196.0 + i), "qty": 0.417})
    recs.append({"ts_epoch": NOW + 9000, "net_pct": -1.0, "life": C.lifecycle_key("men", NOW + 9000, 30.0), "qty": 0.417})
    out = C.dedupe_lifecycles(recs, with_qty=False)
    assert len(out) == 2 and out[0]["net_pct"] == pytest.approx(0.44)


def test_backtest_score_365_overfit_and_confirmation():
    ev = {"valid": True, "gain_pct": 20.0, "bh_pct": 5.0, "trades": 80, "tim_pct": 45.0, "max_dd_pct": 5.0}
    good = C.backtest_score(ev, None, [{"gain": 10.0}, {"gain": 8.0}], 12.0, 4.0)
    over = C.backtest_score(ev, None, [], -3.0, 4.0)
    none = C.backtest_score(None, None, [], None, None)
    assert good["B"] > 0.5 and good["confirmed365"]
    assert over["B"] <= 0 and over["overfit365"]
    assert none["B"] == 0 and "no_board" in none["notes"]


def test_backtest_trade_floor_and_consistency_shrink():
    ev = {"valid": True, "gain_pct": 20.0, "bh_pct": 5.0, "trades": 10, "tim_pct": 45.0}
    thin = C.backtest_score(ev, None, [], None, None)
    full = C.backtest_score(dict(ev, trades=60), None, [], None, None)
    flaky = C.backtest_score(dict(ev, trades=60), None, [{"gain": 5}, {"gain": -5}, {"gain": -3}, {"gain": -1}], None, None)
    assert thin["B"] < full["B"] and flaky["B"] < full["B"]


def _bt(B, confirmed=False, overfit=False):
    return {"B": B, "confirmed365": confirmed, "overfit365": overfit}


def _lv(L, n=20, usd=10.0, mean=0.5):
    return {"L": L, "neff30": n, "n30": n, "usd30": usd, "mean30": mean}


def test_decide_verdicts_and_caps():
    v, m, _ = C.decide(1.0, _bt(0.9, confirmed=True), _lv(0.9, 60), [], False, cap=6.0)
    assert v == "PROVEN" and 3.0 < m <= 6.0
    v, m, _ = C.decide(1.0, _bt(0.9), _lv(0.9, 60), [], False, cap=6.0)
    assert v == "PROVEN" and m <= C.UNCONFIRMED_CAP
    v, m, _ = C.decide(1.0, _bt(0.9, overfit=True), _lv(0.9, 60), [], False, cap=6.0)
    assert v == "NEUTRAL" and m == 1.0
    v, m, _ = C.decide(1.0, _bt(0.9, True), _lv(0.9, 3), [], False)  # unproven live -> stays at tier
    assert v == "NEUTRAL" and m == 1.0
    v, m, _ = C.decide(1.0, _bt(0.9, True), _lv(-0.5, 20, -30.0, -0.8), [], False)
    assert v == "DISPROVEN" and m == C.DISPROVEN_MULT
    v, m, _ = C.decide(3.0, _bt(0.9, True), _lv(0.9, 60), ["STALE_SET_HALT"], False)
    assert v == "TRIGGER" and m == 1.0
    v, m, _ = C.decide(3.0, _bt(0.9, True), _lv(0.9, 60), [], True)
    assert v == "REPROVING" and m == 1.0
    v, m, _ = C.decide(1.0, _bt(0.9, True), _lv(0.9, 60, usd=-5.0), [], False)  # size-weighted loser is never proven
    assert v != "PROVEN"


def test_combine_entry_respects_demotions_staleness_and_cap(tmp_path):
    fresh = {"verdict": "PROVEN", "mult_target": 9.0, "at_epoch": NOW}
    assert C.combine_entry(1.0, "BOOK_POS_HOLD", fresh, NOW, 6.0, root=tmp_path) == 6.0
    assert C.combine_entry(0.25, "MIN", fresh, NOW, 6.0, root=tmp_path) == 0.25
    assert C.combine_entry(1.0, "RANKED", dict(fresh, at_epoch=NOW - 72 * 3600), NOW, 6.0, root=tmp_path) == 1.0
    assert C.combine_entry(4.0, "RANKED", {"verdict": "TRIGGER", "mult_target": 1.0, "at_epoch": NOW}, NOW, 6.0, root=tmp_path) == 1.0
    assert C.combine_entry(4.0, "RANKED", {"verdict": "NEUTRAL", "mult_target": 4.0, "at_epoch": NOW}, NOW, 6.0, root=tmp_path) == 4.0
    (tmp_path / "data" / "conviction").mkdir(parents=True)
    (tmp_path / "data" / "conviction" / "DISABLED").write_text("")
    assert C.combine_entry(1.0, "BOOK_POS_HOLD", fresh, NOW, 6.0, root=tmp_path) == 1.0


def test_apply_to_tiers_idempotent_uses_tier_mult():
    tiers = {"A_LONG": {"mult": 1.0, "tier": "BOOK_POS_HOLD"}, "B_SHORT": {"mult": 0.5, "tier": "RANKED_NO365_SMALL"}, "C_LONG": {"mult": 2.0, "tier": "RANKED"}}
    doc = {"sym_sides": {"A_LONG": {"verdict": "PROVEN", "mult_target": 2.0, "at_epoch": time.time()}, "B_SHORT": {"verdict": "DISPROVEN", "mult_target": 0.5, "at_epoch": time.time()}}}
    C.apply_conviction_to_tiers(tiers, doc, cap=6.0)
    C.apply_conviction_to_tiers(tiers, doc, cap=6.0)
    assert tiers["A_LONG"]["mult"] == 2.0 and tiers["A_LONG"]["tier_mult"] == 1.0
    assert tiers["B_SHORT"]["mult"] == 0.5
    assert tiers["C_LONG"]["mult"] == 2.0 and "conviction" not in tiers["C_LONG"]


def test_replay_clips_at_max_position():
    rows = [{"ts_epoch": NOW, "account": "ang", "notional": 100.0, "net_pct": 1.0, "usd": 1.0}]
    b, a = C.replay_rows(rows, lambda ts: 1000.0, "XUSDT_LONG")
    assert b == pytest.approx(1.0) and a == pytest.approx(C.max_position("XUSDT_LONG", "ang") / 100.0)


@pytest.fixture
def pts(tmp_path, monkeypatch):
    f = tmp_path / "persym_size_tiers.json"
    f.write_text(json.dumps({"tiers": {"WINUSDT_LONG": {"mult": 3.0, "tier_mult": 1.0, "tier": "BOOK_POS_HOLD"}, "HALTUSDT_SHORT": {"mult": 3.0, "tier_mult": 1.0}, "LOWUSDT_LONG": {"mult": 0.25, "tier_mult": 0.25}}}))
    (tmp_path / "data" / "stale_set_halt").mkdir(parents=True)
    (tmp_path / "data" / "stale_set_halt" / "halts_ez.json").write_text(json.dumps({"halts": {"HALTUSDT_SHORT": {"halted_at": "x"}}}))
    monkeypatch.setenv("PERSYM_SIZE_TIERS_PATH", str(f))
    monkeypatch.setenv("PERF_TIER_ROOT", str(tmp_path))
    import perf_tier_sizing
    m = importlib.reload(perf_tier_sizing)
    yield m, tmp_path
    monkeypatch.delenv("PERF_TIER_ROOT")
    monkeypatch.delenv("PERSYM_SIZE_TIERS_PATH")
    importlib.reload(perf_tier_sizing)


def test_live_trigger_clamps_immediately_and_kill_switch(pts):
    m, tmp = pts
    assert m.get_mult("WINUSDT", "LONG") == 3.0
    assert m.get_mult("HALTUSDT", "SHORT") == 1.0  # stale-set halt active -> <= 1x at order time
    assert m.get_mult("LOWUSDT", "LONG") == 0.25  # clamp never raises a small multiplier
    (tmp / "data" / "conviction").mkdir(parents=True)
    (tmp / "data" / "conviction" / "DISABLED").write_text("")
    assert m.get_mult("WINUSDT", "LONG") == 1.0  # kill switch -> tier rule's own multiplier


def test_live_wiring_markers_present():
    ez = (ROOT / "ez_manage.py").read_text(errors="ignore")
    tr = (ROOT / "tradier_manage.py").read_text(errors="ignore")
    assert "[V17_CONVICTION_BOOST]" in ez and "not is_reduce and not is_augment and not is_hedge" in ez
    assert 'getattr(config, "OPEN_CEIL_SPS_MAX_MULT", 6.0)' in ez
    assert "[OPEN_CEIL_SPS] {position_key} {action}: qty" in tr and 'getattr(config, "OPEN_CEIL_SPS_MAX_MULT", 6.0)' in tr
