"""GOLDEN_RULE v3 — proven-winner/loser universe + LTF/HTF bucketed agreement
on the 17 indicators.

USER MANDATE 2026-05-10:
- Symbols come from symbols_trb_long.json (LONG side, proven winners) or
  symbols_trb_short.json (SHORT side, proven losers). DO NOT trade outside.
- GOLDEN RULE setups: pullback to dc_basis_1h or breakout-then-retest. NOT random.
- 17 indicators × 5 TFs, bucketed as LTF (5m, 15m) and HTF (1h, 4h, D).
- Sweep finds (ltf_req, htf_req, indicators_per_tf_min) tuple that hits Sharpe ≥2.

Indicator list (per-TF unless noted):
  1.  WT (wt1 vs wt2)
  2.  DC (close vs dc_high/low — directional break)
  3.  DC basis (close vs dc_basis — pullback target)
  4.  BB (close vs bb_upper/lower — breakout)
  5.  BB basis (close vs bb_basis — mean reversion)
  6.  Stoch (k vs d)
  7.  MFI (oversold for LONG entry, overbought for SHORT entry)
  8.  RSI (50-line direction)
  9.  ATR (>0 — liquidity floor; not directional)
  10. MACD (crossover for LONG, crossunder for SHORT)
  11. ADX (≥20 — trending; not directional)
  12. HA (heikin-ashi color matches direction)
  13. relative_volume (≥1.25 — surge; not directional)
  14. VWAP (D-only) — close above/below daily VWAP
  15. clenow_score (GLOBAL — trend ranking, sign matches direction)
  16. sepa_score (GLOBAL — Minervini SEPA setup)
  17. market_sentiment_score (GLOBAL — overall sentiment direction)

Per-TF: 1-14 (14 indicators max — VWAP D-only).
Global: 15-17.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Tuple, List, Set
import json, os
from pathlib import Path


# ───────────────────────────── universe loaders ───────────────────────────────


_TRB_LONG: Optional[Set[str]] = None
_TRB_SHORT: Optional[Set[str]] = None


def load_trb_universe(base_path: Optional[str] = None) -> Tuple[Set[str], Set[str]]:
    """Load symbols_trb_long.json + symbols_trb_short.json once and cache."""
    global _TRB_LONG, _TRB_SHORT
    if _TRB_LONG is not None and _TRB_SHORT is not None:
        return _TRB_LONG, _TRB_SHORT
    base = Path(base_path) if base_path else Path(__file__).resolve().parent
    longs: Set[str] = set()
    shorts: Set[str] = set()
    try:
        with open(base / 'symbols_trb_long.json') as f:
            d = json.load(f)
            longs = set(d) if isinstance(d, list) else set(d.keys())
    except Exception as e:
        print(f"[GR3] WARN: could not load symbols_trb_long.json: {e}")
    try:
        with open(base / 'symbols_trb_short.json') as f:
            d = json.load(f)
            shorts = set(d) if isinstance(d, list) else set(d.keys())
    except Exception as e:
        print(f"[GR3] WARN: could not load symbols_trb_short.json: {e}")
    _TRB_LONG = longs
    _TRB_SHORT = shorts
    return longs, shorts


# ───────────────────────────── helpers ────────────────────────────────────────


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


# ───────────────────────────── per-TF scoring ─────────────────────────────────


# 14 per-TF indicators (VWAP only on D)
PER_TF_INDICATORS = (
    'WT', 'DC_BREAK', 'DC_BASIS', 'BB_BREAK', 'BB_BASIS', 'STOCH',
    'MFI', 'RSI', 'ATR', 'MACD', 'ADX', 'HA', 'RVOL', 'VWAP',
)


def _score_tf(ind: dict, tf: str, is_long: bool, current_price: float) -> Tuple[int, int, list]:
    """Returns (raw_count, max_possible, hits_list) for ONE timeframe.
    Per-TF maximum is up to 14 (13 if not D since VWAP only on D)."""
    raw = 0
    maxp = 0
    hits = []

    def add(condition: bool, label: str, available: bool = True):
        nonlocal raw, maxp
        if not available:
            return
        maxp += 1
        if condition:
            raw += 1
            hits.append(f'{label}_{tf}')

    # 1. WT
    wt1 = _f(ind, f'wt1_{tf}', float('nan'))
    wt2 = _f(ind, f'wt2_{tf}', float('nan'))
    if wt1 == wt1 and wt2 == wt2:
        add((is_long and wt1 > wt2) or ((not is_long) and wt1 < wt2), 'WT')

    # 2. DC break (close vs dc_high/low)
    dc_h = _f(ind, f'dc_high_{tf}'); dc_l = _f(ind, f'dc_low_{tf}')
    if dc_h > 0 and dc_l > 0 and current_price > 0:
        add((is_long and current_price > dc_h) or ((not is_long) and current_price < dc_l), 'DC_BO')

    # 3. DC basis (close-near-basis on the right side — pullback opportunity)
    dc_b = _f(ind, f'dc_basis_{tf}')
    if dc_h > 0 and dc_l > 0 and dc_b > 0 and current_price > 0:
        # LONG wants close near basis from above (price > basis but < dc_high), SHORT inverse
        if is_long:
            add(dc_b < current_price < dc_h, 'DC_BASIS')
        else:
            add(dc_l < current_price < dc_b, 'DC_BASIS')

    # 4. BB break
    bb_u = _f(ind, f'bb_upper_{tf}'); bb_l = _f(ind, f'bb_lower_{tf}')
    if bb_u > 0 and bb_l > 0 and current_price > 0:
        add((is_long and current_price > bb_u) or ((not is_long) and current_price < bb_l), 'BB_BO')

    # 5. BB basis (close vs basis — mean reversion zone)
    # bb_basis_{tf} field; if not present, derive from upper+lower midpoint.
    bb_b = _f(ind, f'bb_basis_{tf}', 0.0)
    if bb_b == 0 and bb_u > 0 and bb_l > 0:
        bb_b = (bb_u + bb_l) / 2
    if bb_b > 0 and bb_u > 0 and bb_l > 0 and current_price > 0:
        if is_long:
            add(bb_b < current_price < bb_u, 'BB_BASIS')
        else:
            add(bb_l < current_price < bb_b, 'BB_BASIS')

    # 6. Stoch
    sk = _f(ind, f'stoch_k_{tf}', float('nan')); sd = _f(ind, f'stoch_d_{tf}', float('nan'))
    if sk == sk and sd == sd:
        add((is_long and sk > sd) or ((not is_long) and sk < sd), 'STOCH')

    # 7. MFI (LONG: oversold <40 = buyable; SHORT: overbought >60 = sellable)
    mfi = _f(ind, f'mfi_{tf}', float('nan'))
    if mfi == mfi and mfi > 0:
        add((is_long and mfi < 40) or ((not is_long) and mfi > 60), 'MFI')

    # 8. RSI (50-line direction)
    rsi = _f(ind, f'rsi_{tf}', float('nan'))
    if rsi == rsi and rsi > 0:
        add((is_long and rsi >= 50) or ((not is_long) and rsi <= 50), 'RSI')

    # 9. ATR liquidity (just need >0 — not directional)
    atr = _f(ind, f'atr_{tf}', float('nan'))
    if atr == atr and atr > 0:
        add(True, 'ATR')

    # 10. MACD cross
    mxo = _f(ind, f'macd_crossover_{tf}', 0)
    mxu = _f(ind, f'macd_crossunder_{tf}', 0)
    if mxo or mxu:
        add((is_long and mxo > 0) or ((not is_long) and mxu > 0), 'MACD')

    # 11. ADX strength (≥20 — trending; not directional)
    adx = _f(ind, f'adx_{tf}', float('nan'))
    if adx == adx and adx > 0:
        add(adx >= 20, 'ADX')

    # 12. HA color
    ha = _ha_color(ind, tf)
    if ha in ('green', 'red', 'neutral'):
        add((is_long and ha == 'green') or ((not is_long) and ha == 'red'), 'HA')

    # 13. relative_volume surge (≥1.25)
    rv = _f(ind, f'relative_volume_{tf}', float('nan'))
    if rv == rv and rv > 0:
        add(rv >= 1.25, 'RVOL')

    # 14. VWAP (D-only)
    if tf == 'D':
        vwap = _f(ind, 'vwap_D', float('nan'))
        if vwap == vwap and vwap > 0 and current_price > 0:
            add((is_long and current_price > vwap) or ((not is_long) and current_price < vwap), 'VWAP')

    return raw, maxp, hits


# ───────────────────────────── GOLDEN_RULE pattern detection ──────────────────


def _golden_rule_pattern(ind: dict, current_price: float, is_long: bool) -> Tuple[bool, str]:
    """Detect GOLDEN_RULE setup: breakout-with-confirmation OR pullback-to-basis-with-bounce.
    Returns (matched, kind) — kind in {'BREAKOUT', 'RETEST_BASIS', 'CONTINUATION', ''}.
    """
    # 1h DC + BB are the primary GOLDEN_RULE levels
    dc_h_1h = _f(ind, 'dc_high_1h'); dc_l_1h = _f(ind, 'dc_low_1h')
    dc_b_1h = _f(ind, 'dc_basis_1h')
    bb_u_1h = _f(ind, 'bb_upper_1h'); bb_l_1h = _f(ind, 'bb_lower_1h')
    if current_price <= 0:
        return False, ''
    # Phase 1: BREAKOUT — price above dc_high_1h or bb_upper_1h (LONG); below dc_low_1h or bb_lower_1h (SHORT)
    if is_long:
        breakout = (dc_h_1h > 0 and current_price > dc_h_1h) or (bb_u_1h > 0 and current_price > bb_u_1h)
    else:
        breakout = (dc_l_1h > 0 and current_price < dc_l_1h) or (bb_l_1h > 0 and current_price < bb_l_1h)
    if breakout:
        return True, 'BREAKOUT'
    # Phase 2: RETEST_BASIS — price near dc_basis_1h (within ATR or 0.5%)
    if dc_b_1h > 0:
        atr_1h = _f(ind, 'atr_1h', 0)
        tol = max(atr_1h, current_price * 0.005)
        if is_long and dc_b_1h <= current_price <= dc_b_1h + tol * 2:
            return True, 'RETEST_BASIS'
        if (not is_long) and dc_b_1h - tol * 2 <= current_price <= dc_b_1h:
            return True, 'RETEST_BASIS'
    # Phase 3: CONTINUATION — between basis and breakout level, momentum aligned
    if dc_b_1h > 0 and dc_h_1h > 0 and dc_l_1h > 0:
        if is_long and dc_b_1h < current_price < dc_h_1h:
            return True, 'CONTINUATION'
        if (not is_long) and dc_l_1h < current_price < dc_b_1h:
            return True, 'CONTINUATION'
    return False, ''


# ───────────────────────────── main evaluator ─────────────────────────────────


@dataclass
class GR3Signal:
    fire: bool
    is_long: bool
    mult: float
    composite_score: float
    ltf_aligned: int          # of 2 (5m, 15m): how many had ≥min indicators agreeing
    htf_aligned: int          # of 3 (1h, 4h, D): how many had ≥min indicators agreeing
    global_score: float       # clenow + sepa + sentiment + wt_score, sign-aligned (-3..+3)
    pattern_kind: str          # BREAKOUT / RETEST_BASIS / CONTINUATION / ''
    reason: str


def evaluate_golden_rule_v3(
    symbol: str,
    indicators: dict,
    current_price: float,
    is_long: bool,
    mode: str = 'tradier',
    config=None,
) -> GR3Signal:
    """v3 evaluator — universe gate + GOLDEN_RULE pattern + LTF/HTF bucketed agreement.

    Tunables (via config or kwargs):
      GR3_LTF_REQ:        min LTFs aligned (0..2). Default 1.
      GR3_HTF_REQ:        min HTFs aligned (0..3). Default 2.
      GR3_PER_TF_MIN_INDS: min indicators agreeing per TF to count as aligned. Default 7 of 14.
      GR3_GLOBAL_MIN:     min global score sign-aligned (0..4). Default 2.
      GR3_REQUIRE_PATTERN: must be in BREAKOUT/RETEST/CONTINUATION. Default True.
    """
    base_tf = '5m' if mode == 'tradier' else '3m'

    # Universe gate
    universe_long, universe_short = load_trb_universe()
    if mode == 'tradier':
        sym_norm = symbol.upper().replace('USDT', '').replace('USDC', '')
        in_universe = (is_long and sym_norm in universe_long) or ((not is_long) and sym_norm in universe_short)
        if not in_universe:
            return GR3Signal(False, is_long, 0.0, 0.0, 0, 0, 0.0, '',
                             f'GR3_NOT_IN_UNIVERSE_{symbol}_{("LONG" if is_long else "SHORT")}')

    # Pattern gate
    require_pattern = True if config is None else bool(getattr(config, 'GR3_REQUIRE_PATTERN', True))
    pat_ok, pat_kind = _golden_rule_pattern(indicators, current_price, is_long)
    if require_pattern and not pat_ok:
        return GR3Signal(False, is_long, 0.0, 0.0, 0, 0, 0.0, '', f'GR3_NO_PATTERN_{symbol}')

    # Per-TF scoring
    ltfs = [base_tf, '15m']
    htfs = ['1h', '4h', 'D']
    per_tf_min = 7 if config is None else int(getattr(config, 'GR3_PER_TF_MIN_INDS', 7))
    ltf_aligned = 0
    htf_aligned = 0
    composite_num = 0.0
    composite_den = 0.0
    indicator_total = 0
    hits_all: List[str] = []
    weights = {base_tf: 1.0, '15m': 1.5, '1h': 2.0, '4h': 3.0, 'D': 4.0}
    for tf in ltfs + htfs:
        raw, maxp, hits = _score_tf(indicators, tf, is_long, current_price)
        if maxp == 0:
            continue
        if raw >= per_tf_min:
            if tf in ltfs:
                ltf_aligned += 1
            else:
                htf_aligned += 1
        composite_num += (raw / maxp) * weights[tf]
        composite_den += weights[tf]
        indicator_total += raw
        hits_all.extend(hits[:2])

    composite = (composite_num / composite_den) if composite_den > 0 else 0.0

    # Global rankers (15-17 + wt_score as bonus)
    clenow = _f(indicators, 'clenow_score', 0.0)
    sepa = _f(indicators, 'sepa_score', 0.0)
    sentiment = _f(indicators, 'market_sentiment_score', 0.0)
    wt_score_d = _f(indicators, 'wt_score_D', 0.0)
    # Sign-aligned counter: each ranker is +1 if directional match, -1 if against, 0 if zero
    def _sign_align(v: float) -> int:
        if v == 0: return 0
        if is_long: return 1 if v > 0 else -1
        return 1 if v < 0 else -1
    global_score_signed = (
        _sign_align(clenow) + _sign_align(sepa) + _sign_align(sentiment) + _sign_align(wt_score_d)
    )

    # Fire decision
    ltf_req = 1 if config is None else int(getattr(config, 'GR3_LTF_REQ', 1))
    htf_req = 2 if config is None else int(getattr(config, 'GR3_HTF_REQ', 2))
    global_min = 2 if config is None else int(getattr(config, 'GR3_GLOBAL_MIN', 2))

    fire = (
        ltf_aligned >= ltf_req
        and htf_aligned >= htf_req
        and global_score_signed >= global_min
    )

    # Size tier
    if not fire:
        mult = 0.0
    else:
        # Stack tiers based on alignment density
        if pat_kind == 'RETEST_BASIS' and ltf_aligned >= 1 and htf_aligned >= 2 and global_score_signed >= 3:
            mult = 8.0  # The legendary big-pullback entry
        elif pat_kind == 'BREAKOUT' and htf_aligned >= 2:
            mult = 0.3  # Tiny breakout entry per GOLDEN_RULE design
        elif htf_aligned >= 3 and global_score_signed >= 3:
            mult = 5.0
        elif htf_aligned >= 2 and global_score_signed >= 2:
            mult = 2.0
        else:
            mult = 1.0

    reason = (
        f"GR3_{('L' if is_long else 'S')}_{symbol}_{pat_kind or 'NOPAT'}"
        f"_LTF={ltf_aligned}/2_HTF={htf_aligned}/3_g={global_score_signed:+d}/4"
        f"_comp={composite:.2f}_inds={indicator_total}_mult={mult:.1f}x"
    )
    return GR3Signal(
        fire=fire, is_long=is_long, mult=mult, composite_score=composite,
        ltf_aligned=ltf_aligned, htf_aligned=htf_aligned,
        global_score=float(global_score_signed), pattern_kind=pat_kind, reason=reason,
    )


def evaluate_exit_v3(entry_price: float, current_price: float, is_long: bool,
                     bars_held: int, indicators: dict, mode: str = 'tradier',
                     config=None) -> Tuple[bool, str]:
    """Exit logic — TP / SL / max-hold / reverse-pattern."""
    if entry_price <= 0 or current_price <= 0:
        return True, "INVALID_PRICE"
    gain = (current_price - entry_price) / entry_price * 100.0
    if not is_long:
        gain = -gain
    tp = 2.0 if config is None else float(getattr(config, 'GR3_TP_PCT', 2.0))
    sl = 0.7 if config is None else float(getattr(config, 'GR3_SL_PCT', 0.7))
    maxb = 200 if config is None else int(getattr(config, 'GR3_MAX_BARS', 200))
    if gain >= tp: return True, f"TP_{gain:.2f}%"
    if gain <= -sl: return True, f"SL_{gain:.2f}%"
    if bars_held >= maxb: return True, f"MAX_BARS_{bars_held}"
    return False, ""
