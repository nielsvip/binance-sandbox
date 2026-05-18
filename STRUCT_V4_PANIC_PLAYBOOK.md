# STRUCT_V4 — PANIC PLAYBOOK

One-page operator manual for the struct_v4 trading sleeve safety system.
Print this. Keep it next to the keyboard. If something looks wrong and you can't reach Claude, the answers are on this page.

---

## What struct_v4 is

A long-only equity sleeve on the **trb** Tradier account. Day-1 sizing: **$500/position × max 5 positions = $2,500 total deployed**. Entries: model fires inside `tradier_struct_v4_sleeve.py`. Exits: per-position dc_low_1h hard-stop + multi-TF technicals + monitor watchdogs (this doc).

---

## The kill switches

There are 4 flag files. The sleeve checks them at the start of EVERY cycle. Drop a file -> sleeve stops doing that thing immediately. All paths relative to `/Users/niels/Documents/binance/`.

| File | Effect |
|---|---|
| `data/STRUCT_V4_HALT_ENTRIES` | NO new opens. Existing positions ride. |
| `data/STRUCT_V4_HALT_ALL` | NO opens, NO new exits initiated by the model. Existing exits-in-flight still complete. |
| `data/STRUCT_V4_PANIC_CLOSE_ALL` | Close every struct_v4 position at market, NOW. |
| `data/STRUCT_V4_PANIC_CLOSE_<SYMBOL>` | Close ONE symbol at market, NOW. |

### Drop a flag manually (any terminal, no Python needed)

```bash
cd /Users/niels/Documents/binance
touch data/STRUCT_V4_HALT_ENTRIES                  # pause new opens
touch data/STRUCT_V4_HALT_ALL                      # full halt
touch data/STRUCT_V4_PANIC_CLOSE_ALL               # close everything
touch data/STRUCT_V4_PANIC_CLOSE_AAPL              # close just AAPL
```

### Clear a flag (undo)

```bash
rm data/STRUCT_V4_HALT_ENTRIES
rm data/STRUCT_V4_HALT_ALL
rm data/STRUCT_V4_PANIC_CLOSE_ALL
ls data/STRUCT_V4_PANIC_CLOSE_*                    # see which per-sym flags exist
rm data/STRUCT_V4_PANIC_CLOSE_AAPL
```

---

## "If X happens, do Y"

### "I want EVERYTHING flat right now"
```bash
cd /Users/niels/Documents/binance && touch data/STRUCT_V4_PANIC_CLOSE_ALL && touch data/STRUCT_V4_HALT_ALL
```
Then wait 1-2 minutes (sleeve runs every 5 min — to force fast close, also call Tradier directly, see "Nuclear" below).

### "I want to pause opens but let winners run"
```bash
touch data/STRUCT_V4_HALT_ENTRIES
```

### "One symbol looks wrong, close it"
```bash
touch data/STRUCT_V4_PANIC_CLOSE_<SYMBOL>          # e.g. STRUCT_V4_PANIC_CLOSE_NVDA
```

### "I see a halt flag I didn't drop — what triggered it?"
The monitor that touched it wrote the reason inside. Cat it:
```bash
cat data/STRUCT_V4_HALT_ALL                        # JSON: ts, reason, indicators
cat data/STRUCT_V4_HALT_ENTRIES
```
Also check the relevant monitor log:
```bash
tail -100 ~/logs/struct_v4_hard_stop.log
tail -100 ~/logs/struct_v4_pnl_circuit_breaker.log
tail -100 ~/logs/struct_v4_position_sanity.log
tail -100 ~/logs/struct_v4_master_watchdog.log
tail -100 ~/logs/struct_v4_sleeve.log
```

### "Monitors aren't running"
```bash
ps -ef | grep monitor_ | grep struct_v4 | grep -v grep
crontab -l | grep struct_v4
```
No cron entries -> re-install with `crontab -l | cat - /tmp/struct_v4_crontab.txt | crontab -`.

### "I can't reach Claude and something is breaking"
Drop everything to safety. This single line is always safe:
```bash
cd /Users/niels/Documents/binance && touch data/STRUCT_V4_HALT_ALL data/STRUCT_V4_PANIC_CLOSE_ALL
```
Then check positions on Tradier directly — see "Nuclear" below.

---

## Auto-halt triggers (the monitors will do these without you)

| Trigger | Monitor | Effect |
|---|---|---|
| Any position breaks `dc_low_1h * 0.998` | `monitor_hard_stop` (60s) | per-symbol panic-close |
| Any position down > -8% absolute | `monitor_hard_stop` (60s) | per-symbol panic-close |
| Any position down > -10% intraday | `monitor_pnl_circuit_breaker` (60s) | per-symbol panic-close |
| Total unrealized loss > $300 | `monitor_pnl_circuit_breaker` (60s) | `HALT_ALL` |
| Realized loss today > $200 | `monitor_pnl_circuit_breaker` (60s) | `HALT_ENTRIES` |
| >7 open struct_v4 positions | `monitor_position_sanity` (5 min) | `HALT_ENTRIES` |
| >$3,000 deployed capital | `monitor_position_sanity` (5 min) | `HALT_ENTRIES` |
| 3 Tradier API failures in 5 min | `monitor_position_sanity` (5 min) | `HALT_ENTRIES` |
| Any child monitor silent > 3 min | `monitor_master_watchdog` (30s) | restart; if fails -> `HALT_ALL` |
| Sleeve log silent > 10 min during market | `monitor_master_watchdog` (30s) | `HALT_ALL` |

---

## "Nuclear" — close positions directly through Tradier (skipping the sleeve entirely)

If the sleeve is hung and dropping flag files isn't helping, you can close positions directly from a Python REPL using existing project code:

```bash
cd /Users/niels/Documents/binance
/opt/anaconda3/envs/binance_env/bin/python -c "
import asyncio
from tradier_api import TradierAPIClient
async def main():
    c = TradierAPIClient(account_key='trb')
    await c.connect()
    pos = await c.get_account_positions('trb')
    print('current positions:', pos)
    # MANUALLY pick a symbol + qty from above, then:
    # r = await c.place_order('trb', 'AAPL', 'sell', 5, order_type='market')
    # print('order:', r)
    await c.close()
asyncio.run(main())
"
```

Replace `'AAPL', 'sell', 5` with the symbol + qty you want to close. **For long-only struct_v4 positions, side is always `'sell'`.**

Or just log into the Tradier web/mobile app and close manually — fastest fix in a true emergency.

---

## "I want to restart everything cleanly"

```bash
# 1. clear all halt files
cd /Users/niels/Documents/binance
rm -f data/STRUCT_V4_HALT_ENTRIES data/STRUCT_V4_HALT_ALL data/STRUCT_V4_PANIC_CLOSE_ALL
rm -f data/STRUCT_V4_PANIC_CLOSE_*

# 2. verify cron has the entries
crontab -l | grep struct_v4

# 3. tail the sleeve log; you should see activity within 5 minutes of next market open
tail -f ~/logs/struct_v4_sleeve.log
```

---

## Contacts

- Tradier: https://brokerage.tradier.com — login + close positions UI
- Claude: project conversations under `/Users/niels/Documents/binance` — re-engage for any code change. Do not edit monitor scripts yourself; drop flags instead.

---

**Last verified:** 2026-05-18. All 4 monitors tested PASS. See `/tmp/monitors_test_report.md`.
