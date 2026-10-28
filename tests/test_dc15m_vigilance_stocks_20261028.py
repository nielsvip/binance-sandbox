"""USER 2026-10-28 (verbatim): "unlock tradeir_mange for the dc4 vigilance if it is correctly vectorized and tested in the
templates; DELTA_EXIT_DC15M_FLOOR_BREAK should be tested as a switch if it works it works for a sym_side but it needs to be
wired correctly so it does move the needle in both vec and live identically.; DC15M_FORCE_EXIT_ENABLED sounds like the same
thing - make sure this is tested under the same name in stocks and crypto - it should always be skipped if min_hold is not
yet reached".

Fixture parity on REAL NPZ bars (crypto NMRUSDT + stocks AAPL from backtest_v8/indicators): the live getter (a snapshot
dict carrying the prior closed bar's channel, as ez_indicators/tradier_indicators emit dc_low_15m_prev) and the vector
getter (grey_wire_exits.NpzBar) reach the same decision, including the MIN_HOLD skip; the exit gate books the armed close
red and nothing else; both live managers and the engine call the shared functions.
"""
import itertools
import pathlib

import numpy as np
import pytest

from vec_decisions import grey_wire_exits as GW
from vec_decisions import live_native_exit_twins as T
from vec_decisions import min_hold_gate as MH
from vec_decisions import net_exit_gate as NG
from vec_decisions import vigilance_dc4 as VIG

ROOT = pathlib.Path(__file__).resolve().parents[1]
NPZ_DIR = ROOT / "backtest_v8" / "indicators"


def _load(sym):
    p = NPZ_DIR / f"{sym}.npz"
    if not p.exists():
        pytest.skip(f"no local NPZ {p}")
    z = np.load(p, allow_pickle=True)
    keys = ("close_15m", "close", "dc_low_15m", "dc_high_15m", "dc_low_15m_prev", "dc_high_15m_prev", "dc_low4_15m", "dc_high4_15m", "timestamps")
    out = {k: np.asarray(z[k]) for k in keys if k in z.files}
    n = len(out["dc_low_15m"])
    out = {k: v for k, v in out.items() if len(v) == n}
    out["_px"] = out.get("close_15m", out.get("close"))
    return out, n


def _cfg(min_hold, on=True):
    vals = {"DC15M_FORCE_EXIT_ENABLED": on, "MIN_HOLD_BARS": min_hold, "BASE_TF": "15m"}
    return lambda k, d: vals.get(k, d)


@pytest.mark.parametrize("sym", ("NMRUSDT", "AAPL"))
@pytest.mark.parametrize("is_long", (True, False))
def test_live_snapshot_equals_vector_bar_on_real_npz(sym, is_long):
    npz, n = _load(sym)
    bar = GW.NpzBar(npz, n)
    lo_i = max(1, n - 3000)
    fires = held = mism = 0
    for i in range(lo_i, n):
        bar.i = i
        px = float(npz["_px"][i])
        live_snapshot = {k: float(npz[k][i]) for k in ("dc_low_15m", "dc_high_15m", "dc_low_15m_prev", "dc_high_15m_prev") if k in npz}
        for gain, mh, age_bars in itertools.product((-0.7, 0.3), (0, 2, 8), (0, 1, 7, 8, 30)):
            st = {"px": px, "gain": gain, "hold_age_s": age_bars * 900.0}
            v = T.dc15m_evaluate(_cfg(mh), bar, st, is_long)
            lv = T.dc15m_evaluate(_cfg(mh), lambda k, d=None: live_snapshot.get(k, d), dict(st), is_long)
            mism += int(v != lv)
            fires += int(v[0])
            held += int(v[2].startswith("MIN_HOLD"))
            if v[0]:
                assert gain < 0 and (age_bars >= mh or mh == 0)
            if v[2].startswith("MIN_HOLD"):
                assert 0 < mh and age_bars < mh and gain < 0
    assert mism == 0
    assert fires > 0 and held > 0, (sym, is_long, fires, held)


def test_min_hold_skip_equals_bar_gate():
    """Seconds (live, and the vector's wall-clock age) vs the vector's bar gate: one decision on every option/age."""
    for mh, bars in itertools.product((0, 1, 2, 4, 8, 12), range(0, 20)):
        class C:
            MIN_HOLD_BARS = mh
            BASE_TF = "15m"
        sec = T.dc15m_min_hold_refuses(_cfg(mh), bars * 900.0)[0]
        assert sec == MH.refuse_bars(bars, C, "CLOSE", T.DC15M_REASON, live=False)[0] == (0 < mh and bars < mh)
    assert T.dc15m_min_hold_refuses(_cfg(8), None) == (False, "AGE_UNKNOWN")
    assert not MH.is_exempt(T.DC15M_REASON) and not MH.is_exempt(T.DC15M_REASON, live=True)


def test_switch_off_never_fires_and_is_not_listed():
    assert T.dc15m_armed(lambda k, d: d) is False
    assert T.active_exits(lambda k, d: d) == []
    assert T.dc15m_armed(_cfg(0)) is True
    assert T.dc15m_armed(lambda k, d: {"DC15M_FORCE_EXIT_ENABLED": True, "DELTA_EXIT_ENABLED": False}.get(k, d)) is True


def test_exit_gate_books_armed_close_red_only():
    ev = {"type": "CLOSE", "reason": T.DC15M_REASON, "exit_reason": T.DC15M_REASON, "pnl_dollars": -3.0, "qty": 1.0, "exit_price": 10.0}
    assert NG.vector_exit_event_allowed(dict(ev), None, None) is False
    assert NG.vector_exit_event_allowed(dict(ev, mandatory_stop_kind=T.DC15M_MARKER), None, None) is True
    assert NG.vector_exit_event_allowed(dict(ev, reason="X", exit_reason="X", mandatory_stop_kind=T.DC15M_MARKER), None, None) is False
    assert T.dc15m_loss_close_allowed("DELTA_EXIT_DC15M_FLOOR_BREAK_g-1.20", True) is True
    assert T.dc15m_loss_close_allowed("DELTA_EXIT_DC15M_FLOOR_BREAK", False) is False
    assert T.dc15m_loss_close_allowed("FIXED_STOP", True) is False


@pytest.mark.parametrize("is_long", (True, False))
def test_stocks_vigilance_live_prev_channel_equals_vector_on_real_npz(is_long):
    """tradier_manage pre-twin site: level = snapshot dc_*4_15m_prev (prior closed bar) -> vigilance_dc4.fires; the
    vector: _vig_dc_lvl[i-1] (DC_PRIOR_BAR_CHANNEL) -> vigilance_dc4.breach with live_pnl_pct < 0."""
    npz, n = _load("AAPL")
    key = "dc_low4_15m" if is_long else "dc_high4_15m"
    lvl = npz[key].astype(float)
    hits = mism = 0
    for i in range(max(1, n - 3000), n):
        px = float(npz["_px"][i])
        for gain, tol in itertools.product((-0.5, 0.5), (0.1, 0.25, 0.5)):
            vec = gain < 0 and VIG.breach(is_long, px, float(lvl[i - 1]), tol)
            live_snapshot = {f"{key}_prev": float(lvl[i - 1]), key: float(lvl[i])}
            lv_lvl = live_snapshot.get(f"{key}_prev", 0.0) or live_snapshot[key]
            live = VIG.fires(is_long, px, lv_lvl, tol, gain)
            mism += int(vec != live)
            hits += int(vec)
    assert mism == 0 and hits > 0


def test_wiring_sources():
    ez = (ROOT / "ez_manage.py").read_text()
    assert "_lnx_twins.dc15m_evaluate(_dc15_c," in ez
    assert "_lnx_twins.dc15m_loss_close_allowed(reason," in ez
    assert "DC15M_FORCE_EXIT_BLOCKED" not in ez
    tr = (ROOT / "tradier_manage.py").read_text()
    assert "[_lnx_twins.dc15m_force_exit] if _lnx_twins.dc15m_armed(_gx_c)" in tr
    assert '"hold_age_s": _gw_age_s if isinstance(_gw_opened, datetime) else None' in tr
    assert "if _vig_dc4_mod.armed(_vg_get):" in tr and "_vig_dc4_mod.fires(is_long, _px, _vg_lvl, _vg_tol, _gain)" in tr
    assert "_vig_dc4_mod.block_expired(_age_h, _hrs)" in tr
    assert "_vig_dc4_mod.loss_close_allowed(reason," in tr and "_lnx_twins.dc15m_loss_close_allowed(reason," in tr
    eng = (ROOT / "v12_quick_engine.py").read_text()
    assert '"exit_reason": _dc15_reason, "bars_held": int(i - pos["entry_bar"]), "mandatory_stop_kind": "DC15M_FORCE_EXIT_ARMED"}' in eng
    assert "if _lnx.dc15m_force_exit(_gw_c, _dc15_bar," in eng and "_dc15_pre_bar = i" in eng
    # a refused discretionary CLOSE on a bar where the armed floor break fires becomes that DC15M close (live parity)
    assert 'event["dc15m_converted_from"] = str(event.get("exit_reason") or event.get("reason") or "")' in eng
    assert "if (not _acc) and _dc15_pre_bar >= 0 and _dc15_pre_bar == i and source == \"trades\"" in eng
    assert "if _f is not _lnx.dc15m_force_exit]" in eng  # booked once, in its own slot right after the frozen stops
    assert eng.index("if _lnx_dc15_on and pos is not None:") < eng.index("if _bge_on and pos is not None:")
    vpg = (ROOT / "vec_decisions" / "vec_parity_gates.py").read_text()
    assert '("DELTA_EXIT_DC15M_FLOOR_BREAK", "DC15M_FORCE_EXIT_ENABLED")' in vpg
    assert '("VIGILANCE_DC4_", "VIGILANCE_DC4_STOP_ARMED")' in vpg


def test_config_defaults_inert():
    import config_tradier
    import v12_quick_engine as V
    t = config_tradier.TradierConfig()
    q = V.QuickConfig()
    for c in (t, q):
        assert c.DC15M_FORCE_EXIT_ENABLED is False and c.VIGILANCE_DC4_STOP_ARMED is False
    assert t.VIGILANCE_BLOCK_HOURS == q.VIGILANCE_BLOCK_HOURS == 12.0
