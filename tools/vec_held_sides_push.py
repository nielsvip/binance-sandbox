import datetime
import gzip
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path("/Users/niels/Documents/binance")
DEST = ROOT / "data" / "vec_live" / "held_sides.json"
TAIL_DEST = ROOT / "data" / "vec_live" / "held_cache_tails.json.gz"


def held_sides(root: Path) -> list[str]:
    held = set()
    for account in ("ang", "inf", "flz", "men", "fin"):
        for side in ("long", "short"):
            path = root / account / f"{side}_positions.json"
            rows = json.loads(path.read_text())
            for key, row in rows.items():
                if abs(float(row.get("positionAmt") or 0.0)) > 0.0:
                    ss = key.rsplit(":", 1)[-1]
                    if ss.endswith(("_LONG", "_SHORT")):
                        held.add(ss)
    return sorted(held)


def cache_tails(root: Path, sides: list[str]) -> dict:
    out = {}
    for symbol in sorted({ss.rsplit("_", 1)[0] for ss in sides}):
        path = root / "klines_cache" / f"{symbol}_15m.json"
        try:
            rows = json.loads(path.read_text())
            if isinstance(rows, list) and len(rows) >= 2:
                out[symbol] = rows[-4:]
        except (OSError, ValueError):
            continue
    return out


def main() -> None:
    sides = held_sides(ROOT)
    payload = {"generated_ts": time.time(), "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "held": sides}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".held_sides.", suffix=".json", dir=DEST.parent, delete=False) as stream:
        json.dump(payload, stream, sort_keys=True)
        temp = Path(stream.name)
    os.replace(temp, DEST)
    tails = cache_tails(ROOT, sides)
    with tempfile.NamedTemporaryFile(mode="wb", prefix=".held_cache_tails.", suffix=".json.gz", dir=DEST.parent, delete=False) as stream:
        with gzip.GzipFile(fileobj=stream, mode="wb") as zipped:
            zipped.write(json.dumps({"generated_ts": payload["generated_ts"], "rows": tails}, sort_keys=True).encode())
        tail_temp = Path(stream.name)
    os.replace(tail_temp, TAIL_DEST)
    for host in ("s1-pub", "s2"):
        subprocess.run(["rsync", "-az", "--timeout=30", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=8", str(DEST), f"{host}:~/binance-sandbox/data/vec_live/held_sides.json"], check=True, timeout=45)
    subprocess.run(["rsync", "-az", "--timeout=30", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=8", str(TAIL_DEST), "s1-pub:~/binance-sandbox/data/vec_live/held_cache_tails.json.gz"], check=True, timeout=45)
    print(f"held_sides_pushed {payload['generated_utc']} unique={len(sides)} cache_tails={len(tails)}", flush=True)


if __name__ == "__main__":
    main()
