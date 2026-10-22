"""Live twin of the SCALE_DIRECTION law: it may refuse a wrong-way PARTIAL scale, never an exit.

USER 2026-10-22 ordered the sequence: trading scripts first, then configs/QuickConfig, then the TEMPLATE rows
through tools/v15_switch_add.py. This covers the trading-script half — the gate inside execute_now on BOTH
venues, which is the one and only order gate (CLAUDE.md EXECUTE_NOW IS THE ONLY GATE).
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from live_twins import scale_direction_live as SDL  # noqa: E402


def _get(**kw):
    def g(k, d=None):
        return kw.get(k, d)
    return g


ON = _get(SCALE_DIRECTION_LAW_ENABLED=True)
OFF = _get(SCALE_DIRECTION_LAW_ENABLED=False)


def setup_function(_fn):
    SDL._RING.clear()


def _fill(symbol, closes, start=0.0):
    """One sample per 15m bucket, oldest first."""
    for n, c in enumerate(closes):
        SDL.note_price(symbol, c, start + n * SDL.BAR_SEC)


# ── the ring ──────────────────────────────────────────────────────────────────────────────────────
def test_ring_keeps_one_close_per_bar_bucket():
    SDL.note_price("X", 100.0, 0.0)
    SDL.note_price("X", 101.0, 100.0)   # same bucket -> replaces (running close)
    SDL.note_price("X", 102.0, 900.0)   # next bucket -> appends
    assert SDL._RING["X"]["closes"] == [101.0, 102.0]


def test_ref_price_reads_the_requested_depth():
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    assert SDL.ref_price("X", 4) == 100.0
    assert SDL.ref_price("X", 1) == 103.0


def test_ref_price_refuses_a_depth_the_ring_cannot_serve(monkeypatch):
    _fill("X", [100.0, 101.0])
    assert SDL.ref_price("X", 4) == 0.0, "default: say nothing rather than judge off a shallow ring"
    monkeypatch.setenv("SCALE_DIRECTION_LIVE_REQUIRE_FULL_LOOKBACK", "0")
    assert SDL.ref_price("X", 4) == 100.0, "opt-in: deepest available bar (looser than the vector, never stricter)"


def test_ring_is_bounded():
    _fill("X", [float(i) for i in range(SDL._RING_MAX + 40)])
    assert len(SDL._RING["X"]["closes"]) == SDL._RING_MAX


# ── what counts as a partial scale ────────────────────────────────────────────────────────────────
def test_classify_open_on_a_flat_key_is_not_a_scale():
    assert SDL.classify("OPEN", "BUY", "LONG", 10.0, 0.0, False, False) == ""


def test_classify_full_close_and_hedge_are_never_scales():
    assert SDL.classify("CLOSE", "SELL", "LONG", 10.0, 10.0, True, False) == ""
    assert SDL.classify("REDUCE", "SELL", "LONG", 3.0, 10.0, False, True) == ""


def test_classify_a_cut_to_dust_is_an_exit():
    """The engine's own rule: a remainder under 10% of the position is flat, so it is an exit."""
    assert SDL.classify("REDUCE", "SELL", "LONG", 9.5, 10.0, False, False) == ""
    assert SDL.classify("REDUCE", "SELL", "LONG", 3.0, 10.0, False, False) == "REDUCE"


def test_classify_augment_both_sides():
    assert SDL.classify("AUGMENT", "BUY", "LONG", 3.0, 10.0, False, False) == "AUGMENT"
    assert SDL.classify("AUGMENT", "SELL", "SHORT", 3.0, 10.0, False, False) == "AUGMENT"
    assert SDL.classify("REDUCE", "BUY", "SHORT", 3.0, 10.0, False, False) == "REDUCE"


def test_protective_reasons_are_exempt():
    for r in ("FIXED_STOP breach", "VIGILANCE_DC4_15m_STOP", "LIQUIDATION", "exit_guarantee FORCE", "EMERGENCY_KILL"):
        assert SDL.is_protective(r, "REDUCE") is True
    assert SDL.is_protective("PPL_TP_g3.05%", "REDUCE") is False


# ── the decision ──────────────────────────────────────────────────────────────────────────────────
def test_off_by_default_lets_everything_through():
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    assert SDL.blocked(OFF, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0) == ""


def test_long_partial_reduce_refused_while_price_rises():
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    why = SDL.blocked(ON, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                      side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0)
    assert why and "augment territory" in why and "X LONG REDUCE" in why


def test_long_partial_reduce_allowed_while_price_falls():
    _fill("X", [104.0, 103.0, 102.0, 101.0, 100.0])
    assert SDL.blocked(ON, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=100.0) == ""


def test_long_augment_refused_while_price_falls():
    _fill("X", [104.0, 103.0, 102.0, 101.0, 100.0])
    why = SDL.blocked(ON, symbol="X", position_side="LONG", action="AUGMENT", reason="UAG_LADDER",
                      side="BUY", quantity=3.0, original_positionAmt=10.0, price=100.0)
    assert why and "reduce territory" in why


def test_short_is_the_mirror():
    _fill("S", [104.0, 103.0, 102.0, 101.0, 100.0])  # falling = favourable for a short
    assert SDL.blocked(ON, symbol="S", position_side="SHORT", action="REDUCE", reason="PPL_TP",
                       side="BUY", quantity=3.0, original_positionAmt=10.0, price=100.0)
    assert SDL.blocked(ON, symbol="S", position_side="SHORT", action="AUGMENT", reason="UAG",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=100.0) == ""


def test_an_exit_is_never_refused_however_fast_price_rises():
    _fill("X", [100.0, 110.0, 120.0, 130.0, 140.0])
    for kw in ({"is_full_close": True}, {"is_hedge": True}, {"quantity": 9.9}):
        base = dict(symbol="X", position_side="LONG", action="REDUCE", reason="TECHNICAL_DC",
                    side="SELL", quantity=3.0, original_positionAmt=10.0, price=140.0)
        base.update(kw)
        assert SDL.blocked(ON, **base) == "", kw
    assert SDL.blocked(ON, symbol="X", position_side="LONG", action="REDUCE", reason="FIXED_STOP breach",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=140.0) == ""


def test_fails_open_without_enough_history_or_a_usable_price():
    SDL.note_price("X", 100.0, 0.0)
    assert SDL.blocked(ON, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0) == ""
    _fill("Y", [100.0, 101.0, 102.0, 103.0, 104.0])
    assert SDL.blocked(ON, symbol="Y", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=0.0) == ""


def test_env_kill_switch(monkeypatch):
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    monkeypatch.setenv("SCALE_DIRECTION_LIVE", "0")
    assert SDL.blocked(ON, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0) == ""


def test_a_broken_getter_can_only_ever_allow():
    def boom(_k, _d=None):
        raise RuntimeError("per-sym store down")
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    assert SDL.blocked(boom, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0) == ""


def test_absent_knobs_fall_back_to_the_law_defaults_not_to_falsy_none():
    """A per-sym store that returns None for a knob must not turn a True default into False."""
    g = _get(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE=None,
             SCALE_DIRECTION_LOOKBACK_BARS=None, SCALE_DIRECTION_BAND_PCT=None)
    assert SDL._flag(g, "SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE", True) is True
    assert SDL._flag(g, "SCALE_DIRECTION_BLOCK_AUGMENT_ON_ADVERSE", True) is True
    assert SDL._f(g, "SCALE_DIRECTION_LOOKBACK_BARS", 4.0) == 4.0
    assert SDL._f(g, "SCALE_DIRECTION_BAND_PCT", 0.0) == 0.0
    assert SDL._flag(_get(SCALE_DIRECTION_LAW_ENABLED="true"), "SCALE_DIRECTION_LAW_ENABLED", False) is True
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    assert SDL.blocked(g, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0)


def test_exit_chain_exemption_is_the_same_switch_on_both_sides():
    """The vector exempts the exit chain's partial leg under SCALE_DIRECTION_EXEMPT_EXIT_CHAIN; live mirrors it."""
    _fill("X", [100.0, 101.0, 102.0, 103.0, 104.0])
    exempt = _get(SCALE_DIRECTION_LAW_ENABLED=True)                                     # default True
    covered = _get(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_EXEMPT_EXIT_CHAIN=False)
    kw = dict(symbol="X", position_side="LONG", action="REDUCE", reason="WT_CROSS_EXIT_1h_bear",
              side="SELL", quantity=3.0, original_positionAmt=10.0, price=104.0)
    assert SDL.blocked(exempt, **kw) == ""
    assert SDL.blocked(covered, **kw), "with the exemption off the law must cover the exit chain"
    assert SDL.is_exit_chain("STDEV_REJECT_EXIT") and not SDL.is_exit_chain("PPL_TP_g3.05%")


def test_per_sym_knobs_bind_through_the_shim():
    _fill("X", [100.0, 100.2, 100.4, 100.6, 100.8])   # +0.8% over 4 bars
    tight = _get(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BAND_PCT=2.0)
    assert SDL.blocked(tight, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=100.8) == ""
    half = _get(SCALE_DIRECTION_LAW_ENABLED=True, SCALE_DIRECTION_BLOCK_REDUCE_ON_FAVOURABLE=False)
    assert SDL.blocked(half, symbol="X", position_side="LONG", action="REDUCE", reason="PPL_TP",
                       side="SELL", quantity=3.0, original_positionAmt=10.0, price=100.8) == ""


# ── the wiring, in both trading scripts ───────────────────────────────────────────────────────────
def test_both_trading_scripts_gate_inside_execute_now():
    for name, getter in (("ez_manage.py", "_psym_get(symbol, position_side"), ("tradier_manage.py", "_cfg(_k, _d, account_key, symbol, position_side)")):
        src = (ROOT / name).read_text()
        i = src.index("async def execute_now")
        j = src.index("_execute_now_inner(", i)
        head = src[i:j]
        assert "from live_twins import scale_direction_live as _sdl" in head, f"{name}: gate not in execute_now"
        assert "_sdl.blocked(" in head, name
        assert getter in head, f"{name}: the gate must resolve per sym_side"
        assert 'return f"BLOCKED_SCALE_DIRECTION' in head, name


def test_both_trading_scripts_feed_the_close_ring_off_the_price_path():
    for name in ("ez_manage.py", "tradier_manage.py"):
        src = (ROOT / name).read_text()
        assert "scale_direction_live as _sdl_feed" in src, f"{name}: ring is never fed"
        assert "_sdl_feed.note_price(" in src, name


def test_the_live_faithful_verifier_mirrors_the_gate_at_both_seams():
    """backtest_v12_engine REPLACES execute_now with a seam, so a gate added only to the real execute_now is
    invisible to the LIVE_DELTA column — the same trap that made the KINDERGARTEN/EMA veto need a mirror."""
    src = (ROOT / "backtest_v12_engine.py").read_text()
    assert src.count("scale_direction_live as _sdl_seam") == 2, "both seams (crypto + stocks) must mirror it"
    assert src.count('return f"BLOCKED_SCALE_DIRECTION') == 2
    assert "ez_manage._psym_get(" in src and "tradier_manage._cfg(" in src
    c = src.index("async def _v8_execute_now_crypto")
    assert src.index("_sdl_seam", c) < src.index("return await _crypto_eta(", c), "crypto seam: gate before the fill"


def test_the_gate_runs_before_the_order_and_returns_without_placing_it():
    """A refusal must short-circuit — never place and then complain."""
    for name in ("ez_manage.py", "tradier_manage.py"):
        src = (ROOT / name).read_text()
        i = src.index("async def execute_now")
        blocked_at = src.index('return f"BLOCKED_SCALE_DIRECTION', i)
        inner_at = src.index("_res = await self._execute_now_inner(", i)
        assert blocked_at < inner_at, f"{name}: the gate must sit ABOVE the order call"
