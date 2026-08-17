# Current TRB matrix reporting audit — 2026-07-30

Status: **FAIL**

- Campaign: `stocks_repaired_20260730_c5`
- Exact contract: `tradier-matrix-exec-c5-20260730`
- Canonical artifact: `data/reports/SWITCH_MATRIX_TRB.csv.gz`
- CSV numeric cells: **2196**; unexplained/non-current values: **2192**.
- Accepted DB cells newer than the periodic export: **3**.
- Digest accepted current ENGINE rows: **7**.
- `/results` current-only excerpt: **10,659 bytes** of 34,345; vector/historical tail excluded.
- Canonical workbook historical campaign/contract values: **2163**.
- Ranked TIM keys: **115**; errors: **0**.
- LONG top 10 (50–80%): `DVN, AAPL, DINO, CMC, VT, EOG, OKE, GM, FANG, AMZN`.
- SHORT top 10 (50–80%): `AAPL, DIS, CLF, CMC, CDE, FCX, NKE, CLX, TTD, FCN`.
- Every remaining ranked LONG/SHORT key is bound to 20–60%.

No VEC, path-fleet, historical campaign, stale exact fingerprint, or failed validation row is allowed to populate a canonical numeric cell.
