import gzip
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
LIVE = ROOT / "data" / "vec_live"
OVERLAY = LIVE / "forward_npz"


def md5(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def latest_ts(path: Path) -> int:
    import numpy as np
    with np.load(path) as store:
        return int(store["timestamps"][-1])


def active_symbols() -> tuple[list[str], dict]:
    from tools.v15_npz_freeze import frozen_syms
    held = json.loads((LIVE / "held_sides.json").read_text())
    with gzip.open(LIVE / "held_cache_tails.json.gz", "rt") as stream:
        tails = json.load(stream)
    now = time.time()
    if now - float(held["generated_ts"]) > 180 or now - float(tails["generated_ts"]) > 180:
        raise RuntimeError("held-side or Mac cache-tail manifest stale")
    held_symbols = {side.rsplit("_", 1)[0] for side in held["held"]}
    symbols = held_symbols & set(frozen_syms())
    for symbol in held_symbols - symbols:
        overlay = OVERLAY / f"{symbol}.npz"
        base = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
        if overlay.is_file() and base.is_file() and latest_ts(overlay) > latest_ts(base):
            symbols.add(symbol)
    symbols = sorted(symbols)
    return symbols, tails["rows"]


def merge_cache(symbol: str, rows: list) -> int:
    path = ROOT / "klines_cache" / f"{symbol}_15m.json"
    old = json.loads(path.read_text())
    last = old[-1]["timestamp"]
    new = [row for row in rows if row["timestamp"] > last]
    if not new:
        return 0
    backup = ROOT / "backups" / f"before_forward_npz_cache_{symbol}_{int(time.time())}.json"
    shutil.copy2(path, backup)
    merged = old + new
    assert all(merged[i]["timestamp"] < merged[i + 1]["timestamp"] for i in range(len(merged) - 1))
    temp = path.with_suffix(".json.forward_tmp")
    temp.write_text(json.dumps(merged))
    os.replace(temp, path)
    return len(new)


def refresh() -> dict:
    symbols, tails = active_symbols()
    OVERLAY.mkdir(parents=True, exist_ok=True)
    selected = []
    base_hashes = {}
    for symbol in symbols:
        rows = tails.get(symbol)
        if not isinstance(rows, list) or len(rows) < 2:
            continue
        base = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
        if not base.is_file():
            continue
        base_hashes[symbol] = md5(base)
        target = OVERLAY / f"{symbol}.npz"
        if not target.exists():
            shutil.copy2(base, target)
        merge_cache(symbol, rows)
        selected.append(symbol)
    if selected:
        env = dict(os.environ, V15_NPZ_FREEZE="0")
        command = [sys.executable, str(ROOT / "tools" / "npz_bar_refresh.py"), "--symbols", ",".join(selected), "--mode", "crypto", "--workers", str(min(4, len(selected))), "--ind-dir", str(OVERLAY), "--no-heal", "--no-sentiment", "--no-stamp"]
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True, text=True, timeout=240)
        if result.returncode not in (0, 3):
            raise RuntimeError(f"overlay refresh rc={result.returncode}: {result.stdout[-1000:]} {result.stderr[-500:]}")
    manifest = {"generated_ts": time.time(), "symbols": {}}
    for symbol in selected:
        base = ROOT / "backtest_v8" / "indicators" / f"{symbol}.npz"
        if md5(base) != base_hashes[symbol]:
            raise RuntimeError(f"frozen board NPZ changed: {symbol}")
        overlay = OVERLAY / f"{symbol}.npz"
        completed = int(__import__("datetime").datetime.fromisoformat(tails[symbol][-2]["timestamp"].replace("Z", "+00:00")).timestamp())
        end = latest_ts(overlay)
        if end < completed:
            raise RuntimeError(f"overlay stale for {symbol}: {end} < {completed}")
        manifest["symbols"][symbol] = {"base_md5": base_hashes[symbol], "overlay_md5": md5(overlay), "npz_end": end}
    temp = OVERLAY / ".manifest.tmp"
    temp.write_text(json.dumps(manifest, sort_keys=True))
    os.replace(temp, OVERLAY / "manifest.json")
    subprocess.run(["ssh", "-S", "none", "-o", "ConnectTimeout=8", "s2", "mkdir", "-p", "/home/niels/binance-sandbox/data/vec_live/forward_npz"], check=True, timeout=20)
    for symbol in manifest["symbols"]:
        subprocess.run(["rsync", "-az", "--timeout=30", "-e", "ssh -S none -o ConnectTimeout=8", str(OVERLAY / f"{symbol}.npz"), "s2:~/binance-sandbox/data/vec_live/forward_npz/"], check=True, timeout=60)
    subprocess.run(["rsync", "-az", "--timeout=30", "-e", "ssh -S none -o ConnectTimeout=8", str(OVERLAY / "manifest.json"), "s2:~/binance-sandbox/data/vec_live/forward_npz/manifest.json"], check=True, timeout=60)
    return {"symbols": list(manifest["symbols"]), "npz_end": {k: v["npz_end"] for k, v in manifest["symbols"].items()}}


if __name__ == "__main__":
    print(json.dumps(refresh(), sort_keys=True), flush=True)
