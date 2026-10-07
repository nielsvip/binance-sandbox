import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import live_entry_gates as G
STG = ROOT / "data/live_parity/staged/batch5_live_gates"
LH_IND = {"high_1h": 10, "high_1h_prev": 11, "high_4h": 9, "high_4h_prev": 9.5, "low_1h": 5, "low_1h_prev": 6, "low_4h": 4, "low_4h_prev": 5}


def _g(**kw):
    base = {"LIVE_ENTRY_GATES_ENABLED": True, "LH_HL_FILTER_ENABLED": True, "EMA_9_21_FILTER_ENABLED": False, "ALIGNMENT_GATE_MIN": 0, "TREND_GATES": False, "HTF1_CONF": False, "HTF4_CONF": False}
    base.update(kw)
    return lambda k, d: base.get(k, d)


def test_master_off_is_noop():
    assert G.check_entry_gates(_g(LIVE_ENTRY_GATES_ENABLED=False), LH_IND, True, "OPEN") == (False, "")
    assert G.check_entry_gates(lambda k, d: d, LH_IND, True, "OPEN") == (False, "")


def test_lh_hl_matches_vec_predicate():
    import vec_decisions.check_entry_candidates_stocks__lh_hl_filter as LH
    for is_long in (True, False):
        want, _ = LH.check_lh_hl_filter(G._Cfg(_g()), LH_IND, is_long)
        assert G.check_entry_gates(_g(), LH_IND, is_long, "OPEN")[0] == want
    assert G.check_entry_gates(_g(), LH_IND, True, "OPEN") == (True, "LH_HL_FILTER_BLOCK_LONG")


def test_actions_scope():
    assert G.check_entry_gates(_g(), LH_IND, True, "CLOSE")[0] is False
    assert G.check_entry_gates(_g(LH_HL_FILTER_AUGMENT_GATE_ENABLED=False), LH_IND, True, "AUGMENT")[0] is False
    assert G.check_entry_gates(_g(), LH_IND, True, "AUGMENT")[0] is True


def test_ema_gate_failopen_without_key():
    g = _g(EMA_9_21_FILTER_ENABLED=True, LH_HL_FILTER_ENABLED=False, EMA_9_21_TIMEFRAME="1h")
    assert G.check_entry_gates(g, {}, True, "OPEN")[0] is False
    assert G.check_entry_gates(g, {"ema_9_above_21_1h": 0.0}, True, "OPEN")[0] is True
    assert G.check_entry_gates(g, {"ema_9_above_21_1h": 1.0}, True, "OPEN")[0] is False


def test_effective_min_gain():
    f = lambda **kw: (lambda k, d: kw.get(k, d))
    assert G.effective_min_gain(f(AUGMENT_MIN_GAIN_PCT=3.0, MIN_GAIN_TO_BUY_AGGRESSIVELY=3.0)) == 3.0
    assert G.effective_min_gain(f(AUGMENT_MIN_GAIN_PCT=0.0, MIN_GAIN_TO_BUY_AGGRESSIVELY=4.0)) == 4.0
    assert G.effective_min_gain(f(AUGMENT_MIN_GAIN_PCT=0.0, MIN_GAIN_TO_BUY_AGGRESSIVELY=1.0)) == 2.5  # floor
    assert G.effective_min_gain(f()) == 3.0


def test_staged_files_and_defaults():
    t = Path("tradier_manage.py").read_text()
    assert "_live_entry_gates.check_entry_gates" in t and "LIVE_ENTRY_GATES_ENABLED', False" in t and "effective_min_gain" in t
    c = Path("config_tradier.py").read_text()
    assert "LIVE_ENTRY_GATES_ENABLED: bool = False" in c and "LIVE_ENTRY_GATES_INCLUDE_REENTRY: bool = False" in c
    assert "effective_min_gain" in (STG / "ez_manage.py").read_text()


def test_lg20_htf_label_keys_in_staged_tradier():
    t = Path("tradier_manage.py").read_text()
    assert t.count("_k = f'wt1_{_lbl}' if _lbl.upper()!='D' else 'wt1_D'") == 2 and "wt1_{_lbl.upper()}" not in t
    # key construction against the REAL stocks indicator keys
    import json, os, pandas as pd
    os.environ.setdefault("EZ_LOG_DIR", "/tmp/ez_log_parity")
    import tradier_indicators as TI
    ind = {}
    for tf in ("1h", "4h"):
        d = pd.DataFrame(json.load(open(ROOT / "klines_cache" / f"AAPLUSDT_{tf}.json")))
        for c in ("open", "high", "low", "close", "volume"):
            d[c] = pd.to_numeric(d[c], errors="coerce")
        ind.update(TI.IndicatorCalculator().compute(d.dropna(subset=["close"]), "AAPL", tf, None, None, False))
    for lbl in ("1h", "4h", "d"):
        old = f"wt1_{lbl.upper()}" if lbl.upper() != "D" else "wt1_D"
        new = f"wt1_{lbl}" if lbl.upper() != "D" else "wt1_D"
        if lbl != "d":
            assert old not in ind and new in ind, (lbl, old, new)  # old key missing in the real dict, new key present


def test_d8_research_gate_hook_staged_default_off():
    t = Path("tradier_manage.py").read_text()
    assert "_apply_research_only_live_gates(_leg_acct, _leg_sym, _leg_side, _leg_ind, True)" in t and "STOCKS_FRESH_ENTRY_TREND_GATES_ENABLED', False" in t
    assert "STOCKS_FRESH_ENTRY_TREND_GATES_ENABLED: bool = False" in Path("config_tradier.py").read_text()
