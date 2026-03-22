from pathlib import Path
from typing import Any, Optional, Dict, List, Union

import os

import json

import tempfile

import shutil

import asyncio

import time

import pandas as pd

from collections import defaultdict

from influxdb_client import InfluxDBClient, Point, WritePrecision

from influxdb_client.client.write_api import SYNCHRONOUS # Or ASYNCHRONOUS for batching

# Try to import pdfplumber, but don't fail if it's not needed/installed yet

try:
    import pdfplumber

except ImportError:
    pdfplumber = None # Will be checked before use

# Local source folders

BASE_PATH = Path("/Users/niels/Documents/binance") #!UPDATE THIS TO YOUR ACTUAL BASE PATH

KLINES_CACHE_DIR = BASE_PATH / "klines_cache"

DATA_DIR = BASE_PATH / "data" # For incoming market_data_*.json and PDFs

PLOTS_DIR = BASE_PATH / "plots"

# Directory for CSVs generated from market_data_*.json files (one CSV per symbol)

SYMBOL_MARKET_DATA_CSVS_DIR = BASE_PATH / "symbol_market_data_csvs"

# Directory for CSVs generated from PDF files

PDF_CSV_EXPORT_DIR = BASE_PATH / "symbol_pdf_data_csvs" # CSVs from PDFs go here

# External HD target folders

HD_ROOT = Path("/Volumes/SSD2T") #!UPDATE THIS TO YOUR ACTUAL HD ROOT

BACKUP_KLINES_CACHE = HD_ROOT / "backups/klines_cache"

BACKUP_DATA = HD_ROOT / "backups/data" # For market_data_*.json and PDFs

BACKUP_PLOTS = HD_ROOT / "backups/plots"

BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR = HD_ROOT / "backups/symbol_market_data_csvs"

BACKUP_PDF_CSV_EXPORT_DIR = HD_ROOT / "backups/symbol_pdf_data_csvs" # Backup for PDF-derived CSVs

# Config: number of klines to keep locally per file

MAX_LOCAL_KLINES = 2400

# Ensure all necessary folders exist

for folder in [KLINES_CACHE_DIR, DATA_DIR, PLOTS_DIR, SYMBOL_MARKET_DATA_CSVS_DIR, PDF_CSV_EXPORT_DIR, BACKUP_KLINES_CACHE, BACKUP_DATA, BACKUP_PLOTS, BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR, BACKUP_PDF_CSV_EXPORT_DIR]:
    folder.mkdir(parents=True, exist_ok=True)

# --- KLINE DATA FUNCTIONS --- (Consolidated by month: one file per symbol/timeframe/month)

def consolidate_by_month(symbol, interval, all_data_df):
    """Consolidate klines to one file per symbol/timeframe/month"""
    if all_data_df.empty: return {}
    all_data_df['timestamp'] = pd.to_datetime(all_data_df['timestamp'], utc=True, errors='coerce')
    all_data_df = all_data_df.dropna(subset=['timestamp']).drop_duplicates('timestamp').sort_values('timestamp')
    if all_data_df.empty: return {}
    all_data_df['year_month'] = all_data_df['timestamp'].dt.to_period('M')
    consolidated = {}
    for period, group_df in all_data_df.groupby('year_month'):
        month_key = f"{period.year}{period.month:02d}"
        file_name = f"{symbol}_{interval}_{month_key}.json"
        consolidated[file_name] = group_df.drop(columns=['year_month'])
    return consolidated

def append_and_trim_kline_data():
    """Backup and consolidate klines - one file per symbol/timeframe/month"""
    symbol_interval_data = {}
    for file in KLINES_CACHE_DIR.glob("*.json"):
        try:
            file_name = file.stem
            if '_' not in file_name: continue
            parts = file_name.rsplit('_', 1)
            if len(parts) != 2: continue
            symbol, interval = parts
            local_df = pd.read_json(file)
            local_df["timestamp"] = pd.to_datetime(local_df["timestamp"], utc=True, errors='coerce')
            local_df = local_df.dropna(subset=['timestamp']).drop_duplicates("timestamp").sort_values("timestamp")
            if local_df.empty: continue
            key = f"{symbol}_{interval}"
            if key not in symbol_interval_data:
                symbol_interval_data[key] = {'symbol': symbol, 'interval': interval, 'dataframes': []}
            symbol_interval_data[key]['dataframes'].append(local_df)
        except Exception as e:
            print(f" Error reading local kline file {file.name}: {e}")
            continue
    for key, info in symbol_interval_data.items():
        symbol, interval = info['symbol'], info['interval']
        try:
            combined_df = pd.concat(info['dataframes'], ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp')
            consolidated = consolidate_by_month(symbol, interval, combined_df)
            for file_name, month_df in consolidated.items():
                backup_file = BACKUP_KLINES_CACHE / file_name
                if backup_file.exists():
                    try:
                        existing_df = pd.read_json(backup_file)
                        existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True, errors='coerce')
                        month_df['timestamp'] = pd.to_datetime(month_df['timestamp'], utc=True, errors='coerce')
                        merged = pd.concat([existing_df, month_df], ignore_index=True).drop_duplicates('timestamp').sort_values('timestamp')
                        atomic_save_json(merged, backup_file)
                    except Exception as e:
                        print(f" Error merging backup {backup_file.name}: {e}. Overwriting.")
                        atomic_save_json(month_df, backup_file)
                else:
                    atomic_save_json(month_df, backup_file)
                print(f" Updated kline backup: {file_name} ({len(month_df)} rows)")
            trimmed_df = combined_df.tail(MAX_LOCAL_KLINES)
            local_file = KLINES_CACHE_DIR / f"{symbol}_{interval}.json"
            atomic_save_json(trimmed_df, local_file)
            print(f" Trimmed local kline {symbol}_{interval}.json to {len(trimmed_df)} rows")
        except Exception as e:
            print(f" Failed to process {symbol} {interval}: {e}")

def atomic_save_json(df: pd.DataFrame, file_path: Path):
    file_path_str = str(file_path)
    target_dir = file_path.parent
    tmp_fd, tmp_path_str = tempfile.mkstemp(suffix='.json', prefix='tmp_atomic_', dir=str(target_dir))
    os.close(tmp_fd)
    tmp_path = Path(tmp_path_str)
    try:
        df.to_json(tmp_path, orient='records', date_format='iso')
        shutil.move(str(tmp_path), file_path_str)
    except Exception as e:
        print(f" Error during atomic save to {file_path_str}: {e}")
        if tmp_path.exists():
            tmp_path.unlink()
        raise

# --- FILE MOVEMENT FUNCTIONS ---

def move_market_data():
    cutoff_time = time.time() - 0.5 * 12 * 60 * 60  # 6 hours ago
    for file_pattern in ["market_data_*.json", "*.pdf"]:
        for file in DATA_DIR.glob(file_pattern):
            try:
                backup_file_path = BACKUP_DATA / file.name
                shutil.copy(file, backup_file_path)
                print(f" Market data ({file.suffix}) copied: {file.name} to {backup_file_path}")
                mtime = file.stat().st_mtime
                if mtime < cutoff_time:
                    file.unlink()
                    print(f" Old market data ({file.suffix}) deleted from local: {file.name}")
            except Exception as e:
                print(f"Error processing market data file {file.name}: {e}")

def move_plot_files():
    cutoff_time = time.time() - 0.5 * 24 * 60 * 60  # 12 hours ago
    for file in PLOTS_DIR.glob("*.png"):
        try:
            shutil.copy(file, BACKUP_PLOTS / file.name)
            print(f" Plot copied: {file.name}")
            mtime = file.stat().st_mtime
            if mtime < cutoff_time:
                file.unlink()
                print(f" Old plot deleted: {file.name}")
        except Exception as e:
            print(f"Error processing plot file {file.name}: {e}")

# --- JSON MARKET DATA TO CSV PROCESSING ---

def process_market_data_json_to_symbol_csvs(json_source_dirs: list[Path], csv_output_dir: Path):
    print(f"Starting processing of market_data_*.json for symbol CSVs from {json_source_dirs} to {csv_output_dir}")
    csv_output_dir.mkdir(parents=True, exist_ok=True)
    processed_json_files = set() # To avoid processing the same file if present in multiple source_dirs
    # Iterate through all source directories (local DATA_DIR and BACKUP_DATA)
    all_json_files_to_process = []
    for source_dir in json_source_dirs:
        for json_file in source_dir.glob("market_data_*.json"):
            abs_path = json_file.resolve()
            if abs_path not in processed_json_files:
                all_json_files_to_process.append(json_file)
                processed_json_files.add(abs_path)
    # Sort files by name (which often includes timestamp) to process in chronological order
    all_json_files_to_process.sort(key=lambda p: p.name)
    print(f"Found {len(all_json_files_to_process)} unique market_data_*.json files to process.")
    for json_file in all_json_files_to_process:
        print(f"  Processing JSON file: {json_file.name}")
        try:
            with json_file.open("r") as f:
                data_from_json = json.load(f)
            if not isinstance(data_from_json, dict):
                print(f"    WARN: Content of {json_file.name} is not a dictionary. Skipping.")
                continue
            for symbol_key, indicators_dict in data_from_json.items():
                if not isinstance(indicators_dict, dict) or "timestamp" not in indicators_dict:
                    # print(f"    WARN: Invalid/missing data for symbol '{symbol_key}' in {json_file.name}. Skipping.")
                    continue
                # Prepare the single record for this symbol from this JSON file
                record = {"symbol": symbol_key} 
                record.update(indicators_dict)
                # Convert timestamp early
                try:
                    record_timestamp = pd.to_datetime(record["timestamp"], utc=True, errors='coerce')
                    if pd.isna(record_timestamp):
                        print(f"    WARN: Invalid timestamp '{record['timestamp']}' for symbol '{symbol_key}' in {json_file.name}. Skipping record.")
                        continue
                    record["timestamp"] = record_timestamp # Store as datetime object
                except Exception as e_ts:
                    print(f"    ERROR converting timestamp for '{symbol_key}' in {json_file.name}: {e_ts}. Skipping record.")
                    continue
                new_record_df = pd.DataFrame([record]) # DataFrame with a single row
                output_csv_file = csv_output_dir / f"{symbol_key}.csv"
                if output_csv_file.exists():
                    try:
                        # Read only a small part to get headers, or handle potential errors
                        existing_df = pd.read_csv(output_csv_file, nrows=1) # Read only header to check columns
                        header = True # Existing file has header
                        mode = 'a' # Append mode
                        # Check if all columns from new_record_df exist in existing_df
                        # If not, it's safer to read the whole thing, reindex, and rewrite.
                        # This is a trade-off: appending is faster but less robust to schema changes.
                        # For simplicity and robustness if schema changes, we'll read, concat, dedupe, rewrite.
                        # This means it's not truly "append only" but "merge and rewrite".
                        full_existing_df = pd.read_csv(output_csv_file)
                        if "timestamp" in full_existing_df.columns:
                            full_existing_df["timestamp"] = pd.to_datetime(full_existing_df["timestamp"], utc=True, errors='coerce')
                            full_existing_df.dropna(subset=["timestamp"], inplace=True)
                        # Align columns before concat
                        all_cols = new_record_df.columns.union(full_existing_df.columns)
                        full_existing_df_reindexed = full_existing_df.reindex(columns=all_cols)
                        new_record_df_reindexed = new_record_df.reindex(columns=all_cols)
                        combined_df = pd.concat([full_existing_df_reindexed, new_record_df_reindexed], ignore_index=True)
                        key_indicator_columns = [col for col in combined_df.columns if col not in ['symbol']] # All columns except symbol
                        combined_df.sort_values(by='timestamp', inplace=True)
                        combined_df.drop_duplicates(subset=['timestamp'], keep='last', inplace=True) # Keep the latest entry for a given timestamp
                        combined_df.to_csv(output_csv_file, index=False)
                        print(f"    Updated CSV for {symbol_key} with {len(combined_df)} total rows.")
                    except pd.errors.EmptyDataError:
                        # Existing file is empty, write new data with header
                        new_record_df.to_csv(output_csv_file, index=False, header=True, mode='w')
                        # print(f"    Created new CSV for {symbol_key} (existing was empty).")
                    except Exception as e_append:
                        print(f"    ERROR updating CSV for {symbol_key} ({output_csv_file.name}): {e_append}. Attempting overwrite with new record.")
                        try:
                            new_record_df.to_csv(output_csv_file, index=False, header=True, mode='w') # Overwrite
                        except Exception as e_ow:
                             print(f"    FATAL ERROR: Could not even overwrite CSV for {symbol_key}: {e_ow}")
                else:
                    # CSV doesn't exist, create it with the new record
                    new_record_df.to_csv(output_csv_file, index=False, header=True, mode='w')
                    # print(f"    Created new CSV for {symbol_key}.")
        except json.JSONDecodeError as e_json:
            print(f"    ERROR decoding JSON from {json_file.name}: {e_json}. Skipping file.")
        except Exception as e_file:
            print(f"    UNEXPECTED ERROR processing file {json_file.name}: {e_file}")
    print("Processing of market_data_*.json files to symbol CSVs finished.")
    # Backup of these CSVs is implicitly handled because we are writing to BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR

# def process_market_data_json_to_symbol_csvs(json_source_dirs: list[Path], csv_output_dir: Path):

#     print(f"Starting processing of market_data_*.json for symbol CSVs from {json_source_dirs} to {csv_output_dir}")

#     csv_output_dir.mkdir(parents=True, exist_ok=True)

#     all_symbol_records = defaultdict(list)

#     processed_files = set()

#     for source_dir in json_source_dirs:

#         for json_file in source_dir.glob("market_data_*.json"):

#             abs_path = json_file.resolve()

#             if abs_path in processed_files:

#                 continue

#             processed_files.add(abs_path)

#             print(f"  Processing JSON file: {json_file.name}")

#             try:

#                 with json_file.open("r") as f:

#                     data = json.load(f)

#                 for symbol_key, indicators_dict in data.items():

#                     if not isinstance(indicators_dict, dict) or "timestamp" not in indicators_dict:

#                         print(f"    WARN: Invalid/missing data for symbol '{symbol_key}' in {json_file.name}. Skipping.")

#                         continue

#                     record = {"symbol": symbol_key}

#                     record.update(indicators_dict)

#                     all_symbol_records[symbol_key].append(record)

#             except Exception as e:

#                 print(f"    ERROR processing {json_file.name}: {e}")

#     if not all_symbol_records:

#         print("No data records found in any market_data_*.json files.")

#         return

#     for symbol, records_list in all_symbol_records.items():

#         if not records_list: continue

#         print(f"  Updating JSON-derived CSV for symbol: {symbol} with {len(records_list)} new records")

#         new_df = pd.DataFrame(records_list)

#         try:

#             new_df["timestamp"] = pd.to_datetime(new_df["timestamp"], utc=True, errors='coerce')

#             new_df.dropna(subset=["timestamp"], inplace=True)

#             if new_df.empty:

#                 print(f"    WARN: No valid timestamped JSON data for {symbol} after conversion. Skipping.")

#                 continue

#         except Exception as e:

#             print(f"    ERROR converting 'timestamp' for {symbol} from JSON: {e}. Skipping.")

#             continue

#         output_csv_file = csv_output_dir / f"{symbol}.csv"

#         combined_df = new_df

#         if output_csv_file.exists():

#             try:

#                 existing_df = pd.read_csv(output_csv_file)

#                 if not existing_df.empty and "timestamp" in existing_df.columns:

#                     print(f"    Loading existing JSON-derived data for {symbol} from {output_csv_file.name}")

#                     existing_df["timestamp"] = pd.to_datetime(existing_df["timestamp"], utc=True, errors='coerce')

#                     existing_df.dropna(subset=["timestamp"], inplace=True)

#                     all_cols = new_df.columns.union(existing_df.columns)

#                     existing_df = existing_df.reindex(columns=all_cols)

#                     new_df_reindexed = new_df.reindex(columns=all_cols)

#                     combined_df = pd.concat([existing_df, new_df_reindexed], ignore_index=True)

#                 elif existing_df.empty:

#                      print(f"    Existing JSON-derived CSV for {symbol} is empty. Using new data.")

#                 else: # Missing timestamp or other issue

#                     print(f"    WARN: Existing JSON-derived CSV for {symbol} invalid. Overwriting.")

#             except Exception as e:

#                 print(f"    ERROR reading existing JSON-derived CSV for {symbol}: {e}. Using new data.")

#         key_columns = [col for col in combined_df.columns if col not in ['symbol']]

#         if key_columns:

#             combined_df.sort_values(by=['timestamp'] + [k for k in key_columns if k != 'timestamp'], inplace=True)

#             combined_df.drop_duplicates(subset=key_columns, keep='last', inplace=True)

#         combined_df.sort_values(by='timestamp', inplace=True)

#         try:

#             combined_df.to_csv(output_csv_file, index=False)

#             print(f"    SUCCESS: Wrote {len(combined_df)} unique rows for {symbol} to JSON-derived {output_csv_file.name}")

#         except Exception as e:

#             print(f"    ERROR writing JSON-derived CSV for {symbol}: {e}")

#     print("Processing of market_data_*.json files finished.")

#     # if BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR.exists():

#     #     print(f"Backing up generated symbol market data CSVs (from JSON) to {BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR}...")

#     #     for csv_file in csv_output_dir.glob("*.csv"):

#     #         try:

#     #             shutil.copy(csv_file, BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR / csv_file.name)

#     #             print(f"  Backed up {csv_file.name}")

#     #         except Exception as e:

#     #             print(f"  Error backing up {csv_file.name}: {e}")

# --- PDF MARKET DATA TO CSV PROCESSING ---

# --- PDF MARKET DATA TO CSV PROCESSING ---

def process_pdfs_to_symbol_csvs(pdf_source_dirs: list[Path], csv_output_dir: Path):
    if pdfplumber is None:
        print("WARN: pdfplumber library is not installed. PDF processing will be skipped.")
        return
    print(f"Starting processing of PDF files for symbol CSVs from {pdf_source_dirs} to {csv_output_dir}")
    csv_output_dir.mkdir(parents=True, exist_ok=True)
    # --- ###! CUSTOMIZE THIS PDF PARSING LOGIC THOROUGHLY !### ---
    def parse_pdf_content(pdf_file_path: Path) -> list[dict]:
        # ... (Your existing parse_pdf_content function)
        # IMPORTANT: Ensure this function returns a list of dictionaries,
        # and each dictionary MUST have 'symbol' and 'timestamp' keys.
        # It should be robust enough to handle variations in your PDFs.
        extracted_data = []
        # print(f"  Attempting to parse PDF: {pdf_file_path.name}")
        SYMBOL_COLUMN_NAME_IN_PDF = 'Symbol' 
        TIMESTAMP_COLUMN_NAME_IN_PDF = 'Timestamp'
        try:
            with pdfplumber.open(pdf_file_path) as pdf:
                for page_num, page in enumerate(pdf.pages):
                    tables = page.extract_tables()
                    if not tables: continue
                    for table_idx, table_data in enumerate(tables):
                        if not table_data or len(table_data) < 2: continue
                        headers = [str(h).strip() if h is not None else '' for h in table_data[0]]
                        try:
                            symbol_col_idx = headers.index(SYMBOL_COLUMN_NAME_IN_PDF)
                            timestamp_col_idx = headers.index(TIMESTAMP_COLUMN_NAME_IN_PDF)
                        except ValueError:
                            # print(f"    WARN: Table {table_idx} page {page_num+1} in {pdf_file_path.name} missing headers. Headers: {headers}.")
                            continue
                        for row_values in table_data[1:]:
                            if len(row_values) != len(headers): continue
                            record: dict[str, Any] = {'source_pdf_file': pdf_file_path.name, 'source_pdf_page': f"p{page_num+1}_t{table_idx}"}
                            symbol_val = str(row_values[symbol_col_idx]).strip() if row_values[symbol_col_idx] else None
                            timestamp_val = str(row_values[timestamp_col_idx]).strip() if row_values[timestamp_col_idx] else None
                            if not symbol_val or not timestamp_val: continue
                            record['symbol'] = symbol_val
                            record['timestamp'] = timestamp_val
                            for i, header_name in enumerate(headers):
                                if i not in [symbol_col_idx, timestamp_col_idx]:
                                    clean_header = header_name.replace(' ', '_').lower()
                                    record[clean_header] = str(row_values[i]).strip() if row_values[i] is not None else None
                            extracted_data.append(record)
            # if not extracted_data:
            #     print(f"  INFO: No data extracted from {pdf_file_path.name} via parse_pdf_content.")
        except Exception as e:
            print(f"  ERROR parsing PDF {pdf_file_path.name}: {e}")
        return extracted_data
    # --- END OF CUSTOMIZABLE PDF PARSING LOGIC ---
    processed_pdf_files = set()
    all_pdf_files_to_process = []
    for source_dir in pdf_source_dirs:
        for pdf_file in source_dir.glob("*.pdf"): # Adjust glob if needed
            abs_path = pdf_file.resolve()
            if abs_path not in processed_pdf_files:
                all_pdf_files_to_process.append(pdf_file)
                processed_pdf_files.add(abs_path)
    all_pdf_files_to_process.sort(key=lambda p: p.name) # Process PDFs in name order
    print(f"Found {len(all_pdf_files_to_process)} unique PDF files to process.")
    for pdf_file in all_pdf_files_to_process:
        print(f"  Processing PDF file: {pdf_file.name}")
        parsed_rows_from_pdf = parse_pdf_content(pdf_file)
        if not parsed_rows_from_pdf:
            # print(f"    INFO: No data returned from parsing PDF {pdf_file.name}. Skipping.")
            continue
        # Group data by symbol from this single PDF
        data_by_symbol_this_pdf = defaultdict(list)
        for row in parsed_rows_from_pdf:
            if 'symbol' in row and 'timestamp' in row:
                try:
                    # Convert timestamp early
                    row_timestamp = pd.to_datetime(row["timestamp"], utc=True, errors='coerce')
                    if pd.isna(row_timestamp):
                        # print(f"    WARN: Invalid PDF timestamp '{row['timestamp']}' for symbol '{row['symbol']}'. Skipping row.")
                        continue
                    row["timestamp"] = row_timestamp
                    data_by_symbol_this_pdf[row['symbol']].append(row)
                except Exception as e_ts_pdf:
                    print(f"    ERROR converting PDF timestamp for '{row['symbol']}': {e_ts_pdf}. Skipping row.")
            else:
                # print(f"    WARN: Row from PDF {pdf_file.name} missing 'symbol' or 'timestamp'. Row: {row}")
                pass
        for symbol, records_list in data_by_symbol_this_pdf.items():
            if not records_list: continue
            new_records_df = pd.DataFrame(records_list)
            output_csv_file = csv_output_dir / f"{symbol}.csv" # Assumes CSV named by symbol
            if output_csv_file.exists():
                try:
                    existing_df = pd.read_csv(output_csv_file)
                    if "timestamp" in existing_df.columns:
                        existing_df["timestamp"] = pd.to_datetime(existing_df["timestamp"], utc=True, errors='coerce')
                        existing_df.dropna(subset=["timestamp"], inplace=True)
                    all_cols = new_records_df.columns.union(existing_df.columns)
                    existing_df_reindexed = existing_df.reindex(columns=all_cols)
                    new_records_df_reindexed = new_records_df.reindex(columns=all_cols)
                    combined_df = pd.concat([existing_df_reindexed, new_records_df_reindexed], ignore_index=True)
                    # Deduplicate based on timestamp and other defining columns from PDF
                    # Exclude 'source_pdf_file', 'source_pdf_page' from dedupe keys
                    dedupe_subset = [col for col in combined_df.columns if col not in ['symbol', 'source_pdf_file', 'source_pdf_page']]
                    if 'timestamp' not in dedupe_subset: # Should always be there
                        print(f"    CRITICAL WARN: Timestamp somehow missing from dedupe keys for PDF data of {symbol}. This might lead to data loss.")
                        combined_df.sort_values(by=['timestamp'], inplace=True) # Sort anyway
                    else:
                        combined_df.sort_values(by=['timestamp'] + [k for k in dedupe_subset if k != 'timestamp'], inplace=True)
                        combined_df.drop_duplicates(subset=dedupe_subset, keep='last', inplace=True)
                    combined_df.to_csv(output_csv_file, index=False)
                    # print(f"    Updated PDF-derived CSV for {symbol} with {len(combined_df)} total rows.")
                except pd.errors.EmptyDataError:
                    new_records_df.sort_values(by='timestamp', inplace=True)
                    new_records_df.to_csv(output_csv_file, index=False, header=True, mode='w')
                except Exception as e_append_pdf:
                    print(f"    ERROR updating PDF-derived CSV for {symbol} ({output_csv_file.name}): {e_append_pdf}. Attempting overwrite.")
                    try:
                        new_records_df.sort_values(by='timestamp', inplace=True)
                        new_records_df.to_csv(output_csv_file, index=False, header=True, mode='w') # Overwrite
                    except Exception as e_ow_pdf:
                        print(f"    FATAL ERROR: Could not overwrite PDF-derived CSV for {symbol}: {e_ow_pdf}")
            else:
                new_records_df.sort_values(by='timestamp', inplace=True)
                new_records_df.to_csv(output_csv_file, index=False, header=True, mode='w')
                # print(f"    Created new PDF-derived CSV for {symbol}.")
    print("PDF data to symbol-specific CSV processing finished.")
    # Backup of these CSVs is implicitly handled

# def process_pdfs_to_symbol_csvs(pdf_source_dirs: list[Path], csv_output_dir: Path):

#     if pdfplumber is None:

#         print("WARN: pdfplumber library is not installed. PDF processing will be skipped.")

#         print("      To enable PDF processing, run: pip install pdfplumber")

#         return

#     print(f"Starting processing of PDF files for symbol CSVs from {pdf_source_dirs} to {csv_output_dir}")

#     csv_output_dir.mkdir(parents=True, exist_ok=True)

#     # --- ###! CUSTOMIZE THIS ENTIRE FUNCTION TO MATCH YOUR PDF STRUCTURE !### ---

#     def parse_pdf_content(pdf_file_path: Path) -> list[dict]:

#         """

#         Parses a single PDF file and extracts structured data.

#         Each dictionary in the returned list should represent one row of data.

#         It MUST include 'symbol' and 'timestamp' keys.

#         Other keys will become columns in the CSV.

#         This is an EXAMPLE assuming data is in tables.

#         YOU WILL LIKELY NEED TO MODIFY THIS SIGNIFICANTLY.

#         """

#         extracted_data = []

#         print(f"  Attempting to parse PDF: {pdf_file_path.name}")

#         ###! CUSTOMIZE HERE !###

#         # Adjust these based on your PDF table's actual column header names

#         SYMBOL_COLUMN_NAME_IN_PDF = 'Symbol'  # e.g., 'Ticker', 'Pair', etc.

#         TIMESTAMP_COLUMN_NAME_IN_PDF = 'Timestamp' # e.g., 'Date', 'Time', 'datetime_utc'

#         # Add other expected column names if you want to explicitly map them

#         # or handle type conversions for specific columns.

#         # EXPECTED_DATA_COLUMNS = ['Open', 'High', 'Low', 'Close', 'Volume'] 

#         try:

#             with pdfplumber.open(pdf_file_path) as pdf:

#                 for page_num, page in enumerate(pdf.pages):

#                     # Option 1: Extract tables (preferred if data is tabular)

#                     tables = page.extract_tables()

#                     if not tables:

#                         # print(f"    INFO: No tables found on page {page_num + 1} of {pdf_file_path.name}")

#                         # You might fallback to text extraction here if needed:

#                         # text = page.extract_text()

#                         # ... (add regex or line-by-line parsing for text) ...

#                         continue

#                     for table_idx, table_data in enumerate(tables):

#                         if not table_data or len(table_data) < 2: # Needs at least a header and one data row

#                             # print(f"    INFO: Table {table_idx} on page {page_num+1} in {pdf_file_path.name} is empty or has no data rows.")

#                             continue

#                         headers = [str(h).strip() if h is not None else '' for h in table_data[0]]

#                         # Try to find symbol and timestamp columns

#                         try:

#                             symbol_col_idx = headers.index(SYMBOL_COLUMN_NAME_IN_PDF)

#                             timestamp_col_idx = headers.index(TIMESTAMP_COLUMN_NAME_IN_PDF)

#                         except ValueError:

#                             print(f"    WARN: Table {table_idx} on page {page_num+1} in {pdf_file_path.name} "

#                                   f"is missing required headers ('{SYMBOL_COLUMN_NAME_IN_PDF}' or '{TIMESTAMP_COLUMN_NAME_IN_PDF}'). Headers found: {headers}. Skipping table.")

#                             continue

#                         # Process data rows

#                         for row_values in table_data[1:]:

#                             if len(row_values) != len(headers):

#                                 # print(f"    WARN: Row length mismatch in table {table_idx}, page {page_num+1}. Headers: {len(headers)}, Row: {len(row_values)}. Skipping row: {row_values}")

#                                 continue

#                             record = {'source_pdf_page': f"{pdf_file_path.name}_p{page_num+1}_t{table_idx}"}

#                             symbol_val = str(row_values[symbol_col_idx]).strip() if row_values[symbol_col_idx] else None

#                             timestamp_val = str(row_values[timestamp_col_idx]).strip() if row_values[timestamp_col_idx] else None

#                             if not symbol_val or not timestamp_val:

#                                 # print(f"    WARN: Missing symbol or timestamp in row: {row_values} from {pdf_file_path.name}. Skipping.")

#                                 continue

#                             record['symbol'] = symbol_val

#                             record['timestamp'] = timestamp_val # Keep as string for now, convert to datetime later

#                             # Add other columns

#                             for i, header_name in enumerate(headers):

#                                 if i not in [symbol_col_idx, timestamp_col_idx]: # Avoid re-adding symbol/timestamp

#                                     clean_header = header_name.replace(' ', '_').lower() # Clean up header for DataFrame column

#                                     record[clean_header] = str(row_values[i]).strip() if row_values[i] is not None else None

#                             extracted_data.append(record)

#             if not extracted_data:

#                 print(f"  INFO: No data successfully extracted from {pdf_file_path.name} based on current parsing logic.")

#         except Exception as e:

#             print(f"  ERROR during PDF parsing for {pdf_file_path.name}: {e}")

#             import traceback

#             traceback.print_exc() # For more detailed error during development

#         return extracted_data

#     # --- END OF CUSTOMIZABLE PDF PARSING LOGIC ---

#     all_data_by_symbol = defaultdict(list)

#     processed_pdf_files = set()

#     for source_dir in pdf_source_dirs:

#         # ###! CUSTOMIZE HERE !### : Adjust glob pattern if your PDFs have a more specific naming scheme

#         for pdf_file in source_dir.glob("*.pdf"): 

#             abs_path = pdf_file.resolve()

#             if abs_path in processed_pdf_files:

#                 continue

#             processed_pdf_files.add(abs_path)

#             parsed_rows = parse_pdf_content(pdf_file)

#             for row in parsed_rows: # `parse_pdf_content` should ensure symbol and timestamp exist

#                 symbol = row['symbol'] # Assumes parse_pdf_content ensures this key exists

#                 all_data_by_symbol[symbol].append(row)

#     if not all_data_by_symbol:

#         print("No data records found in any PDF files (or PDF parsing needs customization).")

#         return

#     # Process collected data for each symbol

#     for symbol, records_list in all_data_by_symbol.items():

#         if not records_list: continue

#         print(f"  Updating PDF-derived CSV for symbol: {symbol} with {len(records_list)} new records")

#         new_df = pd.DataFrame(records_list)

#         # Convert timestamp column to datetime objects for proper sorting and duplicate handling

#         if 'timestamp' not in new_df.columns:

#             print(f"    ERROR: 'timestamp' column missing in data extracted for {symbol} from PDFs. Skipping.")

#             continue

#         try:

#             new_df['timestamp'] = pd.to_datetime(new_df['timestamp'], utc=True, errors='coerce')

#             new_df.dropna(subset=['timestamp'], inplace=True) # Drop rows where timestamp conversion failed

#             if new_df.empty:

#                 print(f"    WARN: No valid timestamped PDF data for {symbol} after conversion. Skipping.")

#                 continue

#         except Exception as e:

#             print(f"    ERROR: Could not convert 'timestamp' column to datetime for {symbol} from PDF data: {e}. Skipping.")

#             continue

#         # ###! CUSTOMIZE HERE !### : Decide CSV naming. Using symbol name directly.

#         output_csv_file = csv_output_dir / f"{symbol}.csv" 

#         combined_df = new_df # Default if no existing file or existing is bad

#         if output_csv_file.exists():

#             try:

#                 existing_df = pd.read_csv(output_csv_file)

#                 if not existing_df.empty and 'timestamp' in existing_df.columns:

#                     print(f"    Loading existing PDF-derived data for {symbol} from {output_csv_file.name}")

#                     existing_df['timestamp'] = pd.to_datetime(existing_df['timestamp'], utc=True, errors='coerce')

#                     existing_df.dropna(subset=['timestamp'], inplace=True)

#                     # Align columns before concat

#                     all_cols = new_df.columns.union(existing_df.columns)

#                     existing_df = existing_df.reindex(columns=all_cols)

#                     new_df_reindexed = new_df.reindex(columns=all_cols)

#                     combined_df = pd.concat([existing_df, new_df_reindexed], ignore_index=True)

#                 elif existing_df.empty:

#                     print(f"    Existing PDF-derived CSV for {symbol} is empty. Using new data.")

#                 else: # Missing timestamp or other issue

#                     print(f"    WARN: Existing PDF-derived CSV {output_csv_file.name} for {symbol} invalid. Overwriting.")

#             except pd.errors.EmptyDataError:

#                 print(f"    WARN: Existing PDF-derived CSV {output_csv_file.name} for {symbol} is empty. Overwriting.")

#             except Exception as e:

#                 print(f"    ERROR reading existing PDF-derived CSV {output_csv_file.name} for {symbol}: {e}. Will use only new data.")

#         # Deduplicate and sort

#         # Key columns for uniqueness: typically timestamp + all other actual data fields.

#         # Exclude source metadata like 'source_pdf_page'. 'symbol' is already per-file.

#         key_columns = [col for col in combined_df.columns if col not in ['symbol', 'source_pdf_page']]

#         if key_columns: # Ensure there are columns to deduplicate by

#             # Sort before dropping duplicates to control which one is kept (e.g., 'last' based on processing order)

#             # Sorting by all key_columns can make keep='first' or keep='last' more predictable.

#             sort_by_cols = ['timestamp'] + [k for k in key_columns if k != 'timestamp']

#             combined_df.sort_values(by=sort_by_cols, inplace=True) 

#             combined_df.drop_duplicates(subset=key_columns, keep='last', inplace=True)

#         # Final sort by timestamp

#         combined_df.sort_values(by='timestamp', inplace=True)

#         try:

#             combined_df.to_csv(output_csv_file, index=False)

#             print(f"    SUCCESS: Wrote {len(combined_df)} unique rows for {symbol} to PDF-derived {output_csv_file.name}")

#         except Exception as e:

#             print(f"    ERROR writing PDF-derived CSV {output_csv_file.name} for {symbol}: {e}")

#     print("PDF data to symbol-specific CSV processing finished.")

#     if BACKUP_PDF_CSV_EXPORT_DIR.exists():

#         print(f"Backing up generated PDF-derived CSVs to {BACKUP_PDF_CSV_EXPORT_DIR}...")

#         for csv_file in csv_output_dir.glob("*.csv"): # Assumes simple *.csv naming

#             try:

#                 shutil.copy(csv_file, BACKUP_PDF_CSV_EXPORT_DIR / csv_file.name)

#                 print(f"  Backed up {csv_file.name}")

#             except Exception as e:

#                 print(f"  Error backing up {csv_file.name}: {e}")

# --- MAIN EXECUTION ---

async def main():
    while True:
        print(f"\n{time.strftime('%Y-%m-%d %H:%M:%S')} Hourly backup and processing starting...")
        print("\n--- Kline Data Processing (JSON in klines_cache) ---")
        append_and_trim_kline_data()
        print("\n--- Market Data File Management (Copying to Backup & Local Cleanup) ---")
        move_market_data() # Handles market_data_*.json AND *.pdf in DATA_DIR
        print("\n--- Plot File Management ---")
        move_plot_files()
        print("\n--- Processing market_data_*.json to Symbol CSVs (Directly to Backup) ---")
        json_market_data_sources = [DATA_DIR, BACKUP_DATA] 
        # Pass the BACKUP directory as the output directory
        process_market_data_json_to_symbol_csvs(json_market_data_sources, BACKUP_SYMBOL_MARKET_DATA_CSVS_DIR)
        print("\n--- Processing PDF Market Data to Symbol CSVs (Directly to Backup) ---")
        pdf_market_data_sources = [DATA_DIR, BACKUP_DATA] 
        # Pass the BACKUP directory as the output directory
        process_pdfs_to_symbol_csvs(pdf_market_data_sources, BACKUP_PDF_CSV_EXPORT_DIR)
        await asyncio.sleep(3600)

if __name__ == "__main__":
    if pdfplumber is None:
        print("Warning: pdfplumber library is not installed. PDF processing will be skipped.")
        print("If you intend to process PDFs, please install it using: pip install pdfplumber")
        # You might choose to exit here if PDF processing is critical and not optional
        # exit(1) 
    asyncio.run(main())
