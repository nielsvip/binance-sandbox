import ast
import hashlib
from pathlib import Path
from types import SimpleNamespace

import numpy as np

from utils import safe_fetch_float
from vec_decisions.asr_inline_score import bc155_score_array, hlr_rally_score_array, k_zone_score_array, momentum_fade_score_array, satoshit_score_array


ORACLE_SHA256 = "d45e0765b6bddc221cd2bbad5c14f6895729dab925340c7857d3a02adfabad3b"


def _native_rate():
    root = Path(__file__).resolve().parents[1]
    candidates = sorted((root / "backups").glob("before_asr_inline_score_native_oracle_ez_positions_quick_*.py"))
    reference = next(path for path in candidates if hashlib.sha256(path.read_bytes()).hexdigest() == ORACLE_SHA256)
    source = reference.read_text()
    tree = ast.parse(source)
    rate = next(node for node in ast.walk(tree) if isinstance(node, ast.AsyncFunctionDef) and node.name == "rate" and node.lineno == 3616)
    return reference, rate


def _cfg():
    return SimpleNamespace(
        TR_ADX4H_GATE_ENABLED=True, TR_ADX4H_MAX=20.0, TR_ADX4H_BOYCOTT_SCORE=-40,
        TR_BBWIDTH4H_GATE_ENABLED=True, TR_BBWIDTH4H_MAX=10.0, TR_BBWIDTH4H_BOYCOTT_SCORE=-35,
        TR_CHOP4H_GATE_ENABLED=True, TR_CHOP4H_MIN=50.0, TR_CHOP4H_BONUS=15,
        TR_CHOP4H_TREND_MAX=38.0, TR_CHOP4H_PENALTY=-20,
        TR_MFI4H_LONG_ENABLED=True, TR_MFI4H_LONG_MIN=40.0, TR_MFI4H_LONG_BOYCOTT_SCORE=-25,
        TR_DCWIDTH4H_SHORT_ENABLED=True, TR_DCWIDTH4H_SHORT_MAX=15.0, TR_DCWIDTH4H_SHORT_BOYCOTT_SCORE=-25,
        K_ZONE_ENTRY_ENABLED=True, K_ZONE_ENTRY_BONUS=25, K_ZONE_LONG_THRESHOLD=35, K_ZONE_SHORT_THRESHOLD=65,
        K_ZONE_ENTRY_ENABLED_TRADIER=True, K_ZONE_LONG_THRESHOLD_TRADIER=35, K_ZONE_SHORT_THRESHOLD_TRADIER=65, K_ZONE_ENTRY_BONUS_TRADIER=20,
        MOMENTUM_FADE_ENABLED=True, MOMENTUM_FADE_BODY_ATR_MIN=2.0, MOMENTUM_FADE_VOL_MIN=2.0,
        MOMENTUM_FADE_K_ZONE=True, MOMENTUM_FADE_SCORE_BONUS=35,
        MOMENTUM_FADE_ENABLED_TRADIER=True, MOMENTUM_FADE_BODY_ATR_MIN_TRADIER=2.0,
        MOMENTUM_FADE_VOL_MIN_TRADIER=2.0, MOMENTUM_FADE_K_ZONE_TRADIER=True,
        MOMENTUM_FADE_SCORE_BONUS_TRADIER=5,
    )


def test_bc155_score_matches_extracted_native_ast_defaults_and_boundaries():
    reference, rate = _native_rate()
    node = next(item for item in rate.body if isinstance(item, ast.If) and item.lineno == 5075)
    rng = np.random.default_rng(4315072)
    n = 300
    fields = {
        "adx_4h": rng.choice([None, "bad", 15.0, 20.0, 25.0, 40.0], n),
        "bb_upper_4h": rng.choice([None, 0.0, 110.0, 125.0], n),
        "bb_lower_4h": rng.choice([None, 0.0, 90.0, 100.0], n),
        "choppiness_4h": rng.choice([None, 20.0, 38.0, 50.0, 70.0], n),
        "mfi_4h": rng.choice([None, 0.0, 20.0, 40.0, 80.0], n),
        "dc_high_4h": rng.choice([None, 0.0, 115.0, 130.0], n),
        "dc_low_4h": rng.choice([None, 0.0, 90.0, 100.0], n),
    }
    prices = rng.choice([0.0, 100.0, 110.0], n)
    cfg = _cfg()
    for is_long in (True, False):
        actual, masks = bc155_score_array(fields, prices, is_long, cfg)
        expected = np.zeros(n)
        for i in range(n):
            ind = {key: values[i] for key, values in fields.items()}
            env = {"is_exit": False, "is_long": is_long, "ind": ind, "current_price": prices[i],
                   "mfi_4h": safe_fetch_float(ind.get("mfi_4h"), 50), "config": cfg,
                   "safe_fetch_float": safe_fetch_float, "score": 0.0, "reasons": []}
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(reference), "exec"), env)
            expected[i] = env["score"]
        assert np.array_equal(actual, expected)
        assert set(masks) == {"TR_ADX4H_TREND", "TR_BBW4H_WIDE", "TR_CHOP4H_OK", "TR_CHOP4H_TREND", "TR_MFI4H_LOW_LONG", "TR_DCW4H_WIDE_SHORT"}


def test_k_zone_score_matches_extracted_native_ast_for_both_switches_and_sides():
    reference, rate = _native_rate()
    nodes = [item for item in rate.body if isinstance(item, ast.If) and item.lineno in (5141, 5166)]
    rng = np.random.default_rng(4315137)
    n = 300
    values = {
        "close": np.full(n, 100.0),
        "k_3m": rng.choice([20.0, 35.0, 50.0, 65.0, 80.0], n),
        "k_3m_prev": rng.choice([20.0, 35.0, 50.0, 65.0, 80.0], n),
        "ha_3m": rng.choice(["green", "red", "neutral"], n),
        "ha_3m_prev": rng.choice(["green", "red", "neutral"], n),
        "ha_15m": rng.choice(["green", "red", "neutral"], n),
    }
    cfg = _cfg()
    for is_long in (True, False):
        actual, masks = k_zone_score_array(values, is_long, cfg)
        expected = np.zeros(n)
        for i in range(n):
            row = {key: col[i] for key, col in values.items()}
            env = {"is_exit": False, "is_long": is_long, "k_3m": row["k_3m"], "k_3m_prev": row["k_3m_prev"],
                   "ha_3m": row["ha_3m"], "ha_15m": row["ha_15m"], "i": row, "symbol": "FIXTURE",
                   "config": cfg, "_psym_get": lambda symbol, side, key, default: getattr(cfg, key, default),
                   "_kz_enabled": cfg.K_ZONE_ENTRY_ENABLED, "_kz_bonus": cfg.K_ZONE_ENTRY_BONUS,
                   "_kz_tradier_enabled": cfg.K_ZONE_ENTRY_ENABLED_TRADIER,
                   "score": 0.0, "reasons": []}
            for node in nodes:
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(reference), "exec"), env)
            expected[i] = env["score"]
        assert np.array_equal(actual, expected)
        assert masks["K_ZONE_BASE"].shape == (n,)
        assert masks["K_ZONE_TRADIER"].shape == (n,)


def test_momentum_fade_score_matches_extracted_native_ast_for_both_modes_and_sides():
    reference, rate = _native_rate()
    nodes = [item for item in rate.body if isinstance(item, ast.If) and item.lineno in (5215, 5236)]
    rng = np.random.default_rng(4315212)
    n = 300
    values = {
        "close": np.full(n, 100.0),
        "high_3m": rng.choice([0.0, 100.0, 130.0], n), "low_3m": rng.choice([0.0, 90.0, 100.0], n),
        "atr_3m": rng.choice([0.0, 5.0, 10.0], n), "rel_vol_3m": rng.choice([0.0, 2.0, 3.0], n),
        "rel_vol_15m": rng.choice([0.0, 2.0, 4.0], n), "k_3m": rng.choice([20.0, 40.0, 60.0, 80.0], n),
        "high_15m": rng.choice([0.0, 100.0, 130.0], n), "low_15m": rng.choice([0.0, 90.0, 100.0], n),
        "atr_15m": rng.choice([0.0, 5.0, 10.0], n), "k_15m": rng.choice([20.0, 40.0, 60.0, 80.0], n),
    }
    cfg = _cfg()
    for is_long in (True, False):
        actual, masks = momentum_fade_score_array(values, is_long, cfg)
        expected = np.zeros(n)
        for i in range(n):
            row = {key: col[i] for key, col in values.items()}
            env = {"is_exit": False, "is_long": is_long, "high_3m": row["high_3m"], "low_3m": row["low_3m"],
                   "atr_3m": row["atr_3m"], "rel_vol_3m": row["rel_vol_3m"], "rel_vol_15m": row["rel_vol_15m"],
                   "k_3m": row["k_3m"], "high_15m": row["high_15m"], "low_15m": row["low_15m"],
                   "atr_15m": row["atr_15m"], "k_15m": row["k_15m"], "config": cfg,
                   "_mf_enabled": cfg.MOMENTUM_FADE_ENABLED,
                   "_mf_tradier_enabled": cfg.MOMENTUM_FADE_ENABLED_TRADIER,
                   "score": 0.0, "reasons": []}
            for node in nodes:
                exec(compile(ast.Module(body=[node], type_ignores=[]), str(reference), "exec"), env)
            expected[i] = env["score"]
        assert np.array_equal(actual, expected)
        assert masks["MOMENTUM_FADE"].shape == (n,)
        assert masks["MOMENTUM_FADE_TRADIER"].shape == (n,)


def test_satoshit_score_matches_actual_native_bonus_wrapper_both_sides():
    from ez_satoshit import satoshit_score_bonus

    rng = np.random.default_rng(4315255)
    n = 300
    values = {
        "close": np.full(n, 100.0),
        "rsi_15m": rng.choice([20.0, 50.0, 55.0, 80.0, np.nan], n),
        "stoch_k_15m": rng.choice([20.0, 50.0, 60.0, 80.0, np.nan], n),
        "mfi_15m": rng.choice([20.0, 50.0, 60.0, 80.0, np.nan], n),
        "ha_15m": rng.choice(["green", "red", "neutral", 1, -1, 0, None], n),
        "bb_pct_b_1h": rng.choice([0.2, 0.5, 0.55, 0.8, np.nan], n),
        "mfi_D": rng.choice([20.0, 30.0, 50.0, np.nan], n),
        "rsi_D": rng.choice([20.0, 50.0, 80.0, np.nan], n),
        "relative_volume_1h": rng.choice([0.1, 0.3, 1.0, np.nan], n),
    }
    cfg = SimpleNamespace(SATOSHIT_MIN_VOTES=3, SATOSHIT_SCORE_BONUS=30)
    for is_long in (True, False):
        actual, masks = satoshit_score_array(values, is_long, cfg, enabled=True)
        expected = np.zeros(n)
        for i in range(n):
            row = {key: col[i] for key, col in values.items()}
            expected[i] = satoshit_score_bonus(row, is_long, cfg)[0]
        assert np.array_equal(actual, expected)
        assert np.array_equal(masks["SATOSHIT"], expected > 0)
        assert np.all((masks["SATOSHIT_VOTES"] >= 0) & (masks["SATOSHIT_VOTES"] <= 5))


def test_hlr_rally_matches_extracted_native_ast_score_size_and_bypass():
    reference, rate = _native_rate()
    node = next(item for item in rate.body if isinstance(item, ast.If) and item.lineno == 5295)
    rng = np.random.default_rng(4315288)
    n = 120
    fields = {
        key: rng.choice([0.0, 90.0, 95.0, 100.0, 105.0, 110.0], n)
        for key in ("low_3m", "low_3m_prev", "high_3m", "high_3m_prev",
                    "low_15m", "low_15m_prev", "high_15m", "high_15m_prev",
                    "low_1h", "low_1h_prev", "high_1h", "high_1h_prev",
                    "low_4h", "low_4h_prev", "high_4h", "high_4h_prev",
                    "low_D", "low_D_prev", "high_D", "high_D_prev",
                    "low_W", "low_W_prev", "high_W", "high_W_prev",
                    "sma_200_1m", "sma_200_15m", "sma_200_1h", "sma_200_4h",
                    "sma_200_D", "sma_200_W")
    }
    prices = rng.choice([90.0, 97.0, 100.0, 103.0, 110.0], n)
    cfg = SimpleNamespace(HLR_RALLY_ENABLED=True)
    for is_long in (True, False):
        actual, size, bypass, tf_masks = hlr_rally_score_array(fields, prices, is_long, cfg, score=np.full(n, 100.0))
        for i in range(n):
            row = {key: values[i] for key, values in fields.items()}
            env = {"is_exit": False, "is_long": is_long, "current_price": prices[i], "config": cfg,
                   "i": row, "score": 100.0, "reasons": [], "position_key": "fixture",
                   "_hlr_rally_score": 0.0, "_hlr_rally_tfs": [], "_hlr_size_mult": 1.0, "_hlr_bypass": False,
                   "logger": SimpleNamespace(debug=lambda *args, **kwargs: None),
                   "safe_fetch_float": safe_fetch_float}
            for key in ("low_3m", "low_3m_prev", "high_3m", "high_3m_prev",
                        "low_1h", "low_1h_prev", "high_1h", "high_1h_prev",
                        "low_4h", "low_4h_prev", "high_4h", "high_4h_prev",
                        "low_D", "high_D"):
                env[key] = row[key]
            for key in ("sma_200_15m", "sma_200_1h", "sma_200_4h", "sma_200_D"):
                env[key] = row[key]
            exec(compile(ast.Module(body=[node], type_ignores=[]), str(reference), "exec"), env)
            assert actual[i] == env["score"]
            assert size[i] == env["_hlr_size_mult"]
            assert bypass[i] == env["_hlr_bypass"]
        for tf in ("3m", "15m", "1h", "4h", "D", "W"):
            assert tf_masks[tf].shape == (n,)
