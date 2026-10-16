#!/usr/bin/env python3
"""per_sym_live_set — THE per-sym layer for every live reader. One source, no second opinion.

USER 2026-10-15: "we do not store overrides anymore, we store and apply the FULL set of switches and
filters for every symbol and side ... defaults change every morning so overrides are absolutely
useless." That mandate was implemented inside per_sym_store.get_overrides only. Two reader families
never moved onto it and kept resolving the `overrides` half of
data/hourly_reconfig/per_sym_active_config{,_stocks}.json:

    ez_manage._PerSymProxy        backs every `config.KNOB` / `getattr(config, KNOB)` read in ez_manage
    ez_positions_quick            _get_per_sym_overrides (-> _psym_get / _sw_get / _per_sym_cfg) and
                                  _get_btc_sym_cfg — the crypto ENTRY path, which did not import
                                  per_sym_store at all

Measured 2026-10-16 (tools/per_sym_live_parity_audit.py): that JSON view carries 412 sym_sides against
742 live rows and ~835 keys against the board's ~3546 — 142 of 327 live CRYPTO rows were absent from it
entirely and 67 more resolved at least one switch away from their own board, because every key the
board froze that the view does not carry falls through to the cat_side TEMPLATE layer that
tools/build_cat_side_defaults_4.py rebuilds every morning. ez_manage._psym_get (~8248) and
tradier_manage._cfg (~7811) were already correct, so ez_manage had two per-sym readers that disagreed.

CONTRACT
  full_set(sym_side) -> the complete frozen board set, or None when there is no row anywhere.
  READ-ONLY: the returned dict is per_sym_store's live cache itself (3546 keys, read thousands of times
  per cycle — copying cost 18 us/call, which is the event-loop freeze the memo was added for). Never
  mutate it; copy first if you must.

SIZING CAPS BEAT THE BOARD — USER 2026-10-16 ("reduce crypto trading to minimum - only men account is
still eroding my savings for NOTHING"). config.CRYPTO_MINIMUM_TRADING_MODE cuts new crypto exposure to
the exchange floor, and every one of the 328 crypto boards was MEASURED at the old sizes: all 328 carry
START_POSITION_SIZE above the 6.0 cap (up to 45.0) and MAX_POSITION_SIZE above the 12.0 cap (up to
780.0, 65x). ez_manage._psym_get already caps those 8 knobs (_crypto_min_size_cap, fail-SAFE), but
_PerSymProxy and the whole ez_positions_quick entry path never did — measured 2026-10-16: 269 capped
knobs above their cap were ALREADY being served to live through the JSON `overrides` half, so the
minimum-trading mandate was partly bypassed before this module existed. Serving the full board set
without the cap would have raised that to 328 rows on every sizing read. So the cap is applied HERE,
once, for every caller: the newer user instruction wins over the board, exactly as _psym_get has it.
Caps never raise a value that is already below them, and nothing here touches exits or stops.

REVERSIBILITY
  PER_SYM_FULL_SET_READER=0 turns this off at every call site and each caller falls back to the exact
  JSON block it used before. No code change needed to go back.
  config.CRYPTO_MINIMUM_TRADING_MODE=False lifts the sizing caps (its own documented rollback).
  V8_DISABLE_PER_SYM / PER_SYM_STORE_SQLITE_DISABLED / PER_SYM_APPLY_FULL_SET keep working unchanged.
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
JSON_VIEWS = (
    ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json",
    ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json",
)
# MIRROR of ez_manage.CRYPTO_MIN_SIZE_CAPS (USER 2026-10-16 minimum crypto trading). Kept as a literal
# so this module never imports ez_manage; tests/test_per_sym_live_set.py asserts the two are identical.
CRYPTO_MIN_SIZE_CAPS = {
    "START_POSITION_SIZE": 6.0,
    "MAX_ORDER_VALUE": 6.0,
    "MAX_ORDER_VALUE_MEN": 6.0,
    "MAX_ORDER_VALUE_FIN": 6.0,
    "MAX_POSITION_SIZE": 12.0,
    "MAX_POSITION_SIZE_MEN": 12.0,
    "MAX_POSITION_SIZE_FIN": 12.0,
    "MAX_POSITION_SIZE_BTC": 60.0,
}
CRYPTO_SUFFIXES = ("USDT", "USDC", "USD1", "USDS", "BUSD", "FDUSD", "TUSD", "DAI")
_CAP_MEMO_MAX = 256

_json_sets: dict = {}
_json_mtimes: dict = {}
_cap_memo: dict = {}


def enabled() -> bool:
    """False -> every caller reverts to its own legacy JSON `overrides` lookup."""
    return os.environ.get("PER_SYM_FULL_SET_READER", "1") != "0"


def _json_full_sets() -> dict:
    """Fail-soft fallback: the JSON views' `full_config` half (NOT `overrides`), reloaded on mtime."""
    for p in JSON_VIEWS:
        try:
            mtime = p.stat().st_mtime
        except OSError:
            continue
        if _json_mtimes.get(p) == mtime:
            continue
        try:
            with p.open() as fh:
                raw = json.load(fh)
        except Exception:
            continue
        for k, v in raw.items():
            if k == "_meta" or not isinstance(v, dict):
                continue
            fc = v.get("full_config")
            if isinstance(fc, dict) and fc:
                _json_sets[k] = fc
        _json_mtimes[p] = mtime
    return _json_sets


def _min_mode_on() -> bool:
    """Fail-SAFE, same as ez_manage._crypto_min_size_cap: any error -> caps APPLIED, because the harm
    being prevented is opening more crypto exposure than the user asked for."""
    try:
        import config as _cfg

        return bool(getattr(_cfg.Config, "CRYPTO_MINIMUM_TRADING_MODE", False))
    except Exception:
        return True


def _apply_caps(sym_side: str, full: dict) -> dict:
    """Clamp the 8 sizing knobs to CRYPTO_MIN_SIZE_CAPS. Returns `full` itself when nothing is above a
    cap (no copy — the hot path reads this thousands of times per cycle), otherwise a capped copy
    memoized for PER_SYM_OVR_MEMO_SEC. Crypto only; a stock sym_side is never capped."""
    sym = sym_side.rpartition("_")[0]
    if not sym.endswith(CRYPTO_SUFFIXES) or not _min_mode_on():
        return full
    over = {}
    for knob, cap in CRYPTO_MIN_SIZE_CAPS.items():
        v = full.get(knob)
        if v is None:
            continue
        try:
            if float(v) > cap:
                over[knob] = cap
        except (TypeError, ValueError):
            over[knob] = cap
    if not over:
        return full
    now = time.monotonic()
    hit = _cap_memo.get(sym_side)
    if hit is not None and now - hit[0] < float(os.environ.get("PER_SYM_OVR_MEMO_SEC", "30.0")):
        return hit[1]
    capped = dict(full)
    capped.update(over)
    if len(_cap_memo) >= _CAP_MEMO_MAX:
        _cap_memo.clear()
    _cap_memo[sym_side] = (now, capped)
    return capped


def full_set(sym_side: str):
    """The full frozen set for sym_side: per_sym_store SQL first (memoized 30 s, no copy on return),
    the JSON views' full_config half only when the store is unreachable or has no row. None when
    neither has it — the caller then resolves cat_side > global exactly as before. Sizing knobs are
    clamped to the live minimum-trading caps on the way out (see module docstring)."""
    if not enabled():
        return None
    if os.environ.get("PER_SYM_STORE_SQLITE_DISABLED") != "1":
        try:
            import per_sym_store as _pss

            ov = _pss.get_overrides(sym_side)
            if ov:
                return _apply_caps(sym_side, ov)
        except Exception:
            pass
    try:
        ov = _json_full_sets().get(sym_side)
        return _apply_caps(sym_side, ov) if ov else ov
    except Exception:
        return None
