#!/usr/bin/env python3
"""PROMO QuickConfig hook (idempotent, anchor-based): sets the QuickConfig CLASS defaults listed in patch.json (promoted defaults on which all four
cat_side values agree). Edits exactly one `    KEY: type = value` line per key inside `class QuickConfig:`; a key with 0 or >1 matches is skipped and reported.
Usage: python apply_hook.py <path/to/v12_quick_engine.py>   (edits in place; prints per-key result; 'already applied' when nothing changed)"""
import json
import re
import sys
from pathlib import Path

p = Path(sys.argv[1])
patch = json.loads((Path(__file__).with_name("patch.json")).read_text())
tag, values = patch["tag"], patch["values"]
text = p.read_text()
i = text.find("class QuickConfig:")
j = text.find("\n    def ", i)
assert i >= 0 and j > i, "QuickConfig class segment not found"
body = text[i:j]


def lit(v, ann):
    a = ann.strip().lower()
    if isinstance(v, bool) or a == "bool":
        return "True" if bool(v) else "False"
    if a == "float":
        return repr(float(v))
    if a == "int":
        return str(int(float(v)))
    return json.dumps(v) if isinstance(v, str) else repr(v)


done, skipped, same = [], [], []
for key, v in sorted(values.items()):
    rx = re.compile(r"^(?P<head>    " + re.escape(key) + r"\s*:\s*(?P<ann>[^=\n]+?)\s*=\s*)(?P<val>[^#\n]*?)(?P<tail>\s*(#.*)?)$", re.M)
    ms = list(rx.finditer(body))
    if len(ms) != 1:
        skipped.append((key, len(ms)))
        continue
    m = ms[0]
    new = lit(v, m.group("ann"))
    old = m.group("val").strip()
    if old == new:
        same.append(key)
        continue
    c = (m.group("tail") or "").strip()
    line = f"{m.group('head')}{new}  # PROMO {tag}: was {old}" + (f" | {c[1:].strip()}" if c else "")
    body = body[: m.start()] + line + body[m.end():]
    done.append(key)
if done:
    p.write_text(text[:i] + body + text[j:])
print(("applied " + str(len(done)) if done else "already applied") + f"; same={len(same)} skipped={skipped}")
