import fs from 'node:fs/promises';
import path from 'node:path';
import { Workbook, SpreadsheetFile } from '@oai/artifact-tool';

const root = '/Users/niels/Documents/binance';
const outputDir = path.join(root, 'outputs', 's1_trb_last3days_20260803');
const outputPath = path.join(outputDir, 'S1_TRB_Last_3_Days_Deep_Analysis.xlsx');
const previewDir = path.join(outputDir, 'previews');

const palette = {
  navy: '#16324F',
  blue: '#2F75B5',
  teal: '#0F766E',
  green: '#D9EAD3',
  greenText: '#1B5E20',
  amber: '#FFF2CC',
  amberText: '#7F6000',
  red: '#F4CCCC',
  redText: '#990000',
  gray: '#F3F4F6',
  grayText: '#4B5563',
  border: '#D1D5DB',
  ink: '#17202A',
  white: '#FFFFFF',
};

const source = (file, section) => `data/reports/${file}${section ? ` — ${section}` : ''}`;

const lifecycle = [
  ['2026-08-01','Cohort 2','AXTI_SHORT','SHORT','DC_D_N20_B40_R0','same DC','WT_OUT_5m_D16_P75, 25%','WT_FULL_EXIT_4h_D0_P30','DC_15m_N5_B250_R0',233.5101,23.9804,209.5297,22.2998,99.7799,61,28.4459,'WORKS_RESEARCH','Positive causal vector lifecycle; 209 unique / 43,825 duplicate-quarantined behaviors.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','CIEN_SHORT','SHORT','DC_1h_N20_B100_R10','WT_IN_5m_D0_P10','WT_OUT_1h_D3_P30, 75%','WT_FULL_EXIT_15m_D8_P75','WT_IN_5m_D0_P75',214.8574,13.5423,201.3151,16.1051,85.1527,35,14.3436,'WORKS_RESEARCH','Positive causal vector lifecycle; target-TIM alternative retained.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','MNTS_SHORT','SHORT','DC_5m_N20_B40_R0','WT_IN_5m_D0_P10','none','WT_FULL_EXIT_1h_D16_P10','WT_IN_15m_D0_P30',596.0235,36.8841,559.1395,49.2334,99.1667,8,3.7306,'WORKS_RESEARCH','High return but low activity; no positive target-TIM alternative found.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','MP_LONG','LONG','DC_15m_N40_B250_R0','WT_IN_15m_D16_P10','WT_OUT_1h_D0_P30, 75%','WT_FULL_EXIT_15m_D0_P10','DC_5m_N10_B250_R40',4.4305,-6.0561,10.4866,35.6252,90.4876,117,23.5483,'WORKS_RESEARCH','Positive delta versus negative sided B&H; no positive-B&H multiple claimed.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','AG_LONG','LONG','DC_5m_N20_B100_R0','WT_IN_15m_D0_P10','WT_OUT_15m_D3_P0, 75%','WTDC_X40_N3_K75_DC0.8','mandatory exit-price reclaim',5.3215,-10.4460,15.7674,5.0166,1.1264,16,3.2203,'WORKS_RESEARCH','Positive research result but extremely low exposure/TIM; no target-TIM alternative.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','LSCC_SHORT','SHORT','DC_4h_N5_B10_R10','same DC','none','WT_FULL_EXIT_D_D0_P0','mandatory exit-price reclaim',60.8014,7.4281,53.3733,24.7446,99.9169,1,0.4663,'WORKS_RESEARCH','Positive but 1 close; below preferred activity.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','HL_LONG','LONG','DC_5m_N20_B40_R0','WT_IN_5m_D3_P0','none','WT_FULL_EXIT_D_D16_P75','WT_IN_15m_D16_P0',164.3767,11.9542,152.4225,45.4440,99.7617,3,0.2507,'WORKS_RESEARCH','Positive but low activity; target-TIM alternative retained.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','MRVL_LONG','LONG','DC_5m_N40_B100_R40','same DC','none','WTDC_X40_N5_K85_DC0.85','mandatory exit-price reclaim',225.3320,11.7251,213.6069,26.4262,50.1186,30,8.5123,'WORKS_RESEARCH','Accepted post-gap replay; source gap before 2026-04-15 quarantined.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','AMD_LONG','LONG','DC_5m_N5_B40_R10','same DC','none','WT_FULL_EXIT_D_D0_P0','mandatory exit-price reclaim',282.9299,29.2343,253.6956,22.5738,99.9042,2,0.4025,'WORKS_RESEARCH','Positive but low activity; wider target-TIM alternative retained.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-01','Cohort 2','DELL_LONG','LONG','DC_5m_N5_B40_R10','same DC','WT_OUT_15m_D8_P10, 75%','WT_FULL_EXIT_15m_D16_P30','DC_15m_N10_B10_R10',197.9097,15.8163,182.0934,22.2571,90.8663,31,14.4561,'WORKS_RESEARCH','Positive causal vector lifecycle; target-TIM alternative retained.',source('COHORT2_VECTOR_LIFECYCLE_20260801.md','Selected best behavior-unique results')],
  ['2026-08-02','Cohort 3','CIBR_LONG','LONG','DC_5m_N40_B10_R0','WT_IN_15m_D16_P0','none','E02_4h_N30','mandatory exit-price reclaim',79.3192,9.4023,69.9169,18.66,99.38,2,0.47,'WORKS_RESEARCH','Positive but below activity preference.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','PSX_LONG','LONG','DC_5m_N5_B100_R0','same DC','WT_OUT_15m_D16_P10, 25%','E02_4h_N30','DC_15m_N5_B40_R10',16.2579,1.0761,15.1818,42.54,98.64,423,15.12,'WORKS_RESEARCH','Positive and active; TIM above preference.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','USAR_LONG','LONG','DC_5m_N10_B100_R40','same DC','WT_OUT_4h_D3_P75, 75%','WT_FULL_EXIT_1h_D16_P30','WT_IN_15m_D8_P0',145.4748,1.8218,143.6530,35.02,95.75,104,6.28,'WORKS_RESEARCH','Strong positive vector result; exact finalist adapter required before V8.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','EOG_LONG','LONG','DC_15m_N5_B100_R10','WT_IN_15m_D3_P0','WT_OUT_4h_D0_P75, 25%','WTDC_X45_N5_K75_DC0.8','mandatory exit-price reclaim',6.6061,0.5682,6.0379,37.65,99.99,42,1.50,'WORKS_RESEARCH','Positive but low activity/TIM outside preference.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','QRVO_LONG','LONG','DC_5m_N20_B40_R0','WT_IN_5m_D0_P10','WT_OUT_5m_D0_P75, 75%','WTDC_X25_N3_K75_DC0.85','WT_IN_5m_D0_P10',-29.5290,-1.0506,-28.4784,35.83,70.06,238,97.54,'UNRESOLVED_USE_BH','Honest losing best retained; material native 5m source caveat.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','GOOGL_LONG','LONG','DC_5m_N5_B10_R0','same DC','none','WT_FULL_EXIT_D_D16_P30','mandatory exit-price reclaim',34.8942,4.7000,30.1942,44.20,99.97,4,0.14,'WORKS_RESEARCH','Positive but extremely low activity.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','A_LONG','LONG','DC_5m_N20_B250_R40','WT_IN_5m_D0_P0','WT_OUT_1h_D8_P10, 25%','WTDC_X60_N4_K85_DC0.85','mandatory exit-price reclaim',44.5197,5.3397,39.1800,18.32,98.83,39,10.48,'WORKS_RESEARCH','Positive and active; TIM above preference.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','BG_LONG','LONG','DC_D_N5_B10_R40','same DC','WT_OUT_15m_D8_P30, 25%','WTDC_X40_N5_K85_DC0.8','DC_5m_N20_B10_R0',2.7370,0.1222,2.6148,61.35,98.46,510,18.24,'WORKS_RESEARCH','Positive but high drawdown.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','DAR_LONG','LONG','DC_5m_N40_B250_R0','same DC','WT_OUT_4h_D3_P10, 75%','WTDC_X60_N4_K85_DC0.85','WT_IN_5m_D8_P75',15.4567,1.1268,14.3299,85.68,96.21,220,7.87,'WORKS_RESEARCH','Positive but high drawdown/TIM.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 3','TRGP_LONG','LONG','DC_4h_N5_B250_R40','WT_IN_5m_D16_P0','none','WTDC_X45_N5_K75_DC0.8','DC_5m_N5_B10_R0',38.4681,5.0416,33.4265,27.36,100.00,1,0.04,'WORKS_RESEARCH','Profit-ranked winner has 1 close; target-TIM alternative preferred.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 4','USAR_SHORT','SHORT','DC_1h_N20_B250_R40','same DC','WT_OUT_5m_D16_P75, 75%','WTDC_X40_N4_K85_DC0.8','mandatory exit-price reclaim',24.3108,-1.8376,26.1483,26.49,2.42,48,null,'WORKS_RESEARCH','Positive but extremely low TIM; vector winner, no exact/live credit.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','CLF_SHORT','SHORT','DC_5m_N20_B40_R0','none','WT_OUT_1h_D3_P75, 75%','WTDC_X45_N5_K75_DC0.8','mandatory exit-price reclaim',14.0261,1.7679,12.2582,54.00,99.90,149,null,'WORKS_RESEARCH','Positive vector winner; exact complete-recipe review still needed.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','NXE_SHORT','SHORT','DC_5m_N5_B250_R40','WT_IN_5m_D8_P30','WT_OUT_15m_D8_P0, 75%','WTDC_X60_N3_K85_DC0.8','mandatory exit-price reclaim',2.3228,-0.5318,2.8546,15.42,10.73,379,null,'WORKS_RESEARCH','Clears operator floor but low TIM; vector only.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','MP_SHORT','SHORT','DC_5m_N20_B40_R0','none','none','WTDC_X60_N3_K75_DC0.85','mandatory exit-price reclaim',2.1281,-6.5302,8.6583,27.35,6.16,182,null,'WORKS_RESEARCH','Clears operator floor but low TIM; vector only.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','RBLX_SHORT','SHORT','DC_15m_N10_B250_R10','WT_IN_5m_D0_P30','WT_OUT_15m_D0_P0, 50%','WTDC_X25_N5_K85_DC0.85','mandatory exit-price reclaim',1.7027,0.2169,1.4859,56.12,33.29,235,null,'UNRESOLVED_USE_BH','Beats B&H multiple but below the explicit 2%/month operator floor.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','AGI_SHORT','SHORT','DC_15m_N10_B250_R10','same DC','WT_OUT_5m_D3_P0, 75%','WTDC_X40_N5_K85_DC0.85','mandatory exit-price reclaim',-0.2013,-3.1531,2.9518,15.98,9.24,134,null,'UNRESOLVED_USE_BH','Positive delta but strategy gain below 2%/month operator floor.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','GDX_SHORT','SHORT','DC_5m_N20_B40_R0','WT_IN_15m_D3_P75','WT_OUT_5m_D8_P10, 75%','WTDC_X25_N3_K85_DC0.85','mandatory exit-price reclaim',-0.0210,-4.6617,4.6406,0.29,0.15,7,null,'UNRESOLVED_USE_BH','Positive delta but materially below operator floor and low activity.',source('COHORT4_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','RGLD_SHORT','SHORT','DC_15m_N40_B250_R40','same DC','WT_OUT_5m_D0_P0, 75%','WTDC_X25_N3_K85_DC0.8','mandatory exit-price reclaim',-1.6127,-2.1684,0.5558,9.95,5.88,257,null,'UNRESOLVED_USE_BH','Strategy below operator floor; use B&H baseline.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','HII_SHORT','SHORT','DC_D_N5_B40_R0','same DC','WT_OUT_5m_D3_P10, 50%','WTDC_X60_N4_K85_DC0.85','DC_15m_N10_B10_R40',-1.7214,-0.4419,-1.2795,53.59,95.65,1195,null,'UNRESOLVED_USE_BH','Underperforms sided B&H; no promotion.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 4','AAPL_SHORT','SHORT','DC_15m_N20_B250_R10','same DC','WT_OUT_5m_D3_P0, 50%','WT_FULL_EXIT_15m_D16_P0','mandatory exit-price reclaim',-4.0661,-2.9043,-1.1618,47.10,41.84,646,null,'UNRESOLVED_USE_BH','Underperforms sided B&H; no promotion.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Selected complete recipes')],
  ['2026-08-02','Cohort 5','AAPL_LONG','LONG','DC_1h_N5_B250_R40','WT_IN_15m_D0_P0','WT_OUT_1h_D16_P0, 50%','WT_FULL_EXIT_D_D0_P0','DC_5m_N40_B10_R0',23.5064,2.8913,20.6151,23.03,99.77,163,5.83,'WORKS_RESEARCH','Positive causal vector lifecycle; source gap disclosed.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','ARM_LONG','LONG','DC_5m_N20_B40_R0','same DC','none','E02_4h_N20','WT_IN_15m_D0_P75',65.9960,3.1166,62.8794,57.77,95.88,72,2.57,'WORKS_RESEARCH','Positive; target-TIM alternative retained.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','BK_LONG','LONG','DC_5m_N5_B10_R0','same DC','WT_OUT_15m_D3_P0, 25%','WTDC_X45_N4_K85_DC0.8','WT_IN_15m_D0_P0',16.4280,4.1693,12.2586,26.00,94.34,664,27.87,'WORKS_RESEARCH','Positive but below 5x multiple; TIM above preference.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','DVN_LONG','LONG','DC_1h_N10_B100_R0','same DC','none','E02_1h_N20','DC_5m_N20_B40_R0',3.5732,-0.3996,3.9728,71.04,95.04,238,8.51,'WORKS_RESEARCH','Positive after wider causal beam; high DD/TIM.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','INTC_LONG','LONG','DC_5m_N5_B40_R0','same DC','WT_OUT_4h_D0_P0, 75%','E02_4h_N10','mandatory exit-price reclaim',41.0688,3.5742,37.4945,53.49,33.15,52,1.86,'WORKS_RESEARCH','Positive and within non-top TIM range.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','FANG_LONG','LONG','DC_5m_N5_B10_R10','same DC','WT_OUT_15m_D8_P75, 75%','WT_FULL_EXIT_4h_D0_P30','DC_5m_N20_B40_R0',9.6086,0.0683,9.5403,45.33,99.04,254,9.08,'WORKS_RESEARCH','Positive but TIM above preference.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','GM_LONG','LONG','DC_5m_N10_B10_R0','same DC','none','WTDC_X45_N5_K75_DC0.85','mandatory exit-price reclaim',28.8699,3.4502,25.4197,47.59,99.78,1,0.04,'WORKS_RESEARCH','Positive by gain rule but 1 close; higher-activity alternative retained.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','MPC_LONG','LONG','DC_5m_N5_B10_R0','WT_IN_5m_D8_P10','WT_OUT_4h_D3_P75, 75%','E02_D_N30','DC_5m_N5_B10_R0',23.2725,2.0402,21.2323,62.25,99.82,58,2.07,'WORKS_RESEARCH','Positive; target-TIM alternative retained.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','ROKU_LONG','LONG','DC_5m_N5_B40_R0','WT_IN_5m_D16_P0','WT_OUT_4h_D0_P10, 25%','WT_FULL_EXIT_15m_D8_P30','WT_IN_5m_D8_P0',59.8692,4.3603,55.5089,32.19,93.20,396,14.16,'WORKS_RESEARCH','Strong positive; exact finalist adapter required before V8.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
  ['2026-08-02','Cohort 5','RRC_LONG','LONG','DC_5m_N5_B10_R0','WT_IN_5m_D16_P0','WT_OUT_4h_D0_P0, 75%','WTDC_X45_N5_K75_DC0.8','mandatory exit-price reclaim',10.3798,0.5572,9.8225,43.88,99.98,51,1.82,'WORKS_RESEARCH','Positive but TIM above preference.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Selected behavior-unique lifecycle results')],
];

const hotlist = [
  ['2026-08-02',1,'CLF_SHORT','ENTRY_BOUNCE_5M_LOW','BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED — 1h arm / DC / 5m trail',24.291,-12.909,37.200,6.206,69.133,33.383,0,'HOTLIST_EXACT_REPLAY_PENDING','Vector evidence; not dispatched or live.',source('PATH_PRODUCTIVITY_ANALYSIS_20260802.md','Strict priority combinations')],
  ['2026-08-02',2,'ACN_SHORT','ENTRY_BB_RECOVERY','BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED — 4h arm / immediate / 5m trail',17.698,1.663,16.035,1.142,59.160,13.458,0,'HOTLIST_EXACT_REPLAY_PENDING','Vector evidence; not dispatched or live.',source('PATH_PRODUCTIVITY_ANALYSIS_20260802.md','Strict priority combinations')],
  ['2026-08-02',3,'NVDA_LONG','ENTRY_LADDER_GREEN','BOTTOM_A_PROTECTIVE_TRAIL_EXTENDED — 1h arm / immediate / 5m trail',4.420,3.202,1.218,2.094,28.785,16.651,0,'HOTLIST_EXACT_REPLAY_PENDING','Vector evidence; not dispatched or live.',source('PATH_PRODUCTIVITY_ANALYSIS_20260802.md','Strict priority combinations')],
];

const scalar = [
  ['Current differential audit','Current matrix exact rows','Accepted current exact rows',167,'rows','CURRENT_EXACT','Correct current-contract exact evidence; only 4 rows pass TIM gate.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Current evidence')],
  ['Current differential audit','Current matrix exact rows','Accepted c5 exact rows',0,'rows','NOT_YET_AVAILABLE','No c5 exact rows accepted in the differential snapshot.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Current evidence')],
  ['Current differential audit','Current matrix exact rows','TIM pass / fail',4,'rows','TIM_FAIL_DOMINANT','4 pass versus 163 fail on accepted current exact rows.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','TIM gate on accepted current exact rows')],
  ['Current differential audit','Canonical matrix row statuses','OK',32,'rows','WORKS_EXACT','Exact rows marked OK in the current audit.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Current differential audit','Canonical matrix row statuses','RECONNECT',24,'rows','DOES_NOT_WORK_YET','Static reconnect backlog, not economic proof.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Current differential audit','Canonical matrix row statuses','INERT_AT_VALUE',12,'rows','DOES_NOT_WORK','No distinct behavior at tested value.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Current differential audit','Canonical matrix row statuses','DEGENERATE',4,'rows','DOES_NOT_WORK','Degenerate evidence; do not promote.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Current differential audit','Canonical matrix row statuses','NOT_ENGINE_TESTED',1073,'rows','PENDING','Not yet tested by exact engine.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Current differential audit','Canonical matrix row statuses','STALE_ENGINE_CONTRACT',1727,'rows','STALE','Retained but excluded from current promotion.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Canonical matrix row statuses')],
  ['Cohort 3 scalar lane','New reviewed adapter cells','Strict PASS',50,'cells','WORKS_RESEARCH','Unique nonzero action/metric behavior; amber/vector only.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Scalar causal matrix lane')],
  ['Cohort 3 scalar lane','New reviewed adapter cells','Quarantined',100,'cells','DOES_NOT_WORK','Inert or broad-duplicate behavior quarantined.',source('COHORT3_VECTOR_LIFECYCLE_20260802.md','Scalar causal matrix lane')],
  ['Cohort 4 scalar lane','New reviewed adapter cells','Strict PASS',100,'cells','WORKS_RESEARCH','Unique nonzero behavior; no exact/live credit.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Strict scalar lane and portable union audit')],
  ['Cohort 4 scalar lane','New reviewed adapter cells','Quarantined',50,'cells','DOES_NOT_WORK','Inert or duplicate behavior quarantined.',source('COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','Strict scalar lane and portable union audit')],
  ['Cohort 5 scalar lane','New reviewed adapter cells','Strict PASS',89,'cells','WORKS_RESEARCH','Unique nonzero behavior; no exact/live credit.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Cohort 5 scalar lane','New reviewed adapter cells','Quarantined',61,'cells','DOES_NOT_WORK','Inert or duplicate behavior quarantined.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Newest scalar union','Union audit','Logical rows',2189,'rows','AUDIT_SCOPE','Includes prior pilot/MSTR, cohort 3, cohort 4, cohort 5.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Newest scalar union','Union audit','Strict PASS',278,'rows','WORKS_RESEARCH','Only these rows may be considered for later export; no promotion credit.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Newest scalar union','Union audit','Quarantined',1911,'rows','DOES_NOT_WORK','Duplicate/inert amber rows quarantined.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Newest scalar union','Union audit','Causal recalculation groups',133,'groups','PENDING_RECALC','Queued for causal adapter repair or exact V8.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Newest scalar union','Union audit','Accepted isolated plateaus',7,'plateaus','CONTROLLED_EXCEPTION','Only isolated two-value plateaus inside a 5+ value axis.',source('COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md','Strict scalar amber lane and union audit')],
  ['Matrix display completion','Current display artifact','Display cells filled',120,'cells','INCOMPLETE','Exact completion unchanged; current display status is FAIL.',source('MATRIX_DISPLAY_COMPLETION_CURRENT.json','display completion fields')],
  ['Matrix display completion','Current display artifact','Unresolved display cells',1977765,'cells','INCOMPLETE','No B&H fallback allowed; vector_unique=0.',source('MATRIX_DISPLAY_COMPLETION_CURRENT.json','display completion fields')],
  ['Matrix display completion','Current display artifact','Rejected duplicates',2076,'rows','DOES_NOT_WORK','Rejected by uniqueness gate.',source('MATRIX_DISPLAY_COMPLETION_CURRENT.json','display completion fields')],
  ['Current exact distinct proofs','Current audit','Parameters with pairwise distinct proof',7,'parameters','WORKS_EXACT','Distinct proofs are narrow and key-scoped.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Current exact distinct proofs')],
  ['Current exact distinct proofs','Current audit','Confirmed no live decision read',253,'parameters','DOES_NOT_WORK_YET','Reconnect/prune backlog; not dynamic economic proof.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Differential classifications')],
  ['Current exact distinct proofs','Current audit','Discoverability-only',30,'parameters','DOES_NOT_WORK_YET','Metadata reference without functional decision read.',source('SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','Differential classifications')],
];

const accomplishments = [
  ['2026-08-01','S1 core report sync','Digest, workbook and canonical CSV matched S1 timestamp/size/hash in the sync receipt.','DONE','Core S1 artifacts are locally auditable as of the last trusted sync.','Transport identity incident on 2026-08-03 now blocks fresh verification/change.','S1_REPORT_SYNC_RECEIPT_20260801.json'],
  ['2026-08-01','Vector-first path productivity','22-key ENTRY×EXIT campaign completed in 21m50s; median key runtime 7m41s; max 11m22s.','DONE','Interaction search is now tractable compared with scalar exact replay.','Exact replay of the three hotlist recipes is still pending.','PATH_PRODUCTIVITY_ANALYSIS_20260802.md'],
  ['2026-08-01','Hotlist identified','CLF_SHORT, ACN_SHORT and NVDA_LONG produced the strict top-3 BOTTOM_A path combinations.','DONE','Three concrete new paths are ready for full-recipe exact review.','No dispatch/live promotion was authorized.','PATH_PRODUCTIVITY_ANALYSIS_20260802.md'],
  ['2026-08-01','Causal lifecycle cohorts 2–5','40 complete key-side recipes tested; 33 positive research results; 7 unresolved/B&H fallbacks.','DONE','Exact ENTRY/AUGMENT/REDUCE/EXIT/REENTER recipes are now recorded with DD/TIM/close counts.','All remain vector research evidence unless exact V8 parity is proven.','COHORT2_VECTOR_LIFECYCLE_20260801.md; COHORT3_VECTOR_LIFECYCLE_20260802.md; COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md; COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md'],
  ['2026-08-02','Scalar uniqueness union','2,189 logical rows consolidated; 278 strict PASS; 1,911 quarantined; 133 causal recalculation groups.','DONE','Duplicate/inert behavior is prevented from being broadcast into the matrix.','A 133-group repair/recalc queue remains.','COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md'],
  ['2026-08-02','Vector coverage','6/6 required vector paths passed coverage audit; scalar fallback prohibited.','DONE','Ladder sizing, DC bounce/reclaim, WT cross exit, reentry reclaim, filters and accounting are wired for vector research.','Coverage is not a result or promotion receipt.','VECTORIZATION_COVERAGE_AUDIT_20260802.json'],
  ['2026-08-02','Exact finalists selected','USAR_LONG and ROKU_LONG selected as strongest complete lifecycle representatives.','DONE','Clear next exact parity candidates with hash-bound recipes.','Both fail closed as ADAPTER_REQUIRED; exact V8 not dispatched.','EXACT_FINALIST_ADAPTER_AUDIT_20260802.md'],
  ['2026-08-02','Promotion gate','Vector promotion receipt count is 0; full V8 recipe confirmations are 0/60.','BLOCKED','Research evidence remains separated from live/exact credit.','Recipe adapters, current-code reruns and untouched validation remain required.','VECTOR_PROMOTION_RECEIPTS_20260802.json; TRB_60_RESEARCH_READINESS_CURRENT.md'],
  ['2026-08-03','R1 churn containment','Verified submitted fills net -$297.90; final containment set R1 false and newborn window -1 in 201/201 TRB and 170/170 TRC overlays.','CONTAINED','The stale-process precedence bug and newborn-age parsing issue were repaired and regression-tested.','Broker execution ledger reconciliation remains separate from the local log.','R1_CHURN_INCIDENT_20260803.md'],
  ['2026-08-03','Classic formation evidence','Old 15m/ALL formation evidence invalidated after parent-candle and timestamp-source defects.','REJECTED','No invalid evidence can reach matrix, DB or live promotion.','Corrected full-universe train/holdout rerun required.','CLASSIC_FORMATION_PARENT_CANDLE_INVALIDATION_20260803.json'],
  ['2026-08-03','S1 transport integrity','All three trusted S1 host keys mismatched the presented public endpoint; strict checking blocked connections.','BLOCKED','No insecure override or known_hosts mutation occurred.','Independent infrastructure confirmation is required before accepting new keys.','S1_TRANSPORT_IDENTITY_INCIDENT_20260803.md'],
  ['2026-08-03','Current R5 vector qualification','Campaign launched for 77 keys but has 0 completed/current/stale receipts, 0 survivors and 0 promotions.','IN_PROGRESS','No premature result is being written or promoted.','Wait for hash-bound receipts; do not infer from zero-result progress.','R5_VECTOR_RESULTS_CURRENT.md; R5_VECTOR_RESULTS_CURRENT.json'],
  ['2026-08-02','Reversal path registration','115 TRB symbol/sides and 403 reversal path/key jobs registered.','QUEUED','Coverage plan exists for DC break/bounce/rollover and HHHL/WT-cross exits.','Registration is not tested evidence; vector receipts still pending.','REVERSAL_PATH_MATRIX_REGISTRATION_20260802.md'],
];

const sourceFacts = [
  ['Analysis window','Last 3 calendar days relative to 2026-08-03','2026-08-01 through 2026-08-03 UTC','User scope interpreted as recent S1-synced report set.','S1 report files and current snapshots'],
  ['S1 sync authority','Last trusted local receipt','2026-08-01T19:25:29Z','Core digest, workbook and CSV matched S1 at that time.','S1_REPORT_SYNC_RECEIPT_20260801.json'],
  ['Current TRB universe','Directional cells','115','58 LONG + 57 SHORT.','TRB_60_RESEARCH_READINESS_CURRENT.md'],
  ['Market-open floor','Live membership','61 / 60','PASS, but separate from research gate.','TRB_60_RESEARCH_READINESS_CURRENT.md'],
  ['Research gate','Beats B&H and >10 real closes','48 / 60','Separate from full V8 completion.','TRB_60_RESEARCH_READINESS_CURRENT.md'],
  ['Full V8 recipe confirmation','Confirmed','0 / 60','No current full-recipe confirmation.','TRB_60_RESEARCH_READINESS_CURRENT.md'],
  ['Exact finalists','Research candidates','USAR_LONG; ROKU_LONG','Both ADAPTER_REQUIRED; not queued.','EXACT_FINALIST_ADAPTER_AUDIT_20260802.md'],
  ['Current display artifact','Status','FAIL','120 filled; 1,977,765 unresolved; no B&H fallback.','MATRIX_DISPLAY_COMPLETION_CURRENT.json'],
  ['Current vector R5','Receipts','0 / 77','Campaign launched; state_writes=false.','R5_VECTOR_RESULTS_CURRENT.json'],
  ['Current S1 transport','Connection status','BLOCKED','Three trusted host keys mismatched.','S1_TRANSPORT_IDENTITY_INCIDENT_20260803.md'],
];

const primarySourceBasenames = new Set([
  'S1_REPORT_SYNC_RECEIPT_20260801.json','PATH_PRODUCTIVITY_ANALYSIS_20260802.md','PATH_COMBINATION_RUNDOWN_20260802.md','VECTOR_10X_COMBINATION_RUN_20260802.md',
  'COHORT2_VECTOR_LIFECYCLE_20260801.md','COHORT3_VECTOR_LIFECYCLE_20260802.md','COHORT4_SHORT_VECTOR_LIFECYCLE_20260802.md','COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md',
  'EXACT_FINALIST_ADAPTER_AUDIT_20260802.md','SWITCH_MATRIX_TRB_CURRENT_DIFFERENTIAL_AUDIT.md','SWITCH_MATRIX_TRB_HISTORICAL_INTEGRATION_20260801.md',
  'MATRIX_DISPLAY_COMPLETION_CURRENT.json','TRB_60_RESEARCH_READINESS_CURRENT.md','TRB_60_RESEARCH_READINESS_CURRENT.json','R1_CHURN_INCIDENT_20260803.md',
  'CLASSIC_FORMATION_PARENT_CANDLE_INVALIDATION_20260803.json','S1_TRANSPORT_IDENTITY_INCIDENT_20260803.md','R5_VECTOR_RESULTS_CURRENT.md','R5_VECTOR_RESULTS_CURRENT.json',
  'VECTORIZATION_COVERAGE_AUDIT_20260802.json','VECTOR_PROMOTION_RECEIPTS_20260802.json','REVERSAL_PATH_MATRIX_REGISTRATION_20260802.md'
]);

function setValues(sheet, startCell, rows) {
  const range = sheet.getRange(startCell);
  range.writeValues(rows);
}

function styleTitle(sheet, range, title, subtitle) {
  sheet.getRange(range).merge();
  const cell = range.split(':')[0];
  sheet.getRange(cell).values = [[title]];
  sheet.getRange(range).format = { fill: palette.navy, font: { color: palette.white, bold: true, size: 16 }, horizontalAlignment: 'left', verticalAlignment: 'center' };
  sheet.getRange(range).format.rowHeight = 28;
  const rowNum = Number(cell.match(/\d+/)[0]) + 1;
  const endCol = range.split(':')[1].replace(/\d+/g,'');
  sheet.getRange(`A${rowNum}:${endCol}${rowNum}`).merge();
  sheet.getRange(`A${rowNum}`).values = [[subtitle]];
  sheet.getRange(`A${rowNum}:${endCol}${rowNum}`).format = { fill: '#EAF2F8', font: { color: palette.grayText, italic: true, size: 10 }, wrapText: true, verticalAlignment: 'center' };
  sheet.getRange(`A${rowNum}:${endCol}${rowNum}`).format.rowHeight = 32;
}

function styleHeader(sheet, range) {
  sheet.getRange(range).format = { fill: palette.blue, font: { color: palette.white, bold: true }, horizontalAlignment: 'center', verticalAlignment: 'center', wrapText: true, borders: { preset: 'all', style: 'thin', color: palette.white } };
  sheet.getRange(range).format.rowHeight = 32;
}

function styleBody(sheet, range) {
  sheet.getRange(range).format = { font: { color: palette.ink, size: 10 }, verticalAlignment: 'top', wrapText: true, borders: { insideHorizontal: { style: 'thin', color: palette.border }, bottom: { style: 'thin', color: palette.border } } };
}

function addStatusFormatting(sheet, range) {
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'WORKS', format: { fill: palette.green, font: { color: palette.greenText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'PASS', format: { fill: palette.green, font: { color: palette.greenText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'UNRESOLVED', format: { fill: palette.amber, font: { color: palette.amberText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'PENDING', format: { fill: palette.amber, font: { color: palette.amberText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'DOES_NOT_WORK', format: { fill: palette.red, font: { color: palette.redText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'BLOCKED', format: { fill: palette.red, font: { color: palette.redText, bold: true } } });
  sheet.getRange(range).conditionalFormats.add('containsText', { text: 'REJECTED', format: { fill: palette.red, font: { color: palette.redText, bold: true } } });
}

async function recentReportIndex() {
  const dir = path.join(root, 'data', 'reports');
  const entries = await fs.readdir(dir, { withFileTypes: true });
  const cutoff = new Date('2026-07-31T00:00:00Z');
  const rows = [];
  for (const e of entries) {
    if (!e.isFile() || e.name.startsWith('.')) continue;
    const full = path.join(dir, e.name);
    const st = await fs.stat(full);
    if (st.mtime < cutoff) continue;
    const ext = path.extname(e.name).toLowerCase().replace('.', '') || 'none';
    let bucket = '2026-08-03';
    if (st.mtime < new Date('2026-08-02T00:00:00Z')) bucket = '2026-08-01';
    else if (st.mtime < new Date('2026-08-03T00:00:00Z')) bucket = '2026-08-02';
    rows.push([e.name, bucket, st.mtime, ext, primarySourceBasenames.has(e.name) ? 'Primary analysis source' : 'Scope index / supporting artifact', full]);
  }
  rows.sort((a,b) => a[0].localeCompare(b[0]));
  return rows;
}

async function build() {
  await fs.mkdir(outputDir, { recursive: true });
  await fs.mkdir(previewDir, { recursive: true });
  const wb = Workbook.create();
  const summary = wb.worksheets.add('Summary');
  const paths = wb.worksheets.add('Lifecycle Paths');
  const hot = wb.worksheets.add('Hotlist');
  const scal = wb.worksheets.add('Scalar Matrix');
  const ac = wb.worksheets.add('Accomplishments');
  const src = wb.worksheets.add('Source Inventory');

  for (const s of [summary, paths, hot, scal, ac, src]) { s.showGridLines = false; }

  styleTitle(summary, 'A1:J1', 'S1 / TRB last-3-days deep analysis', 'Scope: S1-synced reports and current TRB artifacts observed 2026-08-01 through 2026-08-03 UTC. “Works” means positive, causal vector research evidence; it does not mean exact V8 or live promotion.');
  summary.getRange('A4:B4').values = [['Decision signal','Value']]; styleHeader(summary,'A4:B4');
  summary.getRange('A5:A13').values = [['Complete lifecycle paths reviewed'],['Positive causal research paths'],['Unresolved / B&H fallback paths'],['Hotlist paths awaiting exact replay'],['Newest scalar union strict PASS'],['Newest scalar union quarantined'],['Exact V8 full-recipe confirmations'],['Current TRB research gate'],['Current S1 transport']];
  summary.getRange('B5').formulas = [["=COUNTA('Lifecycle Paths'!$C$4:$C$43)"]];
  summary.getRange('B6').formulas = [["=COUNTIF('Lifecycle Paths'!$Q$4:$Q$43,\"WORKS_RESEARCH\")"]];
  summary.getRange('B7').formulas = [["=COUNTIF('Lifecycle Paths'!$Q$4:$Q$43,\"UNRESOLVED_USE_BH\")"]];
  summary.getRange('B8').formulas = [["=COUNTA('Hotlist'!$C$4:$C$6)"]];
  summary.getRange('B9').values = [[278]];
  summary.getRange('B10').values = [[1911]];
  summary.getRange('B11').values = [[0]];
  summary.getRange('B12').values = [['48 / 60']];
  summary.getRange('B13').values = [['BLOCKED — host-key mismatch']];
  styleBody(summary,'A5:B13');
  summary.getRange('A5:A13').format.font = { bold: true, color: palette.ink };
  summary.getRange('B5:B11').format.numberFormat = [['#,##0'],['#,##0'],['#,##0'],['#,##0'],['#,##0'],['#,##0'],['#,##0']];
  summary.getRange('B6').format.fill = palette.green; summary.getRange('B7').format.fill = palette.amber; summary.getRange('B10').format.fill = palette.red; summary.getRange('B11').format.fill = palette.red; summary.getRange('B13').format.fill = palette.red;

  summary.getRange('D4:J4').merge(); summary.getRange('D4').values = [['Interpretation']]; summary.getRange('D4:J4').format = { fill: palette.teal, font: { color: palette.white, bold: true }, horizontalAlignment: 'left' };
  summary.getRange('D5:J10').merge(true);
  summary.getRange('D5:J10').values = [
    ['1. The strongest new paths are complete lifecycle recipes, not isolated scalar switch values. The workbook preserves every action stage and the source report section.'],
    ['2. 33 of 40 cohort recipes are positive causal research results. Seven are retained as unresolved/B&H fallbacks rather than being forced into a “winner” bucket.'],
    ['3. The scalar lane improved wiring evidence: 278 unique strict-PASS rows exist in the newest union, but 1,911 duplicates/inert rows are quarantined and 133 groups still require repair or exact V8.'],
    ['4. Promotion is not complete: exact full-recipe confirmation is 0/60, vector promotion receipts are 0, and USAR_LONG / ROKU_LONG fail closed as ADAPTER_REQUIRED.'],
    ['5. Operationally, the R1 churn loop is contained, but S1 transport is blocked by a host-key identity incident. Do not accept new S1 keys without independent infrastructure confirmation.'],
    ['6. Classic-formation 15m/ALL evidence is invalidated and must be rerun; the old ranking is not a usable path result.'],
  ];
  summary.getRange('D5:J10').format = { fill: '#F8FAFC', font: { color: palette.ink, size: 10 }, wrapText: true, verticalAlignment: 'center', borders: { preset: 'outside', style: 'thin', color: palette.border } };
  summary.getRange('D5:J10').format.rowHeight = 42;

  summary.getRange('A16:E16').values = [['Priority next action','Why it matters','Evidence state','Owner / gate','Source']]; styleHeader(summary,'A16:E16');
  const next = [
    ['Verify S1 identity before syncing again','Fresh S1 truth cannot be safely verified while all trusted host keys mismatch.','BLOCKED','Infrastructure confirmation','S1_TRANSPORT_IDENTITY_INCIDENT_20260803.md'],
    ['Implement lifecycle adapter for USAR_LONG / ROKU_LONG','Both are strongest complete recipes but have no live/V8 reader or action site.','ADAPTER_REQUIRED','Exact parity gate','EXACT_FINALIST_ADAPTER_AUDIT_20260802.md'],
    ['Exact-replay the 3 hotlist recipes','CLF_SHORT, ACN_SHORT, NVDA_LONG are the first ENTRY×EXIT candidates.','PENDING','Full-recipe replay only','PATH_PRODUCTIVITY_ANALYSIS_20260802.md'],
    ['Run 133 scalar causal recalculation groups','Quarantined/inert cells are excluded until behavior is causally distinct.','PENDING_RECALC','Causal adapter / V8','COHORT5_LONG_VECTOR_LIFECYCLE_20260802.md'],
    ['Rerun corrected classic formation campaign','15m/ALL evidence is invalidated by parent-candle and timestamp defects.','REJECTED','Full-universe train/holdout','CLASSIC_FORMATION_PARENT_CANDLE_INVALIDATION_20260803.json'],
  ];
  setValues(summary,'A17',next); styleBody(summary,'A17:E21'); addStatusFormatting(summary,'C17:C21');
  summary.freezePanes.freezeRows(4);
  summary.getRange('A:A').format.columnWidth = 30; summary.getRange('B:B').format.columnWidth = 22; summary.getRange('C:C').format.columnWidth = 20; summary.getRange('D:D').format.columnWidth = 25; summary.getRange('E:E').format.columnWidth = 42; summary.getRange('F:J').format.columnWidth = 13;

  styleTitle(paths, 'A1:S1', 'Complete lifecycle paths — exact action chain and outcome', 'Positive = WORKS_RESEARCH under causal vector evidence. UNRESOLVED_USE_BH includes losers, below-floor paths, and low-activity rows that are retained honestly. No row here is a live promotion.');
  const pathHeaders = ['Date','Cohort','Key','Side','ENTRY','AUGMENT','REDUCE','EXIT','REENTER','Gain / mo %','B&H / mo %','Alpha pp / mo','Max DD %','TIM %','Real closes','Closes / mo','Verdict','Evidence note','Source'];
  setValues(paths,'A3',[pathHeaders]); styleHeader(paths,'A3:S3');
  setValues(paths,'A4',lifecycle); styleBody(paths,`A4:S${lifecycle.length+3}`); addStatusFormatting(paths,`Q4:Q${lifecycle.length+3}`); paths.getRange(`A4:S${lifecycle.length+3}`).format.rowHeight = 34;
  paths.getRange(`J4:N${lifecycle.length+3}`).format.numberFormat = [['0.0000']];
  paths.getRange(`O4:O${lifecycle.length+3}`).format.numberFormat = [['#,##0']]; paths.getRange(`P4:P${lifecycle.length+3}`).format.numberFormat = [['0.00']];
  paths.freezePanes.freezeRows(3); paths.freezePanes.freezeColumns(4);
  const pWidths = [12,11,14,8,25,23,28,26,28,12,12,13,11,10,12,12,23,42,56];
  pWidths.forEach((w,i)=>paths.getRangeByIndexes(0,i,1,1).format.columnWidth=w);

  styleTitle(hot, 'A1:N1', 'Priority path hotlist — new ENTRY×EXIT combinations', 'The first bounded vector campaign found three strict priority combinations. All are explicitly pending full-recipe exact replay and were not dispatched.');
  const hotHeaders = ['Date','Priority','Key','Entry family','Exit family','Strategy / mo %','B&H / mo %','Alpha pp / mo','Closes / week','TIM %','Max DD %','Clamps','Status','Source'];
  setValues(hot,'A3',[hotHeaders]); styleHeader(hot,'A3:N3'); setValues(hot,'A4',hotlist); styleBody(hot,'A4:N6'); addStatusFormatting(hot,'M4:M6'); hot.getRange('A4:N6').format.rowHeight = 34;
  hot.getRange('F4:K6').format.numberFormat = [['0.000']]; hot.freezePanes.freezeRows(3);
  [12,10,14,28,52,14,12,14,13,10,11,9,30,56].forEach((w,i)=>hot.getRangeByIndexes(0,i,1,1).format.columnWidth=w);

  styleTitle(scal, 'A1:H1', 'Scalar matrix evidence — what works, what does not, and what is still pending', 'Aggregate counts are kept separate from complete lifecycle results. A strict PASS is unique nonzero behavior in the reviewed scalar adapter lane; it is not live/exact promotion credit.');
  const scalarHeaders = ['Source surface','Section','Metric','Count','Unit','Status interpretation','Audit note','Source'];
  setValues(scal,'A3',[scalarHeaders]); styleHeader(scal,'A3:H3'); setValues(scal,'A4',scalar); styleBody(scal,`A4:H${scalar.length+3}`); addStatusFormatting(scal,`F4:F${scalar.length+3}`); scal.getRange(`A4:H${scalar.length+3}`).format.rowHeight = 32;
  scal.getRange(`D4:D${scalar.length+3}`).format.numberFormat = [['#,##0']]; scal.freezePanes.freezeRows(3); scal.freezePanes.freezeColumns(3);
  [24,24,29,12,12,25,46,56].forEach((w,i)=>scal.getRangeByIndexes(0,i,1,1).format.columnWidth=w);

  styleTitle(ac, 'A1:G1', 'S1 / TRB accomplishments and remaining gates', 'Operational, research, matrix and integrity milestones from the last three days. Status is evidence status, not a trading recommendation.');
  const acHeaders = ['Date','Area','Accomplishment','Status','What it accomplished','Remaining caveat / next gate','Source'];
  setValues(ac,'A3',[acHeaders]); styleHeader(ac,'A3:G3'); setValues(ac,'A4',accomplishments); styleBody(ac,`A4:G${accomplishments.length+3}`); addStatusFormatting(ac,`D4:D${accomplishments.length+3}`); ac.getRange(`A4:G${accomplishments.length+3}`).format.rowHeight = 42;
  ac.freezePanes.freezeRows(3); [12,22,34,18,46,52,56].forEach((w,i)=>ac.getRangeByIndexes(0,i,1,1).format.columnWidth=w);

  styleTitle(src, 'A1:F1', 'Recent S1 report inventory', 'All non-hidden top-level files under data/reports with filesystem mtime on or after 2026-07-31 are indexed here. Primary analysis sources are marked; the rest preserve scope coverage.');
  setValues(src,'A3',[['Report file','Mtime bucket','Filesystem mtime','Type','Use in workbook','Local path']]); styleHeader(src,'A3:F3');
  const idx = await recentReportIndex(); setValues(src,'A4',idx); styleBody(src,`A4:F${idx.length+3}`); src.freezePanes.freezeRows(3); [56,14,26,10,28,75].forEach((w,i)=>src.getRangeByIndexes(0,i,1,1).format.columnWidth=w); src.getRange(`A4:F${idx.length+3}`).format.rowHeight = 24;
  src.getRange(`C4:C${idx.length+3}`).format.numberFormat = [['yyyy-mm-dd hh:mm']];
  src.getRange(`E4:E${idx.length+3}`).conditionalFormats.add('containsText',{text:'Primary',format:{fill:'#EAF2F8',font:{bold:true,color:palette.blue}}});

  // Light outside frames for the presentation areas.
  for (const [sheet, range] of [[summary,'A4:B13'],[summary,'A16:E21'],[paths,`A3:S${lifecycle.length+3}`],[hot,'A3:N6'],[scal,`A3:H${scalar.length+3}`],[ac,`A3:G${accomplishments.length+3}`],[src,`A3:F${idx.length+3}`]]) {
    sheet.getRange(range).format.borders = { outside: { style: 'thin', color: palette.border } };
  }

  const summaryCheck = await wb.inspect({kind:'table',range:"Summary!A1:J21",include:'values,formulas',tableMaxRows:21,tableMaxCols:10,tableMaxCellChars:120});
  const pathCheck = await wb.inspect({kind:'table',range:"'Lifecycle Paths'!A1:S8",include:'values,formulas',tableMaxRows:8,tableMaxCols:19,tableMaxCellChars:80});
  const errors = await wb.inspect({kind:'match',searchTerm:'#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A',options:{useRegex:true,maxResults:300},summary:'final formula error scan'});
  console.log('SUMMARY_CHECK\n'+summaryCheck.ndjson);
  console.log('PATH_CHECK\n'+pathCheck.ndjson);
  console.log('ERROR_SCAN\n'+errors.ndjson);

  for (const [sheetName, range, file] of [
    ['Summary','A1:J21','summary.png'],['Lifecycle Paths','A1:S10','lifecycle_paths.png'],['Hotlist','A1:N6','hotlist.png'],['Scalar Matrix','A1:H18','scalar_matrix.png'],['Accomplishments','A1:G16','accomplishments.png'],['Source Inventory','A1:F20','source_inventory.png']
  ]) {
    const blob = await wb.render({sheetName,range,scale:1,format:'png'});
    await fs.writeFile(path.join(previewDir,file), new Uint8Array(await blob.arrayBuffer()));
  }
  const out = await SpreadsheetFile.exportXlsx(wb); await out.save(outputPath);
  console.log(`EXPORTED ${outputPath}`);
  return {outputPath, previewDir, sourceCount: idx.length, lifecycleCount: lifecycle.length, scalarCount: scalar.length};
}

await build();
