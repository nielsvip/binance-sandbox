# V8 Backtest Loss Analysis — flz 5sym Jan 2022

Run: BTCUSDT/BTCDOMUSDT/ETHUSDT/BNBUSDT/SOLUSDT, ang→flz, capital $10k, hedge_mode ON, strict_no_loss ON, delta default settings.
Sim period covered: 2022-01-01 → 2022-02-01 (~32 days)

## Net PnL by close function

| Reason | N losses | Loss sum% | N wins | Win sum% | Net% | Verdict |
|---|---|---|---|---|---|---|
| **DELTA_EXIT** | 29 | -202.09 | 32 | +57.25 | **-144.84** | TOXIC |
| NO_LOSS_EXIT | 0 | 0 | 4 | +0.05 | +0.05 | OK |
| WT_4H_VEL_EXIT | not captured (no `gain=` field in reason) | | | | | unclear |
| H_VEL_EXIT | not captured | | | | | unclear |
| TIERED_TP | only fires on profit | 0 | many | small | +small | OK |
| HEDGE_ORPHAN_KILL | 1667 fires | not in V8_TRADE format | | | | suspect |

## Sample bad trades (DELTA_EXIT losers)

| Symbol | Side | Entry $ | Exit $ | Loss% | Bars held | Verdict |
|---|---|---|---|---|---|---|
| ETHUSDT | LONG | 3821 | 3230 | -15.48 | ~52 (~13h) | bad entry + bad exit |
| ETHUSDT | LONG | 3385 | 2616 | -22.72 | ~210 (~52h) | bad entry + bad exit |
| BNBUSDT | LONG | 510 | 379 | -23.81 | ~50 | bad entry + bad exit |
| SOLUSDT | LONG | 150 | 99.32 | -33.91 | ~75 | bad entry + bad exit |
| SOLUSDT | LONG | 120 | 102.56 | -14.56 | ~40 | bad entry + bad exit |
| SOLUSDT | LONG | 103 | 92.99 | -9.59 | ~30 | bad entry + bad exit |
| ETHUSDT | LONG | 3110 | 2820 | -10.01 | ~25 | bad entry + bad exit |
| BTCUSDT | LONG | 47400 | 43462 | -8.33 | ~30 | bad entry + bad exit |

## Bad ENTRY pattern

100% of deep losses are LONG entries from `QUICK_OPEN_STRONG_BUY` signals firing during early Jan-Feb 2022. The ENTIRE crypto market was in a multi-month bear market starting with the Nov 2021 peak. The signal:

```
QUICK_OPEN_STRONG_BUY_k1:99/d167/k3:99.09/d3:67_...
```

Triggers on:
- 1m stoch K=99 (extreme overbought)
- 3m stoch K=99 (extreme overbought)
- LTF momentum signals

PROBLEM: These LTF (1m/3m) overbought conditions during a bearish 4h+D context = textbook bear-market dead-cat bounces. Going LONG into a downtrend on overbought LTF = catching falling knives.

ROOT CAUSE: The entry path does NOT consult 4h/D WT direction. It fires LONG on LTF momentum even when 4h+D are bearish.

## Bad EXIT pattern

100% of deep losses are closed by `DELTA_EXIT_speed_decay_tfs_lost=?_gain=...`. The `tfs_lost=?` field is BROKEN (literal `?` character — `getattr(_d_sig, 'tf_lost', '?')` returns `?` because the attribute is missing).

Looking at the trade timing:
- Entry: STRONG_BUY at LTF overbought
- Position immediately goes underwater
- DELTA_EXIT does NOT fire on initial decline (-2%, -5%, -10%)
- DELTA_EXIT eventually fires AT THE BOTTOM (-15%, -22%, -33%)

PROBLEM: DELTA_EXIT only fires when "speed_decay" condition is met — speed has to slow DOWN before it triggers. By then the position is at maximum drawdown. The exit is too LATE.

ROOT CAUSE: Speed decay is a momentum-die signal, not a trend-reversal signal. Position needs an EARLIER exit (when 4h/D WT turns against, when DC breaks, when stoch crosses on 15m+) — not a "speed has died" signal that fires post-mortem.

## Functions causing losses

| Function | File | Line | Type | Damage | Fix |
|---|---|---|---|---|---|
| DELTA_EXIT | ez_manage.py | 20128-20136 | EXIT (too late) | -144.84% net | Add WT/DC turn pre-check, exit earlier on 4h/D wt turn |
| QUICK_OPEN_STRONG_BUY | ez_positions_quick.py (entry path) | ? | ENTRY (bad context) | enables losses | Add 4h+D WT alignment gate before LTF stoch entries |

## Fix priority
1. Add 4h+D WT alignment gate to QUICK_OPEN paths — block LONG when 4h_wt < d_wt AND d_wt is bearish
2. Add EARLIER exit triggers: 4h_wt cross + 15m_wt cross while in loss → exit
3. Disable DELTA_EXIT (or change its logic) — it's a net loser

## What to test next
- NO HEDGE MODE + technical exits at loss when wt_dc/delta dictates (no fixed % stops)
- Compare same 5 symbols, same dates, see if technical-driven exits at small losses outperform letting positions run to -33%
