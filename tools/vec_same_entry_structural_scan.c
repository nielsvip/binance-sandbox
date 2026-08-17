#include <math.h>
#include <stdint.h>
#include <string.h>

#define ACCOUNT_EQUITY 10000.0
#define BASE_UNIT 2000.0
#define CAPACITY 16000.0

typedef struct {
    double capital_return_pct;
    double bh_capital_return_pct;
    double exposure_weighted_tim_pct;
    double binary_tim_pct;
    double max_drawdown_account_pct;
    double minimum_account_equity_usd;
    double peak_post_fill_notional_usd;
    double requested_notional_usd;
    double filled_notional_usd;
    double normal_exit_pnl_usd;
    double emergency_exit_pnl_usd;
    int insolvent;
    int entry_capacity_breach;
    int signals;
    int rejected_profit;
    int exit_fills;
    int normal_exit_fills;
    int emergency_exit_fills;
    int entry_fills;
    int ladder_reentries;
    int reclaim_reentries;
    int clamp_count;
    int future_htf_count;
    int bars_flat_beyond_reclaim;
    int dc4h_entry_blocks;
    int dc4h_safety_exit_fills;
    int rows;
} StructuralScanMetrics;

static double dmin(double a, double b) { return a < b ? a : b; }
static double dmax(double a, double b) { return a > b ? a : b; }

int vec_same_entry_structural_scan(
    int n, int left, int right, int side, int semantics,
    const int64_t *ts,
    const double *open_, const double *high, const double *low,
    const double *close, const double *entry_mult,
    const double *dc4h_level, int enforce_dc4h,
    const uint8_t *arm_event, const int64_t *arm_source,
    const double *arm_high, const double *arm_low, const double *arm_close,
    const double *arm_wt, const double *arm_atr,
    const uint8_t *confirm_event, const int64_t *confirm_source,
    const double *confirm_high, const double *confirm_low,
    const double *confirm_close, const double *confirm_wt,
    double rebound_atr, int lookback, int max_wait,
    int confirmation_mode, int confirmation_bars,
    int arm_break_mode, double arm_break_threshold,
    int emergency_mask, double emergency_adverse_atr,
    double emergency_adverse_stdev, int emergency_continued_bars,
    double profit_gate_pct, double commission, double slippage,
    StructuralScanMetrics *out
) {
    if (!out || n <= 0 || left < 0 || right > n || right-left < 100 ||
        (side != 1 && side != -1) || lookback < 2 || lookback > 10 ||
        max_wait < 3 || confirmation_mode < 0 || confirmation_mode > 3 ||
        arm_break_mode < 0 || arm_break_mode > 3 ||
        arm_break_threshold < 0.0 ||
        confirmation_bars < 1 || confirmation_bars > 3) return 1;
    memset(out, 0, sizeof(*out));

    double cash = ACCOUNT_EQUITY, qty = 0.0, avg_entry = NAN;
    double last_exit = NAN, reclaim_level = NAN, prior_exit_notional = 0.0;
    int gap_seen = 0, exit_fill_row = -1;
    int pending = 0, pending_abs = 0, pending_signal = -1;
    double pending_target = 0.0, pending_ref = NAN;
    double peak_eq = ACCOUNT_EQUITY, min_eq = ACCOUNT_EQUITY;
    double weighted = 0.0, held = 0.0, peak_post = 0.0;

    /* Independent completed-bar histories; same-TF ARM/CONFIRM is legal. */
    double ah[10], al[10], ac[10], aw[10];
    int arm_count = 0, arm_slot = 0;
    double prev_arm_low = NAN, prev_arm_high = NAN;
    int have_confirm = 0;
    double prev_ch = NAN, prev_cl = NAN, prev_cc = NAN, prev_cw = NAN;

    /* Structural state: 0 trend, 1 wait rebound, 2 wait confirmation. */
    int phase = 0, just_armed = 0, wait = 0;
    int64_t rebound_source = 0;
    double state_arm_atr = NAN, state_arm_close = NAN, state_arm_stdev = NAN;
    double price_anchor = NAN, wt_anchor = NAN;
    double damage = NAN, rebound_price = NAN, rebound_wt = NAN;
    int confirmation_streak = 0, continued_count = 0;
    int pending_emergency = 0, pending_dc4h_safety = 0;

    for (int i=left; i<right; i++) {
        const double op = open_[i], cp = close[i];

        if (pending) {
            if (pending_signal + 1 != i) return 2;
            if (pending == 2 && qty > 0.0) {
                double px = op * (1.0 - side * slippage);
                double notional = qty * px;
                double realized = side*qty*(px-avg_entry)
                    - commission*(qty*avg_entry+notional);
                cash += side * (notional - side * commission * notional);
                if (pending_dc4h_safety) {
                    prior_exit_notional = 0.0;
                    last_exit = NAN;
                    reclaim_level = NAN;
                } else {
                    prior_exit_notional = dmin(CAPACITY, notional);
                    last_exit = px;
                    reclaim_level = side > 0 ? dmax(px, pending_ref)
                                             : dmin(px, pending_ref);
                }
                qty = 0.0; avg_entry = NAN; gap_seen = 0;
                exit_fill_row = i; out->exit_fills++;
                if (pending_emergency) {
                    out->emergency_exit_fills++;
                    out->emergency_exit_pnl_usd += realized;
                    if (pending_dc4h_safety)
                        out->dc4h_safety_exit_fills++;
                } else {
                    out->normal_exit_fills++;
                    out->normal_exit_pnl_usd += realized;
                }
                pending_emergency = 0;
                pending_dc4h_safety = 0;
            } else if (pending == 1 || pending == 3) {
                double px = op * (1.0 + side * slippage);
                int valid_dc4h = isfinite(dc4h_level[i]) && dc4h_level[i] > 0.0;
                int entry_breach = side > 0 ? px <= dc4h_level[i]
                                            : px >= dc4h_level[i];
                if (enforce_dc4h && (!valid_dc4h || entry_breach)) {
                    out->dc4h_entry_blocks++;
                    pending = 0;
                    continue;
                }
                double current = qty * px;
                double want = pending_abs ? dmax(0.0, pending_target-current)
                                          : pending_target;
                double actual = dmin(want, dmax(0.0, CAPACITY-current));
                out->requested_notional_usd += dmax(0.0, want);
                out->filled_notional_usd += actual;
                out->clamp_count += (actual + 1e-9 < want);
                if (actual > 0.0) {
                    double add_qty = actual / px, new_qty = qty + add_qty;
                    avg_entry = (!isfinite(avg_entry) || qty <= 0.0)
                        ? px : (avg_entry*qty + px*add_qty)/new_qty;
                    cash -= side * actual + commission * actual;
                    qty = new_qty; out->entry_fills++;
                    peak_post = dmax(peak_post, qty*px);
                    if (pending == 3) out->ladder_reentries++;
                }
            }
            pending = 0;
        }

        /* Mandatory resting reclaim before any discretionary signal. */
        if (qty <= 0.0 && i > exit_fill_row && exit_fill_row >= left &&
            isfinite(reclaim_level)) {
            double raw = NAN;
            if (side > 0) {
                if (op >= reclaim_level) raw = op;
                else if (high[i] >= reclaim_level) raw = reclaim_level;
            } else {
                if (op <= reclaim_level) raw = op;
                else if (low[i] <= reclaim_level) raw = reclaim_level;
            }
            if (isfinite(raw)) {
                double px = raw * (1.0 + side*slippage);
                int valid_dc4h = isfinite(dc4h_level[i]) && dc4h_level[i] > 0.0;
                int entry_breach = side > 0 ? px <= dc4h_level[i]
                                            : px >= dc4h_level[i];
                if (enforce_dc4h && (!valid_dc4h || entry_breach)) {
                    out->dc4h_entry_blocks++;
                    goto after_mandatory_reclaim;
                }
                double target = dmax(BASE_UNIT, prior_exit_notional);
                double actual = dmin(target, CAPACITY);
                out->requested_notional_usd += target;
                out->filled_notional_usd += actual;
                out->clamp_count += (actual + 1e-9 < target);
                cash -= side * actual + commission * actual;
                qty = actual/px; avg_entry = px;
                out->entry_fills++; out->reclaim_reentries++;
                peak_post = dmax(peak_post, qty*px);
                last_exit = NAN; reclaim_level = NAN;
            }
        }
after_mandatory_reclaim:

        double equity = cash + side*qty*cp;
        peak_eq = dmax(peak_eq, equity); min_eq = dmin(min_eq, equity);
        if (peak_eq > 0.0)
            out->max_drawdown_account_pct = dmax(
                out->max_drawdown_account_pct,
                100.0*(peak_eq-equity)/peak_eq
            );
        weighted += dmin(CAPACITY, qty*cp)/CAPACITY;
        held += qty > 1e-12;

        int active = qty > 1e-12;
        int signal = 0;
        double signal_ref = NAN;

        /* Live TRB/TRC owns this close before every selected exit path. */
        int valid_dc4h = isfinite(dc4h_level[i]) && dc4h_level[i] > 0.0;
        int held_breach = valid_dc4h &&
            (side > 0 ? cp <= dc4h_level[i] : cp >= dc4h_level[i]);
        if (enforce_dc4h && active && held_breach && i+1 < right) {
            out->signals++;
            pending=2; pending_signal=i; pending_ref=dc4h_level[i];
            pending_emergency=1; pending_dc4h_safety=1;
            continue;
        }

        if (arm_event[i]) {
            if (arm_source[i] > ts[i]) out->future_htf_count++;
            if (!active) {
                phase=0; just_armed=0;
            } else if (phase == 0 && arm_count >= lookback &&
                       isfinite(prev_arm_low) && isfinite(prev_arm_high)) {
                double prior_mean=0.0, prior_stdev=0.0;
                double prior_support=side > 0 ? INFINITY : -INFINITY;
                for (int k=0; k<lookback; k++) {
                    int idx=(arm_slot-1-k+10)%10;
                    prior_mean += ac[idx];
                    prior_support = side > 0
                        ? dmin(prior_support, al[idx])
                        : dmax(prior_support, ah[idx]);
                }
                prior_mean /= lookback;
                for (int k=0; k<lookback; k++) {
                    int idx=(arm_slot-1-k+10)%10;
                    double delta=ac[idx]-prior_mean;
                    prior_stdev += delta*delta;
                }
                prior_stdev=sqrt(prior_stdev/lookback);
                int broke=0;
                if (side > 0) {
                    if (arm_break_mode==1)
                        broke=arm_low[i] < prev_arm_low &&
                            arm_close[i] < ac[(arm_slot-1+10)%10]
                                - arm_break_threshold*arm_atr[i];
                    else if (arm_break_mode==2)
                        broke=arm_low[i] < prev_arm_low &&
                            arm_close[i] < prior_mean
                                - arm_break_threshold*prior_stdev;
                    else if (arm_break_mode==3)
                        broke=arm_close[i] < prior_support
                            - arm_break_threshold*arm_atr[i];
                    else
                        broke=arm_low[i] < prev_arm_low &&
                            arm_close[i] < prev_arm_low;
                } else {
                    if (arm_break_mode==1)
                        broke=arm_high[i] > prev_arm_high &&
                            arm_close[i] > ac[(arm_slot-1+10)%10]
                                + arm_break_threshold*arm_atr[i];
                    else if (arm_break_mode==2)
                        broke=arm_high[i] > prev_arm_high &&
                            arm_close[i] > prior_mean
                                + arm_break_threshold*prior_stdev;
                    else if (arm_break_mode==3)
                        broke=arm_close[i] > prior_support
                            + arm_break_threshold*arm_atr[i];
                    else
                        broke=arm_high[i] > prev_arm_high &&
                            arm_close[i] > prev_arm_high;
                }
                if (broke) {
                    price_anchor = side > 0 ? -INFINITY : INFINITY;
                    wt_anchor = side > 0 ? -INFINITY : INFINITY;
                    for (int k=0; k<lookback; k++) {
                        int idx = (arm_slot - 1 - k + 10) % 10;
                        price_anchor = side > 0 ? dmax(price_anchor, ah[idx])
                                                : dmin(price_anchor, al[idx]);
                        wt_anchor = side > 0 ? dmax(wt_anchor, aw[idx])
                                             : dmin(wt_anchor, aw[idx]);
                    }
                    phase=1; just_armed=1;
                    wait=0; state_arm_atr=arm_atr[i];
                    state_arm_close=arm_close[i]; rebound_source=0;
                    confirmation_streak=0; continued_count=0;
                    state_arm_stdev=prior_stdev;
                }
            }
            ah[arm_slot]=arm_high[i]; al[arm_slot]=arm_low[i];
            ac[arm_slot]=arm_close[i]; aw[arm_slot]=arm_wt[i];
            arm_slot=(arm_slot+1)%10; if (arm_count<10) arm_count++;
            prev_arm_low=arm_low[i]; prev_arm_high=arm_high[i];
        }

        if (confirm_event[i]) {
            if (confirm_source[i] > ts[i]) out->future_htf_count++;
            if (!active) {
                phase=0; just_armed=0;
            } else if (phase != 0) {
                if (just_armed) {
                    just_armed=0;
                    damage = side > 0 ? confirm_low[i] : confirm_high[i];
                    rebound_price = side > 0 ? -INFINITY : INFINITY;
                    rebound_wt = side > 0 ? -INFINITY : INFINITY;
                } else {
                    wait++;
                    int adverse=0, rollover=0, invalid=0, enough=0, price_top=0, wt_top=0;
                    int continued=0;
                    double adverse_distance=0.0;
                    if (side > 0) {
                        damage=dmin(damage,confirm_low[i]);
                        if (confirm_high[i] >= rebound_price) {
                            rebound_price=confirm_high[i];
                            rebound_wt=dmax(rebound_wt,confirm_wt[i]);
                            rebound_source=confirm_source[i];
                        }
                        enough=(rebound_price-damage >= rebound_atr*state_arm_atr);
                        price_top=(rebound_price < price_anchor);
                        wt_top=(rebound_wt < wt_anchor);
                        adverse=have_confirm && confirm_source[i] > rebound_source &&
                            rebound_source > 0 && confirm_high[i] < prev_ch &&
                            confirm_low[i] < prev_cl && confirm_close[i] < prev_cc;
                        rollover=have_confirm && confirm_wt[i] < prev_cw &&
                            prev_cw <= rebound_wt;
                        invalid=confirm_close[i] > price_anchor || confirm_wt[i] > wt_anchor;
                        continued=have_confirm && confirm_low[i] < prev_cl;
                        adverse_distance=state_arm_close-damage;
                    } else {
                        damage=dmax(damage,confirm_high[i]);
                        if (confirm_low[i] <= rebound_price) {
                            rebound_price=confirm_low[i];
                            rebound_wt=dmin(rebound_wt,confirm_wt[i]);
                            rebound_source=confirm_source[i];
                        }
                        enough=(damage-rebound_price >= rebound_atr*state_arm_atr);
                        price_top=(rebound_price > price_anchor);
                        wt_top=(rebound_wt > wt_anchor);
                        adverse=have_confirm && confirm_source[i] > rebound_source &&
                            rebound_source > 0 && confirm_high[i] > prev_ch &&
                            confirm_low[i] > prev_cl && confirm_close[i] > prev_cc;
                        rollover=have_confirm && confirm_wt[i] > prev_cw &&
                            prev_cw >= rebound_wt;
                        invalid=confirm_close[i] < price_anchor || confirm_wt[i] < wt_anchor;
                        continued=have_confirm && confirm_high[i] > prev_ch;
                        adverse_distance=damage-state_arm_close;
                    }
                    int transitioned = 0;
                    if (phase==1 && enough && price_top && wt_top) {
                        phase=2;
                        transitioned=1;
                    }
                    int confirmed =
                        confirmation_mode==1 ? adverse :
                        confirmation_mode==2 ? rollover :
                        confirmation_mode==3 ? (adverse || rollover) :
                        (adverse && rollover);
                    if (!transitioned) {
                        confirmation_streak = confirmed ? confirmation_streak+1 : 0;
                        continued_count = continued ? continued_count+1 : 0;
                    }
                    int emergency =
                        ((emergency_mask & 1) && emergency_adverse_atr > 0.0 &&
                         adverse_distance >= emergency_adverse_atr*state_arm_atr) ||
                        ((emergency_mask & 2) && emergency_adverse_stdev > 0.0 &&
                         state_arm_stdev > 0.0 &&
                         adverse_distance >= emergency_adverse_stdev*state_arm_stdev) ||
                        ((emergency_mask & 4) && wait >= max_wait) ||
                        ((emergency_mask & 8) && emergency_continued_bars > 0 &&
                         continued_count >= emergency_continued_bars);
                    if (!transitioned && emergency) {
                        signal=1;
                        signal_ref=isfinite(rebound_price) ? rebound_price : confirm_close[i];
                        pending_emergency=1; phase=0; just_armed=0;
                    } else if (!transitioned && phase==2 &&
                               confirmation_streak >= confirmation_bars) {
                        signal=1; signal_ref=rebound_price;
                        pending_emergency=0; phase=0; just_armed=0;
                    } else if (!transitioned &&
                               (invalid || (wait >= max_wait && !(emergency_mask & 4)))) {
                        phase=0; just_armed=0;
                    }
                }
            }
            prev_ch=confirm_high[i]; prev_cl=confirm_low[i];
            prev_cc=confirm_close[i]; prev_cw=confirm_wt[i]; have_confirm=1;
        }

        if (i+1 >= right) continue;
        if (signal && active) {
            out->signals++;
            double gain = side*(cp-avg_entry)/avg_entry*100.0;
            if (gain + 1e-12 >= profit_gate_pct) {
                pending=2; pending_signal=i; pending_ref=signal_ref;
                pending_dc4h_safety=0;
                continue;
            }
            out->rejected_profit++;
            pending_emergency=0;
        }
        if (active) {
            if (entry_mult[i] > 0.0) {
                pending=1; pending_signal=i;
                pending_target=BASE_UNIT*entry_mult[i];
                pending_abs=(semantics==0);
            }
        } else if (isfinite(last_exit)) {
            gap_seen |= side > 0 ? (low[i] < last_exit) : (high[i] > last_exit);
            if (entry_mult[i] > 0.0 && gap_seen) {
                pending=1; pending_signal=i;
                pending_target=BASE_UNIT*entry_mult[i];
                pending_abs=(semantics==0);
                /* Encode lower/higher ladder entry for the fill counter. */
                pending=3;
            } else if (i > exit_fill_row &&
                       (side > 0 ? cp > reclaim_level : cp < reclaim_level)) {
                out->bars_flat_beyond_reclaim++;
            }
        } else if (entry_mult[i] > 0.0) {
            pending=1; pending_signal=i;
            pending_target=BASE_UNIT*entry_mult[i];
            pending_abs=(semantics==0);
        }
    }

    if (qty > 0.0) {
        double px=close[right-1]*(1.0-side*slippage), notional=qty*px;
        cash += side*(notional-side*commission*notional);
    }
    min_eq=dmin(min_eq,cash);
    double bh_entry=open_[left]*(1.0+side*slippage);
    double bh_exit=close[right-1]*(1.0-side*slippage);
    double bh_pnl=BASE_UNIT*side*(bh_exit-bh_entry)/bh_entry
                  -2.0*commission*BASE_UNIT;
    out->capital_return_pct=100.0*(cash-ACCOUNT_EQUITY)/BASE_UNIT;
    out->bh_capital_return_pct=100.0*bh_pnl/BASE_UNIT;
    out->exposure_weighted_tim_pct=100.0*weighted/(right-left);
    out->binary_tim_pct=100.0*held/(right-left);
    out->minimum_account_equity_usd=min_eq;
    out->insolvent=(min_eq<=0.0);
    out->peak_post_fill_notional_usd=peak_post;
    out->entry_capacity_breach=(peak_post>CAPACITY+1e-6);
    out->rows=right-left;
    return 0;
}
