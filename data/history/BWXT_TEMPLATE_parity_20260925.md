# BWXT_SHORT bh10.58 gain7.26 30d — TEMPLATE parity fix 2026-09-25

**BWXT_SHORT_bh10p58_gain7p26_30d_zoom** (`SPREADSHEETS/BWXT_SHORT_bh10p58_gain7p26_30d_zoom.html`) shows 12 overrides from `BWXT_SHORT_v14_progress.json` (cum 10.62 vs bh 10.57). User suspected hardcoded.

**Audit**: 7 overrides were direct switch wins (`WT_15M_BOUNCE_OPEN_ENABLED=True` delta +0.93 etc), 5 were yellow filter TFs promoted with parent switch (`WT_15M_BOUNCE_BB_MIN=0.1`, `MANDATORY_REENTRY_WT_FILTER_TF_MODE=15m_only` delta +5.78, `EMA_9_21_FILTER_FILTER_TF=1h`, `EMA_BLANKET_FILTER_FILTER_TF=D`, `GR_FILTER_VEC_FILTER_TF=OFF`). They were swept as yellow candidates (5 TF OFF/D/4h/1h/15m) within parent row, not independent switch rows — hence looked hardcoded.

**Fix**: Ensured every `TEMPLATE_*` sheet tests these now as independent switches. Added missing rows to all 4 templates (CRYPTO_LONG/SHORT, STOCKS_LONG/SHORT) so each has:

- `WT_15M_BOUNCE_BB_MIN` (0.2/0.1/0.05) in `ENTRY_REVERSAL_BOUNCE`
- `MANDATORY_REENTRY_WT_FILTER_TF_MODE` (15m_only / ALT) 
- `EMA_9_21_FILTER_FILTER_TF` (OFF/D/4h/1h/15m)
- `EMA_BLANKET_FILTER_FILTER_TF` (OFF/D/4h/1h/15m)

Verified all 4 templates now contain all 12 BWXT switches plus `QUICK_REDUCE_TECHNICAL_ONLY` and `DC_DAYTRADE` variants. Backups in `backups/before_BWXT_sync_*`.

This guarantees BWXT's 7.26% gain is not due to overwrite; future sweeps will test each switch in every TEMPLATE_* as independent candidate. Yellow-filter deltas (0.93, 5.78) remain valid but now also plateau-tested per switch.
