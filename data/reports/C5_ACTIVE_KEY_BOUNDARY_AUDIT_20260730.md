# C5 active-key and current-only reporting audit — 2026-07-30

## Verdict

The `117 ACTIVE + 42 retained historical` line was wrong. It came from the
dated `trb_matrix_universe_snapshot_20260729`, while the hash-bound c5 digest
used the 129 keys in the S1 `symbols_trb_long.json` and
`symbols_trb_short.json` authority.

This was not a simple twelve-key addition. The two sets differed in 72 places:

- 42 current keys were incorrectly classified as historical.
- 30 removed keys were incorrectly classified as active.
- The net count difference was `42 - 30 = 12`.

All 129 current keys are present in the 159-column canonical matrix. None is
missing. The corrected classification is therefore:

```text
159 visible columns = 129 ACTIVE + 30 retained historical
```

## Current keys incorrectly called historical

```text
ABT_SHORT
ADBE_SHORT
ASTS_LONG
ASTS_SHORT
AXTI_LONG
BHP_SHORT
BOTZ_LONG
CDW_SHORT
CMC_SHORT
COPX_LONG
CRWD_LONG
DAR_SHORT
DUOL_SHORT
EGO_SHORT
EQT_LONG
GM_SHORT
GOOGL_LONG
GOOGL_SHORT
LRCX_SHORT
MPC_SHORT
MRVL_SHORT
MSFT_SHORT
NUKZ_LONG
NVDA_SHORT
OKE_SHORT
PBF_SHORT
PLTR_LONG
PLTR_SHORT
PR_SHORT
PYPL_SHORT
QCOM_LONG
RIO_SHORT
RKLB_LONG
RRC_SHORT
RS_SHORT
SLV_LONG
SMG_SHORT
STZ_SHORT
TSM_LONG
UAN_LONG
UEC_SHORT
VALE_SHORT
```

## Removed keys incorrectly called active

```text
AMAT_SHORT
APO_LONG
BKR_LONG
BK_LONG
BOTZ_SHORT
CAT_SHORT
CDW_LONG
CLX_SHORT
CMC_LONG
COE_SHORT
CRWD_SHORT
DVN_LONG
FCN_LONG
FIVN_LONG
NKE_SHORT
NLR_SHORT
NXE_SHORT
OLED_LONG
ORCL_SHORT
PWR_SHORT
QRVO_LONG
RDDT_LONG
SAP_LONG
SNOW_LONG
STLD_LONG
USAR_LONG
USAR_SHORT
VICR_SHORT
WDAY_LONG
XLE_LONG
```

## C5 current-only numeric boundary

The fresh gzip declares:

```text
CURRENT_CAMPAIGN=stocks_repaired_20260730_c5
CURRENT_ENGINE_CUTOFF=2026-07-30T03:30:10Z
CURRENT_CONTRACT_VERSION=tradier-matrix-exec-c5-20260730
CURRENT_MATRIX_SCOPE=CURRENT_CAMPAIGN_CURRENT_CODE_NPZ_SIDE_FINGERPRINTS_ONLY
CANONICAL_MATRIX=data/reports/SWITCH_MATRIX_TRB.csv.gz
```

It contains exactly one numeric cell:

```text
VT_LONG
DYNAMIC_SCORE_COUNTER_EXIT_ENABLED=true
delta gain/mo versus B&H = 0.1978
```

The S1 database contains exactly one c5 ENGINE row. Its evidence is:

```text
validation_status=PASS
gain_per_mo=0.4745
delta_gain_mo_vs_bh=0.1978
time_in_mkt_pct=96.3753
trades=68
real_closes=67
contract_fingerprint=tradier-matrix-exec-c5-20260730:e293747e5f6ed20cbb303300aa618148fdf5c029b335f6b94fb41996a4b32047
```

The fingerprint is the sole value returned by the current S1
`matrix_contract_fingerprints("VT", "LONG")` and was independently confirmed
accepted. No c4, old-campaign, failed-validation, or stale-fingerprint cell is
present in the canonical numeric grid.

This row is gray, not promotable: its 96.3753% TIM is outside VT_LONG's
20–60% remainder band.

## Repair

Synchronized reporting checkouts now use the digest provenance only when:

- it is bound to the exact local canonical gzip SHA-256;
- gzip campaign, cutoff, contract, scope and canonical path match the sidecar;
- all long and short ranks are contiguous;
- every rank has the required 50–80% top-10 or 20–60% remainder contract.

The S1 writer continues to read its current tradeable JSON files directly.
A stale or malformed matching sidecar fails closed instead of allowing local
symbol files to reinterpret an S1 export.
