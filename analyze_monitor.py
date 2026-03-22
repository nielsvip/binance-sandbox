#!/usr/bin/env python3
"""Analyze trade_monitor output and produce structured data for the analysis report."""
import re
import json
from collections import Counter

lines = open("/home/niels/logs/trade_monitor_stdout.log").readlines()

# Crypto format
RE_CRYPTO = re.compile(
    r"\[(.)\]\s+(\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\|\s+(\w+):(\w+)\s+\|\s+"
    r"(\w+:\w+_(?:LONG|SHORT))\s+\|\s+(OPEN|CLOSE|REDUCE|AUGMENT)\s+(BUY|SELL)\s+"
    r"x(\d+)\s+@\$([0-9.]+)\s+val=\$(\d+)\s+\|\s+([+\-0-9.]+)%\s+\$([+\-0-9.]+)\s+\|\s+"
    r"(\w+)\s+sc:\s*([+\-0-9]+)\s+\|\s+1m:(\d+)/(\d+)"
)
# Tradier format
RE_TRADIER = re.compile(
    r"\[(.)\]\s+(\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}:\d{2})\s+\|\s+(\w+):(\w+)\s+\|\s+"
    r"(\w+)\s+\|\s+(OPEN|CLOSE|REDUCE|AUGMENT)\s+(BUY|SELL)\s+"
    r"x(\d+)\s+@\$([0-9.]+)\s+val=\$(\d+)\s+\|\s+(.*?)\s+\|\s+"
    r"(\w+)\s+sc:\s*([+\-0-9]+)"
)

trades = []
for i, line in enumerate(lines):
    reason_lines = []
    for j in range(i + 1, min(i + 3, len(lines))):
        rl = lines[j].strip()
        if rl and not rl.startswith("[") and not rl.startswith("=") and not rl.startswith("SUMMARY"):
            reason_lines.append(rl)
        else:
            break

    m = RE_CRYPTO.search(line)
    if m:
        g = m.groups()
        side_str = "LONG" if g[4].endswith("_LONG") else "SHORT"
        trades.append({
            "icon": g[0], "ts": g[1], "source": g[2], "account": g[3],
            "pos_key": g[4], "symbol": g[4].split(":")[1].rsplit("_", 1)[0],
            "action": g[5], "side": g[6], "position_side": side_str,
            "qty": int(g[7]), "price": float(g[8]), "val": int(g[9]),
            "gain_pct": float(g[10]), "gain_dollar": float(g[11]),
            "rating": g[12], "score": int(g[13]),
            "k1m": int(g[14]), "d1m": int(g[15]),
            "reason": reason_lines[0] if reason_lines else "",
            "flags": reason_lines[1] if len(reason_lines) > 1 else "",
            "type": "crypto",
        })
        continue

    m = RE_TRADIER.search(line)
    if m:
        g = m.groups()
        gain_str = g[10].strip()
        gain_pct = 0.0
        gain_dollar = 0.0
        if "%" in gain_str:
            parts = gain_str.split("%")
            try:
                gain_pct = float(parts[0].strip())
            except Exception:
                pass
            if len(parts) > 1 and "$" in parts[1]:
                try:
                    gain_dollar = float(parts[1].replace("$", "").strip())
                except Exception:
                    pass
        side_str = "LONG" if g[6] == "BUY" else "SHORT"
        trades.append({
            "icon": g[0], "ts": g[1], "source": g[2], "account": g[3],
            "symbol": g[4], "pos_key": f"{g[3]}:{g[4]}_{side_str}",
            "action": g[5], "side": g[6], "position_side": side_str,
            "qty": int(g[7]), "price": float(g[8]), "val": int(g[9]),
            "gain_pct": gain_pct, "gain_dollar": gain_dollar,
            "rating": g[11], "score": int(g[12]),
            "k1m": 50, "d1m": 50,
            "reason": reason_lines[0] if reason_lines else "",
            "flags": reason_lines[1] if len(reason_lines) > 1 else "",
            "type": "tradier",
        })

result = {}
crypto = [t for t in trades if t["type"] == "crypto"]
tradier = [t for t in trades if t["type"] == "tradier"]
result["total"] = len(trades)
result["crypto_count"] = len(crypto)
result["tradier_count"] = len(tradier)

# Ratings
result["ratings"] = {}
for r in ["SMART", "OK", "NEUTRAL", "BAD", "STUPID"]:
    result["ratings"][r] = sum(1 for t in trades if t["rating"] == r)

# Actions
result["actions"] = {}
for a in ["OPEN", "CLOSE", "AUGMENT", "REDUCE"]:
    result["actions"][a] = sum(1 for t in trades if t["action"] == a)

# Accounts
result["accounts"] = {}
for acc in sorted(set(t["account"] for t in trades)):
    subset = [t for t in trades if t["account"] == acc]
    sub_closes = [t for t in subset if t["action"] == "CLOSE"]
    sub_wins = [t for t in sub_closes if t["gain_pct"] > 0]
    sub_losses = [t for t in sub_closes if t["gain_pct"] < 0]
    sub_pnl = sum(t["gain_dollar"] for t in sub_closes)
    ratings = Counter(t["rating"] for t in subset)
    src = subset[0]["type"]
    avg_val = sum(t["val"] for t in subset) / len(subset) if subset else 0
    result["accounts"][acc] = {
        "type": src, "trades": len(subset), "closes": len(sub_closes),
        "wins": len(sub_wins), "losses": len(sub_losses),
        "pnl": round(sub_pnl, 2), "avg_val": round(avg_val, 0),
        "smart": ratings.get("SMART", 0), "ok": ratings.get("OK", 0),
        "neutral": ratings.get("NEUTRAL", 0), "bad": ratings.get("BAD", 0),
        "stupid": ratings.get("STUPID", 0),
    }

# Closes
closes = [t for t in trades if t["action"] == "CLOSE"]
wins = [t for t in closes if t["gain_pct"] > 0]
losses = [t for t in closes if t["gain_pct"] < 0]
flat = [t for t in closes if t["gain_pct"] == 0]
total_pnl = sum(t["gain_dollar"] for t in closes)
avg_win = sum(t["gain_pct"] for t in wins) / len(wins) if wins else 0
avg_loss = sum(t["gain_pct"] for t in losses) / len(losses) if losses else 0
gross_profit = sum(t["gain_dollar"] for t in wins)
gross_loss = abs(sum(t["gain_dollar"] for t in losses))
pf = gross_profit / gross_loss if gross_loss else float("inf")
result["closes"] = {
    "total": len(closes), "wins": len(wins), "losses": len(losses), "flat": len(flat),
    "pnl": round(total_pnl, 2),
    "win_rate": round(len(wins) / len(closes) * 100, 1) if closes else 0,
    "avg_win_pct": round(avg_win, 3), "avg_loss_pct": round(avg_loss, 3),
    "max_win_pct": round(max(t["gain_pct"] for t in wins), 2) if wins else 0,
    "max_loss_pct": round(min(t["gain_pct"] for t in losses), 2) if losses else 0,
    "gross_profit": round(gross_profit, 2), "gross_loss": round(gross_loss, 2),
    "profit_factor": round(pf, 2),
}

# Top wins / losses
result["top_wins"] = []
for t in sorted(wins, key=lambda x: x["gain_pct"], reverse=True)[:10]:
    result["top_wins"].append({"ts": t["ts"], "key": t["pos_key"], "gain_pct": t["gain_pct"], "gain_dollar": t["gain_dollar"], "reason": t["reason"]})
result["top_losses"] = []
for t in sorted(losses, key=lambda x: x["gain_pct"])[:10]:
    result["top_losses"].append({"ts": t["ts"], "key": t["key"] if "key" in t else t["pos_key"], "gain_pct": t["gain_pct"], "gain_dollar": t["gain_dollar"], "reason": t["reason"]})

# SMART trades
result["smart_trades"] = []
for s in sorted([t for t in trades if t["rating"] == "SMART"], key=lambda x: x["ts"]):
    result["smart_trades"].append({"ts": s["ts"], "account": s["account"], "symbol": s["symbol"], "action": s["action"], "side": s["side"], "gain_pct": s["gain_pct"], "score": s["score"], "reason": s["reason"], "val": s["val"]})

# STUPID trades
result["stupid_trades"] = []
for s in sorted([t for t in trades if t["rating"] == "STUPID"], key=lambda x: x["ts"]):
    result["stupid_trades"].append({"ts": s["ts"], "account": s["account"], "symbol": s["symbol"], "action": s["action"], "side": s["side"], "gain_pct": s["gain_pct"], "score": s["score"], "reason": s["reason"], "flags": s["flags"], "val": s["val"]})

# BAD analysis
bad = [t for t in trades if t["rating"] == "BAD"]
bad_flags = Counter()
for b in bad:
    for flag in b["flags"].split(", "):
        if flag.strip():
            bad_flags[flag.strip()] += 1
result["bad_analysis"] = {
    "total": len(bad),
    "top_flags": bad_flags.most_common(15),
    "by_action": dict(Counter(b["action"] for b in bad)),
    "by_account": dict(Counter(b["account"] for b in bad)),
}

# Long vs Short
longs = [t for t in trades if t["pos_key"].endswith("_LONG")]
shorts = [t for t in trades if t["pos_key"].endswith("_SHORT")]
lc = [t for t in longs if t["action"] == "CLOSE"]
sc = [t for t in shorts if t["action"] == "CLOSE"]
lw = sum(1 for t in lc if t["gain_pct"] > 0)
sw = sum(1 for t in sc if t["gain_pct"] > 0)
result["sides"] = {
    "long": {"trades": len(longs), "closes": len(lc), "wins": lw, "wr": round(lw / len(lc) * 100, 1) if lc else 0, "pnl": round(sum(t["gain_dollar"] for t in lc), 2)},
    "short": {"trades": len(shorts), "closes": len(sc), "wins": sw, "wr": round(sw / len(sc) * 100, 1) if sc else 0, "pnl": round(sum(t["gain_dollar"] for t in sc), 2)},
}

# Score vs outcome
result["score_vs_outcome"] = []
for lo, hi, label in [(-20, -8, "<-8"), (-7, -4, "-7 to -4"), (-3, -1, "-3 to -1"), (0, 2, "0 to 2"), (3, 7, "3 to 7"), (8, 20, "8+")]:
    subset = [t for t in closes if lo <= t["score"] <= hi]
    if not subset:
        continue
    w = sum(1 for t in subset if t["gain_pct"] > 0)
    l = sum(1 for t in subset if t["gain_pct"] < 0)
    pnl = sum(t["gain_dollar"] for t in subset)
    result["score_vs_outcome"].append({"range": label, "closes": len(subset), "wins": w, "losses": l, "wr": round(w / len(subset) * 100, 1), "pnl": round(pnl, 2)})

# Top symbols
sym_counts = Counter()
for t in trades:
    sym_counts[t["symbol"]] += 1
result["top_symbols"] = []
for s, c in sym_counts.most_common(25):
    sym_closes = [t for t in closes if t["symbol"] == s]
    sym_wins = [t for t in sym_closes if t["gain_pct"] > 0]
    sym_pnl = sum(t["gain_dollar"] for t in sym_closes)
    wr = len(sym_wins) / len(sym_closes) * 100 if sym_closes else 0
    result["top_symbols"].append({"symbol": s, "trades": c, "closes": len(sym_closes), "wr": round(wr, 0), "pnl": round(sym_pnl, 2)})

# Top reasons
reason_counts = Counter()
for t in trades:
    r = t["reason"].strip()
    if r:
        reason_counts[r[:60]] += 1
result["top_reasons"] = reason_counts.most_common(25)

# Rating by action
result["rating_by_action"] = {}
for action in ["OPEN", "CLOSE", "AUGMENT", "REDUCE"]:
    subset = [t for t in trades if t["action"] == action]
    rc = Counter(t["rating"] for t in subset)
    result["rating_by_action"][action] = {"SMART": rc.get("SMART", 0), "OK": rc.get("OK", 0), "NEUTRAL": rc.get("NEUTRAL", 0), "BAD": rc.get("BAD", 0), "STUPID": rc.get("STUPID", 0)}

# Hourly distribution
hc = Counter()
for t in trades:
    ts = t["ts"]
    if " " in ts:
        h = ts.split(" ")[1].split(":")[0] if "-" in ts.split(" ")[0] and len(ts.split(" ")[0]) > 5 else ts.split(":")[0][-2:].strip()
    else:
        h = ts.split(":")[0][-2:].strip()
    hc[h] += 1
result["hourly"] = {h: hc[h] for h in sorted(hc)}

# Value distribution
result["val_dist"] = {}
for lo, hi, label in [(0, 5, "$0-5"), (6, 10, "$6-10"), (11, 20, "$11-20"), (21, 50, "$21-50"), (51, 100, "$51-100"), (101, 500, "$101-500"), (501, 99999, "$500+")]:
    c = sum(1 for t in trades if lo <= t["val"] <= hi)
    if c:
        result["val_dist"][label] = c

# All flags
all_flags = Counter()
for t in trades:
    for flag in t["flags"].split(", "):
        f = flag.strip()
        if f:
            all_flags[f] += 1
result["all_flags"] = all_flags.most_common(20)

# Summaries from log
summaries = []
for line in lines:
    if "SUMMARY" in line:
        summaries.append(line.strip())
result["summaries"] = summaries

print(json.dumps(result, indent=2))
