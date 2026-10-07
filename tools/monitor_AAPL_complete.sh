#!/bin/bash
set -e
echo "[$(date -u)] monitor AAPL_LONG worst-first until complete"
for i in $(seq 1 80); do
  RUNNING=$(ssh -o ConnectTimeout=8 s1-pub "ps -p 4188370 2>&1 | grep 4188370 | wc -l" 2>&1 | tail -n 1 | tr -d ' ')
  if [ "$RUNNING" = "0" ] || [ -z "$RUNNING" ]; then
    echo "[$(date -u)] AAPL_LONG DONE at poll $i"
    ssh -o ConnectTimeout=8 s1-pub "ls -lh /tmp/AAPL_LONG_stockstest.xlsx 2>&1 | tail -n 1; wc -c /tmp/AAPL_LONG_stockstest.xlsx 2>&1 | tail -n 1; tail -n 50 /tmp/test_AAPL_LONG_worst.log 2>&1 | tail -n 50" 2>&1 | tail -n 70
    echo "--- validate workbook ---"
    ssh -o ConnectTimeout=8 s1-pub "python3 -c \"import openpyxl, pathlib; p='/tmp/AAPL_LONG_stockstest.xlsx'; wb=openpyxl.load_workbook(p, read_only=True); print(f'sheets {len(wb.sheetnames)}', wb.sheetnames); ws=wb['AUGMENT_TREND']; print(f'AUGMENT_TREND max_row {ws.max_row} max_col {ws.max_column}'); wb2=openpyxl.load_workbook(p, data_only=False); ws2=wb2['AUGMENT_TREND']; print('sample L2', ws2.cell(2,12).value, 'M2', ws2.cell(2,13).value); ws2=wb2['ENTRY_REVERSAL_BOUNCE']; print('ENTRY_REVERSAL max_row', ws2.max_row)\" 2>&1 | tail -n 30" 2>&1 | tail -n 40
    ssh -o ConnectTimeout=8 s1-pub "cp /tmp/AAPL_LONG_stockstest.xlsx ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_LONG_30d_matrix_worst_STOCKS_LONG.xlsx 2>&1 | head; ls -lh ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/AAPL_LONG_30d_matrix_worst_STOCKS_LONG.xlsx 2>&1 | tail -n 1" 2>&1 | tail -n 10
    break
  fi
  echo "[$(date -u)] poll $i running $(ssh -o ConnectTimeout=8 s1-pub "ps -o etime,pcpu -p 4188370 2>&1 | tail -n 1" 2>&1 | tail -n 1) ls $(ssh -o ConnectTimeout=8 s1-pub "ls -lh /tmp/AAPL_LONG_stockstest.xlsx 2>&1 | tail -n 1 | awk '{print \$5}'" 2>&1 | tail -n 1)"
  sleep 45
done
echo "monitor done"
