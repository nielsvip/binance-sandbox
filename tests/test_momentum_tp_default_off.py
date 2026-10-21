"""USER 2026-10-21 EXACT-PARITY: MOMENTUM_TP profit-take has no vector model, so
its global default must be off; only boards that explicitly authorize it keep
firing (per-sym proxy). Source-level pin, no live stack import."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def test_momentum_tp_default_off():
    import config as C

    assert C.Config.MOMENTUM_TP_ENABLED is False


def test_momentum_tp_live_path_is_per_sym_gated():
    src = (ROOT / "ez_manage.py").read_text()
    i = src.index("MANDATORY PROFIT TAKE (MOMENTUM EXHAUSTION)")
    window = src[i:i + 600]
    assert "_psym_get" in window and "MOMENTUM_TP_ENABLED" in window, \
        "profit-take must resolve the per-sym switch, never a bare global"
