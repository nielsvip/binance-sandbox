import json, sys, os, re, collections

BASE = os.path.expanduser("~/v17_ab2")


def canon(v):
    s = str(v).strip()
    try:
        f = float(s)
        return str(int(f)) if f.is_integer() else repr(f)
    except Exception:
        return s.lower() if s.lower() in ("true", "false") else s


def parse(k):
    m = re.match(r"^([^!]+)!([^:]+):(.*)$", k)
    if not m:
        return None
    tab, row, rest = m.groups()
    rest = rest.split(":REPASS")[0]
    sw, _, c = rest.partition("=")
    return tab, row, sw.strip(), c.strip(), k.endswith(":REPASS")


def load(arm, ss):
    return json.load(open(f"{BASE}/{arm}/progress/{ss}_v14_progress.json"))


for ss in sys.argv[1:]:
    o, n = load("old", ss), load("new", ss)
    nd = n.get("done", {})
    idx = {}
    for k, v in nd.items():
        p = parse(k)
        if p and isinstance(v, dict) and p[0] in ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "EXIT_STRUCTURAL", "EXIT_VELOCITY", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE", "AUGMENT_TREND", "AUGMENT_RISK_SIZING", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER", "GLOBAL_RISK_GATES", "STDEV_SLOPE_SIZING") and not p[4]:
            idx.setdefault((p[0], p[2], canon(p[3])), (k, v))
    nfinal = n.get("cumulative_overrides") or {}
    causes = collections.Counter()
    rows = []
    for k, v in o.get("done", {}).items():
        if not isinstance(v, dict) or not v.get("promoted"):
            continue
        d = float(v.get("delta") or 0)
        how = str(v.get("promoted_how") or "")
        p = parse(k)
        filters = list(v.get("promoted_filters") or [])
        if p is None or p[0] not in [x[0] for x in idx] and not p[0].startswith(("ENTRY", "EXIT", "REENTRY", "AUGMENT", "REDUCE", "GLOBAL", "STDEV")):
            # FOCUS / FINAL / other phase in the old arm
            lab = k.split(":", 1)[-1]
            sw = re.split(r"[=@+]", lab)[0]
            in_final = sw in nfinal
            cause = "OLD_POSTSTAGE(" + k.split("!")[0] + ")" + (" key-in-v17-final" if in_final else "")
            causes[cause.split(" ")[0]] += d
            rows.append((ss, k[:70], how, round(d, 3), cause))
            continue
        tab, row, sw, c, rep = p
        hit = idx.get((tab, sw, canon(c)))
        fval_final = all(str(nfinal.get(f.split("=")[0])) == f.split("=", 1)[1] for f in filters) if filters else None
        sw_final = str(nfinal.get(sw)) == str(c) or canon(nfinal.get(sw)) == canon(c)
        if hit is None:
            cause = "V17_NO_ROW_RECORD"
        else:
            nk, nv = hit
            nn = nv.get("naked_delta")
            if nv.get("promoted"):
                miss_f = [f for f in filters if f.split("=")[0] not in [x.split("=")[0] for x in (nv.get("promoted_filters") or [])]]
                cause = "V17_PROMOTED_SAME_ROW" + (f" but filters missing {miss_f[:2]}" if miss_f else "")
            elif rep:
                cause = f"SACRED_REPASS_old (v17 row naked={nn})"
            elif filters and nn is not None and float(nn) <= 1e-9:
                # old won via a joint filter: was the filter cell evaluated in V17?
                hdrs = [f for f in filters]
                skipped = nv.get("sampled_skip_p") or {}
                deferred = set(nv.get("coarse_deferred_filters") or []) | set(nv.get("v17_deferred_filters") or [])
                ev = [h for h in hdrs if h in (nv.get("yellows") or {})]
                if ev:
                    cause = f"V17_YELLOW_EVALUATED_NOT_POS ({[(h, round(nv['yellows'][h], 3)) for h in ev][:2]}) cum_before v17={round(nv.get('cumulative_before') or 0, 2)} old={round(v.get('cumulative_before') or 0, 2)}"
                elif any(h in deferred for h in hdrs):
                    cause = "V17_YELLOW_SAMPLED_OUT p=" + ",".join(str(skipped.get(h)) for h in hdrs if h in deferred)[:40]
                else:
                    cause = "V17_YELLOW_NOT_IN_ROW"
            elif nn is not None and float(nn) > 1e-9:
                cause = f"V17_POS_BUT_NOT_PROMOTED (naked={round(nn, 3)} reason={str(nv.get('reason'))[:50]})"
            elif nn is not None:
                cause = f"V17_NAKED_LE0 (naked={round(nn, 3)} cum_before v17={round(nv.get('cumulative_before') or 0, 2)} old={round(v.get('cumulative_before') or 0, 2)})"
            else:
                cause = f"V17_NOT_EVALUATED (reason={str(nv.get('reason'))[:50]})"
        if sw_final and (fval_final in (None, True)):
            cause += " [key already in V17 final]"
        causes[cause.split(" ")[0] + (" [in final]" if "[key already" in cause else "")] += d
        rows.append((ss, f"{tab}!{row} {sw}={c}" + (f" +{filters}" if filters else ""), how, round(d, 3), cause))
    print(f"=== {ss}: old final {o.get('final_gain') or o.get('cumulative_gain'):.2f} new final {n.get('final_gain') or n.get('cumulative_gain'):.2f}")
    for r in sorted(rows, key=lambda r: -r[3]):
        print("  ", r[1][:95], "|", r[2], "|", r[3], "|", r[4][:150])
    print("  LOST pp by cause:", {k: round(v, 2) for k, v in causes.most_common()})
