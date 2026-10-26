import datetime
import json
import os
from pathlib import Path
import subprocess
import tempfile
import time


ROOT = Path("/Users/niels/Documents/binance")
DEST = ROOT / "data" / "vec_live" / "held_sides.json"


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


def main() -> None:
    sides = held_sides(ROOT)
    payload = {"generated_ts": time.time(), "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(), "held": sides}
    DEST.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", prefix=".held_sides.", suffix=".json", dir=DEST.parent, delete=False) as stream:
        json.dump(payload, stream, sort_keys=True)
        temp = Path(stream.name)
    os.replace(temp, DEST)
    for host in ("s1-pub", "s2"):
        subprocess.run(["rsync", "-az", "--timeout=30", "-e", "ssh -S none -o BatchMode=yes -o ConnectTimeout=8", str(DEST), f"{host}:~/binance-sandbox/data/vec_live/held_sides.json"], check=True, timeout=45)
    print(f"held_sides_pushed {payload['generated_utc']} unique={len(sides)}", flush=True)


if __name__ == "__main__":
    main()
