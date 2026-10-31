import numpy as np
from types import SimpleNamespace

from vec_decisions.asr_breakout_pa_score import breakout_pa_score_array
from vec_decisions.asr_channel_confluence_score import apply_channel_confluence_vec
from vec_decisions.asr_dc_structure_score import dc_structure_votes_array
from vec_decisions.asr_history_score import history_score_array
from vec_decisions.asr_late_score import apply_asr_late_score_vec
from vec_decisions.asr_score_tail import finish_array
from vec_decisions.asr_ordered_composer import ASR_LATE_STAGE_ORDER, ASR_REMAINING_AFTER_COMPOSER, assemble_asr_composer_inputs, bind_asr_npz_arrays, compose_asr_late_score
from vec_decisions.asr_pre_dc_entry_score import pre_dc_entry_score_array
from vec_decisions.asr_post_rank_score import apply_asr_dc_sufficiency_vec, apply_asr_post_rank_vec
from vec_decisions.asr_rank_sentiment_score import apply_rank_sentiment_vec
from vec_decisions.asr_volume_setup_score import volume_setup_array
from vec_decisions.asr_wt_intelligence_score import wt_intelligence_score_array
from vec_decisions.asr_directional_score import directional_score_array


def _inputs(n=7):
    x = np.arange(n, dtype=float)
    npz = {}
    for tf in ("3m", "15m", "1h", "4h"):
        npz[f"k_{tf}"] = 25 + x * 8
        npz[f"d_{tf}"] = 40 + x * 4
        npz[f"dc_high_{tf}"] = 110 + x
        npz[f"dc_low_{tf}"] = 80 + x
    npz.update({"dc_basis_15m": 90 + x, "dc_basis_1h": 95 + x, "dc_basis_4h": 100 + x,
                "dc_high_1m": np.zeros(n), "dc_low_1m": np.zeros(n), "wt_cross_1h": np.full(n, "BULL", object),
                "wt_cross_15m": np.full(n, "BEAR", object)})
    dc = {}
    for tf in ("3m", "15m", "1h", "4h"):
        dc[f"dc_low_{tf}"] = 80 + x
        dc[f"dc_high_{tf}"] = 110 + x
        dc[f"dc_low_{tf}_ant"] = 79 + x
        dc[f"dc_high_{tf}_ant"] = 109 + x
    breakout = {"current_price": 111 + x, "true_lag": np.array([0, 9, 10, 2, 5, 7, 1.]),
                "k_1m": 30 + x * 5, "k_1m_prev": 20 + x, "d_1m": 25 + x,
                "k_3m": 40 + x * 3, "k_3m_prev": 30 + x, "d_3m": 35 + x,
                "k_15m": 45 + x, "d_15m": 40 + x, "higher_high_15m": x % 2 == 0,
                "lower_low_15m": x % 2 == 1, "prev_cross_price": 108 + x, "lr_slope": x - 3}
    for tf in ("3m", "15m", "1h", "4h"):
        breakout[f"dc_high_{tf}"] = npz[f"dc_high_{tf}"]
        breakout[f"dc_low_{tf}"] = npz[f"dc_low_{tf}"]
    directional = {"current_price": 111 + x, "k_1h": 30 + x, "d_1h": 40 + x,
                   "dc_basis_15m": 90 + x, "dc_basis_1h": 95 + x, "dc_basis_4h": 100 + x,
                   "sma_200_1m": 100 + x, "sma_200_15m": 100 + x, "sma_200_1h": 100 + x,
                   "ha_15m": np.where(x % 2 == 0, "green", "red"), "ha_1h": np.full(n, "green", object),
                   "wt_score_15m": x - 3, "wt_score_1h": x * 5}
    wt = {"wt_divergence_1h": np.where(x % 2 == 0, "BULL", "BEAR"), "wt_divergence_4h": np.full(n, "BULL", object),
          "wt_momentum_state_1h": np.full(n, "IMPULSE_UP", object), "wt_momentum_state_4h": np.full(n, "EXHAUST_DOWN", object),
          "wt_trough_structure_1h": np.full(n, "HL", object), "wt_peak_structure_1h": np.full(n, "LH", object),
          "wt_trough_structure_4h": np.full(n, "HL", object), "wt_peak_structure_4h": np.full(n, "LH", object),
          "wt_cross_1h": np.full(n, "BULL", object), "wt_cross_rising_1h": x % 2 == 0,
          "wt_percentile_1h": 10 + x * 12, "wt_percentile_4h": 20 + x * 10, "wt_extreme_1h": x % 3 == 0,
          "wt_trough_structure_D": np.full(n, "HL", object), "wt_peak_structure_D": np.full(n, "LH", object)}
    volume = {"current_price": 111 + x, "rel_vol": .2 + x * .4, "z_conviction": x * 5,
              "dc_high_1h": npz["dc_high_1h"], "dc_low_1h": npz["dc_low_1h"], "trend_ok": x % 2 == 0,
              "pa_confirm": x % 3 == 0, "pr_confirm": x % 4 == 0, "k_1m_prev": 10 + x, "k_1m": 20 + x,
              "k_3m": 20 + x * 10}
    post = {"k1": 30 + x, "d1": 40 + x, "k1_prev": 20 + x, "k3": 35 + x, "d3": 40 + x,
            "k3_prev": 25 + x, "k15": 50 + x, "d15": 45 + x, "k15_prev": 40 + x,
            "true_lag": np.array([0, 9, 10, 2, 5, 7, 1.]), "scalp_entry_detected": x % 2 == 0,
            "lower_low15": x % 3 == 0, "higher_high15": x % 4 == 0, "gain": x * .5,
            "min_gain": .5, "trend_continuation": x % 3 == 1}
    cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=True, OVERBOUGHT_SCORE_GUT_BREAKOUT_BYPASS=True)
    pre_dc = {"current_price": np.full(n, 100.0), "last_reduction_price": np.zeros(n), "minutes_since_reduction": np.full(n, 1300.0),
              "k_1m": np.where(x % 2 == 0, 51.0, 49.0), "d_1m": np.full(n, 50.0), "k_1m_prev": np.full(n, 50.0),
              "k_3m": np.full(n, 50.0), "d_3m": np.full(n, 50.0), "k_3m_prev": np.full(n, 50.0),
              "k_15m": np.full(n, 50.0), "d_15m": np.full(n, 50.0), "k_1h": np.full(n, 50.0), "d_1h": np.full(n, 50.0),
              "k_4h": np.full(n, 50.0), "true_lag": np.full(n, 10.0), "lower_low_15m": np.zeros(n, bool), "higher_high_15m": np.zeros(n, bool),
              "position_last_reduction_price": np.zeros(n), "position_last_augmentation_price": np.zeros(n),
              "dc_basis_15m": np.full(n, 100.0), "dc_basis_1h": np.full(n, 100.0),
              "dc_high_4h": dc["dc_high_4h"], "dc_high_4h_ant": dc["dc_high_4h_ant"],
              "dc_low_4h": dc["dc_low_4h"], "dc_low_4h_ant": dc["dc_low_4h_ant"], "recent_exit": np.zeros(n, bool),
              "k_3m_cross_over": np.zeros(n, bool), "k_3m_cross_under": np.zeros(n, bool)}
    return {"n": n, "cfg": cfg, "score_before_dc": 12.0 + x, "stages": {
        "pre_dc_entry_state": pre_dc, "dc_structure": dc, "breakout_pa": breakout, "directional": directional, "wt_intelligence": wt,
        "volume_setup": volume, "tracker_history_modifier": {"history_present": x % 2 == 0, "win_rate": 75 - x * 10,
        "total_trades": x + 2, "total_realized_pnl": x * 30 - 90},
        "channel_confluence": {"npz": npz, "current_price": 111 + x, "use_ltf": True},
        "rank_sentiment": {"top_sent": x % 2 == 0, "bottom_sent": x % 3 == 0, "ranking_points": 90 - x * 10,
        "has_indicators": np.ones(n, bool), "local_sentiment": x * 20 - 50, "global_sentiment": x * 5 - 10,
        "sentiment_classification": np.where(x % 2 == 0, "EXTREME_BULLISH", "BEARISH")},
        "post_rank_transform": post,
        "dc_sufficiency": {"dc_high_3m": 110 + x, "dc_low_3m": 80 + x, "dc_high_15m": 112 + x,
        "dc_low_15m": 80 + x, "dc_high_1h": 115 + x, "dc_low_1h": 80 + x},
        "late_score_including_research": {"npz": npz, "gain": x * .25, "current_price": 111 + x},
        "finish_tail": {"current_price": 111 + x, "dc_high_1h": npz["dc_high_1h"],
        "dc_high_3m": npz["dc_high_3m"], "dc_low_1h": npz["dc_low_1h"], "dc_low_3m": npz["dc_low_3m"],
        "k_1m": 20 + x, "k_3m": npz["k_3m"], "true_lag": np.array([0, 9, 10, 2, 5, 7, 1.]),
        "recommendation_before_tail": np.full(n, "WAIT", object)},
    }}


def test_ordered_numpy_composer_matches_explicit_component_chain():
    inputs = _inputs()
    stages = inputs["stages"]
    n = inputs["n"]
    pre_dc_expected = pre_dc_entry_score_array({**stages["pre_dc_entry_state"], "score": inputs["score_before_dc"]}, True)
    score = pre_dc_expected["score"].copy()
    delta, _ = dc_structure_votes_array(stages["dc_structure"], True)
    score += delta
    delta, _, pa = breakout_pa_score_array(stages["breakout_pa"], True)
    score += delta + directional_score_array(stages["directional"], True)
    delta, _ = wt_intelligence_score_array(stages["wt_intelligence"], True)
    score += delta
    delta, _, _ = volume_setup_array(stages["volume_setup"], True)
    score += delta
    history, _ = history_score_array(**stages["tracker_history_modifier"])
    score, _ = apply_channel_confluence_vec(stages["channel_confluence"]["npz"], n, True, score, stages["channel_confluence"]["current_price"], True)
    rank = stages["rank_sentiment"]
    score = apply_rank_sentiment_vec(score, True, rank["top_sent"], rank["bottom_sent"], rank["ranking_points"], history,
                                     rank["has_indicators"], rank["local_sentiment"], rank["global_sentiment"], rank["sentiment_classification"])
    score, _ = apply_asr_post_rank_vec(score, True, **stages["post_rank_transform"])
    dc = stages["dc_sufficiency"]
    score, _ = apply_asr_dc_sufficiency_vec(score, dc["dc_high_3m"], dc["dc_low_3m"], dc["dc_high_15m"], dc["dc_low_15m"], dc["dc_high_1h"], dc["dc_low_1h"])
    late = stages["late_score_including_research"]
    score, _ = apply_asr_late_score_vec(late["npz"], n, True, inputs["cfg"], late["gain"], score, late["current_price"])
    tail = stages["finish_tail"]
    final, rec = finish_array(inputs["cfg"], score, tail["current_price"], True, tail["dc_high_1h"], tail["dc_high_3m"], tail["dc_low_1h"], tail["dc_low_3m"], tail["k_1m"], tail["k_3m"], tail["true_lag"], tail["recommendation_before_tail"])
    stages_before = {key: value.copy() if isinstance(value, dict) else value for key, value in inputs["stages"].items()}
    result = compose_asr_late_score(inputs, True)
    assert inputs["stages"]["channel_confluence"]["npz"] is stages_before["channel_confluence"]["npz"]
    assert np.array_equal(result["score_after_late_score"], score)
    assert np.array_equal(result["final_score"], final)
    assert np.array_equal(result["recommendation"], rec)
    assert np.array_equal(result["masks"]["pa_confirm"], pa)
    assert result["stage_order"] == ASR_LATE_STAGE_ORDER
    assert result["complete_rate"] is False
    assert result["remaining_stages"] == ASR_REMAINING_AFTER_COMPOSER


def test_ordered_composer_requires_every_declared_stage():
    inputs = _inputs()
    del inputs["stages"]["tracker_history_modifier"]
    try:
        compose_asr_late_score(inputs, True)
    except ValueError as error:
        assert "tracker_history_modifier" in str(error)
    else:
        raise AssertionError("missing score stage was accepted")


def test_npz_enum_binding_decodes_measured_codes_and_rejects_unknowns():
    cfg = SimpleNamespace(USE_1M_3M_SIGNALS_ENABLED=False)
    npz = {"wt_divergence_15m": np.array([1, 2, -1, -2, 0], dtype=np.int8),
           "wt_momentum_state_15m": np.array([-2, -1, 0, 1, 2], dtype=np.int8),
           "wt_peak_structure_15m": np.array([1, -1, 0, 1, -1], dtype=np.int8),
           "wt_trough_structure_15m": np.array([-1, 1, 0, -1, 1], dtype=np.int8),
           "wt_cross_15m": np.array([1, -1, 0, 1, -1], dtype=np.int8),
           "ha_15m": np.array([1, -1, 0, 1, -1], dtype=np.int8),
           "wt_cross_rising_15m": np.array([1, 0, 0, 1, 0], dtype=np.int8)}
    bound = bind_asr_npz_arrays(npz, cfg)
    assert bound["wt_divergence_3m"].tolist() == ["BULL", "HIDDEN_BULL", "BEAR", "HIDDEN_BEAR", None]
    assert bound["wt_momentum_state_3m"].tolist() == ["EXHAUST_DOWN", "IMPULSE_DOWN", "EXHAUST_DOWN", "IMPULSE_UP", "EXHAUST_UP"]
    assert bound["wt_peak_structure_3m"].tolist() == ["HH", "LH", None, "HH", "LH"]
    assert bound["wt_trough_structure_3m"].tolist() == ["LL", "HL", None, "LL", "HL"]
    assert bound["wt_cross_3m"].tolist() == ["BULL", "BEAR", None, "BULL", "BEAR"]
    assert bound["ha_3m"].tolist() == ["green", "red", "neutral", "green", "red"]
    assert bound["wt_cross_rising_3m"].tolist() == [True, False, False, True, False]
    npz["wt_cross_15m"] = np.array([9])
    try:
        bind_asr_npz_arrays(npz, cfg)
    except ValueError as error:
        assert "unsupported live indicator enum" in str(error)
    else:
        raise AssertionError("unknown encoded enum was accepted")
    npz["wt_cross_15m"] = np.array([1.5])
    try:
        bind_asr_npz_arrays(npz, cfg)
    except ValueError as error:
        assert "unrecognized encoded ASR label" in str(error)
    else:
        raise AssertionError("fractional encoded enum was accepted")


def test_raw_npz_and_explicit_causal_state_assemble_into_composer_inputs():
    fixture = _inputs(7)
    stages = fixture["stages"]
    raw = dict(stages["channel_confluence"]["npz"])
    for key, value in stages["dc_structure"].items():
        raw[key] = value
    for key, value in stages["breakout_pa"].items():
        if key not in ("current_price", "true_lag", "higher_high_15m", "lower_low_15m", "prev_cross_price", "lr_slope"):
            raw[key] = value
    raw.update(stages["directional"])
    raw.update(stages["wt_intelligence"])
    raw["k_15m_prev"] = stages["post_rank_transform"]["k15_prev"]
    volume = stages["volume_setup"]
    pre = stages["pre_dc_entry_state"]
    history = stages["tracker_history_modifier"]
    rank = stages["rank_sentiment"]
    post = stages["post_rank_transform"]
    causal = {"current_price": stages["breakout_pa"]["current_price"], "true_lag": stages["breakout_pa"]["true_lag"],
              "prev_cross_price": stages["breakout_pa"]["prev_cross_price"], "lr_slope": stages["breakout_pa"]["lr_slope"],
              "higher_high_15m": stages["breakout_pa"]["higher_high_15m"], "lower_low_15m": stages["breakout_pa"]["lower_low_15m"],
              "score_before_dc": fixture["score_before_dc"], "last_reduction_price": pre["last_reduction_price"],
              "minutes_since_reduction": pre["minutes_since_reduction"], "position_last_reduction_price": pre["position_last_reduction_price"],
              "position_last_augmentation_price": pre["position_last_augmentation_price"], "recent_exit": pre["recent_exit"],
              "k_3m_cross_over": pre["k_3m_cross_over"], "k_3m_cross_under": pre["k_3m_cross_under"],
              "rel_vol": volume["rel_vol"], "z_conviction": volume["z_conviction"], "trend_ok": volume["trend_ok"],
              "pr_confirm": volume["pr_confirm"], "history_present": history["history_present"],
              "win_rate": history["win_rate"], "total_trades": history["total_trades"],
              "total_realized_pnl": history["total_realized_pnl"], "top_sent": rank["top_sent"],
              "bottom_sent": rank["bottom_sent"], "ranking_points": rank["ranking_points"],
              "has_indicators": rank["has_indicators"], "local_sentiment": rank["local_sentiment"],
              "global_sentiment": rank["global_sentiment"], "sentiment_classification": rank["sentiment_classification"],
              "scalp_entry_detected": post["scalp_entry_detected"], "gain": post["gain"],
              "trend_continuation": post["trend_continuation"]}
    cfg = fixture["cfg"]
    bound = assemble_asr_composer_inputs(raw, cfg, causal, fixture["n"], True)
    result = compose_asr_late_score(bound, True)
    assert result["complete_rate"] is False
    assert result["final_score"].shape == (fixture["n"],)
