# Luna independent classic-formation priority ranking

Status: **SUPERSEDED — FULL 115/115 RERUN REQUIRED**  

> Invalidation notice (2026-08-03): the source NPZ axis is native 5m, but the
> formation-field upgrader did not collapse broadcast 15m parent candles before
> detection. Every isolated `15m` arm and every `ALL` arm in this report is
> therefore invalid for promotion. The 1h/4h/D rows remain diagnostic only. A
> corrected full-universe train/holdout run and a new Luna ranking are required.
Generated from: `CLASSIC_FORMATION_LUNA_INPUT.json`  
Input schema: `classic-formation-luna-input-v1`  
Input SHA-256: `c4f204da55546475c0527287e21d6f07071adad10bfa7b1d6126b68e13ba6aa1`  
Campaign-summary SHA-256: `080025136a6b4cfabf2a1c8e9b9ebb8d35f579635d49c4b3aac149f4228eb3c0`

## Evidence scope

- Current SWITCH_MATRIX_TRB universe: 58 LONG + 57 SHORT = 115 directional
  cells across 108 unique stocks.
- Train: 2025-01-01 through 2025-11-30. Holdout: 2025-12-01 onward.
- Completion: 115/115 cells and 0 failures in both windows.
- Arms: 56 isolated family/action/timeframe arms plus 14 directly simulated
  all-timeframe rollups. Every arm is compared with the same frozen baseline.
- Timeframes: 15m, 1h, 4h and D. `ALL` means all four enabled together; it is
  a direct simulation, not an average of isolated results.
- This is vector-discovery evidence. No matrix, database, or live configuration
  was written by the campaign.

Direction semantics were verified: LONG entry uses bullish formations, SHORT
entry bearish, LONG exit bearish and SHORT exit bullish. Bullish/bearish family
pairs are inverse H&S/H&S, Double Bottom/Top, Falling/Rising Wedge,
Ascending/Descending Triangle, Bull/Bear Flag, Cup/Inverse Cup, and HH-HL/LH-LL.

Side-correct B&H uses the same one-round-trip cost: LONG is
`(last / first - 1) × 100 - cost`; SHORT is
`(first - last) / first × 100 - cost`.

## Decision

Seven entry arms are holdout-supported for continued research. None is approved
for live promotion from this evidence alone.

1. **Cup/Handle entry, ALL** is the best overall balance. Median improvement
   versus baseline was +0.550% in train and +1.283% in holdout; 61.7% of cells
   improved. LONG/SHORT holdout medians were +0.550%/+3.544%. It fired in
   105/115 cells and produced 5,272 trades. Median DD worsened 1.00 pp while
   p90 DD improved 0.79 pp.
2. **Cup/Handle entry, 4h** is the cleanest isolated arm: train +0.029%, holdout
   +0.340%, 53.9% improved cells, LONG/SHORT +0.088%/+0.408%, 92 firing cells,
   4,915 trades, median DD -0.54 pp and p90 DD +1.92 pp versus baseline.
3. **Trend structure entry, 1h** is the most balanced trend arm: train +0.406%,
   holdout +0.369%, 57.4% improved cells, LONG/SHORT +0.412%/+0.315%, and
   104 firing cells. Median DD worsened 1.96 pp, although p90 DD improved 0.83 pp.
4. **Trend structure entry, D** retained train/holdout improvement
   (+1.611%/+0.991%) on both sides, but median DD worsened 2.76 pp.
5. **Trend structure entry, ALL** has the largest return delta
   (+5.391% train, +5.886% holdout; 66.1% cells improved), but is not the safest:
   trades rose from the 4,609 holdout baseline to 10,503, holdout p10 delta was
   -21.278%, and median/p90 DD worsened 4.24/3.52 pp.
6. **Wedge entry, ALL** held up (+2.509% train, +0.800% holdout), but is
   SHORT-heavy (+0.295% LONG versus +3.128% SHORT), increased trades to 8,501,
   and worsened p90 DD 3.20 pp.
7. **Trend structure entry, 4h** improved both sides and both windows, but p90
   DD worsened 3.93 pp.

Three arms remain watch-only. Double Top/Bottom entry ALL improved aggregate
holdout median by +0.999%, but LONG was +1.711% while SHORT was -0.349%.
Trend-structure exits at 1h and 15m had only +0.040%/+0.048% aggregate holdout
medians, no positive train median, and weak/negative LONG evidence. They are not
stable cross-side exit candidates.

No exit arm passed the cross-window, cross-side support gate. Flag/Pennant was a
complete no-op at every timeframe and action. Trend-structure exit ALL was
overfit: +0.183% train became -0.120% holdout. Trend entry 15m, Wedge entry 15m,
and Double Top/Bottom entry 1h also failed to preserve their train edge.

Crucially, **no supported arm beat side-correct B&H at the median holdout cell**.
The supported arms' median deltas versus B&H ranged from -4.507% to -7.292%, and
only 40.9%–44.3% of their cells beat B&H. These are incremental improvements to
the trading baseline, not evidence of absolute benchmark superiority.

## Ranking method

The 70 arms were ranked together using percentile scores available in the frozen
426-row Luna input. The unpenalized score is:

- 30% holdout baseline delta: median (10), p10 (8), positive-cell fraction (12).
- 20% train/holdout stability: train median (5), train positive fraction (5),
  sign consistency (5), train-to-holdout median stability (5).
- 15% side robustness: weaker-side positive fraction (8), weaker-side median
  delta (4), inverse LONG/SHORT asymmetry (3).
- 15% drawdown: holdout median/p90 DD improvement (6/5), train median/p90 (2/2).
- 10% activity: holdout/train firing coverage (4/2), action-change coverage (2),
  and trade-count distance from baseline (2). Firing and action-change counts
  were identical in every supplied row.
- 10% side-correct B&H: holdout median delta (6), p10 delta (2), positive-cell
  fraction (2).

Evidence penalties prevent attractive risk-only or inactive rows from outranking
real holdout edges: watch-only -10 points; no-holdout-edge and overfit -25;
holdout firing below 20% -40; exact train-and-holdout no-op = 0.

`SUPPORTED` requires positive train and holdout median deltas, more than 50% of
ALL cells improving in both windows, positive LONG and SHORT holdout medians,
at least 45% positive cells on each side, and at least 20% firing coverage.
`MATERIAL_RISK` means holdout median or p90 DD worsened by more than 2 pp.

## All-timeframe family/action rollups

|Rank|Arm|Score|Verdict|Train Δmed|Holdout Δmed|H+ cells|L/S Δmed|Fire|Trades|DD Δ med/p90|Δ vs B&H med|
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
|1|cup_handle_entry_ALL|73.8|SUPPORTED_MILD_RISK|+0.550|+1.283|61.7%|+0.550/+3.544|105/115|5,272|-1.00/+0.79|-4.507|
|5|trend_structure_entry_ALL|66.5|SUPPORTED_MATERIAL_RISK|+5.391|+5.886|66.1%|+4.389/+8.156|115/115|10,503|-4.24/-3.52|-5.372|
|6|wedge_entry_ALL|64.1|SUPPORTED_MATERIAL_RISK|+2.509|+0.800|54.8%|+0.295/+3.128|107/115|8,501|-1.94/-3.20|-4.969|
|10|double_top_bottom_entry_ALL|47.1|WATCH_UNSTABLE_ASYMMETRY|+0.603|+0.999|57.4%|+1.711/-0.349|114/115|5,234|-2.00/-3.99|-8.121|
|12|cup_handle_exit_ALL|39.0|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|41.7%|-0.960/+1.627|104/115|4,887|+2.34/+1.81|-5.139|
|13|double_top_bottom_exit_ALL|36.8|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|37.4%|+0.000/-1.219|100/115|4,905|+1.16/+4.75|-6.538|
|18|head_shoulders_exit_ALL|31.9|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|25.2%|+0.000/+0.000|59/115|4,637|-0.43/+0.00|-6.548|
|21|wedge_exit_ALL|31.5|REJECT_NO_HOLDOUT_EDGE|-0.216|+0.000|43.5%|-1.091/+1.277|107/115|4,782|+1.46/+1.59|-4.881|
|23|triangle_exit_ALL|30.7|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|13.9%|+0.000/+0.000|41/115|4,640|+0.00/+0.37|-6.548|
|28|trend_structure_exit_ALL|28.3|REJECT_OVERFIT|+0.183|-0.120|47.0%|-1.011/+4.210|112/115|5,152|+3.76/+3.04|-7.316|
|35|head_shoulders_entry_ALL|25.3|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|32.2%|+0.000/+0.000|61/115|4,723|-1.00/-0.00|-7.033|
|42|triangle_entry_ALL|10.9|REJECT_LOW_FIRING|+0.000|+0.000|4.3%|+0.000/+0.000|7/115|4,618|+0.00/+0.00|-7.033|
|60|flag_pennant_entry_ALL|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|65|flag_pennant_exit_ALL|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|

## Complete priority ranking — all 70 nonbaseline arms

`Δmed` is median strategy-return delta versus baseline. `H+ cells` is the
holdout fraction with positive delta. `DD Δ` is baseline DD minus arm DD, so
positive is better. All values are percentage points unless shown otherwise.

|Rank|Arm|Score|Verdict|Train Δmed|Holdout Δmed|H+ cells|L/S Δmed|Fire|Trades|DD Δ med/p90|Δ vs B&H med|
|---:|---|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|
|1|cup_handle_entry_ALL|73.8|SUPPORTED_MILD_RISK|+0.550|+1.283|61.7%|+0.550/+3.544|105/115|5,272|-1.00/+0.79|-4.507|
|2|cup_handle_entry_4h|70.2|SUPPORTED_MILD_RISK|+0.029|+0.340|53.9%|+0.088/+0.408|92/115|4,915|-0.54/+1.92|-5.482|
|3|trend_structure_entry_1h|70.0|SUPPORTED_MILD_RISK|+0.406|+0.369|57.4%|+0.412/+0.315|104/115|5,105|-1.96/+0.83|-4.591|
|4|trend_structure_entry_D|69.4|SUPPORTED_MATERIAL_RISK|+1.611|+0.991|58.3%|+0.470/+1.377|108/115|5,069|-2.76/+0.64|-7.292|
|5|trend_structure_entry_ALL|66.5|SUPPORTED_MATERIAL_RISK|+5.391|+5.886|66.1%|+4.389/+8.156|115/115|10,503|-4.24/-3.52|-5.372|
|6|wedge_entry_ALL|64.1|SUPPORTED_MATERIAL_RISK|+2.509|+0.800|54.8%|+0.295/+3.128|107/115|8,501|-1.94/-3.20|-4.969|
|7|trend_structure_entry_4h|59.9|SUPPORTED_MATERIAL_RISK|+0.569|+1.057|57.4%|+1.334/+0.855|108/115|5,083|-1.25/-3.93|-7.191|
|8|trend_structure_exit_1h|55.8|WATCH_UNSTABLE_ASYMMETRY|+0.000|+0.040|50.4%|+0.000/+2.379|95/115|4,748|+0.27/+0.29|-4.680|
|9|trend_structure_exit_15m|51.5|WATCH_UNSTABLE_ASYMMETRY|+0.000|+0.048|50.4%|-0.229/+4.911|111/115|5,103|+3.31/+1.90|-6.911|
|10|double_top_bottom_entry_ALL|47.1|WATCH_UNSTABLE_ASYMMETRY|+0.603|+0.999|57.4%|+1.711/-0.349|114/115|5,234|-2.00/-3.99|-8.121|
|11|wedge_exit_15m|41.0|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|43.5%|+0.000/+1.739|96/115|4,698|+1.58/+1.63|-6.301|
|12|cup_handle_exit_ALL|39.0|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|41.7%|-0.960/+1.627|104/115|4,887|+2.34/+1.81|-5.139|
|13|double_top_bottom_exit_ALL|36.8|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|37.4%|+0.000/-1.219|100/115|4,905|+1.16/+4.75|-6.538|
|14|double_top_bottom_exit_15m|36.6|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|37.4%|+0.000/-1.219|99/115|4,902|+1.16/+4.75|-6.538|
|15|cup_handle_entry_D|35.2|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|13.9%|+0.000/+0.000|31/115|4,664|+0.02/+0.00|-4.811|
|16|cup_handle_entry_1h|33.9|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|47.0%|+0.000/+1.456|81/115|4,880|-0.69/-2.40|-4.808|
|17|head_shoulders_exit_15m|32.0|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|25.2%|+0.000/+0.000|58/115|4,636|-0.43/+0.00|-6.548|
|18|head_shoulders_exit_ALL|31.9|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|25.2%|+0.000/+0.000|59/115|4,637|-0.43/+0.00|-6.548|
|19|wedge_entry_4h|31.8|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|31.3%|+0.000/+0.000|62/115|4,772|+0.00/+0.00|-7.033|
|20|wedge_exit_4h|31.7|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|11.3%|+0.000/+0.000|25/115|4,609|+0.00/-0.53|-5.913|
|21|wedge_exit_ALL|31.5|REJECT_NO_HOLDOUT_EDGE|-0.216|+0.000|43.5%|-1.091/+1.277|107/115|4,782|+1.46/+1.59|-4.881|
|22|wedge_entry_1h|31.1|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|33.9%|+0.000/+0.941|56/115|4,943|-0.00/+0.02|-6.368|
|23|triangle_exit_ALL|30.7|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|13.9%|+0.000/+0.000|41/115|4,640|+0.00/+0.37|-6.548|
|24|triangle_exit_15m|29.8|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|13.0%|+0.000/+0.000|39/115|4,640|+0.00/+0.37|-6.548|
|25|wedge_entry_D|28.6|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|17.4%|+0.000/+0.000|42/115|4,675|+0.27/+0.00|-7.349|
|26|double_top_bottom_entry_1h|28.5|REJECT_OVERFIT|+0.001|+0.000|46.1%|+0.000/+0.088|99/115|4,932|-1.42/-2.80|-5.419|
|27|double_top_bottom_entry_4h|28.3|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|48.7%|+1.052/+0.000|94/115|4,856|-0.53/-0.48|-8.336|
|28|trend_structure_exit_ALL|28.3|REJECT_OVERFIT|+0.183|-0.120|47.0%|-1.011/+4.210|112/115|5,152|+3.76/+3.04|-7.316|
|29|cup_handle_entry_15m|27.8|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|17.4%|+0.000/+0.000|29/115|4,661|+0.00/-0.76|-6.548|
|30|cup_handle_exit_15m|27.3|REJECT_NO_HOLDOUT_EDGE|+0.000|-0.271|39.1%|-0.960/+0.870|104/115|4,895|+2.34/+1.81|-5.139|
|31|double_top_bottom_entry_D|27.0|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|18.3%|+0.000/+0.000|43/115|4,673|+0.00/-0.36|-7.349|
|32|head_shoulders_entry_1h|26.9|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|13.0%|+0.000/+0.000|25/115|4,638|-0.53/-0.00|-5.419|
|33|head_shoulders_entry_4h|26.4|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|18.3%|+0.000/+0.000|40/115|4,674|-0.53/-0.00|-7.033|
|34|wedge_exit_1h|25.7|REJECT_NO_HOLDOUT_EDGE|-0.085|-0.208|36.5%|-0.861/+0.403|101/115|4,712|+0.40/+1.98|-6.643|
|35|head_shoulders_entry_ALL|25.3|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|32.2%|+0.000/+0.000|61/115|4,723|-1.00/-0.00|-7.033|
|36|cup_handle_exit_1h|24.6|REJECT_NO_HOLDOUT_EDGE|+0.000|+0.000|12.2%|+0.000/+0.000|32/115|4,602|+0.00/+0.00|-7.033|
|37|wedge_entry_15m|24.3|REJECT_OVERFIT|+0.533|+0.000|49.6%|+0.000/+2.291|100/115|8,190|-1.65/-3.87|-5.497|
|38|trend_structure_entry_15m|20.9|REJECT_OVERFIT|+0.354|+0.000|48.7%|+0.000/+4.329|105/115|9,740|-2.25/-3.05|-6.548|
|39|trend_structure_exit_4h|16.8|REJECT_LOW_FIRING|+0.000|+0.000|7.0%|+0.000/+0.000|16/115|4,606|+0.00/+0.00|-5.419|
|40|head_shoulders_entry_D|14.4|REJECT_LOW_FIRING|+0.000|+0.000|9.6%|+0.000/+0.000|16/115|4,631|+0.00/+0.00|-6.548|
|41|double_top_bottom_exit_1h|11.4|REJECT_LOW_FIRING|+0.000|+0.000|6.1%|+0.000/+0.000|16/115|4,611|+0.00/+0.00|-6.548|
|42|triangle_entry_ALL|10.9|REJECT_LOW_FIRING|+0.000|+0.000|4.3%|+0.000/+0.000|7/115|4,618|+0.00/+0.00|-7.033|
|43|triangle_exit_1h|10.6|REJECT_LOW_FIRING|+0.000|+0.000|1.7%|+0.000/+0.000|3/115|4,609|+0.00/+0.00|-6.548|
|44|triangle_entry_1h|9.9|REJECT_LOW_FIRING|+0.000|+0.000|4.3%|+0.000/+0.000|6/115|4,617|+0.00/+0.00|-7.033|
|45|trend_structure_exit_D|9.1|REJECT_LOW_FIRING|+0.000|+0.000|0.9%|+0.000/+0.000|2/115|4,608|+0.00/+0.00|-6.548|
|46|wedge_exit_D|8.7|REJECT_LOW_FIRING|+0.000|+0.000|1.7%|+0.000/+0.000|7/115|4,614|+0.00/+0.00|-6.548|
|47|double_top_bottom_exit_D|8.7|REJECT_LOW_FIRING|+0.000|+0.000|0.9%|+0.000/+0.000|2/115|4,609|+0.00/+0.00|-6.548|
|48|head_shoulders_exit_1h|8.6|REJECT_LOW_FIRING|+0.000|+0.000|1.7%|+0.000/+0.000|4/115|4,610|+0.00/+0.00|-6.548|
|49|cup_handle_exit_4h|7.8|REJECT_LOW_FIRING|+0.000|+0.000|0.9%|+0.000/+0.000|1/115|4,609|+0.00/+0.00|-6.548|
|50|double_top_bottom_exit_4h|7.3|REJECT_LOW_FIRING|+0.000|+0.000|0.9%|+0.000/+0.000|1/115|4,609|+0.00/+0.00|-6.548|
|51|double_top_bottom_entry_15m|6.5|REJECT_LOW_FIRING|+0.000|+0.000|0.9%|+0.000/+0.000|1/115|4,611|+0.00/+0.00|-6.548|
|52|triangle_entry_15m|5.5|REJECT_LOW_FIRING|+0.000|+0.000|0.0%|+0.000/+0.000|1/115|4,610|+0.00/+0.00|-6.548|
|53|triangle_entry_4h|5.4|REJECT_LOW_FIRING|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|54|cup_handle_exit_D|4.4|REJECT_LOW_FIRING|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|55|head_shoulders_entry_15m|4.3|REJECT_LOW_FIRING|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|56|triangle_entry_D|3.6|REJECT_LOW_FIRING|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|57|flag_pennant_entry_15m|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|58|flag_pennant_entry_1h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|59|flag_pennant_entry_4h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|60|flag_pennant_entry_ALL|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|61|flag_pennant_entry_D|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|62|flag_pennant_exit_15m|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|63|flag_pennant_exit_1h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|64|flag_pennant_exit_4h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|65|flag_pennant_exit_ALL|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|66|flag_pennant_exit_D|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|67|head_shoulders_exit_4h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|68|head_shoulders_exit_D|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|69|triangle_exit_4h|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|
|70|triangle_exit_D|0.0|REJECT_NO_OP|+0.000|+0.000|0.0%|+0.000/+0.000|0/115|4,609|+0.00/+0.00|-6.548|

## Limitations and next evidence gate

The Luna input contains cell-distribution summaries, not the raw per-trade series;
therefore this report does not invent bootstrap confidence intervals or pool
Sharpe values. The next gate for any proposed live use is a frozen combination
campaign using only supported arms, followed by leave-one-arm-out ablations,
rolling temporal blocks, capital-correct portfolio DD, and exact-engine/live
parity checks. Until those pass, current live settings should not be promoted on
the strength of this vector ranking.
