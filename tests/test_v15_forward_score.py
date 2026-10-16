"""Task 04.1 forward score (USER 2026-10-15): on refresh boards, before the main pass, score the
PREVIOUS published set and each promoted key leave-one-out on bars added since that board's NPZ end.
FORWARD records {key, d_gain, d_trades, d_dd, d_tim, d_sharpe, unseen_bars, prev_npz_end} -> delta log +
progress['forward']. Zero extra fits (scoring only), V15_FORWARD_SCORE=0 disables."""
import json

import v15_pilot as P


def test_default_disabled_inert(monkeypatch):
    monkeypatch.delenv("V15_FORWARD_SCORE", raising=False)
    assert P.forward_score_on() is False
    assert P.run_forward_score("X_LONG", 9e9, {}, delta_log_fn=None, progress={}) == []
    monkeypatch.setenv("V15_FORWARD_SCORE", "1")
    assert P.forward_score_on() is True


def test_unseen_window():
    assert P.forward_unseen_window(100.0, 100.0) is None
    assert P.forward_unseen_window(200.0, 100.0) is None
    assert P.forward_unseen_window(None, 100.0) is None
    assert P.forward_unseen_window(0.0, 3600.0) == 1
    assert P.forward_unseen_window(0.0, 86400.0 * 1.5) == 1
    assert P.forward_unseen_window(0.0, 86400.0 * 2.0) == 7
    assert P.forward_unseen_window(0.0, 86400.0 * 8.0) == 7
    assert P.forward_unseen_window(0.0, 86400.0 * 9.0) == 30


def test_pick_prev():
    cands = [{"src": "a", "ts_last": 100.0}, {"src": "b", "ts_last": 200.0}, {"src": "future", "ts_last": 99999.0}]
    assert P.forward_pick_prev(cands, 10000.0)["src"] == "b"
    assert P.forward_pick_prev(cands, 200.0) is None
    assert P.forward_pick_prev([], 1000.0) is None
    assert P.forward_pick_prev(cands, None) is None
    assert P.forward_pick_prev([{"src": "x"}], 1000.0) is None
    assert P.forward_pick_prev(cands, 10000.0, min_gap_s=20000.0) is None


def test_loo_sets():
    fields = sorted(P.known_config_fields())
    assert fields, "needs real engine fields"
    fld = fields[0]
    prev = {fld: "moved-value", "SIM_ACCOUNT": "trb", "ZZZ_NO_SUCH_FIELD_XYZ": 1}
    loos = P.forward_loo_sets(prev, {})
    keys = [k for k, _ in loos]
    assert keys == [fld]
    assert set(loos[0][1]) == {"SIM_ACCOUNT", "ZZZ_NO_SUCH_FIELD_XYZ"}
    assert P.forward_loo_sets({fld: "v"}, {fld: "v"}) == []
    assert P.forward_loo_sets(None, {}) == []


def test_run_scores_prev_and_loo(monkeypatch, tmp_path):
    symside = "FWDTEST_LONG"
    fields = sorted(P.known_config_fields())
    fld = fields[0]
    prev_end = 1_700_000_000.0
    arch = {"cumulative_overrides": {fld: "moved-value"}, "final_gain": 5.0,
            "npz_id": {"ts_last": prev_end, "md5": "old"}}
    (tmp_path / f"{symside}_v14_progress.json.npzprev_20261014000000.json").write_text(json.dumps(arch))
    (tmp_path / "unrelated.npzprev_20261014000000.json").write_text("{}")
    import tools.opt.v12_pilot as VP

    def _prep(ss, win):
        assert ss == symside
        return {"npz_prepared": {"timestamps": list(range(96))}}
    def _eps(prep, ov, win, include_ledger=False):
        gain = 10.0 if fld in ov else 9.0
        return {"gain_pct": gain, "trades": 20, "max_dd_pct": 4.0, "tim_pct": 30.0, "pool_sharpe": 0.5, "valid": True}
    monkeypatch.setattr(VP, "prepare_batch", _prep)
    monkeypatch.setattr(VP, "evaluate_prepared_sanitized", _eps)
    monkeypatch.setenv("V15_FORWARD_SCORE", "1")
    logged = []
    progress = {}
    recs = P.run_forward_score(symside, prev_end + 2 * 86400.0, {}, delta_log_fn=logged.append,
                               progress=progress, progress_dir=str(tmp_path))
    assert len(recs) == 1
    r = recs[0]
    assert r["label"] == "FORWARD" and r["key"] == fld
    assert set(("key", "d_gain", "d_trades", "d_dd", "d_tim", "d_sharpe", "unseen_bars", "prev_npz_end")) <= set(r)
    assert r["d_gain"] == -1.0 and r["unseen_bars"] == 96 and r["prev_npz_end"] == prev_end
    assert logged == recs
    assert progress["forward"][0]["n_keys"] == 1 and progress["forward"][0]["full"]["gain_pct"] == 10.0


def test_run_no_refresh_records_nothing(monkeypatch, tmp_path):
    import tools.opt.v12_pilot as VP
    monkeypatch.setattr(VP, "prepare_batch", lambda ss, w: (_ for _ in ()).throw(AssertionError("no evals")))
    monkeypatch.setattr(VP, "evaluate_prepared_sanitized", lambda *a, **k: (_ for _ in ()).throw(AssertionError("no evals")))
    monkeypatch.setenv("V15_FORWARD_SCORE", "1")
    assert P.run_forward_score("NOREF_LONG", 9e9, {}, progress_dir=str(tmp_path)) == []
