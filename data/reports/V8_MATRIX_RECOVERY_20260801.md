# V8 matrix recovery receipt — 2026-08-01

## Outcome

The stale vector broadcast workbook has been replaced with a precedence-safe
matrix. Existing V8 observations were recovered from the last useful
`PARAM_BASELINE_STOCKS.xlsx!AllCells` copy before that workbook was reduced to
68 daemon rows by a recurring refresh. The recovery archive retained 7,595
distinct historical V8 logical cells and is now append-only and hash-bound.

Display/storage precedence is:

1. current receipt-valid ENGINE V8;
2. immutable preserved historical V8;
3. vector evidence that passes the per-key uniqueness and activity guard;
4. blank.

Vector output cannot overwrite either V8 tier. Repeated vector numbers,
repeated action fingerprints, zero output, inert output, or zero-close output
remain stored as quarantined evidence and do not count as filled cells.

## Recovered V8 coverage

| key | current V8 numeric | preserved V8 numeric | preserved quarantined/red |
|---|---:|---:|---:|
| MU_LONG | 1 | 1,038 | 962 |
| NVDA_LONG | 1 | 94 | 77 |
| VT_LONG | 1 | 1,995 | 1,886 |
| TTD_SHORT | 0 | 175 | 158 |
| ACN_SHORT | 1 | 207 | 197 |
| LAC_SHORT | 1 | 44 | 34 |
| PLTR_SHORT | 1 | 8 | 7 |
| IBIT_LONG | 0 | 1 | 0 |
| MSTR_SHORT | 0 | 1 | 0 |

Red historical V8 cells retain their original numeric value and provenance.
They are red because equal results across distinct settings indicate a dead,
degenerate, or saturated knob; they are not promotion candidates until exact
replay produces unique action and metric fingerprints.

## Workbook state

- `SWITCH_MATRIX_TRB.xlsx`: 3,618 switch/value rows, 133 directional key
  columns, 7,602 Evidence Provenance rows.
- `SWITCH_MATRIX_TRB.csv.gz`: current-contract ENGINE only; vector and
  receipt-missing preserved rows never enter the numeric CSV truth layer.
- `PARAM_BASELINE_STOCKS.xlsx`: 7,595 AllCells rows, 133 PerSym rows, 252 Entry
  path groups and 252 Exit path groups. Preserved V8 evidence is blue when
  unique and red when quarantined.
- Workbook audit: both artifacts pass; 115 active keys, 18 historical keys,
  4,551 switch descriptions, 504 path descriptions, zero formula errors.
- Historical axes include PLTR_SHORT and IBIT_LONG even when absent from the
  current active directional allowlist.

## Overwrite protection

The Mac report puller was observed replacing a newly generated 1.02 MB matrix
with a stale 6.3 MB workbook after export. Canonical exporters now perform an
atomic unlock → replace → immutable relock cycle. The matrix XLSX/CSV, digest
and provenance sidecar, PARAM_BASELINE workbook, and preserved V8 archive are
locked with the macOS `uchg` flag between authorized refreshes. S1/Linux keeps
using the existing exporter surface lock.

## Artifact hashes

- `SWITCH_MATRIX_TRB.xlsx`: `11c6caf6b6e23f92af9c6c314e4f912227fa013ba1c5a13da950a618664bdc82`
- `SWITCH_MATRIX_TRB.csv.gz`: `6b71d557d57825901ce3bac19d4f47dd8eba567c83cae90d0ca75ea08db11584`
- `PARAM_BASELINE_STOCKS.xlsx`: `9f528a69be755864a2fffe6119f67e3da047520865db2a4818264bda40e22254`
- `PRESERVED_V8_CELLS.jsonl`: `e51b04b0b93c815c145aa8a1afa0424e699c962c7fc8b8e27a881d6e974c81c0`
- `PRESERVED_V8_CELLS_RECEIPT.json`: `99a628163c578e32acbc7f960a8eb138b09484309455ec1f577d82ea3c276202`
- `STOCK_MATRIX_WORKBOOK_AUDIT.json`: `52f4040a89d355813353053156362f1c314d8fadef08b5285c0debdef435586d`

## Remaining work

The recovery restores evidence; it does not pretend the collided settings are
good tests. The vector campaign must now rerun only causal, connected adapters
and stop on the first per-key metric or action-fingerprint collision. Exact V8
replay should prioritize the unique historical frontier and any new unique
vector combinations; it must never be used to erase the recovered history.
