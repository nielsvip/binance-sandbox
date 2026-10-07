#!/usr/bin/env python3
"""
24h TEMPLATE defaults verifier — bold B values are source-of-truth from config.
- Crypto: config.py + v12_quick_engine QuickConfig
- Stocks: config_tradier.py + v12_quick_engine QuickConfig
- Bold = default, must be re-verified every 24h, then immutable for 24h
"""
import dataclasses
import pathlib
import time
import json
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
# USER 2026-09-28: TEMPLATE.xlsx is legacy — the 4 cat_side templates are the only source sheets
TEMPLATES = [ROOT / "SPREADSHEETS" / f"TEMPLATE_{cat}.xlsx" for cat in ("STOCKS_LONG", "STOCKS_SHORT", "CRYPTO_LONG", "CRYPTO_SHORT")]
STAMP = ROOT / "SPREADSHEETS" / ".template_defaults_verified.json"
LOCK_HOURS = 24

# Sheets to check (switch sheets)
SWITCH_SHEETS = [
    "STDEV_SLOPE_SIZING","ENTRY_REVERSAL_BOUNCE","ENTRY_BREAKOUT_CHANNEL","ENTRY_CONFIRMATION_GATES",
    "EXIT_STRUCTURAL","EXIT_VELOCITY","REENTRY_WINDOWED","REENTRY_ADAPTIVE",
    "AUGMENT_TREND","AUGMENT_RISK_SIZING","REDUCE_PROFIT_LOCK","REDUCE_SIGNAL_RATER","GLOBAL_RISK_GATES",
]

def load_source_truth():
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    if str(ROOT / "tools") not in sys.path:
        sys.path.insert(0, str(ROOT / "tools"))
    import v12_quick_engine as V
    import config as C
    import config_tradier as CT
    # QuickConfig defaults
    qc = {f.name: f.default for f in dataclasses.fields(V.QuickConfig) if f.default is not dataclasses.MISSING}
    # Config (crypto) and Tradier (stocks)
    cfg_crypto = {}
    cfg_stocks = {}
    for k in dir(C.Config):
        if k.startswith("_"): continue
        try: cfg_crypto[k] = getattr(C.Config, k)
        except: pass
    for k in dir(CT.TradierConfig):
        if k.startswith("_"): continue
        try: cfg_stocks[k] = getattr(CT.TradierConfig, k)
        except: pass
    # Merge: QuickConfig is base, then crypto/stocks override per sheet type
    # For verifier, we check both: if sheet is stock vs crypto, use appropriate
    # We'll just keep both maps and let caller decide per sheet
    return qc, cfg_crypto, cfg_stocks

def is_stock_sheet(sheet: str) -> bool:
    # STDEV, REENTRY, REDUCE are stock/tradier; ENTRY is both but default to stocks for SNDK-like
    # Use config_tradier for all switch sheets per spec: config(for crypto)/config_tradier(stocks) + QuickConfig
    # We'll treat all switch sheets as stocks (config_tradier) + QuickConfig, but also check crypto fallback
    return True  # per spec: config_tradier for stocks, which covers switch sheets

def get_expected(key: str, qc, cfg_crypto, cfg_stocks, sheet: str):
    # Priority: sheet is stock -> cfg_stocks, else crypto
    # QuickConfig is also source; cfg_* overrides QuickConfig per get_defaults_for_symside logic (Tradier overrides V12)
    # So we mimic get_defaults_for_symside: start with QuickConfig, then override with live_cls
    base = qc.get(key)
    live = cfg_stocks.get(key) if is_stock_sheet(sheet) else cfg_crypto.get(key)
    # If live has it, use live (Tradier overrides V12 for stocks)
    if live is not None:
        return live
    return base

def verify_and_update(dry_run=False):
    import openpyxl
    from openpyxl.styles import Font
    qc, cfg_crypto, cfg_stocks = load_source_truth()
    changed = []
    checked = 0
    for TEMPLATE in TEMPLATES:
      if not TEMPLATE.exists():
        print(f"MISSING {TEMPLATE.name}")
        continue
      is_stock_tpl = "STOCKS" in TEMPLATE.name
      wb = openpyxl.load_workbook(str(TEMPLATE), data_only=False)
      tpl_changed = []
      for sheet in SWITCH_SHEETS:
        if sheet not in wb.sheetnames:
            continue
        ws = wb[sheet]
        for r in range(3, ws.max_row+1):
            key = ws.cell(r, 1).value
            if not key or not isinstance(key, str):
                continue
            key = key.strip()
            if not key or key.lower() in ("filter","option value","sheets applicable","gates"):
                continue
            cell_b = ws.cell(r, 2)
            is_bold = bool(cell_b.font.bold)
            if not is_bold:
                continue
            checked += 1
            live = (cfg_stocks if is_stock_tpl else cfg_crypto).get(key)
            expected = live if live is not None else qc.get(key)
            if expected is None:
                continue
            # Normalize for comparison
            cur = cell_b.value
            # Compare with same type coercion as v15_pilot parse_opt
            def norm(v):
                if isinstance(v, bool):
                    return v
                if isinstance(v, str) and v.lower() in ("true","false"):
                    return v.lower() == "true"
                return v
            # Also handle numeric string vs int
            try:
                if isinstance(expected, bool):
                    cur_norm = norm(cur)
                    exp_norm = expected
                elif isinstance(expected, int) and not isinstance(expected, bool):
                    cur_norm = int(float(str(cur))) if cur is not None else None
                    exp_norm = expected
                elif isinstance(expected, float):
                    cur_norm = float(str(cur)) if cur is not None else None
                    exp_norm = float(expected)
                else:
                    cur_norm = str(cur).strip() if cur is not None else None
                    exp_norm = str(expected).strip()
            except:
                cur_norm = cur
                exp_norm = expected
            if cur_norm != exp_norm:
                print(f"UPDATE {sheet} R{r} {key}: {cur!r} -> {expected!r} (bold)")
                if not dry_run:
                    cell_b.value = expected
                    cell_b.font = Font(name="Calibri", bold=True, color="000000")
                tpl_changed.append((sheet, r, key, cur, expected))
      if tpl_changed and not dry_run:
        # BADZIP FIX 2026-09-29: old path had NO zip validation and a direct-save fallback
        _aws_badzip(wb, TEMPLATE)
        print(f"Saved {len(tpl_changed)} updates to {TEMPLATE.name}")
      changed += tpl_changed
    if changed and not dry_run:
        STAMP.write_text(json.dumps({"verified_at": time.time(), "changed": len(changed), "checked": checked}, indent=2))
        print(f"Saved {len(changed)} updates, checked {checked} bold defaults, stamped {STAMP}")
    elif not changed:
        # Still stamp if 24h passed
        STAMP.write_text(json.dumps({"verified_at": time.time(), "changed": 0, "checked": checked}, indent=2))
        print(f"No changes, checked {checked} bold defaults, stamped")
    else:
        print(f"Dry-run would update {len(changed)} of {checked}")
    return changed, checked

def is_locked():
    if not STAMP.exists():
        return False
    try:
        data = json.loads(STAMP.read_text())
        verified_at = float(data.get("verified_at", 0))
        return (time.time() - verified_at) < LOCK_HOURS * 3600
    except:
        return False

if __name__ == "__main__":
    # USER 2026-09-29 (BIBLE §56 R4): template defaults change ONLY via the v15_avg_delta promotion. This verifier
    # overwrote the B candidate of every bold row (two bold rows of one switch -> duplicate rows), so it is REPORT-ONLY
    # unless an operator runs it with --apply.
    dry = True  # USER 2026-09-30: report-only, always — defaults change ONLY via tools/v15_avg_delta_apply.py
    if is_locked() and not dry:
        print(f"LOCKED: verified within {LOCK_HOURS}h, skipping update (use --dry-run to check)")
        sys.exit(0)
    verify_and_update(dry_run=dry)
