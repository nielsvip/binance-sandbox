#!/usr/bin/env python3
"""balance_floor_watchdog.py — NEVER-GO-BELOW-ZERO safety.

Polls each live account's free cash / available balance every WATCH_INTERVAL_SEC.
When balance drops to MIN_FLOOR_USD (default $1) for any account, writes a
sentinel file:

    data/HALT_TRADING_<account>

Live trading scripts (ez_manage / ez_positions_quick / tradier_manage) can
short-circuit at the top of execute_now / execute_trade_wrapper if this file
exists. The watchdog ALSO logs a CRITICAL alert and (optionally) sends SIGSTOP
to the worker process when balance crosses the EMERGENCY_FLOOR (default $0.10).

Recovery: when balance returns above MIN_FLOOR_USD + RECOVERY_BUFFER_USD, the
sentinel is removed and any SIGSTOPped worker is SIGCONTed.

Crypto:   reads futures wallet balance via python-binance Client.futures_account_balance()
Tradier:  reads account balances via tradier_api.TradierClient.get_account_balances()

USAGE:
    python3 balance_floor_watchdog.py                       # daemon, 30s interval
    python3 balance_floor_watchdog.py --once                # single check + report
    python3 balance_floor_watchdog.py --threshold 50        # custom floor in USD
    python3 balance_floor_watchdog.py --emergency-stop      # SIGSTOP worker on floor breach
    python3 balance_floor_watchdog.py --account inf,fin     # subset of accounts
"""
import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

BASE = Path(os.environ.get("BASE_PATH", "/Users/niels/Documents/binance"))
sys.path.insert(0, str(BASE))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler(BASE / "data" / "balance_floor_watchdog.log"),
    ],
)
log = logging.getLogger("balance_floor")

CRYPTO_ACCOUNTS = ["ang", "inf", "flz", "men", "fin"]
STOCK_ACCOUNTS = ["trb", "trc"]
ALL_ACCOUNTS = CRYPTO_ACCOUNTS + STOCK_ACCOUNTS

SENTINEL_DIR = BASE / "data"
STATUS_FILE = BASE / "data" / "balance_floor_status.json"


@dataclass
class WatchdogConfig:
    interval_sec: int = 30
    min_floor_usd: float = 1.0          # below this → sentinel HALT
    recovery_buffer_usd: float = 5.0    # above (min + buffer) → sentinel removed
    emergency_floor_usd: float = 0.10   # below this → SIGSTOP worker (if --emergency-stop)
    emergency_stop: bool = False
    accounts: List[str] = field(default_factory=lambda: list(ALL_ACCOUNTS))


def sentinel_path(account: str) -> Path:
    return SENTINEL_DIR / f"HALT_TRADING_{account}"


def write_sentinel(account: str, balance: float, reason: str) -> None:
    p = sentinel_path(account)
    payload = {
        "account": account,
        "halted_at_utc": datetime.now(timezone.utc).isoformat(),
        "balance_usd": round(float(balance), 4),
        "reason": reason,
    }
    p.write_text(json.dumps(payload, indent=2))
    log.critical(f"🚨 HALT sentinel written: {p} balance=${balance:.2f} reason={reason}")


def clear_sentinel(account: str, balance: float) -> None:
    p = sentinel_path(account)
    if p.exists():
        p.unlink()
        log.warning(f"✅ HALT sentinel cleared: {account} balance=${balance:.2f} (back above floor + buffer)")


def is_halted(account: str) -> bool:
    return sentinel_path(account).exists()


# ─── balance fetchers ─────────────────────────────────────────────────────

def _load_dotenv_gpg() -> Dict[str, str]:
    """Decrypt .env.gpg if available; otherwise read .env / process env.
    Uses absolute gpg path because launchd starts processes with minimal PATH
    (missing /opt/homebrew/bin where gpg lives on Apple Silicon)."""
    env: Dict[str, str] = dict(os.environ)
    gpg = BASE / ".env.gpg"
    if not gpg.exists():
        return env
    import shutil
    import subprocess
    gpg_bin = (
        shutil.which("gpg")
        or "/opt/homebrew/bin/gpg"   # apple silicon brew
        or "/usr/local/bin/gpg"      # intel mac brew
    )
    if not gpg_bin or not Path(gpg_bin).exists():
        log.warning("gpg binary not found; .env.gpg cannot be decrypted")
        return env
    try:
        res = subprocess.run(
            [gpg_bin, "--batch", "--quiet", "--decrypt", str(gpg)],
            capture_output=True, text=True, timeout=10,
            env={**os.environ, "GNUPGHOME": os.environ.get("GNUPGHOME", str(Path.home() / ".gnupg"))},
        )
        if res.returncode != 0:
            log.warning(f"gpg decrypt rc={res.returncode}: {(res.stderr or '')[:200]}")
            return env
        for line in (res.stdout or "").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip().strip('"').strip("'")
    except Exception as e:
        log.warning(f"could not decrypt .env.gpg: {type(e).__name__}: {e}")
    return env


def fetch_crypto_balance(account: str, env: Dict[str, str]) -> Tuple[Optional[float], str]:
    """Return (balance_usd, source_text). USDC + USDT futures wallet for the
    account's API key. .env.gpg uses lowercase prefix: ang_API_KEY, inf_API_KEY..."""
    prefix = account.lower()
    api_key = env.get(f"{prefix}_API_KEY")
    api_secret = env.get(f"{prefix}_API_SECRET")
    if not api_key or not api_secret:
        return None, f"no {prefix}_API_KEY in env"
    try:
        from binance.client import Client  # type: ignore
        c = Client(api_key=api_key, api_secret=api_secret)
        bals = c.futures_account_balance()
        wanted = {"USDC", "USDT", "BUSD"}
        usd = 0.0
        breakdown = []
        for b in bals or []:
            asset = (b.get("asset") or "").upper()
            if asset in wanted:
                try:
                    avail = float(b.get("availableBalance") or b.get("balance") or 0)
                except (TypeError, ValueError):
                    avail = 0.0
                if avail:
                    usd += avail
                    breakdown.append(f"{asset}={avail:.2f}")
        return float(usd), f"futures availableBalance ({', '.join(breakdown) or 'all 0'})"
    except Exception as e:
        return None, f"binance fetch error: {type(e).__name__}: {e}"


def fetch_tradier_balance(account: str, env: Dict[str, str]) -> Tuple[Optional[float], str]:
    """Return (free_cash_usd, source_text). Uses TradierAPIClient — the same
    client tradier_manage uses. Reads TRADIER_API_KEY_<UPPER> + TRADIER_ACCOUNT_ID_<UPPER>."""
    prefix = account.upper()
    api_key = env.get(f"TRADIER_API_KEY_{prefix}")
    acct_id = env.get(f"TRADIER_ACCOUNT_ID_{prefix}")
    if not api_key or not acct_id:
        return None, f"no TRADIER_API_KEY_{prefix} / TRADIER_ACCOUNT_ID_{prefix} in env"
    # Make sure these reach os.environ since TradierAPIClient reads via os.getenv
    os.environ.setdefault(f"TRADIER_API_KEY_{prefix}", api_key)
    os.environ.setdefault(f"TRADIER_ACCOUNT_ID_{prefix}", acct_id)
    try:
        from tradier_api import TradierAPIClient  # type: ignore

        async def _fetch():
            # override_config bypasses TradierConfig.get_account_config — direct creds.
            client = TradierAPIClient(
                config=None,
                account_key=account,
                override_config={"api_key": api_key, "account_id": acct_id},
            )
            try:
                return await client.get_account_balances(account_key=account)
            finally:
                if getattr(client, "session", None):
                    try:
                        await client.session.close()
                    except Exception:
                        pass

        bals = asyncio.run(_fetch())
        if not bals:
            return None, "tradier returned empty balances"
        # Tradier balance shape: top-level + margin/cash/pdt sub-dicts.
        # 'total_cash' is the free cash floor. Fall back through alternatives.
        for k in ("total_cash", "cash_available", "option_buying_power",
                  "stock_buying_power", "total_equity"):
            v = bals.get(k)
            if v is None:
                for sub in ("margin", "cash", "pdt"):
                    s = bals.get(sub)
                    if isinstance(s, dict) and k in s:
                        v = s[k]; break
            if v is not None:
                try:
                    return float(v), f"tradier balances.{k}"
                except (TypeError, ValueError):
                    continue
        return None, f"no cash field in keys={list(bals.keys())[:8]}"
    except Exception as e:
        return None, f"tradier fetch error: {type(e).__name__}: {e}"


# ─── core loop ─────────────────────────────────────────────────────────────

def find_worker_pid(account: str) -> Optional[int]:
    """Match the live trading worker PID by command line. Best-effort."""
    try:
        import subprocess
        out = subprocess.run(
            ["pgrep", "-af", f"--account[ =]{account}"],
            capture_output=True, text=True, timeout=3,
        ).stdout
        for line in out.splitlines():
            if "ez_manage.py" in line or "tradier_manage.py" in line:
                return int(line.split()[0])
    except Exception:
        pass
    return None


def emergency_stop_worker(account: str) -> bool:
    pid = find_worker_pid(account)
    if pid is None:
        log.warning(f"emergency_stop {account}: no worker pid found")
        return False
    try:
        os.kill(pid, signal.SIGSTOP)
        log.critical(f"⛔ SIGSTOP sent to worker {account} pid={pid}")
        return True
    except Exception as e:
        log.error(f"emergency_stop {account} pid={pid}: {e}")
        return False


def emergency_resume_worker(account: str) -> bool:
    pid = find_worker_pid(account)
    if pid is None:
        return False
    try:
        os.kill(pid, signal.SIGCONT)
        log.warning(f"▶ SIGCONT sent to worker {account} pid={pid}")
        return True
    except Exception:
        return False


def check_account(account: str, env: Dict[str, str], cfg: WatchdogConfig) -> Dict[str, object]:
    if account in CRYPTO_ACCOUNTS:
        bal, src = fetch_crypto_balance(account, env)
    elif account in STOCK_ACCOUNTS:
        bal, src = fetch_tradier_balance(account, env)
    else:
        return {"account": account, "error": "unknown account"}

    if bal is None:
        log.warning(f"[{account}] balance unknown ({src}) — sentinel state preserved")
        return {"account": account, "balance": None, "source": src,
                "halted": is_halted(account)}

    halted_now = is_halted(account)
    if bal <= cfg.min_floor_usd:
        if not halted_now:
            write_sentinel(account, bal, f"balance ${bal:.2f} ≤ min_floor ${cfg.min_floor_usd:.2f}")
        else:
            log.error(f"[{account}] STILL HALTED balance=${bal:.2f} (≤ floor ${cfg.min_floor_usd})")
        if cfg.emergency_stop and bal <= cfg.emergency_floor_usd:
            emergency_stop_worker(account)
        return {"account": account, "balance": bal, "source": src,
                "halted": True, "floor": cfg.min_floor_usd}
    if halted_now and bal >= (cfg.min_floor_usd + cfg.recovery_buffer_usd):
        clear_sentinel(account, bal)
        if cfg.emergency_stop:
            emergency_resume_worker(account)
    log.info(f"[{account}] OK balance=${bal:.2f} ({src})")
    return {"account": account, "balance": bal, "source": src,
            "halted": is_halted(account)}


def run_once(cfg: WatchdogConfig) -> Dict[str, object]:
    env = _load_dotenv_gpg()
    results = []
    for acct in cfg.accounts:
        results.append(check_account(acct, env, cfg))
    payload = {
        "checked_at_utc": datetime.now(timezone.utc).isoformat(),
        "interval_sec": cfg.interval_sec,
        "min_floor_usd": cfg.min_floor_usd,
        "recovery_buffer_usd": cfg.recovery_buffer_usd,
        "results": results,
    }
    try:
        STATUS_FILE.write_text(json.dumps(payload, indent=2))
    except Exception as e:
        log.error(f"could not write status file: {e}")
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--interval", type=int, default=30,
                        help="seconds between checks (default 30)")
    parser.add_argument("--threshold", type=float, default=1.0,
                        help="USD min floor — below this triggers HALT (default 1)")
    parser.add_argument("--recovery-buffer", type=float, default=5.0,
                        help="USD buffer above floor for sentinel clear")
    parser.add_argument("--emergency-floor", type=float, default=0.10,
                        help="USD — below this SIGSTOPs worker if --emergency-stop")
    parser.add_argument("--emergency-stop", action="store_true",
                        help="SIGSTOP the worker process when balance breaches emergency floor")
    parser.add_argument("--accounts", default=",".join(ALL_ACCOUNTS),
                        help="comma-separated subset of accounts to watch")
    parser.add_argument("--once", action="store_true",
                        help="run a single check + exit (do not daemonize)")
    args = parser.parse_args()
    cfg = WatchdogConfig(
        interval_sec=args.interval,
        min_floor_usd=args.threshold,
        recovery_buffer_usd=args.recovery_buffer,
        emergency_floor_usd=args.emergency_floor,
        emergency_stop=args.emergency_stop,
        accounts=[a.strip() for a in args.accounts.split(",") if a.strip()],
    )
    log.info(f"balance_floor_watchdog starting · interval={cfg.interval_sec}s · "
             f"floor=${cfg.min_floor_usd} · buffer=${cfg.recovery_buffer_usd} · "
             f"accts={cfg.accounts} · emergency_stop={cfg.emergency_stop}")
    if args.once:
        payload = run_once(cfg)
        print(json.dumps(payload, indent=2))
        return
    while True:
        try:
            run_once(cfg)
        except KeyboardInterrupt:
            log.info("interrupted, exiting")
            break
        except Exception as e:
            log.error(f"cycle error: {e}", exc_info=True)
        time.sleep(cfg.interval_sec)


if __name__ == "__main__":
    main()
