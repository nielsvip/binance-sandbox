#include <math.h>
#include <stdint.h>
#include <string.h>

/*
 * Research-only finite-state scanner for E12/E13.
 *
 * Signals are generated from completed HTF bars by Python.  A close signal at
 * index i is executed at open i+1.  Each tranche owns its own E10 reclaim and
 * E11 lower-price re-add obligation.  Costs match backtest_v8_engine:
 * slippage is embedded in each fill and a 10 bp round-trip charge is allocated
 * to the entry basis when a tranche is closed.
 */

typedef struct {
    double final_equity;
    double max_drawdown_pct;
    double weighted_exposure_bars;
    double binary_exposure_bars;
    double weighted_exposure_seconds;
    double realized_partial_gross;
    double realized_partial_net;
    double realized_full_gross;
    double realized_full_net;
    double total_cost;
    double turnover;
    double saved_price_sum_pct;
    double reclaim_overshoot_sum_pct;
    double reclaim_overshoot_max_pct;
    double missed_move_sum_pct;
    double missed_move_max_pct;
    int partial_exit_count;
    int full_exit_count;
    int reentries;
    int reclaim_reentries;
    int resting_reclaim_reentries;
    int lower_reentries;
    int positive_saved_reentries;
    int bars_flat_beyond_reclaim;
    int technical_exit_count;
    int winning_exit_count;
    int losing_exit_count;
    int rejected_exit_signals;
    int insolvent;
} PartialMetrics;

typedef struct {
    double weight;
    int active;
    int pending_exit;
    int pending_entry; /* 1 reclaim, 2 lower */
    int exit_kind;     /* 1 partial, 2 full */
    double capital;
    double entry_equity;
    double entry_px;
    double exit_px;
    double exit_atr;
    double reclaim_level;
    double pending_ref;
    double trail_stop;
    double flat_extreme;
    double flat_missed_pct;
    int gap_seen;
} Tranche;

static double entry_fill(double px, int side, double slip) {
    return side > 0 ? px * (1.0 + slip) : px * (1.0 - slip);
}

static double exit_fill(double px, int side, double slip) {
    return side > 0 ? px * (1.0 - slip) : px * (1.0 + slip);
}

static double marked(const Tranche *t, double px, int side) {
    if (!t->active || !(t->entry_px > 0.0)) return t->capital;
    return t->entry_equity
        * (1.0 + (double)side * (px - t->entry_px) / t->entry_px);
}

static double total_equity(Tranche *tranches, int count, double px, int side) {
    double result = 0.0;
    for (int j = 0; j < count; ++j) result += marked(&tranches[j], px, side);
    return result;
}

static double active_weight(Tranche *tranches, int count) {
    double result = 0.0;
    for (int j = 0; j < count; ++j) {
        if (tranches[j].active) result += tranches[j].weight;
    }
    return result;
}

static void update_drawdown(
    double equity,
    double *peak,
    double *max_drawdown_pct
) {
    if (!isfinite(equity)) return;
    if (equity > *peak) *peak = equity;
    if (*peak > 0.0) {
        double dd = 100.0 * (*peak - equity) / *peak;
        if (dd > *max_drawdown_pct) *max_drawdown_pct = dd;
    }
}

static void schedule_all(
    Tranche *tranches,
    int count,
    int exit_kind,
    double ref
) {
    for (int j = 0; j < count; ++j) {
        if (tranches[j].active && !tranches[j].pending_exit) {
            tranches[j].pending_exit = 1;
            tranches[j].exit_kind = exit_kind;
            tranches[j].pending_ref = ref;
        }
    }
}

int vec_partial_regime_scan(
    int n,
    int side,
    int family,          /* 12 or 13 */
    int regime_policy,   /* E13: 0 slow-only, 1 partial-fast, 2 full-fast */
    const int64_t *ts,
    const double *open_px,
    const double *high_px,
    const double *low_px,
    const double *close_px,
    const double *atr,
    const uint8_t *fast_event,
    const double *fast_ref,
    const uint8_t *struct_event,
    const double *struct_ref,
    int slow_mode,       /* 1 Chandelier raw stop; 2 completed-bar event */
    const uint8_t *slow_event,
    const double *slow_raw_stop,
    const double *slow_ref,
    const int8_t *regime, /* 1 aligned, 0 neutral, -1 opposing */
    const uint8_t *lower_event,
    double f1,
    double f2,
    double lower_gap_atr,
    int resting_reclaim,
    double min_profit_above_cost,
    double round_trip_cost_rate,
    double slip_rate,
    PartialMetrics *out
) {
    if (
        !out || n < 2 || (side != 1 && side != -1)
        || (family != 12 && family != 13)
    ) return 1;
    memset(out, 0, sizeof(*out));

    Tranche t[3];
    memset(t, 0, sizeof(t));
    int tranche_count = family == 12 ? 3 : 2;
    if (family == 12) {
        t[0].weight = f1;
        t[1].weight = f2;
        t[2].weight = fmax(0.0, 1.0 - f1 - f2);
    } else {
        /* E13 partial-fast uses f1 as its fast clip. */
        t[0].weight = regime_policy == 1 ? f1 : 1.0;
        t[1].weight = regime_policy == 1 ? fmax(0.0, 1.0 - f1) : 0.0;
    }

    double first_fill = entry_fill(open_px[0], side, slip_rate);
    for (int j = 0; j < tranche_count; ++j) {
        t[j].capital = t[j].weight;
        t[j].entry_equity = t[j].weight;
        t[j].entry_px = first_fill;
        t[j].active = t[j].weight > 1e-12;
        t[j].trail_stop = NAN;
        if (t[j].active) out->turnover += t[j].weight;
    }

    double peak = 1.0;
    double max_dd = 0.0;
    for (int i = 0; i < n; ++i) {
        if (!(open_px[i] > 0.0) || !(close_px[i] > 0.0)) continue;

        /* Execute signals from close i-1 at open i. */
        for (int j = 0; j < tranche_count; ++j) {
            if (t[j].pending_exit && t[j].active) {
                double fill = exit_fill(open_px[i], side, slip_rate);
                double gross_dollars = t[j].entry_equity
                    * (double)side * (fill - t[j].entry_px) / t[j].entry_px;
                double cost = t[j].entry_equity * round_trip_cost_rate;
                double net_dollars = gross_dollars - cost;
                t[j].capital = t[j].entry_equity + net_dollars;
                t[j].active = 0;
                t[j].trail_stop = NAN;
                t[j].exit_px = fill;
                t[j].exit_atr = (
                    i > 0 && atr[i - 1] > 0.0 && isfinite(atr[i - 1])
                ) ? atr[i - 1] : fmax(1e-12, 0.01 * fill);
                if (side > 0) {
                    t[j].reclaim_level = fmax(
                        fill,
                        isfinite(t[j].pending_ref) ? t[j].pending_ref : fill
                    );
                } else {
                    t[j].reclaim_level = fmin(
                        fill,
                        isfinite(t[j].pending_ref) ? t[j].pending_ref : fill
                    );
                }
                t[j].gap_seen = 0;
                t[j].flat_extreme = fill;
                t[j].flat_missed_pct = 0.0;
                t[j].pending_exit = 0;
                t[j].pending_ref = NAN;
                out->technical_exit_count += 1;
                if (net_dollars > 0.0) out->winning_exit_count += 1;
                if (net_dollars < 0.0) out->losing_exit_count += 1;
                out->total_cost += cost;
                out->turnover += t[j].weight;
                if (t[j].exit_kind == 1) {
                    out->partial_exit_count += 1;
                    out->realized_partial_gross += gross_dollars;
                    out->realized_partial_net += net_dollars;
                } else {
                    out->full_exit_count += 1;
                    out->realized_full_gross += gross_dollars;
                    out->realized_full_net += net_dollars;
                }
            }
        }

        for (int j = 0; j < tranche_count; ++j) {
            if (t[j].pending_entry && !t[j].active && t[j].capital > 0.0) {
                double fill = entry_fill(open_px[i], side, slip_rate);
                double saved = side > 0
                    ? 100.0 * (t[j].exit_px - fill) / t[j].exit_px
                    : 100.0 * (fill - t[j].exit_px) / t[j].exit_px;
                out->saved_price_sum_pct += saved;
                if (saved > 0.0) out->positive_saved_reentries += 1;
                out->missed_move_sum_pct += t[j].flat_missed_pct;
                if (t[j].flat_missed_pct > out->missed_move_max_pct) {
                    out->missed_move_max_pct = t[j].flat_missed_pct;
                }
                out->reentries += 1;
                if (t[j].pending_entry == 1) {
                    out->reclaim_reentries += 1;
                    double overshoot = side > 0
                        ? 100.0 * fmax(0.0, fill - t[j].reclaim_level)
                            / t[j].reclaim_level
                        : 100.0 * fmax(0.0, t[j].reclaim_level - fill)
                            / t[j].reclaim_level;
                    out->reclaim_overshoot_sum_pct += overshoot;
                    if (overshoot > out->reclaim_overshoot_max_pct) {
                        out->reclaim_overshoot_max_pct = overshoot;
                    }
                }
                if (t[j].pending_entry == 2) out->lower_reentries += 1;
                t[j].entry_equity = t[j].capital;
                t[j].entry_px = fill;
                t[j].active = 1;
                t[j].trail_stop = NAN;
                t[j].pending_entry = 0;
                out->turnover += t[j].weight;
            }
        }

        if (resting_reclaim) {
            for (int j = 0; j < tranche_count; ++j) {
                if (t[j].active || t[j].pending_entry || !(t[j].exit_px > 0.0)) {
                    continue;
                }
                if (side > 0) {
                    t[j].flat_extreme = fmax(t[j].flat_extreme, high_px[i]);
                    t[j].flat_missed_pct = fmax(
                        t[j].flat_missed_pct,
                        100.0 * (t[j].flat_extreme - t[j].exit_px) / t[j].exit_px
                    );
                } else {
                    t[j].flat_extreme = fmin(t[j].flat_extreme, low_px[i]);
                    t[j].flat_missed_pct = fmax(
                        t[j].flat_missed_pct,
                        100.0 * (t[j].exit_px - t[j].flat_extreme) / t[j].exit_px
                    );
                }
                int open_through = side > 0
                    ? open_px[i] >= t[j].reclaim_level
                    : open_px[i] <= t[j].reclaim_level;
                int touched = side > 0
                    ? high_px[i] >= t[j].reclaim_level
                    : low_px[i] <= t[j].reclaim_level;
                if (!open_through && !touched) continue;
                double fill = open_through
                    ? entry_fill(open_px[i], side, slip_rate)
                    : entry_fill(t[j].reclaim_level, side, slip_rate);
                double saved = side > 0
                    ? 100.0 * (t[j].exit_px - fill) / t[j].exit_px
                    : 100.0 * (fill - t[j].exit_px) / t[j].exit_px;
                double overshoot = side > 0
                    ? 100.0 * fmax(0.0, fill - t[j].reclaim_level)
                        / t[j].reclaim_level
                    : 100.0 * fmax(0.0, t[j].reclaim_level - fill)
                        / t[j].reclaim_level;
                out->saved_price_sum_pct += saved;
                if (saved > 0.0) out->positive_saved_reentries += 1;
                out->missed_move_sum_pct += t[j].flat_missed_pct;
                if (t[j].flat_missed_pct > out->missed_move_max_pct) {
                    out->missed_move_max_pct = t[j].flat_missed_pct;
                }
                out->reentries += 1;
                out->reclaim_reentries += 1;
                out->resting_reclaim_reentries += 1;
                out->reclaim_overshoot_sum_pct += overshoot;
                if (overshoot > out->reclaim_overshoot_max_pct) {
                    out->reclaim_overshoot_max_pct = overshoot;
                }
                t[j].entry_equity = t[j].capital;
                t[j].entry_px = fill;
                t[j].active = 1;
                t[j].trail_stop = NAN;
                out->turnover += t[j].weight;
            }
        }

        double exposure = active_weight(t, tranche_count);
        out->weighted_exposure_bars += exposure;
        if (exposure > 1e-12) out->binary_exposure_bars += 1.0;
        if (i + 1 < n) {
            double dt = (double)(ts[i + 1] - ts[i]);
            out->weighted_exposure_seconds += exposure * fmax(0.0, dt);
        }
        double equity = total_equity(t, tranche_count, close_px[i], side);
        update_drawdown(equity, &peak, &max_dd);
        if (!(equity > 0.0)) {
            out->insolvent = 1;
            break;
        }

        /* Flat-tranche re-entry obligations are evaluated independently. */
        for (int j = 0; j < tranche_count; ++j) {
            if (t[j].active || t[j].pending_entry || !(t[j].exit_px > 0.0)) continue;
            if (side > 0) {
                t[j].flat_extreme = fmax(t[j].flat_extreme, high_px[i]);
                t[j].flat_missed_pct = fmax(
                    t[j].flat_missed_pct,
                    100.0 * (t[j].flat_extreme - t[j].exit_px) / t[j].exit_px
                );
                if (close_px[i] > t[j].reclaim_level) {
                    out->bars_flat_beyond_reclaim += 1;
                }
                if (
                    !t[j].gap_seen
                    && low_px[i] <= t[j].exit_px - lower_gap_atr * t[j].exit_atr
                ) t[j].gap_seen = 1;
            } else {
                t[j].flat_extreme = fmin(t[j].flat_extreme, low_px[i]);
                t[j].flat_missed_pct = fmax(
                    t[j].flat_missed_pct,
                    100.0 * (t[j].exit_px - t[j].flat_extreme) / t[j].exit_px
                );
                if (close_px[i] < t[j].reclaim_level) {
                    out->bars_flat_beyond_reclaim += 1;
                }
                if (
                    !t[j].gap_seen
                    && high_px[i] >= t[j].exit_px + lower_gap_atr * t[j].exit_atr
                ) t[j].gap_seen = 1;
            }
            if (i + 1 < n) {
                if (t[j].gap_seen && lower_event[i]) {
                    t[j].pending_entry = 2;
                } else if (!resting_reclaim) {
                    int reclaimed = side > 0
                        ? close_px[i] >= t[j].reclaim_level
                        : close_px[i] <= t[j].reclaim_level;
                    if (reclaimed) t[j].pending_entry = 1;
                }
            }
        }

        int slow_trigger = slow_mode == 2 && slow_event[i];
        if (slow_mode == 1 && isfinite(slow_raw_stop[i]) && slow_raw_stop[i] > 0.0) {
            for (int j = 0; j < tranche_count; ++j) {
                if (!t[j].active) continue;
                if (!isfinite(t[j].trail_stop)) {
                    t[j].trail_stop = slow_raw_stop[i];
                } else if (side > 0) {
                    t[j].trail_stop = fmax(t[j].trail_stop, slow_raw_stop[i]);
                } else {
                    t[j].trail_stop = fmin(t[j].trail_stop, slow_raw_stop[i]);
                }
                if (
                    (side > 0 && close_px[i] < t[j].trail_stop)
                    || (side < 0 && close_px[i] > t[j].trail_stop)
                ) slow_trigger = 1;
            }
        }

        if (i + 1 >= n) continue;
        if (family == 12) {
            if (fast_event[i] && t[0].active && !t[0].pending_exit) {
                double raw_leg = (double)side
                    * (close_px[i] - t[0].entry_px) / t[0].entry_px;
                if (
                    min_profit_above_cost < 0.0
                    || raw_leg >= round_trip_cost_rate + min_profit_above_cost
                ) {
                    t[0].pending_exit = 1;
                    t[0].exit_kind = 1;
                    t[0].pending_ref = fast_ref[i];
                } else {
                    out->rejected_exit_signals += 1;
                }
            }
            if (struct_event[i] && t[1].active && !t[1].pending_exit) {
                t[1].pending_exit = 1;
                t[1].exit_kind = 1;
                t[1].pending_ref = struct_ref[i];
            }
            if (slow_trigger && t[2].active && !t[2].pending_exit) {
                t[2].pending_exit = 1;
                t[2].exit_kind = 2;
                t[2].pending_ref = slow_ref[i];
            }
        } else {
            int aligned = regime[i] > 0;
            int opposing = regime[i] < 0;
            if (slow_trigger) {
                schedule_all(t, tranche_count, 2, slow_ref[i]);
            } else if (opposing && struct_event[i]) {
                schedule_all(t, tranche_count, 2, struct_ref[i]);
            } else if (!aligned && (struct_event[i] || fast_event[i])) {
                schedule_all(
                    t,
                    tranche_count,
                    2,
                    struct_event[i] ? struct_ref[i] : fast_ref[i]
                );
            } else if (aligned && fast_event[i]) {
                if (regime_policy == 1 && t[0].active && !t[0].pending_exit) {
                    t[0].pending_exit = 1;
                    t[0].exit_kind = 1;
                    t[0].pending_ref = fast_ref[i];
                } else if (regime_policy == 2) {
                    schedule_all(t, tranche_count, 2, fast_ref[i]);
                }
            }
        }
    }

    /* Faithful end-of-window liquidation, allocating cost to each open clip. */
    for (int j = 0; j < tranche_count; ++j) {
        if (!t[j].active) continue;
        double fill = exit_fill(close_px[n - 1], side, slip_rate);
        double gross_dollars = t[j].entry_equity
            * (double)side * (fill - t[j].entry_px) / t[j].entry_px;
        double cost = t[j].entry_equity * round_trip_cost_rate;
        double net_dollars = gross_dollars - cost;
        t[j].capital = t[j].entry_equity + net_dollars;
        t[j].active = 0;
        out->total_cost += cost;
        out->turnover += t[j].weight;
        out->realized_full_gross += gross_dollars;
        out->realized_full_net += net_dollars;
        out->full_exit_count += 1;
    }
    for (int j = 0; j < tranche_count; ++j) {
        if (!t[j].active && t[j].exit_px > 0.0) {
            out->missed_move_sum_pct += t[j].flat_missed_pct;
            if (t[j].flat_missed_pct > out->missed_move_max_pct) {
                out->missed_move_max_pct = t[j].flat_missed_pct;
            }
        }
    }

    out->final_equity = total_equity(t, tranche_count, close_px[n - 1], side);
    out->max_drawdown_pct = max_dd;
    return 0;
}
