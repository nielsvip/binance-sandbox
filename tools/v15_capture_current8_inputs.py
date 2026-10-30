"""Capture current exact admission and full-set exports for a diagnostic frozen universe."""
import datetime
import hashlib
import json
from pathlib import Path


def main():
    root = Path(__file__).resolve().parents[1]
    now = datetime.datetime.now(datetime.timezone.utc)
    target = root / "data/reports" / ("nextgen_current8_inputs_" + now.strftime("%Y%m%dT%H%M%SZ"))
    target.mkdir()
    sides = ["1000PEPEUSDC_LONG", "AAVEUSDC_LONG", "1000BONKUSDC_SHORT", "1INCHUSDT_SHORT", "IBM_LONG", "TMO_LONG", "COIN_SHORT", "CDE_SHORT"]
    payloads = {}
    def read(name):
        if name not in payloads:
            payloads[name] = (root / name).read_bytes()
        return json.loads(payloads[name])
    keys = read("tradeable_keys.json")
    admission = {}
    for sym_side in sides:
        symbol, side = sym_side.rsplit("_", 1)
        if symbol in ("IBM", "TMO", "COIN", "CDE"):
            book = f"symbols_trb_{side.lower()}.json"
            assert symbol in read(book)
            admission[sym_side] = {"venue": "stocks", "book": book}
        else:
            accounts = []
            for key in keys:
                if not key.endswith(":" + sym_side):
                    continue
                account = key.split(":", 1)[0]
                fin_mirror_until = datetime.datetime(2026, 11, 9, 1, 10, 12, tzinfo=datetime.timezone.utc)
                book_account = "men" if account == "fin" and now < fin_mirror_until else account
                book = f"symbols_{book_account}_{side.lower()}.json"
                if (root / book).exists() and symbol in read(book):
                    accounts.append(account)
            assert accounts, sym_side
            admission[sym_side] = {"venue": "crypto", "accounts": accounts}
    live = root / "data/live_baseline_snapshots"
    baseline = json.loads((live / "manifest.json").read_bytes())
    (target / "live_baseline_snapshots").mkdir()
    for sym_side in sides:
        blob = (live / f"{sym_side}.json").read_bytes()
        assert hashlib.sha256(blob).hexdigest() == baseline["sets"][sym_side], sym_side
        (target / "live_baseline_snapshots" / f"{sym_side}.json").write_bytes(blob)
    latest = json.loads((live / "manifest.json").read_bytes())
    assert all(latest["sets"][side] == baseline["sets"][side] for side in sides)
    baseline["sets"] = {side: baseline["sets"][side] for side in sides}
    (target / "live_baseline_snapshots/manifest.json").write_text(json.dumps(baseline, indent=2))
    for name, blob in payloads.items():
        assert hashlib.sha256((root / name).read_bytes()).digest() == hashlib.sha256(blob).digest(), name
        (target / name).write_bytes(blob)
    clock = (root / "tools/npz_cutoffs.py").read_bytes()
    (target / "npz_cutoffs.py").write_bytes(clock)
    manifest = {"captured_at_utc": now.strftime("%Y-%m-%dT%H:%M:%S.%fZ"), "sides": sides, "admission": admission, "book_sha256": {name: hashlib.sha256(blob).hexdigest() for name, blob in payloads.items()}, "baseline_sha256": baseline["sets"], "baseline_export_generated_at": baseline["generated_at"], "clock_sha256": hashlib.sha256(clock).hexdigest(), "status": "CAPTURED_ADMISSION_AND_BASELINES_NOT_BACKTEST_RESULTS"}
    (target / "admission.json").write_text(json.dumps(manifest, indent=2))
    print(target)


if __name__ == "__main__":
    main()
