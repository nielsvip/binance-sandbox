import { FileBlob, SpreadsheetFile } from "/Users/niels/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/@oai/artifact-tool/dist/artifact_tool.mjs";
const wb = await SpreadsheetFile.importXlsx(await FileBlob.load("data/reports/SWITCH_MATRIX_TRB.xlsx"));
console.log((await wb.inspect({kind:"workbook,sheet",maxChars:8000})).ndjson);
for (const n of ["Entry","Exit","Sizing","Other","Ladder","BandLadder","Coverage","Baselines","Legend"]) {
  console.log(`---${n}---`);
  console.log((await wb.inspect({kind:"region",sheetId:n,range:n === "Ladder" ? "A1:N18" : n === "Coverage" ? "A1:G12" : n === "Baselines" ? "A1:F12" : n === "Legend" ? "A1:B5" : "A1:L8",maxChars:9000,tableMaxRows:18,tableMaxCols:14})).ndjson);
}
console.log((await wb.inspect({kind:"match",searchTerm:"#REF!|#DIV/0!|#VALUE!|#NAME\\?|#N/A",options:{useRegex:true,maxResults:100},summary:"TRB workbook formula errors"})).ndjson);
