#!/usr/bin/env python3
"""Check true add/remove candidates accounting for USDC pairs."""
import json, requests, time

with open("symbols.json") as f:
    current = set(json.load(f))

# Build base asset set (strip USDT/USDC suffix)
current_bases = {}
for s in current:
    if s.endswith("USDC"):
        base = s[:-4]
        current_bases[base] = current_bases.get(base, []) + [s]
    elif s.endswith("USDT"):
        base = s[:-4]
        current_bases[base] = current_bases.get(base, []) + [s]

# Fetch tickers
resp = requests.get("https://fapi.binance.com/fapi/v1/ticker/24hr", timeout=30)
tickers = {t["symbol"]: float(t.get("quoteVolume", 0)) for t in resp.json()}

# Fetch exchange info
resp2 = requests.get("https://fapi.binance.com/fapi/v1/exchangeInfo", timeout=30)
now_ms = time.time() * 1000
active_info = {}
for s in resp2.json()["symbols"]:
    if s["status"] == "TRADING" and s["contractType"] == "PERPETUAL":
        age = (now_ms - s.get("onboardDate", 0)) / (24 * 3600 * 1000)
        active_info[s["symbol"]] = {"age_days": int(age), "new": age < 180}
active_set = set(active_info.keys())

# Check MAGMA and XNY
print("=== CHECKING MAGMA / XNY ===")
for pattern in ["MAGMA", "XNY", "MAGM", "1000MAGMA", "XNYUSDT", "MAGMAUSDT"]:
    matches = [s for s in active_info if pattern.upper() in s.upper()]
    for m in matches:
        vol = tickers.get(m, 0)
        info = active_info[m]
        in_cur = m in current
        print(f"  {m}: vol=${vol/1e6:.1f}M age={info['age_days']}d in_symbols={in_cur}")
if not any(p.upper() + "USDT" in active_info for p in ["MAGMA", "XNY"]):
    print("  Neither MAGMAUSDT nor XNYUSDT found on Binance Futures")

# DELISTED from current
print(f"\n=== DELISTED / INACTIVE ({len(current - active_set)} symbols) ===")
for s in sorted(current - active_set):
    print(f"  REMOVE: {s} — not active on Binance")

# Under $1M volume
print(f"\n=== REMOVE: <$1M DAILY VOLUME ===")
count = 0
for s in sorted(current & active_set):
    vol = tickers.get(s, 0)
    if vol < 1_000_000:
        count += 1
        print(f"  {s}: ${vol/1e6:.2f}M")
print(f"  Total: {count}")

# TRUE add candidates: base asset NOT covered by any USDT or USDC pair in current
print(f"\n=== TRUE ADD CANDIDATES (base asset not in symbols.json at all) ===")
print(f"  {'Symbol':22s} {'Vol($M)':>8s} {'Age(d)':>7s} {'USDC exists':>12s}")
adds = []
for sym in sorted(active_info.keys()):
    if not sym.endswith("USDT"):
        continue
    base = sym[:-4]
    if base in current_bases:
        continue  # Already have this base as USDT or USDC
    vol = tickers.get(sym, 0)
    if vol < 5_000_000:
        continue
    info = active_info[sym]
    if info["new"]:
        continue
    usdc_sym = base + "USDC"
    usdc_active = usdc_sym in active_info
    usdc_vol = tickers.get(usdc_sym, 0) if usdc_active else 0
    # Prefer USDC if it exists and has decent volume
    if usdc_active and usdc_vol > 1_000_000:
        recommend = usdc_sym
        rec_vol = usdc_vol
    else:
        recommend = sym
        rec_vol = vol
    usdc_note = f"YES ${usdc_vol/1e6:.0f}M" if usdc_active else "NO"
    adds.append((recommend, rec_vol, info["age_days"], usdc_note, sym, vol))

adds.sort(key=lambda x: x[1], reverse=True)
for rec, rec_vol, age, usdc_note, usdt_sym, usdt_vol in adds:
    suffix = f" (prefer USDC)" if rec != usdt_sym else ""
    print(f"  {rec:22s} ${rec_vol/1e6:7.1f}M {age:7d}d  USDC:{usdc_note}{suffix}")

print(f"\n  Total true add candidates: {len(adds)}")

# Summary
print(f"\n=== SUMMARY ===")
delisted = current - active_set
under1m = {s for s in current & active_set if tickers.get(s, 0) < 1_000_000}
print(f"  Current symbols.json: {len(current)}")
print(f"  Delisted (remove):    {len(delisted)}")
print(f"  <$1M vol (remove):    {len(under1m)}")
print(f"  True adds:            {len(adds)}")
print(f"  Projected new total:  {len(current) - len(delisted) - len(under1m) + len(adds)}")
