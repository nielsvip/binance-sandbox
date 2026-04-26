#!/usr/bin/env python3
"""
entry_engine_test.py — Harness combining 4 entry engines.

Imports four pure-function entry engines (wt/stoch/dc/htf), simulates them
across ~10 USDT/USDC pairs over the last 30 days of 15m bars, and reports
fires/day/symbol per engine plus OR / AND composites.

If an engine module is missing, a stub returning False is substituted so
the harness still completes and prints a zero row for that engine.
"""
from __future__ import annotations

import json
import os
import sys
from typing import Callable, Dict, List, Tuple

import numpy as np
import pandas as pd

KLINES_DIR = "/tmp/klines_30d"
SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "DOGEUSDT", "ADAUSDT", "XRPUSDT",
    "BTCUSDC", "ETHUSDC", "BNBUSDC", "DOGEUSDC", "ADAUSDC",
]
DAYS = 30
BARS_PER_DAY_15M = 96  # 24*4
LAST_N_15M = DAYS * BARS_PER_DAY_15M  # 2880

EngineFn = Callable[[str, dict, str], Tuple[bool, str, float]]


def _stub_engine(name: str) -> EngineFn:
    def _fn(symbol: str, indicators: dict, side: str):
        return (False, f"{name}_STUB", 0.0)
    _fn.__name__ = f"stub_{name}"
    return _fn


def _load_engines() -> Dict[str, EngineFn]:
    engines: Dict[str, EngineFn] = {}
    for name, modname, fnname in [
        ("wt", "entry_engine_wt", "should_fire_wt_entry"),
        ("stoch", "entry_engine_stoch", "should_fire_stoch_entry"),
        ("dc", "entry_engine_dc", "should_fire_dc_entry"),
        ("htf", "entry_engine_htf", "should_fire_htf_entry"),
    ]:
        try:
            mod = __import__(modname)
            engines[name] = getattr(mod, fnname)
        except Exception as e:
            print(f"[engine-load] {modname}.{fnname} unavailable ({type(e).__name__}: {e}); using stub.")
            engines[name] = _stub_engine(name)
    return engines


# ----- indicator helpers (vectorized, approximate) -----

def _ema(s: pd.Series, n: int) -> pd.Series:
    return s.ewm(span=n, adjust=False).mean()


def _wt(df: pd.DataFrame, esa_n: int = 10, ci_n: int = 21) -> Tuple[pd.Series, pd.Series]:
    hlc3 = (df["high"] + df["low"] + df["close"]) / 3.0
    esa = _ema(hlc3, esa_n)
    de = _ema((hlc3 - esa).abs(), esa_n)
    ci = (hlc3 - esa) / (0.015 * de.replace(0, np.nan))
    wt1 = _ema(ci, ci_n)
    wt2 = wt1.rolling(4, min_periods=1).mean()
    return wt1.fillna(0.0), wt2.fillna(0.0)


def _stoch(df: pd.DataFrame, k_n: int = 14, d_n: int = 3) -> Tuple[pd.Series, pd.Series]:
    low_n = df["low"].rolling(k_n, min_periods=1).min()
    high_n = df["high"].rolling(k_n, min_periods=1).max()
    denom = (high_n - low_n).replace(0, np.nan)
    k_raw = 100 * (df["close"] - low_n) / denom
    k = k_raw.rolling(d_n, min_periods=1).mean()
    d = k.rolling(d_n, min_periods=1).mean()
    return k.fillna(50.0), d.fillna(50.0)


def _donchian(df: pd.DataFrame, n: int = 20) -> Tuple[pd.Series, pd.Series, pd.Series]:
    hi = df["high"].rolling(n, min_periods=1).max()
    lo = df["low"].rolling(n, min_periods=1).min()
    basis = (hi + lo) / 2.0
    return hi, lo, basis


def _ha_color(df: pd.DataFrame) -> pd.Series:
    ha_close = (df["open"] + df["high"] + df["low"] + df["close"]) / 4.0
    ha_open = ha_close.copy()
    ha_open.iloc[0] = (df["open"].iloc[0] + df["close"].iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0
    return (ha_close > ha_open).astype(int)  # 1 = green, 0 = red


def _resample(df15: pd.DataFrame, rule: str) -> pd.DataFrame:
    return df15.resample(rule, label="right", closed="right").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    ).dropna()


def _load_symbol(symbol: str) -> pd.DataFrame | None:
    path = os.path.join(KLINES_DIR, f"{symbol}.json")
    if not os.path.exists(path):
        return None
    with open(path) as f:
        rows = json.load(f)
    df = pd.DataFrame(rows)
    df["ts"] = pd.to_datetime(df["timestamp"])
    df = df.set_index("ts")
    df = df[["open", "high", "low", "close", "volume"]].astype(float)
    if len(df) > LAST_N_15M:
        df = df.iloc[-LAST_N_15M:]
    return df


def _build_indicator_panel(df15: pd.DataFrame) -> pd.DataFrame:
    """For each 15m bar, attach indicators across multiple TFs (forward-filled from HTF).

    Field names match the engines' contracts:
      - WT: wt1_<tf>/wt2_<tf> + wt_velocity_<tf>
      - Stoch: stoch_k_<tf>/stoch_d_<tf> + k_3m_prev/d_3m_prev
      - DC: current_price, dc_high_<tf>/dc_low_<tf>/dc_basis_<tf>, ha_3m string
      - HTF: sma_200_D, dc_basis_D, dc_basis_D_ant, ha_4h/ha_D string, stoch_k_4h
    """
    out = pd.DataFrame(index=df15.index)

    # 15m base
    wt1_15, wt2_15 = _wt(df15)
    k_15, d_15 = _stoch(df15)
    dh_15, dl_15, db_15 = _donchian(df15, 20)
    out["wt1_15m"] = wt1_15
    out["wt2_15m"] = wt2_15
    out["stoch_k_15m"] = k_15
    out["stoch_d_15m"] = d_15
    out["k_15m"] = k_15
    out["d_15m"] = d_15
    out["dc_high_15m"] = dh_15
    out["dc_low_15m"] = dl_15
    out["dc_basis_15m"] = db_15
    out["ha_15m"] = _ha_color(df15)
    out["close"] = df15["close"]
    out["current_price"] = df15["close"]
    out["price"] = df15["close"]

    # 3m proxy — same series shifted/lagged. Engines treat 3m as the trigger TF.
    out["wt1_3m"] = wt1_15
    out["wt2_3m"] = wt2_15
    out["stoch_k_3m"] = k_15
    out["stoch_d_3m"] = d_15
    out["k_3m"] = k_15
    out["d_3m"] = d_15
    out["k_3m_prev"] = k_15.shift(1).fillna(50.0)
    out["d_3m_prev"] = d_15.shift(1).fillna(50.0)
    out["dc_high_3m"] = dh_15
    out["dc_low_3m"] = dl_15
    out["dc_basis_3m"] = db_15
    # ha_3m as string (engine reads .lower())
    ha15_int = _ha_color(df15)
    out["ha_3m"] = pd.Series(["green" if v else "red" for v in ha15_int.values], index=df15.index)

    # WT velocity 3m proxy = wt1 - wt1.shift(1)
    out["wt_velocity_3m"] = (wt1_15 - wt1_15.shift(1)).fillna(0.0)

    # HTF: 1h, 4h, D
    for rule, tag in [("1h", "1h"), ("4h", "4h"), ("1D", "D")]:
        dfh = _resample(df15, rule)
        if len(dfh) < 5:
            for col in ("wt1", "wt2", "stoch_k", "stoch_d", "k", "d",
                        "dc_high", "dc_low", "dc_basis"):
                out[f"{col}_{tag}"] = np.nan
            out[f"wt_velocity_{tag}"] = 0.0
            out[f"ha_{tag}"] = "red"
            continue
        wt1, wt2 = _wt(dfh)
        k, d = _stoch(dfh)
        dh, dl, db = _donchian(dfh, 20)
        out[f"wt1_{tag}"] = wt1.reindex(out.index, method="ffill")
        out[f"wt2_{tag}"] = wt2.reindex(out.index, method="ffill")
        out[f"stoch_k_{tag}"] = k.reindex(out.index, method="ffill")
        out[f"stoch_d_{tag}"] = d.reindex(out.index, method="ffill")
        out[f"k_{tag}"] = k.reindex(out.index, method="ffill")
        out[f"d_{tag}"] = d.reindex(out.index, method="ffill")
        out[f"dc_high_{tag}"] = dh.reindex(out.index, method="ffill")
        out[f"dc_low_{tag}"] = dl.reindex(out.index, method="ffill")
        out[f"dc_basis_{tag}"] = db.reindex(out.index, method="ffill")
        # WT velocity at this TF
        vel = (wt1 - wt1.shift(1)).fillna(0.0)
        out[f"wt_velocity_{tag}"] = vel.reindex(out.index, method="ffill").fillna(0.0)
        # HA color string at this TF
        ha = _ha_color(dfh)
        ha_str = pd.Series(["green" if v else "red" for v in ha.values], index=dfh.index)
        out[f"ha_{tag}"] = ha_str.reindex(out.index, method="ffill").fillna("red")

    # dc_basis_D antecedent (one-bar-back daily basis)
    dfd = _resample(df15, "1D")
    if len(dfd) >= 2:
        _, _, dbD = _donchian(dfd, 20)
        out["dc_basis_D_ant"] = dbD.shift(1).reindex(out.index, method="ffill")
    else:
        out["dc_basis_D_ant"] = 0.0

    # SMA200 on Daily, ffilled
    if len(dfd) >= 1:
        sma200 = dfd["close"].rolling(200, min_periods=20).mean()
        out["sma_200_D"] = sma200.reindex(out.index, method="ffill")
    else:
        out["sma_200_D"] = np.nan

    return out.ffill().fillna(0.0)


def _row_to_indicators(row: pd.Series) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, str):
            out[k] = v
        elif isinstance(v, (int, np.integer)):
            out[k] = int(v)
        else:
            try:
                out[k] = float(v)
            except (TypeError, ValueError):
                out[k] = v
    return out


def main():
    sys.path.insert(0, "/Users/niels/Documents/binance")
    engines = _load_engines()
    engine_names = ["wt", "stoch", "dc", "htf"]

    # Counters: counts[engine][side] = total fires across all bars/symbols
    counts: Dict[str, Dict[str, int]] = {n: {"LONG": 0, "SHORT": 0} for n in engine_names}
    counts["any"] = {"LONG": 0, "SHORT": 0}
    counts["all"] = {"LONG": 0, "SHORT": 0}

    total_bars = 0
    n_symbols_loaded = 0

    for sym in SYMBOLS:
        df15 = _load_symbol(sym)
        if df15 is None or len(df15) < 200:
            print(f"[load] {sym}: skipped (missing or too short)")
            continue
        n_symbols_loaded += 1
        panel = _build_indicator_panel(df15)
        # iterate bars
        # Skip warmup first 200 bars (need HTF + SMA settlement)
        warm = 200
        rows = panel.iloc[warm:]
        sym_bars = len(rows)
        total_bars += sym_bars

        for ts, row in rows.iterrows():
            ind = _row_to_indicators(row)
            for side in ("LONG", "SHORT"):
                fires = []
                for name in engine_names:
                    try:
                        fire, _, _ = engines[name](sym, ind, side)
                    except Exception:
                        fire = False
                    if fire:
                        counts[name][side] += 1
                    fires.append(bool(fire))
                if any(fires):
                    counts["any"][side] += 1
                if all(fires):
                    counts["all"][side] += 1

        print(f"[bars] {sym}: {sym_bars} bars (after warmup)")

    if n_symbols_loaded == 0:
        print("ERROR: no symbols loaded.")
        return

    # Convert to fires/day/symbol
    # bars-per-symbol-day = 96; total bar-days = total_bars / 96
    bar_days = total_bars / BARS_PER_DAY_15M
    # fires/day per symbol = total_fires / bar_days * n_symbols / n_symbols = total / bar_days
    # but that gives per-symbol-day average since bar_days already aggregates across symbols.
    # Per-symbol-day: counts / (bar_days)
    def per_sym_day(c: int) -> float:
        return c / max(bar_days, 1.0)

    print("\n" + "=" * 60)
    print(f"Symbols loaded: {n_symbols_loaded}  | total 15m bars: {total_bars}  | sym-days: {bar_days:.1f}")
    print("Fires per symbol per day (15m bars only):")
    print(f"{'ENGINE':<14}{'LONG/d':>10}{'SHORT/d':>10}{'TOTAL/d':>10}")
    for name in engine_names + ["any", "all"]:
        L = per_sym_day(counts[name]["LONG"])
        S = per_sym_day(counts[name]["SHORT"])
        label = {"any": "any-1-fire", "all": "all-4-fire"}.get(name, name)
        print(f"{label:<14}{L:>10.2f}{S:>10.2f}{L + S:>10.2f}")

    # Headline: extrapolate to 2000 symbols
    any_total_per_sym_day = per_sym_day(counts["any"]["LONG"]) + per_sym_day(counts["any"]["SHORT"])
    headline_2000 = any_total_per_sym_day * 2000
    print()
    print(f"HEADLINE (OR composite, extrapolated to 2000 symbols):")
    print(f"  per-sym-day total = {any_total_per_sym_day:.2f}")
    print(f"  2000 syms × {any_total_per_sym_day:.2f} = {headline_2000:,.0f} fires/day")
    print(f"  Target 200,000/day: {'HIT' if headline_2000 >= 200000 else 'MISS'} "
          f"({headline_2000 / 200000 * 100:.0f}% of target)")

    # Sanity: flag implausibly hot engines
    print("\nSanity flags:")
    for name in engine_names:
        L = per_sym_day(counts[name]["LONG"])
        S = per_sym_day(counts[name]["SHORT"])
        if L + S > BARS_PER_DAY_15M * 0.5:
            print(f"  ! {name}: {L+S:.1f}/d > 48 (>50% of bars) — TOO LOOSE")
        elif L + S < 0.05 and counts[name]["LONG"] + counts[name]["SHORT"] == 0:
            print(f"  - {name}: zero fires (stub or too strict)")


if __name__ == "__main__":
    main()
