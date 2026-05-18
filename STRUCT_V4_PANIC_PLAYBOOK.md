# STRUCT_V4 — PANIC PLAYBOOK (dual-account)

One-page operator manual for the struct_v4 trading sleeve safety system.
Print this. Keep it next to the keyboard. If something looks wrong and you can't reach Claude, the answers are on this page.

---

## What struct_v4 is

A long-only equity sleeve running on **two accounts**:

| Account | Role | Universe | Sizing | Orders |
|---|---|---|---|---|
| `trb` | **LIVE $$$** | Ranked universe | $500/pos x max 5 = **$2,500 deployed** | Real Tradier orders |
| `trc` | **PAPER** | All-symbols universe | $500/pos, no hard cap | NO real orders — A/B comparison only |

Entries fire inside `tradier_struct_v4_sleeve.py` (two processes — one per account). Exits: per-position `dc_low_1h` hard-stop + multi-TF technicals + monitor watchdogs (this doc).

### Paper window (trb only)

Every weekday **13:30-14:00 UTC**, `trb` runs in forced-paper mode (no real orders). At 14:00 UTC it arms live. The monitors know this — they LOG ONLY for trb during the window. trc is always paper.

---

## The kill switches (account-aware)

The sleeve checks halt flags at the start of every cycle. Drop a file -> the sleeve stops doing that thing immediately.

All flag files now live under `data/struct_v4/`. The sleeve checks **account-specific first, then global**, so the `_trb` and `_trc` files isolate one side without affecting the other. Legacy paths in `data/STRUCT_V4_*` are still respected.

| File | Effect |
|---|---|
| `data/struct_v4/STRUCT_V4_HALT_ENTRIES_trb` | trb only: NO new opens. Existing positions ride. |
| `data/struct_v4/STRUCT_V4_HALT_ENTRIES_trc` | trc only (paper): pauses paper opens. |
| `data/struct_v4/STRUCT_V4_HALT_ENTRIES` | **BOTH** accounts: NO new opens anywhere. |
| `data/struct_v4/STRUCT_V4_HALT_ALL_trb` | trb only: NO opens, NO model-driven exits. |
| `data/struct_v4/STRUCT_V4_HALT_ALL_trc` | trc only (paper) full halt. |
| `data/struct_v4/STRUCT_V4_HALT_ALL` | **BOTH** accounts: full halt. |
| `data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL_trb` | Close every trb position at market, NOW. |
| `data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL_trc` | (Paper) flat trc bookkeeping. |
| `data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL` | Close every position on BOTH accounts. |
| `data/struct_v4/STRUCT_V4_PANIC_CLOSE_trb_<SYMBOL>` | Close ONE trb symbol at market. |
| `data/struct_v4/STRUCT_V4_PANIC_CLOSE_trc_<SYMBOL>` | (Paper) flat one trc symbol. |

---

## SSH-from-anywhere one-liners

These are the commands the user should be able to fire from a phone over SSH without thinking. Each one is a single line, copy-paste.

### trb is bleeding — close everything on the live account
```bash
ssh niels@<mac-host> 'touch /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL_trb /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_HALT_ALL_trb'
```

### trb is bleeding but you only want to pause opens (let winners run)
```bash
ssh niels@<mac-host> 'touch /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_HALT_ENTRIES_trb'
```

### Just one trb symbol looks wrong — close it
```bash
ssh niels@<mac-host> 'touch /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_PANIC_CLOSE_trb_NVDA'
```

### trc shows bad behavior, trb is fine — pause trc only (no urgency, it's paper)
```bash
ssh niels@<mac-host> 'touch /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_HALT_ALL_trc'
```

### Anything weird, halt BOTH accounts
```bash
ssh niels@<mac-host> 'touch /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_HALT_ALL'
```

### Nuclear — flat everything everywhere, halt everything
```bash
ssh niels@<mac-host> 'cd /Users/niels/Documents/binance && touch data/struct_v4/STRUCT_V4_HALT_ALL data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL'
```

### Clear flags after you've stabilised
```bash
ssh niels@<mac-host> 'rm -f /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_HALT_* /Users/niels/Documents/binance/data/struct_v4/STRUCT_V4_PANIC_CLOSE_*'
```

---

## "If X happens, do Y" (interactive shell)

### "I want EVERYTHING flat right now (both accounts)"
```bash
cd /Users/niels/Documents/binance && touch data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL && touch data/struct_v4/STRUCT_V4_HALT_ALL
```

### "I want to pause opens on trb but let winners run"
```bash
touch data/struct_v4/STRUCT_V4_HALT_ENTRIES_trb
```

### "One trb symbol looks wrong, close it"
```bash
touch data/struct_v4/STRUCT_V4_PANIC_CLOSE_trb_<SYMBOL>           # e.g. STRUCT_V4_PANIC_CLOSE_trb_NVDA
```

### "trc paper drifted/bug — pause only trc"
```bash
touch data/struct_v4/STRUCT_V4_HALT_ALL_trc
```

### "I see a halt flag I didn't drop — what triggered it?"
The monitor that touched it wrote the reason inside. `cat` it:
```bash
cat data/struct_v4/STRUCT_V4_HALT_ALL_trb       # account-scoped
cat data/struct_v4/STRUCT_V4_HALT_ALL           # global
cat data/struct_v4/STRUCT_V4_HALT_ENTRIES_trb
```
Then check the relevant monitor log:
```bash
tail -100 ~/logs/struct_v4_hard_stop.log
tail -100 ~/logs/struct_v4_pnl_circuit_breaker.log
tail -100 ~/logs/struct_v4_position_sanity.log
tail -100 ~/logs/struct_v4_master_watchdog.log
tail -100 ~/logs/struct_v4_sleeve_trb.log
tail -100 ~/logs/struct_v4_sleeve_trc.log
```

### "Monitors aren't running"
```bash
ps -ef | grep monitor_ | grep struct_v4 | grep -v grep
crontab -l | grep struct_v4
```
No cron entries -> re-install with `crontab -l | cat - /tmp/struct_v4_crontab.txt | crontab -`.

### "Is the sleeve actually running on both accounts?"
```bash
pgrep -af tradier_struct_v4_sleeve.py
```
Expect TWO lines — one matching `--account=trb` (or `STRUCT_V4_ACCOUNT=trb`), one matching `--account=trc`. The master watchdog drops `STRUCT_V4_HALT_ALL_<acct>` if a sleeve dies for 10+ min during market hours.

### "I can't reach Claude and something is breaking"
Drop everything to safety. This single line is always safe:
```bash
cd /Users/niels/Documents/binance && touch data/struct_v4/STRUCT_V4_HALT_ALL data/struct_v4/STRUCT_V4_PANIC_CLOSE_ALL
```
Then check positions on Tradier directly — see "Nuclear" below.

---

## Auto-halt triggers (the monitors will do these without you)

trb (LIVE) — enforces real flags. trc (PAPER) — LOG ONLY, no flags fire.

| Trigger | Monitor | Account | Effect |
|---|---|---|---|
| Any trb position breaks `dc_low_1h * 0.998` | `monitor_hard_stop` (60s) | trb | `PANIC_CLOSE_trb_<SYM>` |
| Any trb position down > -8% absolute | `monitor_hard_stop` (60s) | trb | `PANIC_CLOSE_trb_<SYM>` |
| Any trb position down > -10% intraday | `monitor_pnl_circuit_breaker` (60s) | trb | `PANIC_CLOSE_trb_<SYM>` |
| trb total unrealized loss > $300 | `monitor_pnl_circuit_breaker` (60s) | trb | `HALT_ALL_trb` |
| trb realized loss today > $200 | `monitor_pnl_circuit_breaker` (60s) | trb | `HALT_ENTRIES_trb` |
| trc hypothetical daily loss > $1,000 | `monitor_pnl_circuit_breaker` (60s) | trc | EMAIL ALERT (NO halt — paper) |
| >7 open trb positions | `monitor_position_sanity` (5 min) | trb | `HALT_ENTRIES_trb` |
| >$3,000 deployed on trb | `monitor_position_sanity` (5 min) | trb | `HALT_ENTRIES_trb` |
| 3 Tradier API failures in 5 min (trb) | `monitor_position_sanity` (5 min) | trb | `HALT_ENTRIES_trb` |
| trc count / deployed / API failures | `monitor_position_sanity` (5 min) | trc | LOG ONLY |
| Any sub-monitor silent > 3 min | `monitor_master_watchdog` (30s) | both | restart; on failure -> `HALT_ALL` |
| One sleeve silent > 10 min | `monitor_master_watchdog` (30s) | per-acct | `HALT_ALL_<acct>` |
| BOTH sleeves silent > 10 min | `monitor_master_watchdog` (30s) | both | `HALT_ALL` |

**During the trb paper window (13:30-14:00 UTC)**, the hard-stop / PnL / position-sanity monitors LOG ONLY for trb — no flag writes. Process-alive enforcement (master watchdog) still applies. At 14:00 UTC trb arms live, monitors fully enforce.

---

## "Nuclear" — close positions directly through Tradier (skipping the sleeve)

If the sleeve is hung and dropping flag files isn't helping, you can close trb positions directly from a Python REPL using existing project code:

```bash
cd /Users/niels/Documents/binance
/opt/anaconda3/envs/binance_env/bin/python -c "
import asyncio
from tradier_api import TradierAPIClient
async def main():
    c = TradierAPIClient(account_key='trb')        # change to 'trc' for paper (no real orders)
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
# 1. clear all halt + panic files (both accounts + global + legacy)
cd /Users/niels/Documents/binance
rm -f data/struct_v4/STRUCT_V4_HALT_* data/struct_v4/STRUCT_V4_PANIC_CLOSE_*
rm -f data/STRUCT_V4_HALT_* data/STRUCT_V4_PANIC_CLOSE_*

# 2. verify cron has the entries
crontab -l | grep struct_v4

# 3. confirm both sleeve processes
pgrep -af tradier_struct_v4_sleeve.py    # expect 2 lines: trb + trc

# 4. tail both sleeve logs
tail -f ~/logs/struct_v4_sleeve_trb.log ~/logs/struct_v4_sleeve_trc.log
```

---

## Contacts

- Tradier: https://brokerage.tradier.com — login + close positions UI
- Claude: project conversations under `/Users/niels/Documents/binance` — re-engage for any code change. Do not edit monitor scripts yourself; drop flags instead.

---

**Last verified:** 2026-05-18 dual-account rewrite. All 4 monitors compile-clean.
