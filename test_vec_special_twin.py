"""test_vec_special_twin.py — pytest for vec_decisions/twin_vec_special.py.

Twin-vs-live mirror tests (synthetic NPZ dicts) + disposition evidence tests
(COOLDOWN_BARS unit math, no-consumer proofs for REMOVE-ROW/NEEDS-DECISION).
Run: python -m pytest test_vec_special_twin.py -q
"""
import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from vec_decisions import twin_vec_special as T

ROOT = Path(__file__).resolve().parent


class Cfg:
    def __init__(self, **kw):
        self.__dict__.update(kw)


def test_dc_breakout_inert_when_parent_off():
    npz = {"close": np.array([105.0]), "dc_high_1h": np.array([100.0]), "dc_low_1h": np.array([90.0]), "adx_1h": np.array([30.0])}
    assert T.get("DC_BREAKOUT_TF_EXPANDED", npz, 1, True, Cfg(DC_BREAKOUT_ENTRY_ENABLED=False)) is None


def test_dc_breakout_long_short_and_tf():
    npz = {"close": np.array([105.0, 85.0]), "dc_high_1h": np.array([100.0, 100.0]), "dc_low_1h": np.array([90.0, 90.0]), "adx_1h": np.array([30.0, 30.0]),
           "dc_high_4h": np.array([200.0, 200.0]), "dc_low_4h": np.array([10.0, 10.0]), "adx_4h": np.array([30.0, 30.0])}
    cfg = Cfg(DC_BREAKOUT_ENTRY_ENABLED=True, DC_BREAKOUT_TF_EXPANDED="1h")
    assert T.get("DC_BREAKOUT_TF_EXPANDED", npz, 2, True, cfg).tolist() == [True, False]
    assert T.get("DC_BREAKOUT_TF_EXPANDED", npz, 2, False, cfg).tolist() == [False, True]
    cfg4 = Cfg(DC_BREAKOUT_ENTRY_ENABLED=True, DC_BREAKOUT_TF_EXPANDED="4h")
    assert T.get("DC_BREAKOUT_TF_EXPANDED", npz, 2, True, cfg4).tolist() == [False, False]
    assert T.dc_breakout_fires(105.0, 100.0, 90.0, 30.0, True) is True
    assert T.dc_breakout_fires(105.0, 100.0, 90.0, 25.0, True) is False
    assert T.dc_breakout_fires(105.0, 0.0, 90.0, 30.0, True) is False


def test_pyramid_price_ok_scalar_and_vec():
    assert T.pyramid_price_ok(100.0, 100.0, True, 0.02) is True
    assert T.pyramid_price_ok(102.0, 100.0, True, 0.02) is True
    assert T.pyramid_price_ok(102.01, 100.0, True, 0.02) is False
    assert T.pyramid_price_ok(98.0, 100.0, False, 0.02) is True
    assert T.pyramid_price_ok(97.99, 100.0, False, 0.02) is False
    assert T.pyramid_price_ok(100.0, 0.0, True, 0.02) is False
    assert T.pyramid_price_ok(0.0, 100.0, True, 0.02) is False
    m = T.delta_pyramid_price_mask([100.0, 103.0], [100.0, 100.0], True, 0.02)
    assert m.tolist() == [True, False]
    assert T.get("DELTA_PYRAMID_PRICE_TOL", {}, 2, True, Cfg(DELTA_ENGINE_ENABLED=False), cur_px=[1], last_px=[1]) is None
    assert T.get("DELTA_PYRAMID_PRICE_TOL", {}, 2, True, Cfg(DELTA_ENGINE_ENABLED=True, DELTA_PYRAMID_PRICE_TOL=0.02), cur_px=[100.0, 103.0], last_px=[100.0, 100.0]).tolist() == [True, False]


def test_brake_scalar_order_and_exempt():
    assert T.emergency_brake_blocked(0, 0, 0, 0, False) == (False, "OK")
    assert T.emergency_brake_blocked(10, 0, 0, 0, False) == (True, "EMERGENCY_BRAKE_10_PER_MIN_CHURN")
    assert T.emergency_brake_blocked(9, 501, 0, 0, False) == (True, "EMERGENCY_BRAKE_MAX_ENTRIES")
    assert T.emergency_brake_blocked(0, 500, 1001, 0, False) == (True, "EMERGENCY_BRAKE_MAX_TRADES")
    assert T.emergency_brake_blocked(0, 0, 0, 51, False) == (True, "EMERGENCY_BRAKE_SYMBOL_CHURN")
    assert T.emergency_brake_blocked(999, 999, 9999, 999, True) == (False, "OK")
    assert T.emergency_brake_blocked(10, 0, 0, 0, False, include_min_churn=False) == (False, "OK")
    m = T.emergency_brake_mask([0, 10], [0, 0], [0, 0], [0, 0])
    assert m.tolist() == [False, True]
    assert T.get("LIVE_VEC_EMERGENCY_BRAKE_ENABLED", {}, 1, True, Cfg(LIVE_VEC_EMERGENCY_BRAKE_ENABLED=False), min_total=[0], entries=[0], total=[0], sym_count=[0]) is None


def test_brake_adapter_reads_decisions_jsonl(tmp_path):
    now = datetime.now(timezone.utc)
    d = tmp_path / "dec"
    d.mkdir()
    acct = "tst"
    with open(d / f"decisions_{acct}_{now.strftime('%Y%m%d')}.jsonl", "w") as fh:
        for k in range(11):
            fh.write(json.dumps({"timestamp": (now - timedelta(seconds=k * 5)).isoformat(), "action": "OPEN", "position_key": f"{acct}:X_LONG"}) + "\n")
    cfg = Cfg(EMERGENCY_BRAKE_MAX_TRADES_PER_MIN=10, BASE_PATH=str(tmp_path))
    blocked, reason = T.evaluate_emergency_brake_core(acct, now, f"{acct}:X_LONG", "OPEN", config=cfg, decisions_dir_path=str(d))
    assert (blocked, reason) == (True, "EMERGENCY_BRAKE_10_PER_MIN_CHURN")
    blocked2, _ = T.evaluate_emergency_brake_core(acct, now, f"{acct}:X_LONG", "OPEN", is_profitable_close=True, config=cfg, decisions_dir_path=str(d))
    assert blocked2 is False


class TradierConfig:
    MODE = "crypto"
    EMERGENCY_BRAKE_MAX_TRADES_PER_MIN = 10


def test_brake_adapter_stocks_has_no_min_tier(tmp_path):
    now = datetime.now(timezone.utc)
    d = tmp_path / "dec"
    d.mkdir()
    acct = "trb"
    with open(d / f"decisions_{acct}_{now.strftime('%Y%m%d')}.jsonl", "w") as fh:
        for k in range(11):
            fh.write(json.dumps({"timestamp": (now - timedelta(seconds=k * 5)).isoformat(), "action": "OPEN", "position_key": f"{acct}:X_LONG"}) + "\n")
    cfg = TradierConfig()
    cfg.BASE_PATH = str(tmp_path)
    blocked, reason = T.evaluate_emergency_brake_core(acct, now, f"{acct}:X_LONG", "OPEN", config=cfg, decisions_dir_path=str(d))
    assert (blocked, reason) == (False, "OK")


def _r3_npz():
    return {"close": np.array([99.0, 101.0]), "dc_basis_D": np.array([100.0, 100.0]), "wt1_D": np.array([-5.0, 5.0]), "wt2_D": np.array([0.0, 0.0]),
            "wt1_W": np.array([-3.0, 3.0]), "wt2_W": np.array([0.0, 0.0]), "ema_20_4h": np.array([100.0, 100.0]), "atr_4h": np.array([2.0, 2.0]),
            "wt1_4h": np.array([0.0, 0.0]), "wt2_4h": np.array([0.0, 0.0])}


def test_r3_crypto_or_vs_stocks_require_wt():
    npz = _r3_npz()
    crypto = Cfg(R3_HTF_FLIP_EXIT_ENABLED=True, MODE="crypto")
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", npz, 2, True, crypto).tolist() == [True, False]
    stocks = Cfg(R3_HTF_FLIP_EXIT_ENABLED=True, MODE="tradier")
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", npz, 2, True, stocks).tolist() == [True, False]
    dc_only = dict(npz, wt1_D=np.array([5.0, 5.0]), wt1_W=np.array([3.0, 3.0]))
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", dc_only, 2, True, crypto).tolist() == [True, False]
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", dc_only, 2, True, stocks).tolist() == [False, False]
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", npz, 2, True, Cfg(R3_HTF_FLIP_EXIT_ENABLED=False)) is None
    assert T.r3_htf_flip_fires(99.0, 100.0, -5, 0, -3, 0, 100, 2, 0, 0, True) is True
    assert T.r3_htf_flip_fires(99.0, 100.0, -5, 0, -3, 0, 100, 2, 0, 0, True, age_min=5.0, newborn_min=15.0) is False
    assert T.r3_htf_flip_fires(99.0, 100.0, -5, 0, -3, 0, 100, 2, 0, 0, True, r1_stop=90.0) is False
    t4 = Cfg(R3_HTF_FLIP_EXIT_ENABLED=True, MODE="crypto", R3_HTF_FLIP_4H_TIER_ENABLED=True)
    npz4 = dict(npz, close=np.array([97.0, 101.0]), dc_basis_D=np.array([0.0, 0.0]), wt1_D=np.array([5.0, 5.0]), wt1_W=np.array([3.0, 3.0]), wt1_4h=np.array([-1.0, 1.0]), wt2_4h=np.array([0.0, 0.0]))
    assert T.get("R3_HTF_FLIP_EXIT_ENABLED", npz4, 2, True, t4).tolist() == [True, False]


def test_churn_with_age_size():
    npz = {"wt1_1h": np.array([0.0]), "wt2_1h": np.array([0.0]), "wt1_15m": np.array([2.0]), "wt2_15m": np.array([1.0]), "wt1_4h": np.array([0.0]), "wt2_4h": np.array([0.0])}
    cfg = Cfg(HTF_WT_CHURN_REENTRY_ENABLED=True, HTF_WT_CHURN_REENTRY_MAX_AGE_MIN=120.0, START_POSITION_SIZE=28.0)
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, True, cfg).tolist() == [True]
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, False, cfg).tolist() == [False]
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, True, cfg, age_min_arr=[121.0]).tolist() == [False]
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, True, cfg, notional_arr=[84.0]).tolist() == [False]
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, True, cfg, age_min_arr=[120.0], notional_arr=[83.9]).tolist() == [True]
    assert T.get("HTF_WT_CHURN_REENTRY_ENABLED", npz, 1, True, Cfg(HTF_WT_CHURN_REENTRY_ENABLED=False)) is None
    assert T.htf_wt_churn_fires(0, 0, 2, 1, 0, 0, True, 60.0, 120.0, 0.0, 28.0) is True


def test_rsi_t55_gate_and_mfi():
    npz = {"rsi_D": np.array([30.0, 50.0]), "mfi_4h": np.array([35.0, 60.0])}
    cfg = Cfg(RSI_ENTRY_PERIOD_TRADIER=10, RSI_ENTRY_LONG_TRADIER=40.0, RSI_ENTRY_SHORT_TRADIER=58.0, ENTRY_PRIMARY_TF="4h")
    assert T.get("RSI_ENTRY_LONG_TRADIER", npz, 2, True, cfg).tolist() == [True, False]
    assert T.get("RSI_ENTRY_SHORT_TRADIER", npz, 2, False, cfg).tolist() == [False, False]
    npz_hi = {"rsi_D": np.array([30.0, 70.0])}
    assert T.get("RSI_ENTRY_SHORT_TRADIER", npz_hi, 2, False, cfg).tolist() == [False, True]
    assert T.get("RSI_ENTRY_LONG_TRADIER", {}, 2, True, cfg).tolist() == [True, True]
    npz0 = {"rsi_D": np.array([0.0])}
    assert T.get("RSI_ENTRY_LONG_TRADIER", npz0, 1, True, cfg).tolist() == [False]
    npz10 = {"rsi_10_D": np.array([30.0]), "rsi_D": np.array([90.0])}
    assert T.get("RSI_ENTRY_LONG_TRADIER", npz10, 1, True, cfg).tolist() == [True]
    assert T.rsi_mfi_score_mask(npz, 2, True, cfg).tolist() == [True, False]
    assert T.rsi_mfi_score_mask(npz, 2, False, cfg).tolist() == [False, True]


def test_get_unknown_raises_and_supported_complete():
    with pytest.raises(KeyError):
        T.get("NOPE", {}, 1, True, Cfg())
    assert set(T.SUPPORTED) == {"DC_BREAKOUT_TF_EXPANDED", "DELTA_PYRAMID_PRICE_TOL", "LIVE_VEC_EMERGENCY_BRAKE_ENABLED", "R3_HTF_FLIP_EXIT_ENABLED", "HTF_WT_CHURN_REENTRY_ENABLED", "RSI_ENTRY_LONG_TRADIER", "RSI_ENTRY_SHORT_TRADIER"}


def _num_default(path, name):
    m = re.search(rf"^\s*{name}\s*:\s*\w+\s*=\s*([0-9.]+)", (ROOT / path).read_text(), re.M)
    assert m, f"{name} default not found in {path}"
    return float(m.group(1))


def test_cooldown_unit_math_vec_side_verification():
    cd_bars = _num_default("v12_quick_engine.py", "COOLDOWN_BARS")
    cd_tr = _num_default("v12_quick_engine.py", "COOLDOWN_BARS_TRADIER")
    live_s = _num_default("config.py", "REENTRY_COOLDOWN_S")
    assert (cd_bars, cd_tr, live_s) == (3.0, 8.0, 60.0)
    assert cd_bars * 15 * 60 / live_s == 45.0
    assert cd_tr * 15 == 120.0
    cd, blocks = int(cd_bars), 0
    for _ in range(10):
        if cd > 0:
            cd -= 1
            blocks += 1
    assert blocks == 3


def _decision_consumers(path, name):
    return [ln for ln in (ROOT / path).read_text().splitlines() if name in ln and re.search(r"^\s*if\b.*%s|%s.*[><=!]=" % re.escape(name), ln) and "getattr" not in ln and "==" not in ln.split(name)[0][-40:]]


def test_stdev_breakout_fail_has_no_decision_consumer():
    for path in ("ez_manage.py", "tradier_manage.py", "v12_quick_engine.py"):
        lines = [ln for ln in (ROOT / path).read_text().splitlines() if "EXIT_STDEV_BREAKOUT_FAIL_ENABLED" in ln]
        assert lines, f"expected at least stub refs in {path}"
        liveish = [ln for ln in lines if re.match(r"\s*if\b", ln) and "_ = " not in ln and "import" not in ln]
        assert liveish == [], f"{path}: unexpected decision consumer(s): {liveish}"


def test_wt_dc_direct_tf_params_unread_and_simple_gt0_vec_only():
    decl = 0
    for path in ("config.py", "config_tradier.py", "v12_quick_engine.py", "ez_manage.py", "tradier_manage.py", "wt_dc_contract.py"):
        for ln in (ROOT / path).read_text().splitlines():
            if "WT_DC_DIRECT_TF_ENTRY" in ln or "WT_DC_DIRECT_DC_TF" in ln:
                decl += 1
                assert re.search(r":\s*str\s*=", ln), f"non-declaration read in {path}: {ln.strip()}"
    assert decl == 6
    for path in ("ez_manage.py", "tradier_manage.py"):
        hits = [ln.strip() for ln in (ROOT / path).read_text().splitlines() if "SIMPLE_PRICE_GT0_ENABLED" in ln]
        assert hits, f"expected stub refs in {path}"
        for h in hits:
            assert h.startswith(("if bool(getattr", "if bool(_cfg_auto", "#")), f"live consumer in {path}: {h}"
    assert "SIMPLE_PRICE_GT0_ENABLED" in (ROOT / "v12_quick_engine.py").read_text()
    ez = (ROOT / "ez_manage.py").read_text()
    i = ez.index('if bool(getattr(config, "SIMPLE_PRICE_GT0_ENABLED", False)):')
    assert "_ = _simple_price" in ez[i:i + 300]
    tm = (ROOT / "tradier_manage.py").read_text()
    j = tm.index("if bool(_cfg_auto('SIMPLE_PRICE_GT0_ENABLED', False)):")
    assert "WAVE3" in tm[j - 300:j] and "Default False = unchanged" in tm[j - 300:j]
