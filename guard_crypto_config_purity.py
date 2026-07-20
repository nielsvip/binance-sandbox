#!/usr/bin/env python3
"""guard_crypto_config_purity.py — quarantines any non-crypto key found in the
LIVE crypto per-symbol config file.

Background (2026-07-06): data/hourly_reconfig/per_sym_active_config.json is read
by ez_manage.py + ez_positions_quick.py for LIVE CRYPTO per-symbol overrides. It
was found contaminated with ~124 stock (tradier trb/trc) keys and ZERO crypto
keys, because several producers (per_sym_trb_profiles.py, promote_pending_per_sym.py,
rate_filter_promoter.py, tradier_hourly_reconfig.py -> flz_hourly_reconfig.py
pending pipeline) share this filename. Crypto and stocks are separate systems and
must never share this file. Some of those producers are LOCKED (per
LOCKED_FILES.md) and cannot be edited to stop writing here directly, and the file
is also rsync'd down from S1 every 10 minutes (crontab), so contamination can
recur without any local write at all. This guard re-splits the file on every run:
any key whose base symbol does not end in USDT/USDC is moved OUT into the
sibling stocks-only file (per_sym_active_config_stocks.json) so the crypto file
stays crypto-only between producer runs.

Safe to run frequently (cron every few minutes). No-ops (does not touch either
file's mtime) when the crypto file is already clean, to avoid needless churn on
the mtime-cache readers in ez_manage.py/ez_positions_quick.py.
"""
from __future__ import annotations
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CRYPTO_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config.json"
STOCKS_CFG_PATH = ROOT / "data" / "hourly_reconfig" / "per_sym_active_config_stocks.json"
LOG_PATH = ROOT / "logs" / "crypto_config_purity_guard.log"


def is_crypto_key(key: str) -> bool:
    base = key.rsplit("_", 1)[0]
    return base.upper().endswith(("USDT", "USDC"))


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, default=str))
    tmp.replace(path)


def _log(message: str) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with LOG_PATH.open("a") as handle:
        handle.write(f"{stamp} {message}\n")
    print(f"[guard_crypto_config_purity] {message}", flush=True)


def run() -> int:
    try:
        crypto_cfg = json.loads(CRYPTO_CFG_PATH.read_text())
    except Exception as exc:
        _log(f"ABORT could not read {CRYPTO_CFG_PATH}: {exc}")
        return 1
    contaminant_keys = [k for k in crypto_cfg if not k.startswith("_") and not is_crypto_key(k)]
    if not contaminant_keys:
        return 0
    try:
        stocks_cfg = json.loads(STOCKS_CFG_PATH.read_text())
    except Exception:
        stocks_cfg = {}
    for key in contaminant_keys:
        stocks_cfg[key] = crypto_cfg.pop(key)
    stocks_cfg["_meta"] = {
        "purpose": "STOCK-ONLY per-symbol overrides (tradier trb/trc tickers). Quarantined here by guard_crypto_config_purity.py.",
        "last_quarantine_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_stock_keys": len([k for k in stocks_cfg if not k.startswith("_")]),
    }
    crypto_cfg["_meta"] = {
        "purpose": "CRYPTO-ONLY per-symbol overrides (USDT/USDC keys). Stock keys are FORBIDDEN here.",
        "last_guard_run_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "n_crypto_keys": len([k for k in crypto_cfg if not k.startswith("_")]),
    }
    _atomic_write(STOCKS_CFG_PATH, stocks_cfg)
    _atomic_write(CRYPTO_CFG_PATH, crypto_cfg)
    _log(f"QUARANTINED {len(contaminant_keys)} non-crypto key(s) -> {STOCKS_CFG_PATH.name}: {sorted(contaminant_keys)[:20]}{' ...' if len(contaminant_keys) > 20 else ''}")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
