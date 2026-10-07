"""Shared pure per-bar cores for the LIVE MTF compound-exit branches 2-4 (crypto).

2026-09-28 EXIT VECTORIZATION PARITY (user: "make sure MTF_ATR_TRAIL does not only test
fixed % but also dc_low4/high4_15m and mtf wt crosses as the exit signal"): faithful vec
twins of the remaining branches of ez_manage's MTF compound exit block, so sweeps can
test DC-band rejects (incl. the dc_low4/high4 4-bar channel via MTF_DC_REJECT_USE_DC4)
and MTF WT crosses as exit signals — not just the ATR-multiple trail
(vec_decisions/mtf_atr_trail_exit.py, branch 1).

LIVE source of truth (crypto): ez_manage.py MTF compound block —
    branch 2 MTF_DC_REJECT  ~47706-47723: LONG band=dc_high_{TF}; px>band arms
        ever_outside; afterwards px<band fires "MTF_DC_REJECT_{TF}_px{px:.6f}".
        SHORT mirror on dc_low_{TF}. With MTF_DC_REJECT_USE_DC4 (new knob, wired live
        same session) the band field is dc_high4_/dc_low4_{TF} and the reason carries
        a "_dc4" suffix.
    branch 3 MTF_BB_REJECT  ~47725-47755: LONG tag = high_{TF}>=bb_upper_{TF}-1e-9,
        fail = high<bb_upper; fire when fail AND any tag within LOOKBACK bars of the
        exit TF (LOOKBACK*timeframe_seconds(TF); 2026-09-28 USER FIX — was LOOKBACK*60
        seconds flat, structurally dead on 1h data, 0 live fires ever).
        SHORT mirror on low/bb_lower. high/low fall back to current price when absent.
    branch 4 MTF_GR_WT_EXIT ~47756-47773: wt1_{WT_TF} vs wt2_{WT_TF} against the
        position AND >=MIN_TFS of the (15m,1h,4h,D) ladder against (wt1!=0) →
        "MTF_GR_WT_EXIT_{WT_TF}_grTFs={n}". Live nests MTF_WT_CROSS_EXIT_ENABLED
        inside MTF_GR_EXIT_GATE_ENABLED — both must be True.

NOT vectorized (documented live-only orchestration):
    - account flz skip on branch 4 (ez_manage.py:47752 suicide-close guard)
    - tradier variants (time-based _mtf_dc_reject_step, W-ladder, structural_exit_
      permitted veto) — stocks live defaults are OFF (config_tradier), so tradier mode
      stays unwired in vec; wiring it without the structural veto would overfire.

CONFIG THRESHOLDS READ (crypto live defaults in parentheses):
    MTF_EXIT_USE_COMPOUND (True), MTF_DC_REJECT_EXIT_ENABLED (True),
    MTF_DC_REJECT_EXIT_TF ('1h'), MTF_DC_REJECT_USE_DC4 (False — NEW),
    MTF_BB_REJECT_EXIT_ENABLED (True), MTF_BB_REJECT_EXIT_TF ('1h'),
    MTF_BB_REJECT_EXIT_LOOKBACK (5), MTF_GR_EXIT_GATE_ENABLED (True),
    MTF_GR_EXIT_MIN_TFS (3), MTF_WT_CROSS_EXIT_ENABLED (True),
    MTF_WT_CROSS_EXIT_TF ('15m'; 'either' is inert live and in vec)
"""


def dc_reject_step(ever_outside, price, band, is_long):
    """Branch 2 pure step — returns (ever_outside_new, fire).

    Faithful to ez_manage.py:47708-47723: outside-band arms, re-cross back fires;
    band<=0 (missing TF field) is inert, exactly like live's safe_fetch 0."""
    if band <= 0 or price <= 0:
        return ever_outside, False
    if is_long:
        if price > band:
            return True, False
        if ever_outside and price < band:
            return ever_outside, True
    else:
        if price < band:
            return True, False
        if ever_outside and price > band:
            return ever_outside, True
    return ever_outside, False


def bb_reject_step(tag_ts_list, now_s, high, low, bb_upper, bb_lower, lookback_bars, is_long, tf_secs=60):
    """Branch 3 pure step — returns (new_tag_ts_list, fire).

    Faithful to ez_manage.py:47726-47755. tf_secs = seconds per exit-TF bar
    (mtf_exit_timing.timeframe_seconds). 2026-09-28 USER FIX: the original live
    window was lookback*60 SECONDS regardless of TF, which made the exit
    structurally dead on the default 1h TF (band data updates hourly; 0 live
    fires ever). Live and vec now both use lookback*tf_secs — LOOKBACK bars of
    the exit TF, identical to the stocks path (event_within_lookback)."""
    if is_long and bb_upper > 0:
        tag_now = high >= bb_upper - 1e-9
        fail_now = (high < bb_upper) and high > 0
    elif (not is_long) and bb_lower > 0:
        tag_now = low <= bb_lower + 1e-9 and low > 0
        fail_now = (low > bb_lower) and low > 0
    else:
        tag_now = False
        fail_now = False
    tags = list(tag_ts_list)
    if tag_now:
        tags.append(now_s)
    cutoff = now_s - lookback_bars * max(1, int(tf_secs))
    tags = [b for b in tags if b >= cutoff]
    return tags, bool(fail_now and len(tags) > 0)


def gr_wt_exit_fires(wt1_x, wt2_x, ladder_pairs, min_tfs, is_long):
    """Branch 4 pure predicate — returns (fire, against_count).

    Faithful to ez_manage.py:47757-47773: WT cross against on the exit TF AND
    >= min_tfs ladder TFs against (wt1 != 0 required per TF)."""
    against = (is_long and wt1_x < wt2_x) or ((not is_long) and wt1_x > wt2_x)
    count = 0
    for w1, w2 in ladder_pairs:
        if is_long and w1 < w2 and w1 != 0:
            count += 1
        elif (not is_long) and w1 > w2 and w1 != 0:
            count += 1
    return bool(against and count >= min_tfs), count
