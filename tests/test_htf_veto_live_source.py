"""Live HTF_TREND_VETO_ON_REDUCE must imitate the vec twin (USER 2026-10-09).

Regression: live read wt1_D/wt2_D only via self.tradier_indicators, which is
never wired (None) -> the veto silently passed every reduce/close while vec
(HTF_TREND_VETO_ON_REDUCE_ENABLED default True) blocked. First proven bar:
AAPL 1787927400 vec held / live DAYTRADE-exited.
"""
import re
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from vec_decisions.stocks_live_session_gates import HTF_VETO_BYPASS_TOKENS, VEC_REASON_ALIASES


def _src():
    return (ROOT / "tradier_manage.py").read_text()


def _htfr_block():
    m = re.search(r"_htfr_bypass_substrings = \((.*?)\)", _src(), re.S)
    assert m, "live ON_REDUCE bypass tuple missing"
    return m.group(0)


def test_bypass_tokens_match_vec():
    live = set(re.findall(r'"([A-Z0-9_]+)"', _htfr_block()))
    assert set(HTF_VETO_BYPASS_TOKENS) <= live, set(HTF_VETO_BYPASS_TOKENS) - live


def test_ppl_alias_matches_vec():
    assert "PPL_" in VEC_REASON_ALIASES
    assert '_htfr_reason_up.startswith("PPL_")' in _src()


def test_indicator_fallback_when_unwired():
    s = _src()
    i = s.find("_htfr_bypass = any(")
    assert i > 0
    blk = s[i:i + 1500]
    assert "_htfr_ti = getattr(self, \"tradier_indicators\", None)" in blk
    assert "_htfr_ind = self.get_indicators(symbol)" in blk


def test_zone_gate_removed():
    s = _src()
    assert "BLOCKED_ZONE_" not in s
    assert "is_zone_blocked" not in s


def test_tier_mode_sizes_instead_of_blocks():
    t = _src()
    assert "_tier_on = bool(getattr(config, \"PERF_TIER_SIZING_ENABLED\", False))" in t
    assert "not _tier_on and g is not None and g <= 0" in t
    assert "not _tier_on and gv is not None and gv <= 0" in t
    assert "not _tier_on and g is not None and bh is not None and g <= bh" in t
    e = (ROOT / "ez_manage.py").read_text()
    assert 'not bool(getattr(config, "PERF_TIER_SIZING_ENABLED", False))' in e
    w = (ROOT / "tools/v15_persym_size_tiers.py").read_text()
    assert '"--max-mult", type=float, default=4.0' in w


def test_dc_stops_prior_bar():
    s = _src()
    assert "def _dc_stop_prev_aware(ind, field, use_prior):" in s
    assert s.count("_dc_stop_prev_aware(i, \"dc_low_") == 3
    assert s.count("_dc_stop_prev_aware(i, \"dc_high_") == 3
    assert "_gx_stopf = " in s and "_tx_stopf = " in s and "_dt_stopf = " in s
    assert "_dc_stop_prev_aware(i, _f, _gx_prior) if _f in _gx_stopf" in s


def test_parser_carries_dc_prev():
    s = _src()
    assert 'd[f"dc_low_{tf}_prev"]' in s
    assert 'd[f"dc_high_{tf}_prev"]' in s


def test_uncond_bypasses_veto_like_vec():
    assert '_htfr_reason_up.startswith("UNCOND_")' in _src()


def test_vec_cooldown_default_zero_ruling():
    v = (ROOT / "v12_quick_engine.py").read_text()
    assert "COOLDOWN_BARS_TRADIER: int = 0" in v
    assert '"COOLDOWN_BARS_TRADIER": 0' in v


def test_cooldown_zero_all_layers():
    import json
    ct = (ROOT / "config_tradier.py").read_text()
    assert "COOLDOWN_BARS_TRADIER: int = (\n        0" in ct
    d = json.load(open(ROOT / "data/per_sym_settings.json"))
    assert d["STOCKS_LONG"]["COOLDOWN_BARS_TRADIER"] == 0
    assert d["STOCKS_SHORT"]["COOLDOWN_BARS_TRADIER"] == 0
