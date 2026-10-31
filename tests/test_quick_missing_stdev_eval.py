import ez_positions_quick as quick
from vec_decisions.quick_stdev_eval import evaluate_stdev_once


def test_cached_detector_result_advances_one_allowed_retest_per_bar(monkeypatch):
    monkeypatch.setattr(quick.config, "STDEV_BREAKOUT_ENABLED", True, raising=False)
    monkeypatch.setattr(quick.config, "STDEV_BREAKOUT_RETEST_TF_LIST", ["15m"], raising=False)
    monkeypatch.setattr(quick.config, "STDEV_BREAKOUT_HTF_LIST", ["4h"], raising=False)
    monkeypatch.setattr(quick.config, "STDEV_BREAKOUT_MAX_RETESTS", 3, raising=False)
    now = [1_800_000_000.0]
    monkeypatch.setattr(quick.time, "time", lambda: now[0])
    bar = int(now[0] // 900)
    monkeypatch.setattr(quick, "_stdev_breakout_state", {
        "PEPEUSDC": {
            "active": True, "direction": "LONG", "breakout_time": 0.0,
            "breakout_pctb": 1.2, "htf": "4h", "retest_count": 0,
            "bars_since": 0, "last_bar": bar - 1,
        }
    })
    indicators = {"bb_pct_b_15m": 0.9, "k_15m": 60.0, "d_15m": 50.0}
    state = quick._stdev_breakout_state["PEPEUSDC"]

    cache = {}
    first = evaluate_stdev_once(cache, quick.detect_stdev_breakout, "PEPEUSDC", True, indicators, {})
    repeated = evaluate_stdev_once(cache, quick.detect_stdev_breakout, "PEPEUSDC", True, indicators, {})
    assert first["phase"] == "RETEST" and first["retest_num"] == 1
    assert repeated is first
    assert state["retest_count"] == 1
    assert state["bars_since"] == 1

    for expected_count in (2, 3):
        now[0] += 900
        result = evaluate_stdev_once({}, quick.detect_stdev_breakout, "PEPEUSDC", True, indicators, {})
        assert result["phase"] == "RETEST"
        assert result["retest_num"] == expected_count
        assert state["retest_count"] == expected_count
    now[0] += 900
    exhausted = evaluate_stdev_once({}, quick.detect_stdev_breakout, "PEPEUSDC", True, indicators, {})
    assert exhausted is None
    assert state["retest_count"] == 3


def test_live_source_uses_same_iteration_cache_at_both_stdev_call_sites():
    from pathlib import Path

    source = Path(quick.__file__).read_text()
    compact = "".join(source.split())
    marker = "#==2.5σSTDEVBREAKOUT:standaloneentry"
    end_marker = "#==STDEVBounce:mean-reversionentry"
    begin = compact.index("#==2.5σSTDEVBREAKOUT:standaloneentry")
    end = compact.index("#==STDEVBOUNCE:mean-reversionentry", begin)
    section = compact[begin:end]
    assert "_sb_eval_cache={}" in section
    assert "_eval_stdev_once(_sb_eval_cache,detect_stdev_breakout" in section
    boost = compact.index("#==2.5σSTDEVBREAKOUTBOOST", end)
    boost_section = compact[boost:compact.index("if(should_tradeand", boost)]
    assert "_eval_stdev_once(_sb_eval_cache,detect_stdev_breakout" in boost_section
    assert "_sb_result=detect_stdev_breakout(" not in boost_section
