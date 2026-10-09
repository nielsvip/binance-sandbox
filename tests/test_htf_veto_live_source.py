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
