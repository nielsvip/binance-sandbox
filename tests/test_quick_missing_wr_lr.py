import numpy as np

from vec_decisions.check_entry_candidates_crypto__wr_lr_pullback import check_wr_lr_pullback
from vec_decisions.quick_missing_wr_lr import historical_wr_membership_mask, wr_lr_good_score, wr_lr_pullback_15m_mask


class Config:
    USE_1M_3M_SIGNALS_ENABLED = False


def test_long_mask_matches_live_15m_fallback_and_good_score_floor():
    ha = np.array(["green", "red", "neutral", "neutral"])
    k1h = np.array([30.0, 30.0, 45.0, 30.0])
    k15 = np.array([25.0, 25.0, 25.0, 35.0])
    k15p = np.array([20.0, 20.0, 20.0, 30.0])
    cross = np.array(["", "BULL", "BULL", "BULL"])
    listed = np.array([True, True, True, False])
    mask = wr_lr_pullback_15m_mask(ha, k1h, k15, k15p, cross, listed, True)
    expected = []
    for i in range(len(mask)):
        fires, _ = check_wr_lr_pullback(
            Config(),
            {"ha_4h": ha[i], "stoch_k_1h": k1h[i], "stoch_k_15m": k15[i], "stoch_k_15m_prev": k15p[i], "wt_cross_15m": cross[i]},
            {},
            bool(listed[i]),
            True,
        )
        expected.append(fires)
    np.testing.assert_array_equal(mask, expected)
    np.testing.assert_array_equal(wr_lr_good_score(np.array([10, 22, 30, 15]), mask), [22, 22, 30, 15])


def test_short_mask_matches_live_15m_fallback_and_respects_membership():
    ha = np.array(["red", "green", "neutral", "neutral"])
    k1h = np.array([70.0, 70.0, 55.0, 70.0])
    k15 = np.array([75.0, 75.0, 75.0, 65.0])
    k15p = np.array([80.0, 80.0, 80.0, 70.0])
    cross = np.array(["", "BEAR", "BEAR", "BEAR"])
    listed = np.array([True, True, True, False])
    mask = wr_lr_pullback_15m_mask(ha, k1h, k15, k15p, cross, listed, False)
    expected = []
    for i in range(len(mask)):
        fires, _ = check_wr_lr_pullback(
            Config(),
            {"ha_4h": ha[i], "stoch_k_1h": k1h[i], "stoch_k_15m": k15[i], "stoch_k_15m_prev": k15p[i], "wt_cross_15m": cross[i]},
            {},
            bool(listed[i]),
            False,
        )
        expected.append(fires)
    np.testing.assert_array_equal(mask, expected)


def test_score_floor_is_conditional_and_shape_checked():
    score = np.array([5.0, 22.0, 31.0])
    mask = np.array([True, False, True])
    np.testing.assert_array_equal(wr_lr_good_score(score, mask), [22.0, 22.0, 31.0])


def test_historical_membership_is_account_specific_and_unknown_fails_closed(monkeypatch, tmp_path):
    import json
    import vec_decisions.quick_missing_wr_lr as missing

    archive = tmp_path / "wr_lists_history.jsonl"
    archive.write_text(json.dumps({"ts": 100.0, "ang_long": ["1000PEPEUSDC"], "ang_short": [], "inf_long": [], "inf_short": ["1000PEPEUSDC"], "flz_long": ["1000PEPEUSDC"], "fin_short": ["1000PEPEUSDC"], "men_long": []}) + "\n")
    monkeypatch.setattr(missing, "_archive_path", lambda: str(archive))
    monkeypatch.setattr(missing, "_ARCHIVE_CACHE", {"mtime": None, "rows": [], "times": []})
    times = np.array([99.0, 100.0, 101.0])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, True, "ang"), [False, True, True])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, True, "inf"), [False, False, False])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, False, "inf"), [False, True, True])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, True, "flz"), [False, True, True])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, False, "fin"), [False, True, True])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, True, "men"), [False, False, False])
    np.testing.assert_array_equal(historical_wr_membership_mask("1000PEPEUSDC", times, True, None), [False, False, False])


def test_npz_quick_good_adapter_uses_archive_membership_and_real_fields():
    from vec_decisions.quick_missing_wr_lr import wr_lr_quick_good_from_npz

    npz = {
        "timestamps": np.array([100., 200., 300.]),
        "ha_4h": np.array([1, 1, 0], dtype=np.int8),
        "stoch_k_1h": np.array([30., 70., 30.]),
        "stoch_k_15m": np.array([25., 75., 25.]),
        "stoch_k_15m_prev": np.array([20., 80., 20.]),
        "wt_cross_15m": np.array([0, 0, 0], dtype=np.int8),
    }
    lookups = {100.: True, 200.: True, 300.: None}
    mask, score = wr_lr_quick_good_from_npz(
        npz, "1000PEPEUSDC", True, Config(), np.array([15., 20., 30.]),
        membership_lookup=lambda account, symbol, side, ts: lookups[ts], account="ang",
    )
    expected = []
    for i, ts in enumerate(npz["timestamps"]):
        expected.append(check_wr_lr_pullback(
            Config(),
            {"ha_4h": "green", "stoch_k_1h": npz["stoch_k_1h"][i],
             "stoch_k_15m": npz["stoch_k_15m"][i],
             "stoch_k_15m_prev": npz["stoch_k_15m_prev"][i], "wt_cross_15m": ""},
            {}, lookups[ts] is True, True,
        )[0])
    np.testing.assert_array_equal(mask, expected)
    np.testing.assert_array_equal(score, [22., 20., 30.])


def test_npz_quick_good_fails_closed_without_archived_membership_or_timestamps():
    from vec_decisions.quick_missing_wr_lr import wr_lr_quick_good_from_npz

    npz = {
        "timestamps": np.array([100.]), "ha_4h": np.array([1], dtype=np.int8),
        "stoch_k_1h": np.array([30.]), "stoch_k_15m": np.array([25.]),
        "stoch_k_15m_prev": np.array([20.]), "wt_cross_15m": np.array([1], dtype=np.int8),
    }
    mask, _ = wr_lr_quick_good_from_npz(npz, "1000PEPEUSDC", True, Config(), membership_lookup=lambda *args: None, account="ang")
    assert not mask.any()
    missing = {key: value for key, value in npz.items() if key != "timestamps"}
    assert wr_lr_quick_good_from_npz(missing, "1000PEPEUSDC", True, Config(), account="ang") is None


def test_npz_quick_good_refuses_15m_approximation_when_ltf_is_enabled():
    from vec_decisions.quick_missing_wr_lr import wr_lr_quick_good_from_npz

    class LtfEnabled:
        USE_1M_3M_SIGNALS_ENABLED = True
        WR_PULLBACK_ENABLED = True

    npz = {"timestamps": np.array([100.]), "ha_4h": np.array([1], dtype=np.int8)}
    assert wr_lr_quick_good_from_npz(npz, "1000PEPEUSDC", True, LtfEnabled(), membership_lookup=lambda *args: True, account="ang") is None


def test_npz_quick_good_short_wrapper_matches_native_scalar():
    from vec_decisions.quick_missing_wr_lr import wr_lr_quick_good_from_npz

    npz = {
        "timestamps": np.array([400.]),
        "ha_4h": np.array([-1], dtype=np.int8),
        "stoch_k_1h": np.array([70.]),
        "stoch_k_15m": np.array([75.]),
        "stoch_k_15m_prev": np.array([80.]),
        "wt_cross_15m": np.array([-1], dtype=np.int8),
    }
    membership = lambda account, symbol, side, ts: account == "inf" and symbol == "1000PEPEUSDC" and not side and ts == 400.0
    mask, score = wr_lr_quick_good_from_npz(
        npz, "1000PEPEUSDC", False, Config(), np.array([18.]),
        membership_lookup=membership, account="inf",
    )
    expected, _ = check_wr_lr_pullback(
        Config(),
        {"ha_4h": "red", "stoch_k_1h": 70., "stoch_k_15m": 75.,
         "stoch_k_15m_prev": 80., "wt_cross_15m": "BEAR"},
        {}, True, False,
    )
    np.testing.assert_array_equal(mask, [expected])
    np.testing.assert_array_equal(score, [22.])
