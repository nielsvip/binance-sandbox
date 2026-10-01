# PENDING WORK — 2026-10-01 (user orders, binding). Owners are agent roles; update status in place.

## Standing law
- A switch/filter only has meaning if it is IDENTICALLY connected in live AND vector AND all configs. In switches => must be in live. In vector => in live. In live => in vector.
- CONFIG (via data/cat_side_defaults_4.json) is source of truth for defaults. Every switch and filter has exactly ONE default row, BOLD, is_default column == config for that cat_side.
- Anything that changes baselines is a SWITCH with default OFF (neutral): never moves a trade until tested; per sym_side keeps it only if it wins.
- EVERY filter must produce a delta in every test. Never 0 in column F for a None. 0 only for exact 0.00000.
- Draft first, prove, then push to servers. Backup before every edit. Never revert; diff and merge.
- User GO (2026-10-01) given: unlock and fix live files where needed (ez_manage, execute_now, tradier_manage, config*, *_indicators, ez_positions_quick), then relock. Test before restart.

## Items
| # | Item | Owner | Status |
|---|------|-------|--------|
| 1 | Defaults: every template group one bold default == config per cat_side (430 groups/template had none); is_default col; use cat_side_defaults_4.json | DEF2 | todo FIRST |
| 2 | PROFIT_TARGET = switch: dc_high_15m-0.1% (short: low+0.1%), tested for 15m/1h/4h, multiple may be True; must exist in live+vector+configs | DEF2 | todo |
| 3 | Held items C2/006, C2/007, N2/007, N2/011, N2/012, N4/2 become switches default OFF (neutral), deploy, then tested per sym_side | DEF2+FLT2 | todo |
| 4 | Filters all produce deltas: pilot + v12_quick first, then execute_now. Kindergarten ~28 variants/TFs proven on entries and exits (crypto+stocks); EMA_BLANKET removed only after that proof; EMA_9_21 filter + MIN_TFS live consumer; check_ema_9_21 fix | FLT2 | todo |
| 5 | Unwired filter families (25 *_FILTER_TF etc., 36 yellow cols with no consumer): wire live+vector | FLT2 | todo |
| 6 | wt_dc_entry_scorer_vec to s1; pilots must LOAD NPZ at start (verify); pilot blocked-code stalls analysed/fixed immediately | FLT2+SCH | todo |
| 7 | LIVE patches apply: staged batch1 (indicator keys), batch5 (stock trend gates; LH_HL, kindergarten, ADX_RANGING_THRESHOLD, HTF_GATE_*), batch6, batch_defaults, HARDCODED_RALLY live twin, KINDERGARTEN_CUMULATIVE_MIN_TFS=0, AUGMENT_MIN_GAIN_PCT -> MIN_GAIN_TO_BUY_AGGRESSIVELY alias; relock | LIVE | todo |
| 8 | 33 crypto reentry paths (REENTRY_BOUNCE_BAR_GR_*, REENTRY_DC_MID_*...) hooked identically in ez_manage; full reentry path parity (308 keys, data/wiring/reentry/paths.csv) | PAR | todo |
| 9 | Test REENTRY_APPLY_ENTRY_GATES_ENABLED across all reentry paths; stack ON = live must do what vector says, ignore vintage switches that break parity | PAR | todo |
| 10 | GAP_RISK_EXIT True everywhere; GAP_RISK_REENTRY after gap exit: reenter when price moves in right direction or LTF bottom, first 120 min; vector twin of GAP_MORNING_REENTRY | PAR | todo |
| 11 | Funding/OI NPZs (crypto only): install staged data/wiring/f2 when no pilot holds the symbol; daily refresh cron | PAR | go given |
| 12 | Column F: no zeros; sync fixed v15_pilot.py to s1/s2/s5 after diffing server copies; rescan sheets | SCH | in flight |
| 13 | Scheduler: before market open new avg deltas + new defaults for new templates; CPU >85% always | SCH | todo |
| 14 | After one more full sym_side run on correct defaults: drop rows that never produce delta to speed up | SCH | later |
| 15 | Total live=vector parity tested path by path (numpy vs live) | PAR | night |
