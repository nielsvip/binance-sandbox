"""Pin: ez_rankings hot path reads duplicates from market_data instead of recomputing.

Static pin (ez_rankings is a live loop; executing it here is infeasible):
- process_single_symbol must not run add_stochrsi_zones (scores never use
  stoch_rsi/zone; plot_loop and signals_loop compute their own on own DFs).
- price_vs_sma must prefer indicators_data sma_200_1h, keeping the local
  rolling(200) fallback for symbols missing from the feed.
- breakthrough ATR must prefer indicators_data atr_1h, keeping the local
  calculate_atr fallback.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "ez_rankings.py"


def test_no_stochrsi_in_ranking_hot_path():
    text = SRC.read_text()
    assert "\n                dftf = add_stochrsi_zones(dftf)" not in text
    assert "add_stochrsi_zones(df_p_raw.copy())" in text
    assert "add_stochrsi_zones(df_3m_s.copy())" in text


def test_sma_prefers_feed_with_local_fallback():
    text = SRC.read_text()
    assert '.get("sma_200_1h")' in text
    assert "rolling(200).mean()" in text


def test_atr_prefers_feed_with_local_fallback():
    text = SRC.read_text()
    assert '.get("atr_1h")' in text
    assert "calculate_atr(df_1h, period=14)" in text
