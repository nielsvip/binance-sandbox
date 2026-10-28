"""USER 2026-10-28: one-time MIN_HOLD migration. new MIN_HOLD_BARS (15m bars) = max(MIN_HOLD_BARS, ceil(MIN_HOLD_BARS_BEFORE_EXIT*180/900));
MIN_HOLD_BARS_BEFORE_EXIT (a 3m-bar count) is removed. Values stored verbatim (no snapping to template options). updated_at untouched."""
import json, math, sqlite3, sys, os, time, collections, datetime as dt

def migrate(d):
    if "MIN_HOLD_BARS_BEFORE_EXIT" not in d:
        return d, None
    be = d.get("MIN_HOLD_BARS_BEFORE_EXIT")
    mh = d.get("MIN_HOLD_BARS")
    out = dict(d); out.pop("MIN_HOLD_BARS_BEFORE_EXIT")
    try:
        conv = int(math.ceil(float(be) * 180.0 / 900.0 - 1e-9)) if be is not None else 0
    except (TypeError, ValueError):
        conv = 0
    if mh is None:
        new = conv if conv > 0 else None
    else:
        new = max(int(mh), conv)
    if new is not None:
        out["MIN_HOLD_BARS"] = new
    return out, (mh, be, new)

mode, kind, path = sys.argv[1], sys.argv[2], os.path.expanduser(sys.argv[3])
chg = collections.Counter(); before = collections.Counter(); after = collections.Counter(); n = 0
if kind == "db":
    con = sqlite3.connect(path, timeout=60)
    if mode == "apply":
        bk = os.path.expanduser(f"~/binance/backups/per_sym_store_before_minhold_migration_{dt.datetime.utcnow():%Y%m%d%H%M}.db")
        os.makedirs(os.path.dirname(bk), exist_ok=True)
        b = sqlite3.connect(bk); con.backup(b); b.close()
        print("backup", bk, os.path.getsize(bk))
        con.execute("BEGIN IMMEDIATE")
    rows = con.execute("SELECT sym_side, full_config_json FROM per_sym_active").fetchall()
    for ss, fj in rows:
        try: d = json.loads(fj or "{}")
        except Exception: continue
        if not isinstance(d, dict) or not d: continue
        n += 1
        nd, info = migrate(d)
        if info is None: continue
        before[info[0]] += 1; after[info[2]] += 1
        if info[0] != info[2]: chg[(info[0], info[2])] += 1
        if mode == "apply":
            con.execute("UPDATE per_sym_active SET full_config_json=? WHERE sym_side=?", (json.dumps(nd, separators=(",", ":")), ss))
    if mode == "apply":
        con.commit()
    con.close()
else:
    doc = json.load(open(path))
    sets = doc.get("sets") if isinstance(doc, dict) and isinstance(doc.get("sets"), dict) else doc
    for ss, d in list(sets.items()):
        cfg = d.get("full_config") if isinstance(d, dict) and isinstance(d.get("full_config"), dict) else d
        if not isinstance(cfg, dict): continue
        n += 1
        nd, info = migrate(cfg)
        if info is None: continue
        before[info[0]] += 1; after[info[2]] += 1
        if info[0] != info[2]: chg[(info[0], info[2])] += 1
        if cfg is d: sets[ss] = nd
        else: d["full_config"] = nd
    if mode == "apply":
        bk = path + f".before_minhold_migration_{dt.datetime.utcnow():%Y%m%d%H%M}"
        os.rename(path, bk) if False else None
        import shutil; shutil.copy2(path, bk)
        tmp = path + ".tmp_minhold"; json.dump(doc, open(tmp, "w")); os.replace(tmp, path)
        print("backup", bk)
print(mode, kind, path, "sets", n, "changed", sum(chg.values()), "before", dict(sorted(before.items(), key=lambda x: str(x[0]))), "after", dict(sorted(after.items(), key=lambda x: str(x[0]))))
print("transitions", dict(chg))
