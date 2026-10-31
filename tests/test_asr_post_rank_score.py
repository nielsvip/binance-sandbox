import random
import textwrap
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from vec_decisions.asr_post_rank_score import (
    apply_asr_dc_sufficiency_scalar,
    apply_asr_dc_sufficiency_vec,
    apply_asr_post_rank_scalar,
    apply_asr_post_rank_vec,
)


_BACKUP = Path(__file__).resolve().parents[1] / "backups/before_asr_post_rank_score_native_hook_ez_positions_quick_20261031T062116Z_1793427676402689000.py"


def _legacy_blocks():
    lines = _BACKUP.read_text().splitlines()
    start = next(i for i, line in enumerate(lines) if 'logger.info(f" in rate4 {symbol} {score} ")' in line)
    adjust_start = next(i for i, line in enumerate(lines[start:], start) if "c1_long = k_1m > d_1m" in line)
    dc_start = next(i for i, line in enumerate(lines[adjust_start:], adjust_start) if "if dc_low_3m and dc_low_15m and dc_low_1h:" in line)
    end = next(i for i, line in enumerate(lines[dc_start:], dc_start) if 'rec = "WAIT"' in line)
    return textwrap.dedent("\n".join(lines[adjust_start:dc_start])), textwrap.dedent("\n".join(lines[dc_start:end]))


def _legacy_adjust(values):
    score_env = {**values, "logger": SimpleNamespace(info=lambda *args: None)}
    score_env["config"] = SimpleNamespace(MIN_GAIN=values["min_gain"])
    score_env["reasons"] = ["Trend_Continuation_ReEntry"] if values["trend_continuation"] else []
    exec(_legacy_blocks()[0], score_env)
    return score_env["score"], score_env["final_score"]


def _legacy_dc(values, score):
    env = {**values, "score": score}
    exec(_legacy_blocks()[1], env)
    return env["score"]


def _inputs(rng, is_long):
    return {
        "score": rng.uniform(-20, 40), "is_long": is_long,
        "k_1m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "d_1m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "k_1m_prev": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "k_3m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "d_3m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "k_3m_prev": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "k_15m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "d_15m": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "k_15m_prev": rng.choice([5.0, 15.0, 20.0, 40.0, 60.0, 85.0, 95.0]),
        "true_lag": rng.choice([0.0, 9.9, 10.0, 30.0]), "scalp_entry_detected": bool(rng.randrange(2)),
        "lower_low_15m": bool(rng.randrange(2)), "higher_high_15m": bool(rng.randrange(2)),
        "gain": rng.choice([-1.0, 0.0, 2.0, 3.0, 6.0, 9.01]), "min_gain": 3.0,
        "trend_continuation": bool(rng.randrange(2)),
        "dc_high_3m": 110.0, "dc_low_3m": rng.choice([0.0, 80.0, 90.0]),
        "dc_high_15m": 108.0, "dc_low_15m": rng.choice([0.0, 80.0, 90.0]),
        "dc_high_1h": 120.0, "dc_low_1h": rng.choice([0.0, 70.0, 80.0]), "k_str": "", "position_key": "TEST_LONG",
    }


def test_scalar_stages_match_exact_pre_edit_native_ast_blocks():
    rng = random.Random(20261031)
    for is_long in (True, False):
        for _ in range(600):
            x = _inputs(rng, is_long)
            native_score, native_final = _legacy_adjust(x)
            scalar_score, scalar_final = apply_asr_post_rank_scalar(
                x["score"], is_long, x["k_1m"], x["d_1m"], x["k_1m_prev"],
                x["k_3m"], x["d_3m"], x["k_3m_prev"], x["k_15m"], x["d_15m"],
                x["k_15m_prev"], x["true_lag"], x["scalp_entry_detected"],
                x["lower_low_15m"], x["higher_high_15m"], x["gain"], x["min_gain"],
                x["trend_continuation"],
            )
            assert scalar_score == native_score and scalar_final == native_final
            assert apply_asr_dc_sufficiency_scalar(
                scalar_score, x["dc_high_3m"], x["dc_low_3m"], x["dc_high_15m"],
                x["dc_low_15m"], x["dc_high_1h"], x["dc_low_1h"],
            )[0] == _legacy_dc(x, scalar_score)


def test_numpy_stages_match_scalar_both_sides_with_preclamp_ordering():
    rng = random.Random(99)
    for is_long in (True, False):
        cases = [_inputs(rng, is_long) for _ in range(250)]
        fields = {
            "score": np.array([x["score"] for x in cases]),
            "k1": np.array([x["k_1m"] for x in cases]), "d1": np.array([x["d_1m"] for x in cases]),
            "k1p": np.array([x["k_1m_prev"] for x in cases]), "k3": np.array([x["k_3m"] for x in cases]),
            "d3": np.array([x["d_3m"] for x in cases]), "k3p": np.array([x["k_3m_prev"] for x in cases]),
            "k15": np.array([x["k_15m"] for x in cases]), "d15": np.array([x["d_15m"] for x in cases]),
            "k15p": np.array([x["k_15m_prev"] for x in cases]), "lag": np.array([x["true_lag"] for x in cases]),
            "scalp": np.array([x["scalp_entry_detected"] for x in cases]), "ll": np.array([x["lower_low_15m"] for x in cases]),
            "hh": np.array([x["higher_high_15m"] for x in cases]), "gain": np.array([x["gain"] for x in cases]),
            "trend": np.array([x["trend_continuation"] for x in cases]),
        }
        expected_score = []
        expected_final = []
        expected_dc = []
        for x in cases:
            score, final = apply_asr_post_rank_scalar(
                x["score"], is_long, x["k_1m"], x["d_1m"], x["k_1m_prev"], x["k_3m"],
                x["d_3m"], x["k_3m_prev"], x["k_15m"], x["d_15m"], x["k_15m_prev"],
                x["true_lag"], x["scalp_entry_detected"], x["lower_low_15m"],
                x["higher_high_15m"], x["gain"], x["min_gain"], x["trend_continuation"],
            )
            expected_score.append(score)
            expected_final.append(final)
            expected_dc.append(apply_asr_dc_sufficiency_scalar(
                score, x["dc_high_3m"], x["dc_low_3m"], x["dc_high_15m"], x["dc_low_15m"],
                x["dc_high_1h"], x["dc_low_1h"],
            )[0])
        actual_score, actual_final = apply_asr_post_rank_vec(
            fields["score"], is_long, fields["k1"], fields["d1"], fields["k1p"], fields["k3"],
            fields["d3"], fields["k3p"], fields["k15"], fields["d15"], fields["k15p"],
            fields["lag"], fields["scalp"], fields["ll"], fields["hh"], fields["gain"], 3.0,
            fields["trend"],
        )
        np.testing.assert_array_equal(actual_score, np.array(expected_score))
        np.testing.assert_array_equal(actual_final, np.array(expected_final))
        actual_dc, _ = apply_asr_dc_sufficiency_vec(
            actual_score,
            np.array([x["dc_high_3m"] for x in cases]), np.array([x["dc_low_3m"] for x in cases]),
            np.array([x["dc_high_15m"] for x in cases]), np.array([x["dc_low_15m"] for x in cases]),
            np.array([x["dc_high_1h"] for x in cases]), np.array([x["dc_low_1h"] for x in cases]),
        )
        np.testing.assert_array_equal(actual_dc, np.array(expected_dc))
