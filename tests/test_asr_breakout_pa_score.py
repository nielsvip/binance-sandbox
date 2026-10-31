import random
import textwrap
from pathlib import Path

import numpy as np

from vec_decisions.asr_breakout_pa_score import breakout_pa_score_array, breakout_pa_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_dc_structure_votes_ez_positions_quick_20261031T062918Z_1793428158252830000.py"


def _source():
    lines = ORIGINAL.read_text().splitlines()
    votes = next(index for index, line in enumerate(lines) if "dc_low_15m_ant = safe_fetch_float(i.get(" in line)
    long_start = next(index for index in range(votes, len(lines)) if "current_price >= dc_high_4h" in lines[index])
    while lines[long_start] != "                if (":
        long_start -= 1
    short_level = next(index for index in range(long_start, len(lines)) if "current_price <= dc_low_4h" in lines[index])
    short_start = short_level
    while lines[short_start] != "                if (":
        short_start -= 1
    shared_start = next(index for index in range(short_level, len(lines)) if lines[index] == "            if (is_long and k_1m > k_1m_prev + 50.0 or k_3m > k_3m_prev + 40.0) or (")
    long_end = next(index for index in range(long_start, len(lines)) if 'logger.info(f" in rate1' in lines[index]) + 1
    long_body = textwrap.dedent("\n".join(lines[long_start:long_end]))
    short_body = textwrap.dedent("\n".join(lines[short_start:shared_start]))
    shared_start = next(index for index in range(shared_start, len(lines)) if lines[index] == "            logger.info(")
    shared = textwrap.dedent("\n".join(lines[next(index for index in range(short_level, shared_start) if lines[index] == "            if (is_long and k_1m > k_1m_prev + 50.0 or k_3m > k_3m_prev + 40.0) or ("):shared_start]))
    return "if is_long:\n" + textwrap.indent(long_body, "    ") + "\nelse:\n" + textwrap.indent(short_body, "    ") + "\n" + shared


class _Logger:
    def info(self, *args, **kwargs):
        return None


def _values(rng):
    values = {"current_price": rng.uniform(80.0, 120.0), "true_lag": rng.choice([0.0, 9.9, 10.0, 12.0]),
              "prev_cross_price": rng.choice([0.0, 90.0, 100.0, 110.0, float("nan")]), "lr_slope": rng.choice([-1.0, 0.0, 1.0]),
              "higher_high_15m": rng.choice([False, True]), "lower_low_15m": rng.choice([False, True]),
              "k_str": "fixture", "position_key": "TEST_LONG", "logger": _Logger(),
              "k_1m": rng.uniform(0, 100), "k_1m_prev": rng.uniform(0, 100), "d_1m": rng.uniform(0, 100),
              "k_3m": rng.uniform(0, 100), "k_3m_prev": rng.uniform(0, 100), "d_3m": rng.uniform(0, 100),
              "k_15m": rng.uniform(0, 100), "d_15m": rng.uniform(0, 100)}
    for tf in ("3m", "15m", "1h", "4h"):
        values[f"dc_high_{tf}"] = rng.uniform(80, 120)
        values[f"dc_low_{tf}"] = rng.uniform(80, 120)
    return values


def _native(values, is_long):
    scope = dict(values, is_long=is_long, score=0.0, reasons=[])
    exec(compile(_source(), str(ORIGINAL), "exec"), {}, scope)
    return scope["score"], scope["reasons"], scope["pa_confirm"]


def test_exact_native_breakout_k_acceleration_and_pa_source_matches_scalar():
    rng = random.Random(241027)
    for side in (True, False):
        for _ in range(700):
            values = _values(rng)
            assert breakout_pa_score_scalar(values, side) == _native(values, side)


def test_numpy_breakout_and_pa_outputs_match_native_per_bar():
    rng = random.Random(241028)
    for side in (True, False):
        rows = [_values(rng) for _ in range(400)]
        arrays = {key: np.asarray([row[key] for row in rows], dtype=bool if key in ("higher_high_15m", "lower_low_15m") else object if key in ("prev_cross_price", "logger", "k_str", "position_key") else float) for key in rows[0] if key not in ("logger", "k_str", "position_key")}
        score, masks, pa = breakout_pa_score_array(arrays, side)
        native = [_native(row, side) for row in rows]
        assert np.array_equal(score, np.asarray([item[0] for item in native]))
        assert np.array_equal(pa, np.asarray([item[2] for item in native]))


def test_native_rate_keeps_rate1_log_and_rate2_boundary_around_shared_stage():
    source = (ROOT / "ez_positions_quick.py").read_text()
    assert 'logger.info(f" in rate1 {position_key} {score} {k_str}")' in source
    assert 'f" in rate2 {position_key} {score} {k_str} tts1:{true_lag} ts3:{timestamp_str}"' in source
    assert "_asr_breakout_pa_score_scalar" in source
