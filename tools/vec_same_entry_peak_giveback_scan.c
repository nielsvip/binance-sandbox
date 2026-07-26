#include <math.h>
#include <stdint.h>
#include <string.h>

#define ACCOUNT_EQUITY 10000.0
#define BASE_UNIT 2000.0
#define CAPACITY 16000.0
#define MAX_OBLIGATIONS 2048

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
    double realized_partial_gross_usd;
    double realized_partial_net_usd;
    double realized_full_gross_usd;
    double realized_full_net_usd;
    double unfilled_obligation_notional_usd;
    int insolvent;
    int entry_capacity_breach;
    int partial_signals; /* all peak-giveback signals, including fraction=1 */
    int regime_vetoed_partial_signals;
    int partial_exit_fills;
    int full_exit_fills;
    int technical_exit_fills;
    int entry_fills;
    int reclaim_reentries;
    int lower_reentries;
    int clamp_count;
    int reclaim_obligations_created;
    int reclaim_obligations_filled;
    int reclaim_obligations_unfilled_at_end;
    int bars_flat_beyond_reclaim;
    int future_htf_count;
    int rows;
} SameEntryPeakGivebackMetrics;

typedef struct {
    double remaining;
    double level;
    int created_row;
    int active;
} Obligation;

static double dmin(double a, double b) { return a < b ? a : b; }
static double dmax(double a, double b) { return a > b ? a : b; }

static int add_obligation(
    Obligation *items, int *count, double notional, double level, int row
) {
    if (!(notional > 1e-9) || !isfinite(level)) return 0;
    if (*count >= MAX_OBLIGATIONS) return -1;
    Obligation *item = &items[*count];
    item->remaining = notional;
    item->level = level;
    item->created_row = row;
    item->active = 1;
    (*count)++;
    return 1;
}

int vec_same_entry_peak_giveback_scan(
    int n, int left, int right, int side, int semantics,
    const int64_t *ts,
    const double *open_, const double *high, const double *low,
    const double *close, const double *entry_mult,
    int slow_mode, const uint8_t *slow_event,
    const int64_t *slow_source, const double *slow_raw_stop,
    const double *slow_ref,
    double arm_gain_pct, double giveback_fraction, double reduce_fraction,
    double commission, double slippage,
    SameEntryPeakGivebackMetrics *out
) {
    if (!out || n <= 0 || left < 0 || right > n || right-left < 100 ||
        (side != 1 && side != -1) || (semantics != 0 && semantics != 1) ||
        (slow_mode != 1 && slow_mode != 2) ||
        !(arm_gain_pct >= 0.0) ||
        !(giveback_fraction > 0.0 && giveback_fraction < 1.0) ||
        !(reduce_fraction > 0.0 && reduce_fraction <= 1.0)) return 1;
    memset(out, 0, sizeof(*out));

    double cash = ACCOUNT_EQUITY, qty = 0.0, avg_entry = NAN;
    double peak_eq = ACCOUNT_EQUITY, min_eq = ACCOUNT_EQUITY;
    double peak_post = 0.0, weighted = 0.0, held = 0.0;
    double last_exit = NAN, reclaim_level = NAN, trail_stop = NAN;
    double cycle_peak_gain = 0.0, cycle_peak_price = NAN;
    int cycle_clip_done = 0;
    int exit_fill_row = -1, gap_seen = 0;
    int pending = 0, pending_signal = -1, pending_abs = 0;
    double pending_target = 0.0, pending_ref = NAN, pending_fraction = 0.0;
    Obligation obligations[MAX_OBLIGATIONS];
    memset(obligations, 0, sizeof(obligations));
    int obligation_count = 0;

    for (int i = left; i < right; ++i) {
        double op = open_[i], cp = close[i];
        if (pending) {
            if (pending_signal + 1 != i) return 2;
            if ((pending == 2 || pending == 3) && qty > 1e-12) {
                double px = op * (1.0 - side * slippage);
                double close_qty = pending == 2 ? qty * pending_fraction : qty;
                close_qty = dmin(qty, dmax(0.0, close_qty));
                double notional = close_qty * px;
                double gross = close_qty * side * (px - avg_entry);
                double allocated_entry_fee = commission * close_qty * avg_entry;
                double net = gross - allocated_entry_fee - commission * notional;
                cash += side * (notional - side * commission * notional);
                qty -= close_qty;
                double level = side > 0 ? dmax(px, pending_ref)
                                        : dmin(px, pending_ref);
                int added = add_obligation(
                    obligations, &obligation_count, notional, level, i
                );
                if (added < 0) return 3;
                out->reclaim_obligations_created += added;
                if (pending == 2 && pending_fraction < 1.0 - 1e-12) {
                    out->partial_exit_fills++;
                    out->realized_partial_gross_usd += gross;
                    out->realized_partial_net_usd += net;
                    cycle_clip_done = 1;
                } else {
                    out->full_exit_fills++;
                    out->realized_full_gross_usd += gross;
                    out->realized_full_net_usd += net;
                    last_exit = px;
                    reclaim_level = level;
                    exit_fill_row = i;
                    gap_seen = 0;
                    cycle_clip_done = 0;
                    cycle_peak_gain = 0.0;
                    cycle_peak_price = NAN;
                    trail_stop = NAN;
                }
                out->technical_exit_fills++;
                if (qty <= 1e-12) {
                    qty = 0.0;
                    avg_entry = NAN;
                }
            } else if (pending == 1) {
                double px = op * (1.0 + side * slippage);
                double current = qty * px;
                double want = pending_abs
                    ? dmax(0.0, pending_target-current) : pending_target;
                double actual = dmin(want, dmax(0.0, CAPACITY-current));
                out->requested_notional_usd += dmax(0.0, want);
                out->filled_notional_usd += actual;
                out->clamp_count += actual + 1e-9 < want;
                if (actual > 0.0) {
                    int was_flat = qty <= 1e-12;
                    double add_qty = actual / px, new_qty = qty + add_qty;
                    avg_entry = (!isfinite(avg_entry) || was_flat)
                        ? px : (avg_entry*qty + px*add_qty)/new_qty;
                    cash -= side * actual + commission * actual;
                    qty = new_qty;
                    out->entry_fills++;
                    peak_post = dmax(peak_post, qty*px);
                    if (was_flat) {
                        cycle_clip_done = 0;
                        cycle_peak_gain = 0.0;
                        cycle_peak_price = px;
                    }
                }
            }
            pending = 0;
        }

        /* Every partial/full clip owns an independent persistent reclaim. */
        for (int j = 0; j < obligation_count;) {
            Obligation *o = &obligations[j];
            if (i <= o->created_row) { ++j; continue; }
            if (CAPACITY - qty*op <= 1e-7) break;
            int open_through = side > 0 ? op >= o->level : op <= o->level;
            int touched = side > 0 ? high[i] >= o->level : low[i] <= o->level;
            if (!open_through && !touched) { ++j; continue; }
            double raw = open_through ? op : o->level;
            double px = raw * (1.0 + side*slippage);
            double current = qty*px;
            double want = o->remaining;
            double actual = dmin(want, dmax(0.0, CAPACITY-current));
            out->requested_notional_usd += want;
            out->filled_notional_usd += actual;
            out->clamp_count += actual + 1e-9 < want;
            if (actual > 0.0) {
                int was_flat = qty <= 1e-12;
                double add_qty = actual/px, new_qty = qty+add_qty;
                avg_entry = (!isfinite(avg_entry) || was_flat)
                    ? px : (avg_entry*qty + px*add_qty)/new_qty;
                cash -= side*actual + commission*actual;
                qty = new_qty;
                out->entry_fills++;
                out->reclaim_reentries++;
                peak_post = dmax(peak_post, qty*px);
                o->remaining -= actual;
                if (was_flat) {
                    cycle_clip_done = 0;
                    cycle_peak_gain = 0.0;
                    cycle_peak_price = px;
                }
            }
            if (o->remaining <= 1e-7) {
                out->reclaim_obligations_filled++;
                obligations[j] = obligations[obligation_count-1];
                obligation_count--;
                continue;
            }
            ++j;
        }

        double equity = cash + side*qty*cp;
        peak_eq = dmax(peak_eq, equity);
        min_eq = dmin(min_eq, equity);
        if (peak_eq > 0.0) out->max_drawdown_account_pct = dmax(
            out->max_drawdown_account_pct,
            100.0*(peak_eq-equity)/peak_eq
        );
        weighted += dmin(CAPACITY, qty*cp)/CAPACITY;
        held += qty > 1e-12;
        if (equity <= 0.0) out->insolvent = 1;

        int slow_trigger = slow_mode == 2 && slow_event[i];
        if (slow_mode == 1 && isfinite(slow_raw_stop[i]) &&
            slow_raw_stop[i] > 0.0 && qty > 1e-12) {
            if (!isfinite(trail_stop)) trail_stop = slow_raw_stop[i];
            else trail_stop = side > 0 ? dmax(trail_stop, slow_raw_stop[i])
                                       : dmin(trail_stop, slow_raw_stop[i]);
            slow_trigger = side > 0 ? cp < trail_stop : cp > trail_stop;
        }
        if (i+1 >= right) continue;

        if (qty > 1e-12 && slow_trigger) {
            if (slow_source[i] > ts[i]) out->future_htf_count++;
            pending = 3;
            pending_signal = i;
            pending_ref = slow_ref[i];
            trail_stop = NAN;
            continue;
        }

        if (qty > 1e-12 && isfinite(avg_entry) && avg_entry > 0.0) {
            double favorable = side > 0 ? high[i] : low[i];
            double favorable_gain =
                100.0*side*(favorable-avg_entry)/avg_entry;
            if (favorable_gain > cycle_peak_gain) {
                cycle_peak_gain = favorable_gain;
                cycle_peak_price = favorable;
            }
            double current_gain = 100.0*side*(cp-avg_entry)/avg_entry;
            double cost_gate = 200.0*(commission+slippage);
            int armed = cycle_peak_gain >= arm_gain_pct;
            int gave_back = current_gain <=
                cycle_peak_gain*(1.0-giveback_fraction);
            int still_above_cost = current_gain > cost_gate;
            if (!cycle_clip_done && armed && gave_back && still_above_cost) {
                out->partial_signals++;
                pending = reduce_fraction < 1.0-1e-12 ? 2 : 3;
                pending_signal = i;
                pending_ref = cycle_peak_price;
                pending_fraction = reduce_fraction;
                continue;
            }
        }

        if (qty > 1e-12) {
            if (entry_mult[i] > 0.0) {
                pending = 1;
                pending_signal = i;
                pending_target = BASE_UNIT*entry_mult[i];
                pending_abs = semantics == 0;
            }
        } else if (isfinite(last_exit)) {
            gap_seen |= side > 0 ? low[i] < last_exit : high[i] > last_exit;
            if (entry_mult[i] > 0.0 && gap_seen) {
                pending = 1;
                pending_signal = i;
                pending_target = BASE_UNIT*entry_mult[i];
                pending_abs = semantics == 0;
                out->lower_reentries++;
            } else if (
                i > exit_fill_row &&
                (side > 0 ? cp > reclaim_level : cp < reclaim_level)
            ) {
                out->bars_flat_beyond_reclaim++;
            }
        } else if (entry_mult[i] > 0.0) {
            pending = 1;
            pending_signal = i;
            pending_target = BASE_UNIT*entry_mult[i];
            pending_abs = semantics == 0;
        }
    }

    if (qty > 1e-12) {
        double px = close[right-1]*(1.0-side*slippage);
        double notional = qty*px;
        double gross = qty*side*(px-avg_entry);
        double net = gross - commission*qty*avg_entry - commission*notional;
        cash += side*(notional-side*commission*notional);
        out->realized_full_gross_usd += gross;
        out->realized_full_net_usd += net;
    }
    for (int j = 0; j < obligation_count; ++j) {
        out->reclaim_obligations_unfilled_at_end++;
        out->unfilled_obligation_notional_usd += obligations[j].remaining;
    }
    min_eq = dmin(min_eq, cash);
    double bh_entry = open_[left]*(1.0+side*slippage);
    double bh_exit = close[right-1]*(1.0-side*slippage);
    double bh_pnl = BASE_UNIT*side*(bh_exit-bh_entry)/bh_entry
                  - 2.0*commission*BASE_UNIT;
    out->capital_return_pct = 100.0*(cash-ACCOUNT_EQUITY)/BASE_UNIT;
    out->bh_capital_return_pct = 100.0*bh_pnl/BASE_UNIT;
    out->exposure_weighted_tim_pct = 100.0*weighted/(right-left);
    out->binary_tim_pct = 100.0*held/(right-left);
    out->minimum_account_equity_usd = min_eq;
    out->insolvent |= min_eq <= 0.0;
    out->peak_post_fill_notional_usd = peak_post;
    out->entry_capacity_breach = peak_post > CAPACITY+1e-6;
    out->rows = right-left;
    return 0;
}
