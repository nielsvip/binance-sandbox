"""tests/test_small_account_sizer.py — DH parity twin regression tests.

Replay vectors are live Oct 4 2026 executions (LIGHT_MODE effective config:
START_POSITION_SIZE=16, MAX_ORDER_VALUE=80, _MEN=440, _FIN=18).

Live proof lines (~/logs/ez_manage_{acct}.log*):
  fin  DASH $25/59  -> MAKER_ZERO_QTY qty_abs=0.00077748 step=0.001
  men  DASH $25/59  -> MAKER_QTY qty_abs=0.001079 -> placed, never filled
  men  VET  2830x1.28 -> MAKER_QTY qty_abs=3626.30 -> FILLED
  ang  QNT  0.0937  -> MAKER_ZERO_QTY step=0.1
  flz  DASH 0.422   -> MAKER_QTY qty_str=0.422 -> FILLED
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from vec_decisions import small_account_sizer as sas


def light_cfg(**over):
    kw = dict(START_POSITION_SIZE=16.0, MAX_ORDER_VALUE=80.0,
              MAX_ORDER_VALUE_MEN=440.0, MAX_ORDER_VALUE_FIN=18.0,
              SMALL_ACCOUNT_MIN_NOTIONAL_USD=5.0, MODE="crypto",
              SMALL_ACCOUNT_SIZER_ENABLED=True)
    kw.update(over)
    return types.SimpleNamespace(**kw)


def test_step_sizes_from_live_file():
    assert sas.step_size_for("DASHUSDT") == 0.001
    assert sas.step_size_for("QNTUSDT") == 0.1
    assert sas.step_size_for("VETUSDT") == 1.0
    assert sas.step_size_for("DASHUSDT_LONG") == 0.001  # suffix-tolerant


def test_fin_dash_zero_qty_replay():
    # $18 FIN clamp: 0.423944 coins -> 18/58.97 = 0.3052; 0.15x -> 0.000776
    # -> lot floor 0. Live logged qty_abs=0.00077748.
    q, tok = sas.apply_live_sizing(0.423944, 58.97, "DASHUSDT", "fin", light_cfg())
    assert q == 0.0 and tok == "MAKER_ZERO_QTY", (q, tok)


def test_men_dash_dust_replay():
    # no $440 clamp; 0.15x -> 0.001078 -> floor 0.001 = $0.06 dust.
    # Live placed it (MAKER_QTY 0.001079) and it never filled 6/6.
    q, tok = sas.apply_live_sizing(0.423944, 58.97, "DASHUSDT", "men", light_cfg())
    assert q == 0.0 and tok == "MAKER_DUST_NO_FILL_RISK", (q, tok)


def test_men_vet_fill_replay():
    # min() picks the $32 cap leg: 32/0.0088247 = 3626 coins. Live filled.
    q, tok = sas.apply_live_sizing(3626.304761, 0.0088247, "VETUSDT", "men", light_cfg())
    assert tok == "" and abs(q - 3626.0) < 1.0, (q, tok)


def test_ang_qnt_pure_lot_step():
    # ang has no 0.15x rule; 0.0937 < step 0.1 -> zero.
    q, tok = sas.apply_live_sizing(0.0937, 266.81, "QNTUSDT", "ang", light_cfg())
    assert q == 0.0 and tok == "MAKER_ZERO_QTY", (q, tok)


def test_flz_dash_passes():
    q, tok = sas.apply_live_sizing(0.422012, 59.24, "DASHUSDT", "flz", light_cfg())
    assert tok == "" and abs(q - 0.422) < 1e-9, (q, tok)


def test_hedge_and_scalp_exempt():
    q, tok = sas.apply_live_sizing(0.423944, 58.97, "DASHUSDT", "fin",
                                   light_cfg(), is_hedge=True)
    assert tok == "" and q > 0.3, (q, tok)


def test_size_open_qty_gating():
    # default cfg (flag off / no account) -> legacy passthrough
    off = types.SimpleNamespace(MODE="crypto")
    q, tok = sas.size_open_qty(25.0, 59.0, "DASHUSDT", off)
    assert tok == "" and abs(q - 25.0 / 59.0) < 1e-12
    # tradier MODE passes through even when enabled
    tr = light_cfg(MODE="tradier", VEC_ACCOUNT_KEY="fin")
    q, tok = sas.size_open_qty(25.0, 59.0, "DASHUSDT", tr)
    assert tok == "" and abs(q - 25.0 / 59.0) < 1e-12
    # enabled + fin -> blocked
    en = light_cfg(VEC_ACCOUNT_KEY="fin")
    q, tok = sas.size_open_qty(25.0, 58.97, "DASHUSDT", en)
    assert q == 0.0 and tok == "MAKER_ZERO_QTY", (q, tok)


def test_quickconfig_defaults_preserve_baseline():
    # BIBLE section 14.1/21: defaults must keep the sweep baseline identical.
    import v12_quick_engine as V

    cfg = V.QuickConfig()
    assert cfg.SMALL_ACCOUNT_SIZER_ENABLED is False
    assert cfg.VEC_ACCOUNT_KEY == ""
    assert cfg.SMALL_ACCOUNT_MIN_NOTIONAL_USD == 5.0
