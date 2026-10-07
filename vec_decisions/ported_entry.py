# -*- coding: utf-8 -*-
"""ported_entry — faithful numpy twins of ez_manage/tradier_manage entry-lifecycle switches.
Owned by the entry wiring agent (SWITCH_WIRING_GUIDE.md). Rules: IDENTICAL to live (same thresholds,
operators, TF; 15m floor, 3m/5m->15m), NO proxies, NO fabrication (BIBLE §19). Each switch block is
gated on its cfg value differing from the effective default and applies a REAL mask to the signal.
apply() is a pure passthrough when no switch is active (returns sig unchanged).

WORKLIST DISPOSITION (40 ENTRY switches, investigated one-by-one against the REAL live decision site):

WIRED (3) — genuine, live-invoked, NPZ-portable, and not already wired elsewhere in simulate_one:
  • FUNDING_GATE (family: _ENABLED / _LONG_MAX / _SHORT_MIN / _MTF_REQUIRED / _MTF_LONG_MAX_BULL_TFS /
        _MTF_SHORT_MAX_BEAR_TFS) — REAL live site ez_positions_quick.py:12718-12752 (crypto NEW-entry
        gate). CORRECTION: the earlier pass wrongly marked this VEC_UNSUPPORTED ("funding_rate not in the
        NPZ"). It IS in the NPZ — backtest_v8_precompute._inject_funding_oi writes funding_rate_15m + the
        unqualified alias funding_rate (verified on s1: len==n, all-nonzero). Live delegates to the
        SHARED module vec_paths.funding_gate.funding_block_mask — the *exact same* predicate
        (funding_long_veto/funding_short_veto) live calls, so live and vec can never drift. Wired here by
        calling that shared helper 1:1. NOTE: FUNDING_GATE_FILTER_TF has NO effect in the real path (the
        shared MTF vote uses a FIXED 15m/1h/4h/D set and never reads FILTER_TF) → NOT gated on; inventing
        a use for it would be fabrication. Stocks: funding_rate is zero-filled → helper returns zeros →
        naturally inert (stocks use the separate put/call FUNDING_GATE_*_TRADIER family, not this).
  • GR_FILTER_ALL_ENTRIES   — ez_manage.py:29940-29966 (LONG-only, crypto). Faithful vectorized twin
        already exists as vec_paths.gr_filter_vec.build_gr_filter_mask (same 11 inds/6 TFs, same
        MTF_GR_* knobs + sma_200_15m min_ind relaxation). It was NOT called from simulate_one (only
        the forbidden fabricated proxies at v12_quick_engine.py:17897 '# balanced' and :19867
        'entry_mask[0]^=True # guarantee', which live in unowned code). Wired here honestly.
  • LH_HL_FILTER_ENABLED (+ LH_HL_FILTER_TF_REQ param) — tradier_manage.py:27519/27877 (stocks).
        Faithful vectorized twin already exists as
        vec_decisions.check_entry_candidates_stocks__lh_hl_filter.check_lh_hl_filter_vec (shared pure
        predicate that also drives the scalar/live path). Imported at v12_quick_engine.py:82 but NEVER
        called. One block covers the family (_ENABLED + TF_REQ + MODE + DC_THRESHOLD_PCT + REQUIRE_BOTH).

VEC_UNSUPPORTED (5) — need data/state not in the frozen NPZ; NEVER proxied:
  • EXECUTE_NOW_SINGLE_GATE_ENFORCE — execute_now single-gate/portfolio enforcement state, not per-bar
        NPZ data (and its only site, ez_manage.py:59479, is a no-op stub anyway).
  • OPEN_RATE_BREAKER_ENABLED / OPEN_RATE_MAX — ez_manage.py:30300-30312 process-wide open-flood rate
        limiter over a wall-clock deque (_RECENT_OPEN_ATTEMPTS + time.time()); account/process state.
  • HTF_TREND_VETO_BYPASS_ENABLED — ez_manage.py:25293 bypass is driven by the live reason-string
        (HTF_TREND_VETO_BYPASS_REASONS) plus per-position gain/max_gain and an HTF score engine; none
        available at the vec entry-signal stage.
  • MTF_FILTER_STRONG_BUY_QUICK_BYPASS — ez_manage.py:30179 bypass keys entirely on the live
        reason/action strings ("STRONG_BUY"/"QUICK_OPEN"/... , _kill_act); not NPZ data.
  • HTF_GATE_D_MANDATORY / HTF_GATE_MIN_CONFIRMATIONS / HTF_GATE_SIGNALS_SMA200D — the only real site
        (ez_manage.py:37479-37484) is inside the RATIO_REBALANCE candidate-selection loop gated by
        RATIO_REBALANCE_APPLY_HTF_GATE, a portfolio-rebalance path — not the per-bar simulate_one entry
        signal. Applying it as a universal entry gate would diverge from live → would break parity.

SKIPPED (30 total incl. the above families' siblings) — no REAL live effect / already wired / absent:
  • BTC_BREAKOUT_ENTRY_ENABLED, DD_BOUNCE_ENABLED — their only "real" predicate lives in
        ez_manage._batch3_template_live_wiring(), a zero-arg function that is NEVER CALLED (a dead
        audit farm like GUIDE §10.0). Wiring it would fabricate deltas live cannot reproduce.
  • DC_BREAKOUT_TF_EXPANDED, WT_DC_DIRECT_TF_ENTRY — ABSENT: 0 occurrences in ez_manage AND
        tradier_manage. Nothing to port.
  • EMA_BLANKET_FILTER_ENABLED, EMA_BLANKET_FILTER_MIN_TFS — REAL (ez_manage.py:29911-29931) but the
        faithful vec twin (vec_decisions.wave4_families.ema_blanket_entry_gate) is ALREADY wired into
        simulate_one (v12_quick_engine.py:22130). Re-wiring here would double-apply.
  • WT_PERCENTILE_ENTRY_GATE_ENABLED — REAL (tradier_manage.py:13988) but ALREADY wired via
        vec_decisions.grey_wire_entries.wt_percentile_entry_gate (v12_quick_engine.py:22130).
  • RZ_BREAKOUT_BAND — ALREADY wired: it is a param of the RZ_BREAKOUT_ENTRY family already ported in
        v12_quick_engine.py:9854 (_rz_breakout_fires) — the live comment at ez_manage.py:40635 says so.
  • WT_AGAINST_FILTER_ENABLED — its only REAL site is tradier_manage._v12_b11_reentry_allowed:16420,
        a REENTRY-family sub-condition (gated by REENTRY_B11_MFI_UP_ENABLED), not an ENTRY-stage gate.
  • ENTRY_PRIMARY_TF, GR_FILTER_VEC_ENABLED, GR_FILTER_VEC_MIN_TFS, HAIKU_ENTRY_GATE_ENABLED,
        HTF_GATE_APPLY_TO_OPEN, HTF_GATE_BYPASS_RZ, V8_ENTRY_ENGINE_DC_ENABLED, V8_ENTRY_ENGINE_WT_ENABLED,
        WT_15M_CROSS_ENTRY_ENABLED, WT_COMPOSITE_ENTRY_BLOCK/GOOD/OK/STRONG, WT_DIV_ENTRY_GATE_ENABLED,
        WT_EXHAUST_ENTRY_GATE_ENABLED, WT_PERCENTILE_ENTRY_OB_D, WT_PERCENTILE_ENTRY_OS_D — every one of
        these exists ONLY as a no-op stub-farm entry in BOTH ez_manage (region ~58500-59230:
        `_=getattr(config,X)` / `if ...: _ = 1  # BATCH`) AND tradier_manage. No real live decision →
        honest no-op (GUIDE §8.3, §10.0). Wiring would fabricate.
"""
import numpy as np


def apply(npz, n, is_long, cfg, sig, _safe, close, _entry_filter_masks=None):
    # === GR_FILTER_ALL_ENTRIES — ez_manage.py:29940-29966 (crypto, LONG-only) ===================
    # Live: every fresh LONG OPEN/ENTRY must pass mtf_live_evaluator.gr_filter_pass (breakout min7,
    # min_ind relaxed to 4 when close>sma_200_15m*1.05). Faithful vectorized twin already exists.
    # Gate on the switch AND is_long (live is LONG-only). SHORT / stocks: live has only a stub → inert.
    if is_long and bool(getattr(cfg, 'GR_FILTER_ALL_ENTRIES', False)):
        try:
            import vec_paths.gr_filter_vec as _grv
            _gr_pass = _grv.build_gr_filter_mask(npz, n, is_long, 'crypto', cfg)
            _gr_pass = np.asarray(_gr_pass, dtype=bool)
            if _gr_pass.shape == sig.shape:
                sig = sig & _gr_pass
                if _entry_filter_masks is not None:
                    _entry_filter_masks.append(_gr_pass)
        except Exception:
            pass
    # === LH_HL_FILTER (family: _ENABLED / _TF_REQ / _MODE / _DC_THRESHOLD_PCT / _REQUIRE_BOTH) ====
    # tradier_manage.py:27519/27877 (stocks): block LONG on lower-highs, SHORT on higher-lows across
    # 1h/4h. Shared pure predicate drives scalar+vec; the vectorized blocking mask already exists.
    if bool(getattr(cfg, 'LH_HL_FILTER_ENABLED', False)):
        try:
            import vec_decisions.check_entry_candidates_stocks__lh_hl_filter as _lhf
            _h1h = _safe(npz, 'high_1h', n, 0.0); _h1hp = _safe(npz, 'high_1h_prev', n, 0.0)
            _h4h = _safe(npz, 'high_4h', n, 0.0); _h4hp = _safe(npz, 'high_4h_prev', n, 0.0)
            _l1h = _safe(npz, 'low_1h', n, 0.0); _l1hp = _safe(npz, 'low_1h_prev', n, 0.0)
            _l4h = _safe(npz, 'low_4h', n, 0.0); _l4hp = _safe(npz, 'low_4h_prev', n, 0.0)
            _dch1h = _safe(npz, 'dc_high_1h', n, 0.0); _dch4h = _safe(npz, 'dc_high_4h', n, 0.0)
            _dcl1h = _safe(npz, 'dc_low_1h', n, 0.0); _dcl4h = _safe(npz, 'dc_low_4h', n, 0.0)
            _lh_block = _lhf.check_lh_hl_filter_vec(cfg, _h1h, _h1hp, _h4h, _h4hp, _l1h, _l1hp,
                                                    _l4h, _l4hp, _dch1h, _dch4h, _dcl1h, _dcl4h, is_long)
            _lh_block = np.asarray(_lh_block, dtype=bool)
            if _lh_block.shape == sig.shape:
                sig = sig & ~_lh_block
                if _entry_filter_masks is not None:
                    _entry_filter_masks.append(~_lh_block)
        except Exception:
            pass
    # === FUNDING_GATE (family: _ENABLED / _LONG_MAX / _SHORT_MIN / _MTF_REQUIRED / _MTF_*_TFS) ======
    # REAL live: ez_positions_quick.py:12718-12752 (crypto NEW-entry gate). funding_rate IS in the NPZ
    # (funding_rate_15m + alias funding_rate, injected by backtest_v8_precompute._inject_funding_oi).
    # Delegate to the SHARED predicate live itself calls (vec_paths.funding_gate) so the vec twin can
    # never drift from live. funding_block_mask self-guards on FUNDING_GATE_ENABLED and returns zeros
    # when funding_rate is absent/zero (stocks) -> naturally inert. Master enable = FUNDING_GATE_ENABLED
    # (live's own gate condition); the swept params (LONG_MAX/SHORT_MIN/MTF_REQUIRED/MTF_*_TFS) flow into
    # the shared predicate and move the veto mask exactly as they do live.
    if bool(getattr(cfg, 'FUNDING_GATE_ENABLED', True)):
        try:
            import vec_paths.funding_gate as _fg
            _fg_block = np.asarray(_fg.funding_block_mask(npz, n, is_long, cfg), dtype=bool)
            if _fg_block.shape == sig.shape:
                sig = sig & ~_fg_block
                if _entry_filter_masks is not None:
                    _entry_filter_masks.append(~_fg_block)
        except Exception:
            pass
    # NOTE 2026-10-02: REQUIRE-style entry gates (DIV_ENTRY_GATE et al) do NOT belong here:
    # rally/watchdog/block pathways bypass entry_sig, so an entry_sig mask binds nothing (proven
    # 0.00 on UNIUSDC). They belong as per-bar vetoes at the _open choke point (v12:12744, next to
    # the OVERTRADE_GUARD/QTA_CT_BLOCK vetoes) with masks precomputed once. See WIRING_SPEC.
    # === lane entry-crypto (2026-10-03, staged): veto twins also ANDed here so
    # REENTRY_APPLY_ENTRY_GATES sees the same final stack; the BINDING veto is
    # the precomputed _open-choke block (open_choke_veto.diff). Sources ORed.
    # --- ENTRY_VET (ez_manage.check_entry_vetting; crypto only) ---
    if str(getattr(cfg, 'MODE', 'crypto')) != 'tradier':
        try:
            import vec_decisions.entry_vet_gate as _evg
            _ev_ok = _evg.vet_pass_mask(npz, n, is_long, cfg, close, _safe)
            if _ev_ok is not None:
                _ev_ok = np.asarray(_ev_ok, dtype=bool)
                if _ev_ok.shape == sig.shape:
                    sig = sig & _ev_ok
                    if _entry_filter_masks is not None:
                        _entry_filter_masks.append(_ev_ok)
        except Exception:
            pass
    # ENTRY_VET RSI-T55 twin REMOVED 2026-10-06 (USER: never approved; v12 choke site + RSI_ENTRY_VETO_ENABLED field already gone).
    # A stale vec_decisions/entry_vet_rsi_t55.py left on a server read the missing field with default True and vetoed every crypto LONG.
    # --- STDEV_MACRO entry veto (stdev_macro.entry_veto; both venues) ---
    try:
        import vec_decisions.stdev_macro_entry as _sme
        _sm_blk = _sme.entry_veto_mask(npz, n, is_long, cfg, _safe)
        if _sm_blk is not None:
            _sm_blk = np.asarray(_sm_blk, dtype=bool)
            if _sm_blk.shape == sig.shape:
                sig = sig & ~_sm_blk
                if _entry_filter_masks is not None:
                    _entry_filter_masks.append(~_sm_blk)
    except Exception:
        pass
    # --- WT_DIV R-G7 gate (rate() BOYCOTT twin; both venues share rate()) ---
    try:
        import vec_decisions.wt_div_entry_gate as _wdv
        _dv_blk = _wdv.div_block_mask(npz, n, is_long, cfg, _safe)
        if _dv_blk is not None:
            _dv_blk = np.asarray(_dv_blk, dtype=bool)
            if _dv_blk.shape == sig.shape:
                sig = sig & ~_dv_blk
                if _entry_filter_masks is not None:
                    _entry_filter_masks.append(~_dv_blk)
    except Exception:
        pass
    # --- LR_BAND_ENTRY source (ez_manage:40770; OR — live returns a Signal) ---
    try:
        import vec_decisions.lr_band_entry as _lbe
        _lb_fire = _lbe.entry_fire_mask(npz, n, is_long, cfg, _safe)
        if _lb_fire is not None:
            _lb_fire = np.asarray(_lb_fire, dtype=bool)
            if _lb_fire.shape == sig.shape:
                sig = sig | _lb_fire
    except Exception:
        pass
    # --- GR_HTF_DIRECT_ENTRY source (golden_rule_htf.score_entry_htf; OR) ---
    # Dead at defaults (max score 3x2=6 << 23); fires only via SCORE_MIN
    # override — transcribe, don't rescale. Double-size mask is returned for
    # the sizing lane via cfg-attached stash (no sizing change in this lane).
    try:
        import vec_decisions.gr_htf_direct_entry as _grd
        _gr_fire, _gr_dbl = _grd.gr_direct_masks(npz, n, is_long, cfg, close)
        if _gr_fire is not None:
            _gr_fire = np.asarray(_gr_fire, dtype=bool)
            if _gr_fire.shape == sig.shape:
                sig = sig | _gr_fire
        if _gr_dbl is not None:
            try:
                cfg.GR_HTF_DIRECT_DOUBLE_BARS = np.asarray(_gr_dbl, dtype=bool)
            except Exception:
                pass
    except Exception:
        pass
    return sig
