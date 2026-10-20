#!/usr/bin/env python3
"""v18_precision_extract - delta logs -> precision evidence rows WITH the default in force (USER 2026-10-20).

Reuses the brain's streaming Assembler (tools/v18_brain.py, imported, never edited): same identity law (cached lines are
settlement, never evidence), same row key `rk` (so overlapping copies / archive stamps / hosts dedupe exactly), same
naked / yellow-cell / marginal arithmetic. What it ADDS per main-pass row is the DEFAULT IN FORCE for that row's switch at
the moment of the eval (the chain value the candidate was measured against), resolved per board in two passes:

  cv / ck   P  = the switch was promoted earlier on this board -> that cand (ROW_DONE promoted / inferred cum step)
            B  = before any in-board promotion, the cat_side TEMPLATE bold cand measured delta exactly 0 on this board
                 (fresh or cached naked) -> the chain held the bold value
            BI = the bold cand has NO naked line on this board before any promotion (the pilot writes no naked for the
                 chain's current value - only its yellow cells), so the chain held bold (implied, labelled)
            Z  = exactly one cand of the switch measured naked delta exactly 0 (the chain value; the start set overrode bold)
            A  = several zero-delta cands, bold not among them (inert alternatives; cv = sorted '|'-joined set)
            NB = the bold cand was measured with a NON-zero delta and no cand measured 0: chain held a non-template value
            U  = nothing on this board pins the chain value (cv = null)
  The TEMPLATE bold is today's (md5 in the report); a bold of an older template is not reconstructable from the logs and is
  never guessed - B / Z / NB come from the board's own zero-delta measurements, not from the template date.

Output: one gz TSV `<ts>\\t<json>` per run (events board / row / id / abl / remedy / phase as the brain, rows extended with
cv, ck, seq) + a counters JSON. Read-only on its inputs; nothing else is written.

    python3 tools/v18_precision_extract.py --from 'GLOB' [--from GLOB2] --host s2 --out-dir DIR [--ctx-map ctxmap_s2.json]
"""
import argparse
import collections
import glob
import gzip
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import v18_brain as B  # noqa: E402

EPS = 1e-9


def load_bolds(path):
    """{cat_side: {switch: bold cand}} from a json written on the Mac (template_index defaults), or {}."""
    if path and Path(path).exists():
        return json.loads(Path(path).read_text())
    return {}


class PrecisionAssembler(B.Assembler):
    """Brain Assembler + per-board buffering so every row carries the default in force for its switch."""

    def __init__(self, sym_side, src, emit, counters, ctx_lookup=None, bolds=None):
        self._out = emit
        self._pending = []
        self._zero = collections.defaultdict(list)
        self._evald = collections.defaultdict(list)
        self._closing = None
        self._bold = (bolds or {}).get(B.cat_side_of(sym_side) or "", {})
        super().__init__(sym_side, src, self._collect, counters, ctx_lookup=ctx_lookup)

    def _collect(self, ev):
        if ev.get("e") in ("row", "id") and self._closing is not None:
            b = self._closing
            ev["seq"] = b.get("seq", 0)
            ev["sw"] = b["key"][2]
            ev["cand"] = b["key"][3]
            self._pending.append(ev)
            return
        if ev.get("e") == "board":
            self._out(ev)
            return
        self._pending.append(ev)

    def flush(self):
        promos = collections.defaultdict(list)
        for ev in self._pending:
            if ev.get("e") == "row" and ev.get("p"):
                promos[ev["sw"]].append((ev["seq"], ev["cand"]))
        for ev in self._pending:
            if ev.get("e") not in ("row", "id"):
                self._out(ev)
                continue
            sw, q = ev.pop("sw"), ev["seq"]
            ev.pop("cand", None)
            pl = promos.get(sw, [])
            prev = [c for s, c in pl if s < q]
            if prev:
                cv, ck = prev[-1], "P"
            else:
                first = pl[0][0] if pl else float("inf")
                zs = sorted({c for s, c in self._zero.get(sw, []) if s < first})
                bold = self._bold.get(sw)
                bold_naked = bold is not None and any(c == bold and s < first for s, c in self._evald.get(sw, []))
                if bold is not None and bold in zs:
                    cv, ck = bold, "B"
                elif bold is not None and not bold_naked:
                    cv, ck = bold, "BI"
                elif len(zs) == 1:
                    cv, ck = zs[0], "Z"
                elif zs:
                    cv, ck = "|".join(zs), "A"
                elif bold_naked:
                    cv, ck = None, "NB"
                else:
                    cv, ck = None, "U"
            ev["cv"], ev["ck"] = cv, ck
            self.c["chain_" + ck] += 1
            self._out(ev)
        self._pending = []
        self._zero = collections.defaultdict(list)
        self._evald = collections.defaultdict(list)

    def _new_board(self, ts, ctx, line):
        self.close_row(None)
        self.flush()
        super()._new_board(ts, ctx, line)

    def close_row(self, promoted, how=None, next_cb=None):
        self._closing = self.buf
        try:
            super().close_row(promoted, how, next_cb)
        finally:
            self._closing = None

    def feed(self, r, line):
        lab = str(r.get("label") or "")
        if lab.startswith("RED_RETRY:"):
            lab = lab[len("RED_RETRY:"):]
        sw, cand = r.get("switch"), r.get("cand")
        if lab == "naked" and r.get("sheet") in B.SHEET_SET and sw and sw not in B.DENY:
            d = B._f(r.get("delta"))
            c = cand if cand is not None else ""
            self._evald[sw].append((line, c))
            if d is not None and abs(d) <= EPS:
                self._zero[sw].append((line, c))
        super().feed(r, line)
        if self.buf is not None and "seq" not in self.buf:
            self.buf["seq"] = line

    def finish(self):
        self.close_row(None)
        self.flush()


def run(a):
    files = []
    for g in a.source_from:
        files.extend(glob.glob(os.path.expanduser(g)))
    files = sorted(set(files))
    out_dir = Path(os.path.expanduser(a.out_dir))
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime()) + f"_{os.getpid()}"
    sp = out_dir / f"prec_{a.host}_{stamp}.tsv.gz"
    spill = B.Spill(sp)
    counters = collections.Counter()
    ctx = B.CtxMap(a.ctx_map)
    bolds = load_bolds(a.bolds)
    t0 = time.time()
    per_file = {}
    for i, f in enumerate(files, 1):
        sym = B.sym_side_of_path(f)
        if B.cat_side_of(sym) is None:
            counters["files_refused_cat_side"] += 1
            continue
        try:
            size = os.path.getsize(f)
        except OSError:
            continue
        counters["files"] += 1
        counters["bytes"] += size
        n0 = counters["lines"]
        line_no = [0]

        def emit(ev, _src=f):
            ev["src"] = a.host
            spill(ev)

        asm = PrecisionAssembler(sym, os.path.abspath(f), emit, counters, ctx_lookup=ctx, bolds=bolds)
        with open(f, "rb") as fh:
            for raw in fh:
                if not raw.endswith(b"\n"):
                    counters["partial_last_line"] += 1
                    break
                line_no[0] += 1
                counters["lines"] += 1
                try:
                    r = json.loads(raw)
                except Exception:
                    counters["bad_json"] += 1
                    continue
                if not isinstance(r, dict):
                    continue
                s2 = r.get("sym_side")
                if s2 and s2 != asm.sym and B.cat_side_of(s2):
                    counters["sym_side_switch_in_file"] += 1
                    asm.finish()
                    asm = PrecisionAssembler(s2, os.path.abspath(f), emit, counters, ctx_lookup=ctx, bolds=bolds)
                asm.feed(r, line_no[0])
        asm.finish()
        per_file[os.path.abspath(f)] = {"bytes": size, "lines": counters["lines"] - n0}
        if i % 25 == 0:
            print(f"[prec] {i}/{len(files)} files {counters['lines']:,} lines {time.time() - t0:.0f}s", flush=True)
    spill.close()
    rep = {"host": a.host, "from": a.source_from, "spill": str(sp), "secs": round(time.time() - t0, 1), "counters": dict(counters),
           "events": dict(spill.n), "files": per_file, "brain_module": B.__file__, "bolds_cat_sides": sorted(bolds)}
    (out_dir / f"prec_{a.host}_{stamp}.json").write_text(json.dumps(rep, indent=1, sort_keys=True))
    print(json.dumps({k: v for k, v in rep.items() if k != "files"}, indent=1, sort_keys=True))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--from", dest="source_from", action="append", required=True)
    ap.add_argument("--host", required=True)
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--ctx-map")
    ap.add_argument("--bolds", help="json {cat_side: {switch: bold cand}} (tools/v18_precision_build.py bolds)")
    return run(ap.parse_args(argv))


if __name__ == "__main__":
    sys.exit(main())
