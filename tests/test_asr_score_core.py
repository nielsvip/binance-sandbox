import numpy as np

from vec_decisions.asr_score_core import (
    apply_score_prefix_scalar, apply_score_prefix_vec,
    apply_structure_score_scalar, apply_structure_score_vec,
    apply_15m_momentum_scalar, apply_15m_momentum_vec,
    apply_ma_distance_score_scalar, apply_ma_distance_score_vec,
    apply_rank_conviction_scalar, apply_rank_conviction_vec,
    apply_channel_k_cross_score_scalar, apply_channel_k_cross_score_vec,
)


class Cfg:
    WT_COMPOSITE_SCORING_ENABLED = True
    WT_COMPOSITE_SCORING_ENABLED_TRADIER = False
    WT_COMPOSITE_ENTRY_STRONG = 50
    WT_COMPOSITE_ENTRY_GOOD = 30
    WT_COMPOSITE_ENTRY_OK = 10
    R_S1_WT_COMPOSITE_DELTA_USE_ENABLED = True
    R_S1_WT_COMPOSITE_DELTA_THR = 50
    R_S2_WT_ADAPTIVE_OS_ENABLED = True
    R_S2_WT_PCT_OS_LONG = 10
    R_S2_WT_PCT_OB_SHORT = 90
    DC_MOMENT_ENABLED = True
    DC_MOMENT_STRONG_THRESHOLD = 40
    DC_MOMENT_STRONG_BONUS = 10
    DC_MOMENT_OPPOSITE_PENALTY = -15
    WT_COMPOSITE_DELTA_SCORE_ENABLED = True
    WT_COMPOSITE_DELTA_SCORE_THRESHOLD = 50
    WT_COMPOSITE_DELTA_SCORE_BONUS = 3


def _indicator_row(arrays, i):
    return {key: np.asarray(value)[i].item() for key, value in arrays.items()}


def test_ordered_prefix_scalar_matches_vector_both_sides_and_boundaries():
    arrays = {
        "wt_composite_long": np.array([50, 30, 10, np.nan, 49]),
        "wt_composite_short": np.array([50, 30, 10, np.nan, 51]),
        "wt_oversold_tf_count": np.array([2, 0, 3, 2, 1]),
        "wt_overbought_tf_count": np.array([0, 2, 3, 2, 1]),
        "wt_hl_count": np.array([2, 2, 0, 2, 2]),
        "wt_lh_count": np.array([0, 2, 3, 2, 2]),
        "wt_composite_delta": np.array([51, -51, 50, np.nan, -60]),
        "wt_percentile_15m": np.array([9, 91, 10, np.nan, 95]),
        "wt_any_bull_div": np.array([1, 0, 0, 0, 1]),
        "wt_any_bear_div": np.array([0, 1, 0, 0, 1]),
        "wt_bull_cross_count": np.array([2, 1, 2, 3, 0]),
        "wt_bear_cross_count": np.array([1, 2, 2, 3, 0]),
        "0dc_moment": np.array([41, -41, 40, np.nan, -70]),
    }
    for is_long in (True, False):
        vector, _ = apply_score_prefix_vec(arrays, 5, is_long, Cfg)
        for i in range(5):
            scalar, _ = apply_score_prefix_scalar(
                0, [], _indicator_row(arrays, i), {}, is_long, Cfg, True,
                _indicator_row(arrays, i).get("wt_bull_cross_count", 0),
                _indicator_row(arrays, i).get("wt_bear_cross_count", 0),
            )
            assert vector[i] == scalar


def test_scalar_and_vector_preserve_metric_fallback_and_reason_order():
    scalar, reasons = apply_score_prefix_scalar(
        1.5,
        [],
        {"wt_composite_long": 50, "wt_bull_cross_count": 2},
        {"0dc_moment": 41},
        True,
        Cfg,
        True,
        2,
        0,
    )
    vector, _ = apply_score_prefix_vec(
        {"wt_composite_long": np.array([50]), "wt_bull_cross_count": np.array([2])},
        1, True, Cfg, score=np.array([1.5]), metrics={"0dc_moment": np.array([41])}
    )
    assert vector[0] == scalar
    assert scalar == 1.5 + 8 + 10 + 3
    assert reasons == [
        "WT_STRONG(L=50,+8)",
        "WT_MULTI_BULL_X(2,+3)",
        "DC_MOMENT_STRONG_L(41,+10)",
    ]


def test_shared_prefix_matches_sha_verified_pre_edit_native_ast():
    import ast
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    from utils import safe_fetch_float

    source_dir = Path(__file__).resolve().parents[1]
    backups = sorted((source_dir / "backups").glob("before_asr_score_prefix_wire_ez_positions_quick_*.py"))
    reference = next(path for path in backups if hashlib.sha256(path.read_bytes()).hexdigest() == "7634c897c67a28dbbfcbf99f39cabc1e564084bf82d07f632b6042267399d1a8")
    source = reference.read_text()
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    native_nodes = [node for node in rate.body if (4648 <= node.lineno <= 4664) or (4696 <= node.lineno <= 4822)]
    namespace = {
        "safe_fetch_float": safe_fetch_float,
        "config": SimpleNamespace(
            WT_COMPOSITE_SCORING_ENABLED=True,
            WT_COMPOSITE_SCORING_ENABLED_TRADIER=False,
            WT_COMPOSITE_ENTRY_STRONG=50.0,
            WT_COMPOSITE_ENTRY_GOOD=30.0,
            WT_COMPOSITE_ENTRY_OK=10.0,
            R_S1_WT_COMPOSITE_DELTA_USE_ENABLED=True,
            R_S1_WT_COMPOSITE_DELTA_THR=50.0,
            R_S2_WT_ADAPTIVE_OS_ENABLED=True,
            R_S2_WT_PCT_OS_LONG=10.0,
            R_S2_WT_PCT_OB_SHORT=90.0,
            DC_MOMENT_ENABLED=True,
            DC_MOMENT_STRONG_THRESHOLD=40.0,
            DC_MOMENT_STRONG_BONUS=10.0,
            DC_MOMENT_OPPOSITE_PENALTY=-15.0,
            WT_COMPOSITE_DELTA_SCORE_ENABLED=True,
            WT_COMPOSITE_DELTA_SCORE_THRESHOLD=50.0,
            WT_COMPOSITE_DELTA_SCORE_BONUS=3.0,
        ),
    }
    from vec_decisions.asr_score_core import apply_score_prefix_scalar

    cases = [
        (True, {"wt_composite_long": 50, "wt_oversold_tf_count": 2, "wt_hl_count": 2, "wt_composite_delta": 51, "wt_percentile_15m": 9, "wt_any_bull_div": True, "wt_strongest_bull_div_tf": "1h", "wt_bull_cross_count": 2, "0dc_moment": 41}),
        (False, {"wt_composite_short": 30, "wt_overbought_tf_count": 2, "wt_lh_count": 2, "wt_composite_delta": -51, "wt_percentile_15m": 91, "wt_any_bear_div": True, "wt_strongest_bear_div_tf": "4h", "wt_bear_cross_count": 2, "0dc_moment": -41}),
        (True, {"wt_composite_long": None, "wt_oversold_tf_count": "bad", "wt_hl_count": 1, "wt_bull_cross_count": 0, "0dc_moment": None}),
    ]
    for is_long, indicators in cases:
        metric_values = {}
        env = dict(namespace, is_long=is_long, is_exit=False, ind=indicators, i=indicators, metrics=metric_values, score=2.75, reasons=[])
        exec(compile(ast.Module(body=native_nodes, type_ignores=[]), str(reference), "exec"), env)
        expected_score, expected_reasons = env["score"], env["reasons"]
        actual_score, actual_reasons = apply_score_prefix_scalar(2.75, [], indicators, metric_values, is_long, env["config"], env["_wt_comp_enabled"], env["_wt_bull_cross_n"], env["_wt_bear_cross_n"])
        assert actual_score == expected_score
        assert actual_reasons == expected_reasons


def test_structure_score_matches_sha_verified_pre_edit_native_ast_and_numpy():
    import ast
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    source_dir = Path(__file__).resolve().parents[1]
    candidates = sorted((source_dir / "backups").glob("before_asr_structure_score_wire_ez_positions_quick_*.py"))
    reference = next(path for path in candidates if hashlib.sha256(path.read_bytes()).hexdigest() == "4f79156dd11f7736977952061d1ed36e55276f093bc8cbbc354f7ca0b31cc014")
    tree = ast.parse(reference.read_text())
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    start = next(i for i, node in enumerate(rate.body) if isinstance(node, ast.If) and "R_S3_DIV_STACK_ENABLED" in ast.unparse(node))
    end = next(i for i, node in enumerate(rate.body[start:], start) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_regime_info" for t in node.targets))
    native_nodes = rate.body[start:end]
    cfg = SimpleNamespace(
        R_S3_DIV_STACK_ENABLED=True, R_S3_HIDDEN_BONUS=25.0, R_S3_MAIN_PENALTY=-20.0, R_S3_HTF_WEIGHT_ENABLED=True,
        R_S3_TF_WEIGHT_3M=0.25, R_S3_TF_WEIGHT_15M=0.5, R_S3_TF_WEIGHT_1H=1.0, R_S3_TF_WEIGHT_4H=2.5, R_S3_TF_WEIGHT_D=4.0,
        R_S7_HHLL_STACK_ENABLED=True, R_S7_HHLL_TFS="15m,1h,4h,D", R_S7_HHLL_MIN_INDICATORS=2, R_S7_HHLL_BONUS_PER_TF=3.0, R_S7_HHLL_MIN_TFS_FOR_BONUS=2,
        R_S4_HA_STREAK_ENABLED=True, R_S4_HA_STREAK_TF="1h", R_S4_HA_STREAK_WEIGHT=5.0,
        R_S5_SENT_VEL_ENABLED=True, R_S5_SENT_VEL_PCT_THR=75.0, R_S5_SENT_VEL_BONUS=5.0,
    )
    rows = []
    for row in range(4):
        item = {
            "wt_divergence_3m": "HIDDEN_BULL" if row in (0, 2) else "BEAR",
            "wt_divergence_15m": "BEAR" if row in (0, 1) else "HIDDEN_BULL",
            "wt_divergence_1h": "HIDDEN_BULL" if row in (0, 3) else "BEAR",
            "wt_divergence_4h": "BEAR" if row == 1 else "NONE",
            "wt_divergence_D": "HIDDEN_BULL" if row == 2 else "NONE",
            "ha_1h": "green" if row % 2 == 0 else "red",
            "ha_streak_1h": row + 1, "0sentiment_velocity": 80.0 if row % 2 == 0 else -90.0,
        }
        for tf in ("15m", "1h", "4h", "D"):
            item.update({f"high_{tf}": 11+row, f"high_{tf}_prev": 10, f"low_{tf}": 6+row, f"low_{tf}_prev": 5, f"wt_structure_{tf}": "HH", f"k_{tf}": 40+row, f"k_{tf}_prev": 30})
        rows.append(item)
    arrays = {key: np.asarray([row.get(key, 0) for row in rows]) for key in set().union(*(row.keys() for row in rows))}
    for is_long in (True, False):
        actual, _ = apply_structure_score_vec(arrays, len(rows), is_long, cfg, score=np.full(len(rows), 1.25))
        for index, indicators in enumerate(rows):
            from utils import safe_fetch_float
            env = {"score": 1.25, "reasons": [], "ind": indicators, "is_long": is_long, "is_exit": False, "config": cfg, "safe_fetch_float": safe_fetch_float}
            exec(compile(ast.Module(body=native_nodes, type_ignores=[]), str(reference), "exec"), env)
            scalar, _ = apply_structure_score_scalar(1.25, [], indicators, is_long, cfg)
            assert scalar == env["score"] == actual[index]
            assert apply_structure_score_scalar(1.25, [], indicators, is_long, cfg)[1] == env["reasons"]


def test_15m_momentum_matches_sha_verified_pre_edit_native_ast_and_numpy():
    import ast
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    source_dir = Path(__file__).resolve().parents[1]
    refs = sorted((source_dir / "backups").glob("before_asr_15m_momentum_wire_retry_ez_positions_quick_*.py"))
    reference = next(path for path in refs if hashlib.sha256(path.read_bytes()).hexdigest() == "c7fab7fa75c83c1996d55f80fb85ea29c5e2be33ada2740bca377a85a9cff2db")
    rate = next(node for node in ast.walk(ast.parse(reference.read_text())) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    native = next(node for node in rate.body if isinstance(node, ast.If) and "_wt1_15m_prev_r" in ast.unparse(node))
    cfg = SimpleNamespace(WT15M_AGAINST_PENALTY=-5.0)
    rows = [
        {"wt1_15m": 3.0, "wt2_15m": 2.0, "wt1_15m_prev": 4.0},
        {"wt1_15m": 4.0, "wt2_15m": 3.0, "wt1_15m_prev": 3.0, "wt_cross_bear_3m": 1, "wt_crossover_value_3m": 2.0, "wt_crossover_value_3m_prev": 3.0},
        {"wt1_15m": 1.0, "wt2_15m": 2.0, "wt1_15m_prev": 2.0, "wt_cross_bull_3m": 1, "wt_crossover_value_3m": 5.0, "wt_crossover_value_3m_prev": 4.0},
        {"wt1_15m": float("inf"), "wt2_15m": 0.0, "wt1_15m_prev": 0.0},
        {"wt1_15m": float("nan"), "wt2_15m": 1.0, "wt1_15m_prev": 1.0},
    ]
    keys = set().union(*(row.keys() for row in rows))
    arrays = {key: np.asarray([row.get(key, np.nan if key.endswith("_prev") else 0.0) for row in rows]) for key in keys}
    for is_long in (True, False):
        vector, _ = apply_15m_momentum_vec(arrays, len(rows), is_long, cfg, score=np.full(len(rows), 2.5))
        for index, indicators in enumerate(rows):
            wt_bull = bool(indicators.get("wt_cross_bull_3m", False))
            wt_bear = bool(indicators.get("wt_cross_bear_3m", False))
            env = {"score": 2.5, "reasons": [], "ind": indicators, "i": indicators, "is_long": is_long, "is_exit": False, "config": cfg, "wt1_15m": indicators["wt1_15m"], "wt2_15m": indicators["wt2_15m"], "wt_cross_bull_3m": wt_bull, "wt_cross_bear_3m": wt_bear, "wt2_3m": 0.0, "safe_fetch_float": __import__("utils").safe_fetch_float}
            exec(compile(ast.Module(body=[native], type_ignores=[]), str(reference), "exec"), env)
            scalar, reasons = apply_15m_momentum_scalar(2.5, [], indicators, is_long, cfg, indicators["wt1_15m"], indicators["wt2_15m"], wt_bull, wt_bear)
            assert scalar == env["score"] == vector[index]
            assert reasons == env["reasons"]


def test_ma_distance_score_matches_sha_verified_pre_edit_native_ast_and_numpy():
    import ast
    import hashlib
    from pathlib import Path

    source_dir = Path(__file__).resolve().parents[1]
    refs = sorted((source_dir / "backups").glob("before_asr_ma_distance_wire_ez_positions_quick_*.py"))
    reference = next(path for path in refs if hashlib.sha256(path.read_bytes()).hexdigest() == "71a781c2757a18ecaeba1007b649ef54f5f70bbeea22326c31816c360d0f08a9")
    rate = next(node for node in ast.walk(ast.parse(reference.read_text())) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    start = next(i for i, node in enumerate(rate.body) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_sma500_1h" for t in node.targets))
    end = next(i for i, node in enumerate(rate.body[start:], start) if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "_k3m_cap" for t in node.targets))
    native_nodes = rate.body[start:end]
    rows = [
        {"close": 90.0, "sma_500_1h": 100.0, "ema_20_4h": 100.0},
        {"close": 110.0, "sma_500_1h": 100.0, "ema_20_4h": 100.0},
        {"close": 103.0, "sma_500_1h": 100.0, "ema_20_4h": 100.0},
        {"close": 100.0, "sma_500_1h": 0.0, "ema_20_4h": 0.0},
        {"close": 100.0, "sma_500_1h": float("nan"), "ema_20_4h": float("inf")},
    ]
    arrays = {key: np.asarray([row.get(key, 0.0) for row in rows]) for key in set().union(*(row.keys() for row in rows))}
    from utils import safe_fetch_float
    for is_long in (True, False):
        vector, _ = apply_ma_distance_score_vec(arrays, len(rows), is_long, score=np.full(len(rows), 1.5))
        for index, indicators in enumerate(rows):
            env = {"score": 1.5, "reasons": [], "i": indicators, "is_long": is_long, "current_price": indicators["close"], "safe_fetch_float": safe_fetch_float}
            exec(compile(ast.Module(body=native_nodes, type_ignores=[]), str(reference), "exec"), env)
            scalar, reasons = apply_ma_distance_score_scalar(1.5, [], indicators, indicators["close"], is_long)
            assert scalar == env["score"] == vector[index]
            assert reasons == env["reasons"]


def test_rank_conviction_matches_sha_verified_pre_edit_native_ast_and_numpy():
    import ast
    import hashlib
    from pathlib import Path
    from types import SimpleNamespace

    source_dir = Path(__file__).resolve().parents[1]
    refs = sorted((source_dir / "backups").glob("before_asr_rank_conviction_wire_ez_positions_quick_*.py"))
    reference = next(path for path in refs if hashlib.sha256(path.read_bytes()).hexdigest() == "bda14b42c1f9771b32ed21990e0aff781d8ba595536719cae92dbd3f25a10cd5")
    rate = next(node for node in ast.walk(ast.parse(reference.read_text())) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    native = next(node for node in rate.body if isinstance(node, ast.If) and "RANK_CONVICTION_ENABLED" in ast.unparse(node))
    cfg = SimpleNamespace(RANK_CONVICTION_ENABLED=True, RP_STRONG_THRESHOLD=70.0, RP_WEAK_THRESHOLD=30.0, RP_STRONG_BONUS=15.0, RP_WEAK_PENALTY=-10.0, RP_OPPOSITE_PENALTY=-20.0)
    rows = [
        {"0ranking_points_global": 70.0},
        {"0ranking_points_global": -70.0},
        {"0ranking_points_global": 29.9},
        {"0ranking_points_global": 30.0},
        {"0ranking_points_global": float("nan")},
        {"0ranking_points_global": 0.0},
        {"0ranking_points_global": 0.0},
    ]
    arrays = {key: np.asarray([row.get(key, np.nan) for row in rows]) for key in set().union(*(row.keys() for row in rows))}
    for is_long in (True, False):
        vector, _ = apply_rank_conviction_vec(arrays, len(rows), is_long, cfg, score=np.full(len(rows), 2.0))
        for index, indicators in enumerate(rows):
            env = {"score": 2.0, "reasons": [], "ind": indicators, "metrics": {}, "is_long": is_long, "is_exit": False, "config": cfg, "safe_fetch_float": __import__("utils").safe_fetch_float}
            exec(compile(ast.Module(body=[native], type_ignores=[]), str(reference), "exec"), env)
            scalar, reasons = apply_rank_conviction_scalar(2.0, [], indicators, {}, is_long, cfg)
            assert scalar == env["score"] == vector[index]
            assert reasons == env["reasons"]


def test_rank_conviction_falls_back_when_whole_global_column_is_absent():
    from types import SimpleNamespace

    cfg = SimpleNamespace(RANK_CONVICTION_ENABLED=True, RP_STRONG_THRESHOLD=70.0, RP_WEAK_THRESHOLD=30.0, RP_STRONG_BONUS=15.0, RP_WEAK_PENALTY=-10.0, RP_OPPOSITE_PENALTY=-20.0)
    arrays = {"0ranking_points": np.array([85.0, -85.0, 0.0])}
    vector, masks = apply_rank_conviction_vec(arrays, 3, True, cfg)
    assert vector.tolist() == [15.0, -20.0, -10.0]
    assert masks["RANK_STRONG"].tolist() == [True, False, False]


def test_15m_previous_uses_wt2_only_when_whole_npz_column_is_absent():
    from types import SimpleNamespace

    cfg = SimpleNamespace(WT15M_AGAINST_PENALTY=-5.0)
    arrays = {"wt1_15m": np.array([3.0]), "wt2_15m": np.array([4.0])}
    vector, fire = apply_15m_momentum_vec(arrays, 1, True, cfg)
    scalar, _ = apply_15m_momentum_scalar(0.0, [], {}, True, cfg, 3.0, 4.0, False, False)
    assert fire.tolist() == [True]
    assert vector.tolist() == [scalar] == [-5.0]
    present_nan, present_nan_fire = apply_15m_momentum_vec(
        {"wt1_15m": np.array([3.0]), "wt2_15m": np.array([4.0]), "wt1_15m_prev": np.array([np.nan])},
        1, True, cfg
    )
    assert not present_nan_fire[0]
    assert present_nan[0] == 0.0


def test_channel_k_cross_matches_sha_verified_pre_edit_native_ast_and_numpy():
    import ast
    import hashlib
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    candidates = sorted((root / "backups").glob("before_asr_channel_k_cross_wire_retry_ez_positions_quick_*.py"))
    reference = next(path for path in candidates if hashlib.sha256(path.read_bytes()).hexdigest() == "6b531e499a15d716f25f0feed85b68fb928eeba6f914c02b3b012b4b294af500")
    rate = next(node for node in ast.walk(ast.parse(reference.read_text())) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    original = next(node for node in rate.body if isinstance(node, ast.If) and "JUMP_PULLBACK" in ast.unparse(node) and "DROP_RECOVERY" in ast.unparse(node))
    long_end = next(i for i, node in enumerate(original.body) if isinstance(node, ast.If) and "current_price > ema_50_15m" in ast.unparse(node))
    short_end = next(i for i, node in enumerate(original.orelse) if isinstance(node, ast.If) and "current_price < ema_50_15m" in ast.unparse(node))
    native = ast.fix_missing_locations(ast.If(test=original.test, body=original.body[:long_end], orelse=original.orelse[:short_end]))
    rows = []
    for j in range(8):
        row = {"close": 110.0 + j, "k_15m": 10.0 + j * 10, "k_1h": 10.0 + j * 10, "k_4h": 20.0 + j * 10, "k_D": 25.0 + j * 10}
        for tf, gap in (("1h",20.0),("D",30.0),("4h",15.0)):
            row[f"dc_low_{tf}"] = 100.0
            row[f"dc_high_{tf}"] = 100.0 + gap
            row[f"dc_basis_{tf}"] = 105.0
        for tf in ("3m","15m","1h","4h","D"):
            row[f"dc_basis_crossover_{tf}"] = int((j + len(tf)) % 2 == 0)
            row[f"dc_basis_crossunder_{tf}"] = int((j + len(tf)) % 3 == 0)
        rows.append(row)
    arrays = {key: np.asarray([row.get(key, 0.0) for row in rows]) for key in set().union(*(row.keys() for row in rows))}
    for is_long in (True, False):
        vector, _ = apply_channel_k_cross_score_vec(arrays, len(rows), is_long)
        for j, indicators in enumerate(rows):
            env = {"is_long": is_long, "current_price": indicators["close"], "score": 0.0, "reasons": []}
            for key in ("dc_high_1h","dc_low_1h","dc_high_D","dc_low_D","dc_high_4h","dc_low_4h","dc_basis_1h","dc_basis_D","dc_basis_4h","k_15m","k_1h","k_4h","k_D"):
                env[key] = indicators.get(key, 50.0 if key.startswith("k_") else 0.0)
            for tf in ("3m","15m","1h","4h","D"):
                env[f"dc_basis_crossover_{tf}"] = indicators[f"dc_basis_crossover_{tf}"]
                env[f"dc_basis_crossunder_{tf}"] = indicators[f"dc_basis_crossunder_{tf}"]
            env["dc_w1h"] = ((env["dc_high_1h"]-env["dc_low_1h"])/env["dc_low_1h"])*100 if env["dc_low_1h"]>0 else 0
            env["dc_wD"] = ((env["dc_high_D"]-env["dc_low_D"])/env["dc_low_D"])*100 if env["dc_low_D"]>0 else 0
            env["dc_w4h"] = ((env["dc_high_4h"]-env["dc_low_4h"])/env["dc_low_4h"])*100 if env["dc_low_4h"]>0 else 0
            exec(compile(ast.Module(body=[native], type_ignores=[]), str(reference), "exec"), env)
            scalar, reasons = apply_channel_k_cross_score_scalar(0.0, [], indicators, indicators["close"], is_long)
            assert scalar == env["score"] == vector[j]
            assert reasons == env["reasons"]
