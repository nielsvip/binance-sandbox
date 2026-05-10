"""GOLDEN_RULE v4 — COUNTER-TREND entry on proven winners/losers.

USER MANDATE 2026-05-10 (after v2/v3 failed at ~0.27 baseline):
  - Universe is directional (symbols_trb_long = proven winners, _short = proven losers)
  - On a proven winner: BUY THE DIP — enter at LOCAL BOTTOMS, exit at LOCAL TOPS
  - On a proven loser:  SHORT THE RALLY — enter at LOCAL TOPS, exit at LOCAL BOTTOMS
  - Target: baseline pool_sharpe ≥ 0.7 over 100+ syms × 2yr → per-sym tuning then 4×
  - 17 indicators score "at bottom" (LONG) or "at top" (SHORT) per TF

Per-TF "AT-BOTTOM-FOR-LONG" / "AT-TOP-FOR-SHORT" indicators (14 per TF, 4 global):

  Per-TF:
    1.  DC position (close near dc_low for LONG / near dc_high for SHORT)
    2.  BB position (close below bb_basis for LONG / above for SHORT)
    3.  RSI extreme (< 35 for LONG / > 65 for SHORT)
    4.  MFI extreme (< 30 for LONG / > 70 for SHORT)
    5.  Stoch oversold/overbought (k < 25 for LONG / > 75 for SHORT)
    6.  Stoch turning (k > k_prev for LONG / k < k_prev for SHORT)
    7.  WT bullish/bearish CROSS (wt1 just crossed wt2 in our direction)
    8.  WT extreme (wt1 < -50 for LONG / > 50 for SHORT)
    9.  MACD just crossed (crossover for LONG / crossunder for SHORT)
    10. HA color flip (green for LONG / red for SHORT)
    11. ATR rising (volatility expansion — capitulation/distribution)
    12. Relative volume surge (≥ 1.5)
    13. ADX moderate (15-35 — trending but not exhausted)
    14. VWAP position (D only — close vs vwap_D appropriate side)

  Global (4):
    15. clenow_score sign-aligned (LONG > 0 / SHORT < 0)
    16. sepa_score sign-aligned
    17. market_sentiment_score sign-aligned

LTF (5m, 15m): how many indicators score
HTF (1h, 4h, D): how many indicators score
Aggregate: ltf_count + htf_count + global_count → fire if score is high enough.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, List, Set
import json
from pathlib import Path


_TRB_LONG: Optional[Set[str]] = None
_TRB_SHORT: Optional[Set[str]] = None


def load_trb_universe(base_path: Optional[str] = None) -> Tuple[Set[str], Set[str]]:
    global _TRB_LONG, _TRB_SHORT
    if _TRB_LONG is not None and _TRB_SHORT is not None:
        return _TRB_LONG, _TRB_SHORT
    base = Path(base_path) if base_path else Path(__file__).resolve().parent
    longs: Set[str] = set(); shorts: Set[str] = set()
    try:
        with open(base / 'symbols_trb_long.json') as f:
            d = json.load(f); longs = set(d) if isinstance(d, list) else set(d.keys())
    except Exception: pass
    try:
        with open(base / 'symbols_trb_short.json') as f:
            d = json.load(f); shorts = set(d) if isinstance(d, list) else set(d.keys())
    except Exception: pass
    _TRB_LONG = longs; _TRB_SHORT = shorts
    return longs, shorts


def _f(d: dict, k: str, default: float = 0.0) -> float:
    if d is None: return default
    v = d.get(k, default)
    if v is None: return default
    try: return float(v)
    except Exception:
        try: return float(str(v).strip())
        except Exception: return default


def _ha_color(d: dict, tf: str) -> str:
    v = d.get(f'ha_{tf}', '') if d else ''
    if isinstance(v, bytes):
        try: v = v.decode()
        except Exception: v = str(v)
    return str(v).strip().lower()


# ───────────── 2026-05-10 user mandate: GOLDEN_RULE pattern + bounce-on-3-of-5 ─


def _bounce_at_tf(ind: dict, tf: str, is_long: bool) -> Tuple[bool, str]:
    """Return (bounced, what) — TRUE if THIS TF shows a fresh turn in our direction.
    A bounce is at least one of:
      - Stoch crossover this bar (k > d AND k_prev <= d_prev for LONG; mirror SHORT)
      - MACD just crossed (macd_crossover>0 LONG, macd_crossunder>0 SHORT)
      - WT1 just crossed WT2 in direction (requires wt1_prev/wt2_prev)
    """
    sk = _f(ind, f'stoch_k_{tf}', float('nan'))
    sd = _f(ind, f'stoch_d_{tf}', float('nan'))
    sk_prev = _f(ind, f'stoch_k_{tf}_prev', float('nan'))
    sd_prev = _f(ind, f'stoch_d_{tf}_prev', float('nan'))
    if sk == sk and sd == sd and sk_prev == sk_prev and sd_prev == sd_prev:
        if is_long and sk > sd and sk_prev <= sd_prev:
            return True, f'STOCH_XO_{tf}'
        if (not is_long) and sk < sd and sk_prev >= sd_prev:
            return True, f'STOCH_XU_{tf}'
    mxo = _f(ind, f'macd_crossover_{tf}', 0)
    mxu = _f(ind, f'macd_crossunder_{tf}', 0)
    if is_long and mxo > 0:
        return True, f'MACD_XO_{tf}'
    if (not is_long) and mxu > 0:
        return True, f'MACD_XU_{tf}'
    wt1 = _f(ind, f'wt1_{tf}', float('nan'))
    wt2 = _f(ind, f'wt2_{tf}', float('nan'))
    wt1_prev = _f(ind, f'wt1_{tf}_prev', float('nan'))
    wt2_prev = _f(ind, f'wt2_{tf}_prev', float('nan'))
    if wt1 == wt1 and wt2 == wt2 and wt1_prev == wt1_prev and wt2_prev == wt2_prev:
        if is_long and wt1 > wt2 and wt1_prev <= wt2_prev:
            return True, f'WT_XO_{tf}'
        if (not is_long) and wt1 < wt2 and wt1_prev >= wt2_prev:
            return True, f'WT_XU_{tf}'
    return False, ''


def _bounce_count(ind: dict, is_long: bool, mode: str = 'tradier') -> Tuple[int, list]:
    """Return (count, list_of_hits) — count of 5 TFs showing fresh bounce."""
    base_tf = '5m' if mode == 'tradier' else '3m'
    tfs = [base_tf, '15m', '1h', '4h', 'D']
    count = 0; hits = []
    for tf in tfs:
        ok, h = _bounce_at_tf(ind, tf, is_long)
        if ok:
            count += 1
            hits.append(h)
    return count, hits


# ───────────── per-TF "at-bottom-for-LONG / at-top-for-SHORT" scoring ─────────


def _score_tf_counter(ind: dict, tf: str, is_long: bool, current_price: float,
                       state: Optional[dict] = None) -> Tuple[int, int, list]:
    """Score (raw, max, hits) for one TF — counter-trend entry detection.
    is_long=True: detect 'at local bottom' for entering proven winner
    is_long=False: detect 'at local top' for shorting proven loser"""
    raw = 0; maxp = 0; hits = []

    def add(cond: bool, label: str):
        nonlocal raw, maxp
        maxp += 1
        if cond:
            raw += 1
            hits.append(f'{label}_{tf}')

    # 1. DC position — close near dc_low (LONG) or dc_high (SHORT)
    dc_h = _f(ind, f'dc_high_{tf}'); dc_l = _f(ind, f'dc_low_{tf}')
    if dc_h > dc_l > 0 and current_price > 0:
        # Position 0..1 in channel (0 = at low, 1 = at high)
        pos = (current_price - dc_l) / max(dc_h - dc_l, 1e-9)
        add((is_long and pos < 0.30) or ((not is_long) and pos > 0.70), 'DC')

    # 2. BB position — below basis (LONG) or above basis (SHORT)
    bb_u = _f(ind, f'bb_upper_{tf}'); bb_l = _f(ind, f'bb_lower_{tf}')
    bb_b = _f(ind, f'bb_basis_{tf}')
    if bb_b == 0 and bb_u > 0 and bb_l > 0:
        bb_b = (bb_u + bb_l) / 2
    if bb_b > 0 and bb_u > bb_l > 0 and current_price > 0:
        pos_bb = (current_price - bb_l) / max(bb_u - bb_l, 1e-9)
        add((is_long and pos_bb < 0.40) or ((not is_long) and pos_bb > 0.60), 'BB')

    # 3. RSI extreme — < 35 for LONG, > 65 for SHORT
    rsi = _f(ind, f'rsi_{tf}', float('nan'))
    if rsi == rsi and rsi > 0:
        add((is_long and rsi < 35) or ((not is_long) and rsi > 65), 'RSI')

    # 4. MFI extreme
    mfi = _f(ind, f'mfi_{tf}', float('nan'))
    if mfi == mfi and mfi > 0:
        add((is_long and mfi < 30) or ((not is_long) and mfi > 70), 'MFI')

    # 5. Stoch extreme zone — k oversold (LONG) / overbought (SHORT)
    sk = _f(ind, f'stoch_k_{tf}', float('nan'))
    sd = _f(ind, f'stoch_d_{tf}', float('nan'))
    if sk == sk and sk > 0:
        add((is_long and sk < 25) or ((not is_long) and sk > 75), 'STOCH_EXTREME')

    # 6. Stoch turning back — k_prev field if available
    sk_prev_key = f'stoch_k_{tf}_prev' if f'stoch_k_{tf}_prev' in (ind or {}) else f'k_{tf}_prev'
    sk_prev = _f(ind, sk_prev_key, sk)
    if sk == sk and sk_prev == sk_prev:
        add((is_long and sk > sk_prev) or ((not is_long) and sk < sk_prev), 'STOCH_TURN')

    # 7. WT cross — wt1 in our direction vs wt2 (recent cross)
    wt1 = _f(ind, f'wt1_{tf}', float('nan'))
    wt2 = _f(ind, f'wt2_{tf}', float('nan'))
    if wt1 == wt1 and wt2 == wt2:
        add((is_long and wt1 > wt2) or ((not is_long) and wt1 < wt2), 'WT_CROSS')

    # 8. WT extreme zone — wt1 deeply oversold (LONG) / overbought (SHORT)
    if wt1 == wt1:
        add((is_long and wt1 < -40) or ((not is_long) and wt1 > 40), 'WT_EXTREME')

    # 9. MACD just crossed
    mxo = _f(ind, f'macd_crossover_{tf}', 0)
    mxu = _f(ind, f'macd_crossunder_{tf}', 0)
    if mxo or mxu:
        add((is_long and mxo > 0) or ((not is_long) and mxu > 0), 'MACD')

    # 10. HA color
    ha = _ha_color(ind, tf)
    if ha in ('green', 'red', 'neutral'):
        add((is_long and ha == 'green') or ((not is_long) and ha == 'red'), 'HA')

    # 11. ATR > 0 (volatility present — both sides)
    atr = _f(ind, f'atr_{tf}', float('nan'))
    if atr == atr:
        add(atr > 0, 'ATR')

    # 12. Volume surge ≥1.5
    rv = _f(ind, f'relative_volume_{tf}', float('nan'))
    if rv == rv and rv > 0:
        add(rv >= 1.5, 'RVOL')

    # 13. ADX moderate (15..35) — trending but not exhausted
    adx = _f(ind, f'adx_{tf}', float('nan'))
    if adx == adx and adx > 0:
        add(15 <= adx <= 35, 'ADX_MODERATE')

    # 14. VWAP (D only)
    if tf == 'D':
        vwap = _f(ind, 'vwap_D', float('nan'))
        if vwap == vwap and vwap > 0 and current_price > 0:
            add((is_long and current_price < vwap * 1.02) or ((not is_long) and current_price > vwap * 0.98), 'VWAP')

    return raw, maxp, hits


# ───────────── main evaluator ─────────────────────────────────────────────────


@dataclass
class GR4Signal:
    fire: bool
    is_long: bool
    mult: float
    composite_score: float
    ltf_score: int           # raw indicator count across LTFs
    htf_score: int           # raw indicator count across HTFs
    global_score: int        # 0..4 — sign-aligned global rankers
    pattern_at_extreme: bool # True if at local bottom (LONG) / local top (SHORT)
    reason: str


def evaluate_golden_rule_v4(
    symbol: str,
    indicators: dict,
    current_price: float,
    is_long: bool,
    mode: str = 'tradier',
    config=None,
    state: Optional[dict] = None,
) -> GR4Signal:
    """v4 evaluator — counter-trend entry on directional universe.

    Tunables (via config):
      GR4_LTF_MIN_SCORE:  min indicators agreeing across LTFs (sum across 5m+15m). Default 12.
      GR4_HTF_MIN_SCORE:  min indicators agreeing across HTFs (sum across 1h+4h+D). Default 18.
      GR4_GLOBAL_MIN:     min global rankers sign-aligned. Default 1.
      GR4_REQUIRE_DC_EXTREME: require DC position < 0.30 (LONG) / > 0.70 (SHORT) on at least one HTF. Default True.
    """
    base_tf = '5m' if mode == 'tradier' else '3m'
    longs, shorts = load_trb_universe()

    if mode == 'tradier':
        sym_norm = symbol.upper().replace('USDT', '').replace('USDC', '')
        in_universe = (is_long and sym_norm in longs) or ((not is_long) and sym_norm in shorts)
        if not in_universe:
            return GR4Signal(False, is_long, 0.0, 0.0, 0, 0, 0, False,
                             f'GR4_NOT_IN_UNIVERSE_{symbol}')

    # 2026-05-10 USER MANDATE: GOLDEN_RULE pattern gate (recent peak/trough + pullback)
    # The harness threads this in via state['recent_pullback_long'/'recent_rally_short'].
    if state is not None:
        if is_long and not state.get('recent_pullback_long', False):
            return GR4Signal(False, is_long, 0.0, 0.0, 0, 0, 0, False,
                             f'GR4_NO_PULLBACK_{symbol}')
        if (not is_long) and not state.get('recent_rally_short', False):
            return GR4Signal(False, is_long, 0.0, 0.0, 0, 0, 0, False,
                             f'GR4_NO_RALLY_{symbol}')

    # 2026-05-10 USER MANDATE: bounce confirmed on ≥3 of 5 TFs.
    bounce_n, bounce_hits = _bounce_count(indicators, is_long, mode=mode)
    bounce_min = 3 if config is None else int(getattr(config, 'GR4_BOUNCE_MIN_TFS', 3))
    if bounce_n < bounce_min:
        return GR4Signal(False, is_long, 0.0, 0.0, 0, 0, 0, False,
                         f'GR4_BOUNCE_INSUFFICIENT_{bounce_n}/5_lt_{bounce_min}')

    ltfs = [base_tf, '15m']
    htfs = ['1h', '4h', 'D']
    ltf_score = 0; htf_score = 0
    ltf_max = 0; htf_max = 0
    composite_num = 0.0; composite_den = 0.0
    weights = {base_tf: 1.0, '15m': 1.5, '1h': 2.0, '4h': 3.0, 'D': 4.0}
    hits_all: List[str] = []
    at_extreme_htf = False  # any HTF showing DC extreme

    for tf in ltfs + htfs:
        raw, maxp, hits = _score_tf_counter(indicators, tf, is_long, current_price, state)
        if maxp == 0: continue
        if tf in ltfs:
            ltf_score += raw; ltf_max += maxp
        else:
            htf_score += raw; htf_max += maxp
            # Flag DC extreme on any HTF
            for h in hits:
                if h.startswith('DC_'):
                    at_extreme_htf = True; break
        composite_num += (raw / maxp) * weights[tf]
        composite_den += weights[tf]
        hits_all.extend(hits[:2])

    composite = (composite_num / composite_den) if composite_den > 0 else 0.0

    # Global rankers
    clenow = _f(indicators, 'clenow_score', 0.0)
    sepa = _f(indicators, 'sepa_score', 0.0)
    sentiment = _f(indicators, 'market_sentiment_score', 0.0)
    wt_score_d = _f(indicators, 'wt_score_D', 0.0)
    def _sa(v):
        if v == 0: return 0
        return 1 if (is_long and v > 0) or ((not is_long) and v < 0) else 0
    global_score = _sa(clenow) + _sa(sepa) + _sa(sentiment) + _sa(wt_score_d)

    # Tunables
    ltf_min = 12 if config is None else int(getattr(config, 'GR4_LTF_MIN_SCORE', 12))
    htf_min = 18 if config is None else int(getattr(config, 'GR4_HTF_MIN_SCORE', 18))
    global_min = 1 if config is None else int(getattr(config, 'GR4_GLOBAL_MIN', 1))
    require_dc_extreme = True if config is None else bool(getattr(config, 'GR4_REQUIRE_DC_EXTREME', True))

    fire = (
        ltf_score >= ltf_min
        and htf_score >= htf_min
        and global_score >= global_min
        and (at_extreme_htf or not require_dc_extreme)
    )

    # Size
    if not fire:
        mult = 0.0
    else:
        # Stronger signal → bigger size
        if ltf_score >= ltf_min + 4 and htf_score >= htf_min + 8 and global_score >= 3:
            mult = 5.0  # all aligned
        elif htf_score >= htf_min + 4 and global_score >= 2:
            mult = 2.5
        else:
            mult = 1.0

    reason = (
        f"GR4_{('L' if is_long else 'S')}_{symbol}_LTF={ltf_score}/{ltf_max}"
        f"_HTF={htf_score}/{htf_max}_g={global_score}/4"
        f"_dc_extreme={at_extreme_htf}_comp={composite:.2f}_mult={mult:.1f}x"
    )
    return GR4Signal(
        fire=fire, is_long=is_long, mult=mult, composite_score=composite,
        ltf_score=ltf_score, htf_score=htf_score, global_score=global_score,
        pattern_at_extreme=at_extreme_htf, reason=reason,
    )


def evaluate_exit_v4(entry_price: float, current_price: float, is_long: bool,
                     bars_held: int, indicators: dict, mode: str = 'tradier',
                     config=None) -> Tuple[bool, str]:
    """Exit AT THE TOP for LONG / AT THE BOTTOM for SHORT.

    Triggers:
      1. Take profit at TP%
      2. Stop loss at SL%
      3. Reverse extreme: counter-trend signal flips against (now at top for LONG / bottom for SHORT)
      4. Max hold bars
    """
    if entry_price <= 0 or current_price <= 0:
        return True, "INVALID_PRICE"
    gain = (current_price - entry_price) / entry_price * 100.0
    if not is_long: gain = -gain

    tp = 2.5 if config is None else float(getattr(config, 'GR4_TP_PCT', 2.5))
    sl = 0.8 if config is None else float(getattr(config, 'GR4_SL_PCT', 0.8))
    maxb = 200 if config is None else int(getattr(config, 'GR4_MAX_BARS', 200))
    if gain >= tp: return True, f"TP_{gain:.2f}%"
    if gain <= -sl: return True, f"SL_{gain:.2f}%"
    if bars_held >= maxb: return True, f"MAXBARS"

    # Reverse-extreme exit: flip side counter-signal
    rev = evaluate_golden_rule_v4('_exit', indicators, current_price, not is_long, mode=mode, config=config)
    # Not gated by universe (we pass _exit which fails universe gate but per-TF still scores)
    # Actually that returns NOT_IN_UNIVERSE — let me bypass universe for the reverse check
    return False, ""
