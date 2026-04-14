# HTF WT Exit Tests — Priority Queue

## After 121-symbol baseline completes, test these exit configurations:

### Test 1: NOLOSS sweep (min gain before ANY exit)
- **0.0% (NO FLOOR)** — let technicals decide, no restrictions
- 0.5%, 1.0%, 1.5%, 2.0%, 3.0%
- The question: does removing the floor improve or hurt? With mandatory reentry, early exits should be fine.

### Test 2: WT exit TF combinations
- A: 5m+15m+1h (current — 30% WR, -$9k)
- B: 15m+1h+4h (skip noisy 5m)
- C: 1h+4h+D (only medium/long-term)
- D: 4h+D only (high conviction)
- E: D only (exit only on daily WT turn)
- F: 5m fires BUT 4h must confirm (noise filter)
- G: Any 2 of {1h, 4h, D} against (HTF-only 2/3)

### Test 3: WT exhaustion zones
- Current: wt1 < wt2 = "against"
- Alt A: wt1 < wt2 AND wt1 < -30 (only exit when WT in oversold zone)
- Alt B: wt1 crossed below wt2 in LAST 3 bars (fresh cross, not stale)
- Alt C: wt_velocity < -5 on 4h (momentum dying fast)

### Test 4: Combined best
- IBS exhaustion (keep, proven +$16k)
- HTF WT exit (winner from Test 2)
- NOLOSS (winner from Test 1)
- Mandatory reentry on WT favor flip

### Test 5: ENTRY FILTER — Daily WT momentum gate
- Block LONG entry when wt_velocity_D < 0 (daily WT slowing/turning down)
- Block SHORT entry when wt_velocity_D > 0 (daily WT slowing/turning up)
- Variants:
  - A: wt_velocity_D sign must match direction (strict)
  - B: wt_velocity_D must be > +2 for LONG / < -2 for SHORT (strong momentum)
  - C: wt1_D must be > wt2_D for LONG / < for SHORT (daily trend alignment)
  - D: wt1_D rising for LONG / falling for SHORT (direction of daily WT)
  - E: Combine D WT + 4h WT both must favor entry direction
- Logic: if the daily wave is already exhausted, a 5m entry signal is just catching the last drip.
  Even if you catch a scalp, the risk/reward is terrible — the big move already happened.

### Test 6: ENTRY + EXIT combined best
- Entry: only when D WT favors direction (Test 5 winner)
- Exit: HTF WT exhaustion (Test 2 winner) + IBS
- No NOLOSS floor (Test 1 winner — expected 0%)
- Mandatory reentry on WT favor flip
- This should produce: fewer entries (higher quality), faster exits (on real turns), guaranteed reentry

### Test 7: HOLD DURATION SPECTRUM — frequent flips vs diamond hands
The core question: does frequent in/out (exit on 1h WT slow) beat HODL (exit only on D/W WT turn)?
Crypto rallies keep WT_D high for MONTHS — exiting on 1h would miss the entire run.

**Matrix: exit on WT delta slowdown (velocity drops below threshold)**

| Config | Exit TF | Reentry TF | Expected behavior |
|--------|---------|------------|-------------------|
| H01 | 1h vel < -2 | 1h vel > 0 | Frequent scalps, many round-trips |
| H02 | 1h vel < -5 | 1h vel > 0 | Moderate scalps |
| H03 | 4h vel < -1 | 4h vel > 0 | Swing trades (days) |
| H04 | 4h vel < -3 | 4h vel > 0 | Bigger swings |
| H05 | D vel < 0 | D vel > 0 | Position trades (weeks) |
| H06 | D vel < -2 | D vel > 0 | Only on real daily reversal |
| H07 | W vel < 0 | W vel > 0 | Monthly position trades |
| H08 | 1h vel<-2 AND 4h confirms | 4h vel > 0 | 1h triggers, 4h validates |
| H09 | 4h vel<-1 AND D confirms | D vel > 0 | 4h triggers, D validates |
| H10 | ANY of 1h/4h/D slowing | fastest to recover | Most active — catches ALL slowdowns |
| H11 | ALL of 1h+4h slowing | 4h vel > 0 | Only exit on multi-TF consensus |
| H12 | D vel < 0 + 4h vel < 0 | D vel > 0 + 4h vel > 0 | Conservative: both must agree |
| H13 | NEVER exit (pure HODL) | N/A | Baseline: buy and hold forever |
| H14 | IBS only (no WT exit) | reenter on WT favor | Quick profit take, no trend exit |

**For crypto specifically (rallies last months):**

| Config | Logic | Why test |
|--------|-------|---------|
| H15 | Exit on D WT cross (wt1_D < wt2_D) | Only sell when daily TREND actually flips |
| H16 | Exit on D WT overbought (wt1_D > 60) + vel slowing | Sell INTO strength, not after crash |
| H17 | Exit on 4h WT cross + D still bullish = REDUCE 50% | Partial exit on swing turn, keep core |
| H18 | Exit on 1h WT cross + 4h bullish = scalp exit only | Take quick profit, hold swing core |
| H19 | Tiered: 1h slow=reduce 25%, 4h slow=reduce 50%, D slow=full exit | Cascade exits by conviction |
| H20 | D vel slowing from peak (vel < peak_vel * 0.5) | Momentum fading from HIGH, not from zero |

**Reentry tests (when you underestimated the trend):**

| Config | Logic | Why test |
|--------|-------|---------|
| R01 | Reenter when exit TF flips back (WT favor) | Standard — wait for TF confirmation |
| R02 | Reenter when exit price crossed + any WT favor | Don't wait for full flip — price tells you |
| R03 | Reenter at 50% size when 1h WT favors, full size when 4h confirms | Scale back in |
| R04 | Immediate reenter if D WT never turned (false exit on LTF noise) | Catch false exits fast |
| R05 | Reenter with 150% size if D WT accelerating (missed the continuation) | Aggressive re-entry on proven trend |

## Expected outcome:
- HTF exits = fewer trades, higher WR, bigger avg win
- Daily WT turn = only 1-2 exits per position per month vs 50+ on 5m
- Combined with IBS for quick profit-taking = best of both worlds
- Crypto: D/W exits will massively outperform 1h exits during bull runs
- Stocks: 4h exits may be optimal (faster mean-reversion than crypto)
- Tiered exits (H19) could be the sweet spot — partial exits reduce risk without killing runners
- Reentry R04/R05 catches the "I sold too early" problem that kills trend followers
