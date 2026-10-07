#!/usr/bin/env python3
"""Look up market data from Gamma API for our positions."""
import urllib.request, json

market_ids = ["1363498", "1363503", "1143818", "1438189", "1328718", "1285270", "1353139", "1385699"]
for mid in market_ids:
    url = f"https://gamma-api.polymarket.com/markets/{mid}"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            m = json.loads(r.read())
        toks = json.loads(m.get("clobTokenIds", "[]"))
        cid = m.get("conditionId", "")
        closed = m.get("closed", False)
        q = m.get("question", "")[:50]
        yes_tok = toks[0] if toks else "?"
        print(f"{mid} | closed={closed} | condId={cid[:24]} | yesTok={yes_tok[:24]} | {q}")
    except Exception as e:
        print(f"{mid} | ERROR: {e}")
