#include <math.h>
#include <stdint.h>
#include <string.h>

/*
 * Compiled parity implementation of vec_band_ladder_walkforward._simulate_python.
 *
 * This scanner deliberately consumes already-built causal entry/exit arrays.  It
 * does not construct indicators, interpolate bars, or inspect an unfinished HTF
 * candle.  Repeated parent-release rows share one timestamp; a pending order can
 * fill only at the first strictly later timestamp.
 */

typedef struct {
    double strategy_pnl;
    double bh_pnl;
    double max_drawdown_account_pct;
    double minimum_account_equity_usd;
    double weighted_exposure;
    double peak_mark_notional;
    double peak_post_fill_notional;
    double requested_notional;
    double filled_notional;
    int clamp_count;
    int fill_count;
    int exit_count;
    int reclaim_count;
    int ladder_reentry_count;
    int held_bars;
    int bars_flat_beyond_reclaim;
    int rows;
} BandLadderMetrics;

static double dmin(double a, double b) { return a < b ? a : b; }
static double dmax(double a, double b) { return a > b ? a : b; }

int vec_band_ladder_scan(
    int n,
    int left,
    int right,
    int side,
    int target_semantics,
    const int64_t *ts,
    const double *open_,
    const double *high,
    const double *low,
    const double *close,
    const double *entry_mult,
    const uint8_t *exit_event,
    const double *exit_ref,
    double account_equity,
    double base_unit,
    double capacity,
    double commission,
    double slippage,
    BandLadderMetrics *out
) {
    if (!out || !ts || !open_ || !high || !low || !close || !entry_mult ||
        !exit_event || !exit_ref || n <= 0 || left < 0 || right > n ||
        right - left < 100 || (side != 1 && side != -1) ||
        (target_semantics != 0 && target_semantics != 1) ||
        !(account_equity > 0.0) || !(base_unit > 0.0) ||
        !(capacity > 0.0)) {
        return 1;
    }
    memset(out, 0, sizeof(*out));

    double cash = account_equity;
    double qty = 0.0; /* signed, exactly like the Python oracle */
    double last_exit_fill = NAN;
    double reclaim_level = NAN;
    double prior_exit_notional = 0.0;
    /* Fill-cost capital committed to the current position.  Mark notional is
       deliberately not the deployed-capital denominator: it would reward a
       winning SHORT merely because its marked notional falls, and penalize an
       equally good LONG because its mark rises. */
    double committed_notional = 0.0;
    int gap_seen = 0;

    /* pending: 0 none, 1 entry, 2 exit */
    int pending = 0;
    int pending_signal = -1;
    int pending_absolute_target = 0;
    int pending_reason = 0; /* 1 reclaim, 2 ladder-lower/higher */
    double pending_requested_notional = 0.0;
    double pending_ref = NAN;

    double peak_equity = account_equity;
    double min_equity = account_equity;

    for (int i = left; i < right; ++i) {
        if (i > left && ts[i] < ts[i - 1]) return 2;
        const double op = open_[i];
        const double cp = close[i];
        const int has_position = side * qty > 1e-12;

        if (pending && ts[i] > ts[pending_signal]) {
            if (pending == 2 && has_position) {
                const double px = op * (1.0 - side * slippage);
                const double close_qty = fabs(qty);
                const double notional = close_qty * px;
                cash += side * (notional - side * commission * notional);
                prior_exit_notional = dmin(capacity, notional);
                last_exit_fill = px;
                reclaim_level = side > 0
                    ? dmax(px, pending_ref)
                    : dmin(px, pending_ref);
                qty = 0.0;
                committed_notional = 0.0;
                gap_seen = 0;
                out->exit_count++;
            } else if (pending == 1) {
                const double px = op * (1.0 + side * slippage);
                const double current = fabs(qty) * px;
                const double want = pending_absolute_target
                    ? dmax(0.0, pending_requested_notional - current)
                    : pending_requested_notional;
                const double capacity_left = dmax(0.0, capacity - current);
                const double actual = dmin(want, capacity_left);
                out->requested_notional += dmax(0.0, want);
                out->filled_notional += actual;
                out->clamp_count += actual + 1e-9 < want;
                if (actual > 0.0) {
                    cash -= side * actual + commission * actual;
                    qty += side * actual / px;
                    committed_notional = dmin(
                        capacity, committed_notional + actual
                    );
                    out->peak_post_fill_notional = dmax(
                        out->peak_post_fill_notional, fabs(qty) * px
                    );
                    out->fill_count++;
                    out->reclaim_count += pending_reason == 1;
                    out->ladder_reentry_count += pending_reason == 2;
                }
            }
            pending = 0;
        }

        const double mark_equity = cash + qty * cp;
        min_equity = dmin(min_equity, mark_equity);
        peak_equity = dmax(peak_equity, mark_equity);
        if (peak_equity > 0.0) {
            out->max_drawdown_account_pct = dmax(
                out->max_drawdown_account_pct,
                100.0 * (peak_equity - mark_equity) / peak_equity
            );
        }
        const double notional = fabs(qty) * cp;
        out->peak_mark_notional = dmax(out->peak_mark_notional, notional);
        if (side * qty > 1e-12) out->held_bars++;
        out->weighted_exposure += committed_notional / capacity;

        if (pending || i + 1 >= right) continue;
        /* Monotone availability timestamps make this equivalent to
           next_strictly_later_index(ts, i, right) != None. */
        if (ts[right - 1] <= ts[i]) continue;

        if (side * qty > 1e-12) {
            if (exit_event[i]) {
                double ref = exit_ref[i];
                if (!isfinite(ref)) ref = side > 0 ? high[i] : low[i];
                pending = 2;
                pending_signal = i;
                pending_ref = ref;
            } else if (entry_mult[i] > 0.0) {
                pending = 1;
                pending_signal = i;
                pending_requested_notional = base_unit * entry_mult[i];
                pending_absolute_target = target_semantics;
                pending_reason = 0;
            }
        } else if (isfinite(last_exit_fill)) {
            if (side > 0) gap_seen |= low[i] < last_exit_fill;
            else gap_seen |= high[i] > last_exit_fill;
            const int reclaim_crossed = side > 0
                ? cp >= reclaim_level
                : cp <= reclaim_level;
            if (reclaim_crossed) {
                pending = 1;
                pending_signal = i;
                pending_requested_notional = dmax(base_unit, prior_exit_notional);
                pending_absolute_target = 1;
                pending_reason = 1;
            } else if (entry_mult[i] > 0.0 && gap_seen) {
                pending = 1;
                pending_signal = i;
                pending_requested_notional = base_unit * entry_mult[i];
                pending_absolute_target = target_semantics;
                pending_reason = 2;
            } else if (side > 0 ? cp > reclaim_level : cp < reclaim_level) {
                out->bars_flat_beyond_reclaim++;
            }
        } else if (entry_mult[i] > 0.0) {
            pending = 1;
            pending_signal = i;
            pending_requested_notional = base_unit * entry_mult[i];
            pending_absolute_target = target_semantics;
            pending_reason = 0;
        }
    }

    if (side * qty > 1e-12) {
        const double px = close[right - 1] * (1.0 - side * slippage);
        const double notional = fabs(qty) * px;
        cash += side * (notional - side * commission * notional);
        qty = 0.0;
    }
    min_equity = dmin(min_equity, cash);
    out->strategy_pnl = cash - account_equity;
    const double bh_entry = open_[left] * (1.0 + side * slippage);
    const double bh_exit = close[right - 1] * (1.0 - side * slippage);
    out->bh_pnl = (
        base_unit * side * (bh_exit - bh_entry) / bh_entry
        - 2.0 * commission * base_unit
    );
    out->minimum_account_equity_usd = min_equity;
    out->rows = right - left;
    return 0;
}
