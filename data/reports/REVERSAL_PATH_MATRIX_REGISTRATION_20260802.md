# TRB reversal-path matrix registration — 2026-08-02

Registered **115** current TRB symbol/sides and **403** path/key jobs.
This is a research queue, not a claim that the matrix has been tested or filled.

| path family | kind | registered keys | status | runner |
|---|---|---:|---|---|
| `ENTRY_REVERSAL_DC_BREAK_BOUNCE_ROLLOVER` | ENTRY | 115 | `REGISTERED_PENDING_VECTOR` | `tools/mu_reversal_ladder_campaign.py` |
| `EXIT_REVERSAL_HHHL` | EXIT | 115 | `REGISTERED_PENDING_VECTOR` | `tools/mu_reversal_ladder_campaign.py` |
| `EXIT_REVERSAL_WT_CROSS` | EXIT | 115 | `REGISTERED_PENDING_VECTOR` | `tools/mu_reversal_ladder_campaign.py` |
| `ENTRY_OVERBOUGHT_FLIP_SHORT` | ENTRY | 58 | `REGISTERED_PENDING_VECTOR` | `tools/mu_reversal_ladder_campaign.py` |

Required coverage: SHORT then LONG; DC-low/high break→rebound/pullback→rollover/turn-up;
HH+HL/LL+LH structure exit and WT-cross exit independently on 5m/15m/1h/4h/D.
The overbought flip is research-only until an exact no-lookahead ledger exists.
No amber/ENGINE/live write is made by this registration.
