import random
from pathlib import Path
import textwrap

import numpy as np

from vec_decisions.asr_pre_dc_entry_score import pre_dc_entry_score_array, pre_dc_entry_score_scalar

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = ROOT / "backups/before_asr_pre_dc_oracle_ez_positions_quick_20261031T070725Z_1793430445564248000.py"


def _value(rng):
    row = {"score": rng.uniform(-5, 50), "current_price": rng.uniform(80, 120), "last_reduction_price": rng.choice([0.0, 90.0, 100.0, 110.0]),
           "minutes_since_reduction": rng.choice([0.0, 30.0, 1199.0, 1200.0, 1201.0]), "k_1m": rng.uniform(0, 100), "d_1m": rng.uniform(0, 100),
           "k_1m_prev": rng.uniform(0, 100), "k_3m": rng.uniform(0, 100), "d_3m": rng.uniform(0, 100), "k_3m_prev": rng.uniform(0, 100),
           "k_15m": rng.uniform(0, 100), "d_15m": rng.uniform(0, 100), "k_1h": rng.uniform(0, 100), "d_1h": rng.uniform(0, 100),
           "k_4h": rng.uniform(0, 100), "true_lag": rng.choice([0.0, 9.9, 10.0, 12.0]), "lower_low_15m": rng.choice([True, False]),
           "higher_high_15m": rng.choice([True, False]), "recent_exit": rng.choice([True, False]), "position_last_reduction_price": rng.choice([0.0, 90.0, 100.0]),
           "position_last_augmentation_price": rng.choice([0.0, 90.0, 100.0]), "dc_basis_15m": rng.uniform(80, 120), "dc_basis_1h": rng.uniform(80, 120),
           "dc_high_4h": rng.uniform(80, 120), "dc_high_4h_ant": rng.uniform(80, 120), "dc_low_4h": rng.uniform(80, 120), "dc_low_4h_ant": rng.uniform(80, 120),
           "k_3m_cross_over": rng.choice([True, False]), "k_3m_cross_under": rng.choice([True, False])}
    return row


def _native_prefix(row, is_long):
    values = dict(row)
    score = values.pop("score")
    reasons = []
    values["is_long"] = is_long
    values["is_exit"] = False
    values["last_reduction_price"] = row["last_reduction_price"]
    values["min_since_red"] = row["minutes_since_reduction"]
    values["lower_low_15m"] = row["lower_low_15m"]
    values["higher_high_15m"] = row["higher_high_15m"]
    values["dc_basis_15m"] = row["dc_basis_15m"]
    values["dc_basis_1h"] = row["dc_basis_1h"]
    values["dc_high_4h"] = row["dc_high_4h"]
    values["dc_high_4h_ant"] = row["dc_high_4h_ant"]
    values["dc_low_4h"] = row["dc_low_4h"]
    values["dc_low_4h_ant"] = row["dc_low_4h_ant"]
    values["k_3mco"] = row["k_3m_cross_over"]
    values["k_3mcu"] = row["k_3m_cross_under"]
    class Pos:
        last_reduction_price = row["position_last_reduction_price"]
        last_augmentation_price = row["position_last_augmentation_price"]
    values["position"] = Pos()
    from datetime import datetime, timedelta, timezone
    values["last_exit_timestamp"] = datetime.now(timezone.utc) - timedelta(seconds=1200) if row["recent_exit"] else None
    values["datetime"] = datetime
    values["timezone"] = timezone
    values["safe_fetch_float"] = lambda value, default=0.0: default if value is None else float(value)
    values["i"] = {key: row[key] for key in ("dc_high_4h_ant", "dc_low_4h_ant")}
    source_lines = ORIGINAL.read_text().splitlines()
    marker = next(i for i, line in enumerate(source_lines) if 'logger.info(f" in rate0 {position_key} {score} {k_str}")' in line)
    start = next(i for i in range(marker, len(source_lines)) if source_lines[i] == "        if not is_exit:")
    end = next(i for i in range(start, len(source_lines)) if source_lines[i].startswith("            dc_high_4h_ant = safe_fetch_float"))
    source = textwrap.dedent("\n".join(source_lines[start:end]))
    program = "def _native(score, reasons):\n" + textwrap.indent(source, "    ") + '\n    return score, pr_confirm, valid_setup, force_reentry, special_reentry, locals().get("set_a", False), locals().get("set_b", False), locals().get("set_c", False), locals().get("set_d", False), locals().get("set_e", False), locals().get("quick_reentry", False)\n'
    ns = dict(values, score=score, reasons=reasons, position_key="TEST_LONG" if is_long else "TEST_SHORT", k_str="fixture")
    exec(compile(program, str(ORIGINAL), "exec"), ns)
    return ns["_native"](score, reasons)


def test_array_stage_matches_scalar_for_both_sides_and_boundaries():
    rng = random.Random(20261031)
    for is_long in (True, False):
        rows = [_value(rng) for _ in range(900)]
        values = {key: np.asarray([row[key] for row in rows]) for key in rows[0]}
        result = pre_dc_entry_score_array(values, is_long)
        scalar = [pre_dc_entry_score_scalar(row["score"], row, is_long) for row in rows]
        assert np.allclose(result["score"], [item[0] for item in scalar])
        assert np.array_equal(result["quick_reentry"], [item[1] for item in scalar])
        assert np.array_equal(result["pr_confirm"], [item[2] for item in scalar])
        assert np.array_equal(result["valid_setup"], [item[3] for item in scalar])
        assert result["quick_reentry_reason"].shape == (len(rows),)
        assert np.all(result["quick_reentry_reason"][~result["quick_reentry"]] == "")
        for name in result["masks"]:
            assert np.array_equal(result["masks"][name], [item[5][name] for item in scalar])


def test_pre_dc_stage_matches_unchanged_native_inline_branch():
    rng = random.Random(881203)
    for is_long in (True, False):
        for _ in range(40):
            row = _value(rng)
            native = _native_prefix(row, is_long)
            scalar = pre_dc_entry_score_scalar(row["score"], row, is_long)
            if native is None:
                continue
            if len(native) == 3:
                assert native[0] == 12 and native[1] == "QUICK_REENTRY"
                assert scalar[1] is True
                continue
            assert np.isclose(native[0], scalar[0])
            assert native[1] == scalar[2]
            assert native[2] == scalar[3]
            assert native[3] == scalar[5]["force_reentry"]
            assert native[4] == scalar[5]["special_reentry"]
            assert native[5:10] == tuple(scalar[5][f"set_{letter}"] for letter in "abcde")
            assert native[10] == scalar[1]


def test_exact_native_quick_reentry_return_preempts_later_score_stages():
    row = _value(random.Random(77))
    row.update({"score": 9.0, "current_price": 110.0, "recent_exit": True, "last_reduction_price": 0.0,
                "minutes_since_reduction": 5000.0, "k_1m": 60.0, "d_1m": 50.0, "k_1m_prev": 55.0,
                "k_3m": 45.0, "d_3m": 40.0, "k_3m_prev": 40.0, "k_15m": 60.0, "d_15m": 50.0,
                "k_1h": 60.0, "d_1h": 50.0, "position_last_reduction_price": 0.0,
                "position_last_augmentation_price": 0.0, "dc_basis_15m": 100.0, "dc_basis_1h": 100.0,
                "higher_high_15m": True, "lower_low_15m": False, "dc_high_4h": 110.0,
                "dc_high_4h_ant": 110.0, "dc_low_4h": 80.0, "dc_low_4h_ant": 80.0})
    scalar = pre_dc_entry_score_scalar(row["score"], row, True)
    native = _native_prefix(row, True)
    assert scalar[1] is True
    assert native[0] == 12
    assert native[1] == "QUICK_REENTRY"
