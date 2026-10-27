"""V20 cell selector — ONE evidence-based decision per (row, cell), the must-set truth rules and the budget planner.

Spec: docs/v15_pending/22_V19_NETWORK_UPGRADE_SPEC_20261022.md §3 (decision), §4 (must-set), §5 (dead ends), §7 (budget).
Pure functions only; the pilot hooks live in v15_pilot.py (applied under unlock) and call these. Every function fails OPEN:
a missing or foreign binding map, profile or evidence file never removes a cell — it only leaves the pilot's existing policy
in force. Laws kept here: own cat_side only (CAT_SIDE_MISMATCH), floor >= 1/1000 and never zero, NEVER cells are settled as
identities (never a fake 0), ESSENTIAL rows and evidence-backed cells are never budget-skipped, nothing is deleted.
"""
from __future__ import annotations
import hashlib
import json
import os
import time
from pathlib import Path

CONTRACT = "binding_map_v1"
HARD_MIN_FLOOR = 1.0 / 1000.0
STRUCTURAL_REFUSALS = frozenset({"DEAD_VEC_PATH", "LIVE_DEAD_KEY", "NOT_A_TEMPLATE_KEY", "GLOBAL_ONLY", "ABLATION_NEVER_ON", "TYPE_LAW", "MAXAUG", "UNWIRED", "SELF_PAIR"})
VERDICT_EVAL = ("BINDS", "CONDITIONAL", "INDIRECT", "UNKNOWN")
# reason codes written on every settled cell (SETTLE line `reason_code`)
RC_STRUCTURAL_IDENTITY = "STRUCTURAL_IDENTITY"
RC_MASTER_OFF = "MASTER_OFF"
RC_INDIRECT_TAB_LEVEL = "INDIRECT_TAB_LEVEL"
RC_BUDGET_SKIPPED = "BUDGET_SKIPPED"
RC_DEAD_END_ROW = "DEAD_END_ROW"
RC_MODEL_SAMPLED_OUT = "MODEL_SAMPLED_OUT"
RC_PREDICTED_NOOP = "PREDICTED_NOOP"
RC_KEEP_EVIDENCE = "KEEP_EVIDENCE"
RC_KEEP_MUST = "KEEP_MUST"
RC_KEEP_ESSENTIAL = "KEEP_ESSENTIAL"
RC_KEEP_FLOOR = "KEEP_FLOOR"
RC_KEEP_MODEL = "KEEP_MODEL"
RC_EXHAUSTED = "EXHAUSTED"
RC_KEEP_RECHECK = "KEEP_RECHECK"
EXHAUSTED_CONTRACT = "exhausted_cells_v1"
_EXHAUSTED_CACHE: dict = {}


class CatSideMismatch(ValueError):
    pass


def _canon(v) -> str:
    s = str(v).strip()
    if s.lower() in ("true", "false"):
        return s.lower()
    try:
        f = float(s)
        return str(int(f)) if f == int(f) else repr(f)
    except Exception:
        return s.lower()


def learn_path(name: str, root=None, sub: str = "learn") -> Path:
    """Same resolution as the V18 planner's learn_file: data/<sub>/<name> (production's nightly files win) -> tools/v18_data/<sub>/<name>
    -> the flat gate-deploy copy tools/v18_data_<sub>_<name> (the gate deploys only into existing parents). Returns the first that exists,
    else the first path (absent)."""
    r = Path(root or ".")
    cands = [r / "data" / sub / name, r / "tools" / "v18_data" / sub / name, r / "tools" / f"v18_data_{sub}_{name}"]
    for p in cands:
        if p.exists():
            return p
    return cands[0]


def load_binding_map(cat_side: str, root=None, path=None):
    """-> dict or None (fail-open). Refuses a file whose meta.cat_side differs (law: four cat_sides apart)."""
    p = Path(path) if path else learn_path(f"binding_map_{cat_side}.json", root)
    if not p.exists():
        return None
    try:
        bm = json.loads(p.read_text())
    except Exception:
        return None
    meta = bm.get("meta") or {}
    if str(meta.get("cat_side")) != str(cat_side):
        raise CatSideMismatch(f"CAT_SIDE_MISMATCH: {p} is {meta.get('cat_side')}, board is {cat_side}")
    if meta.get("contract") != CONTRACT:
        return None
    return bm


def exhausted_on(env=None) -> bool:
    return str((env or os.environ).get("V20_EXHAUSTED", "0")).strip() == "1"


def load_exhausted(cat_side: str, root=None, path=None):
    """-> (rows, p_recheck): rows = {'TAB!SW=c': {hdr: {n, s, pos, ub, pos_syms}}} from exhausted_cells_<CS>.json
    (tools/v20_exhausted_cells.py), cached per (cat_side, path). Fail-open: absent / unreadable / foreign contract -> ({}, 1.0)."""
    p = Path(path) if path else learn_path(f"exhausted_cells_{cat_side}.json", root or Path(__file__).resolve().parent.parent)
    ck = (str(cat_side), str(p))
    if ck in _EXHAUSTED_CACHE:
        return _EXHAUSTED_CACHE[ck]
    out = ({}, 1.0)
    try:
        d = json.loads(p.read_text())
        meta = d.get("meta") or {}
        if str(meta.get("cat_side")) != str(cat_side):
            raise CatSideMismatch(f"CAT_SIDE_MISMATCH: {p} is {meta.get('cat_side')}, board is {cat_side}")
        if meta.get("contract") == EXHAUSTED_CONTRACT:
            out = (d.get("rows") or {}, float((meta.get("rule") or {}).get("p_recheck", 0.04)))
    except CatSideMismatch:
        raise
    except Exception:
        pass
    _EXHAUSTED_CACHE[ck] = out
    return out


class BoardEvidence:
    """THIS board's own filter evidence, read incrementally from its delta log (the ground truth, BIBLE §20) — the pilot is not
    touched. Per filter NAME (all options, rows and tabs): n0 = fresh cell evals whose row naked was <= EPS, rescued = one of them
    was > EPS. Measured 2026-10-27 over 2.88M fresh cells: a filter's value is board-specific and bursty (one board rescued 510
    rows with a filter dead everywhere else), so a cat_side-wide count alone loses deltas; requiring the board's own silence too
    kept the replayed rescue-delta loss <= 0.12%. Unreadable log -> n0 = 0 for every filter = nothing throttled (fail-open)."""

    EPS = 1e-9

    def __init__(self, symside: str, log_dir=None):
        self.symside = str(symside)
        base = Path(os.environ["V15_PROGRESS_DIR"]) if os.environ.get("V15_PROGRESS_DIR") else Path(__file__).resolve().parent.parent / "data" / "reports" / "lifecycle_pilot"
        self.log_dir = Path(log_dir) if log_dir else base / "v15_delta_log"
        self.pos: dict = {}
        self.n0: dict = {}
        self.rescued: set = set()
        self.naked: dict = {}
        self.pending: dict = {}

    def _files(self):
        try:
            return sorted(p for p in self.log_dir.glob(f"{self.symside}_*.jsonl") if "_" not in p.name[len(self.symside) + 1:-6])
        except Exception:
            return []

    def _cell(self, fname: str, nd, d) -> None:
        if nd is None or nd > self.EPS:
            return
        self.n0[fname] = self.n0.get(fname, 0) + 1
        if d is not None and d > self.EPS:
            self.rescued.add(fname)

    def refresh(self) -> None:
        for p in self._files():
            try:
                size = p.stat().st_size
                off = self.pos.get(p, 0)
                if size <= off:
                    continue
                with open(p, "rb") as fh:
                    fh.seek(off)
                    chunk = fh.read(size - off)
            except Exception:
                continue
            end = chunk.rfind(b"\n")
            if end < 0:
                continue
            self.pos[p] = off + end + 1
            for raw in chunk[:end].split(b"\n"):
                if b'"delta"' not in raw or b'"cached": true' in raw:
                    continue
                try:
                    r = json.loads(raw)
                except Exception:
                    continue
                lab = str(r.get("label") or "")
                if r.get("cached") or not r.get("sheet") or ":" in lab or "+" in lab:
                    continue
                key = (r.get("sheet"), r.get("row"), r.get("switch"), r.get("cand"))
                try:
                    d = None if r.get("delta") is None else float(r.get("delta"))
                except Exception:
                    d = None
                if lab == "naked":
                    self.naked[key] = d
                    for fname, dd in self.pending.pop(key, []):
                        self._cell(fname, d, dd)
                    continue
                if "=" not in lab:
                    continue
                fname = lab.split("=", 1)[0]
                if key in self.naked:
                    self._cell(fname, self.naked[key], d)
                else:
                    self.pending.setdefault(key, []).append((fname, d))
            if len(self.pending) > 20000:
                self.pending.clear()

    def silent(self, fname: str, k: int) -> bool:
        return fname not in self.rescued and self.n0.get(fname, 0) >= k


_BOARD_EVIDENCE: dict = {}


def board_evidence(symside: str) -> BoardEvidence:
    be = _BOARD_EVIDENCE.get(str(symside))
    if be is None:
        be = _BOARD_EVIDENCE[str(symside)] = BoardEvidence(symside)
        be.t_read = 0.0
    if time.monotonic() - be.t_read >= 2.0:  # rows are discovered in bursts (a whole tab); one read per burst is enough
        be.refresh()
        be.t_read = time.monotonic()
    return be


def exhausted_for_row(cat_side: str, tab: str, switch: str, cand, symside: str, root=None, board=None, k=None) -> dict:
    """-> {hdr: p_recheck} for this row's cells that are BOTH cat_side-exhausted (exhausted_cells_<CS>.json) AND whose filter has
    been silent on THIS board for >= k (V20_EXH_BOARD_K, 20) non-positive-naked evals with no rescue. A cell positive on this
    sym_side in the corpus is never throttled; a filter that rescues once on this board is never throttled again on it."""
    rows, p_re = load_exhausted(cat_side, root)
    cells = rows.get(row_key(tab, switch, cand)) or {}
    if not cells:
        return {}
    k = int(k if k is not None else os.environ.get("V20_EXH_BOARD_K", "20") or 20)
    be = board if board is not None else board_evidence(symside)
    return {h: p_re for h, c in cells.items() if str(symside) not in (c.get("pos_syms") or ()) and be.silent(str(h).split("=", 1)[0].strip(), k)}


def row_key(tab: str, switch: str, cand) -> str:
    return f"{str(tab).strip()}!{str(switch).strip()}={str(cand).strip()}"


def _condition_holds(keys: list, mode: str, running_set: dict) -> bool:
    if not keys:
        return True
    hits = []
    for k in keys:
        k = str(k)
        if ">" in k and "=" not in k:
            name, thr = k.split(">", 1)
            try:
                hits.append(float(running_set.get(name.strip(), 0) or 0) > float(thr))
            except Exception:
                hits.append(False)
            continue
        if "=" not in k:
            hits.append(False)
            continue
        name, val = k.split("=", 1)
        hits.append(_canon(running_set.get(name.strip(), None)) == _canon(val))
    return any(hits) if str(mode or "all") == "any" else all(hits)


def cell_verdict(bm, tab: str, switch: str, cand, header: str, running_set: dict) -> tuple:
    """-> (verdict, reason, anchor). CONDITIONAL is resolved against the RUNNING SET: master on -> BINDS, else MASTER_OFF.
    Unknown row/header -> ('UNKNOWN', 'NOT_IN_MAP', '') = evaluate at the model's p (never NEVER by ignorance)."""
    if not bm:
        return ("UNKNOWN", "NO_MAP", "")
    row = (bm.get("rows") or {}).get(row_key(tab, switch, cand))
    if not row:
        return ("UNKNOWN", "ROW_NOT_IN_MAP", "")
    if row.get("dead_row"):
        return ("NEVER", "DEAD_ROW", "binding_map rows.dead_row")
    cell = (row.get("cells") or {}).get(str(header).strip())
    if not cell:
        fname = str(header).split("=", 1)[0].strip()
        if fname == str(switch).strip():
            return ("NEVER", "SELF_PAIR", "filter name == row switch name")
        return ("UNKNOWN", "CELL_NOT_IN_MAP", "")
    v = str(cell.get("verdict") or "UNKNOWN")
    reason = str(cell.get("reason") or "")
    if v == "CONDITIONAL":
        if _condition_holds(cell.get("condition_keys") or [], cell.get("condition_mode") or "all", running_set or {}):
            return ("BINDS", f"ARMED:{reason}", str(cell.get("anchor") or ""))
        return ("MASTER_OFF", reason or RC_MASTER_OFF, ",".join(cell.get("condition_keys") or []))
    return (v, reason, str(cell.get("anchor") or ""))


def floor_draw(cat_side, symside, tab, key, rid) -> float:
    h = hashlib.sha1(f"{cat_side}|{symside}|{tab}|{key}|{rid}".encode()).hexdigest()
    return int(h[:12], 16) / float(16**12)


def decide_cells(hdrs: list, bm, tab: str, switch: str, cand, running_set: dict, *, cat_side: str, symside: str, rid: str, essential: bool = False, must_cells=(), evidence_cells=(), model_p=None, floor_p: float = 0.005, untried_p: float = 0.05, untried_cells=(), indirect_seen=None, dead_end_row: bool = False, predicted_noop_cells=(), exhausted_cells=None) -> dict:
    """ONE decision per cell. Returns {'keep': [hdr...], 'settled': {hdr: {'reason_code', 'verdict', 'why', 'p'}}, 'counts': {...}}.
    Order: ESSENTIAL rows keep everything evaluable; MUST and evidence-backed cells are always kept (CDE_SHORT guard); NEVER ->
    STRUCTURAL_IDENTITY (no engine call, floor draw may still rescue 1/1000 to re-verify wiring); MASTER_OFF -> settled unless the
    floor rescues; INDIRECT -> once per (tab, filter) via `indirect_seen` (mutable set); DEAD_END rows -> floor only; else the model
    p (dict hdr -> P(pos) or None) with max(p, floor); untried pairs use untried_p. p is never below HARD_MIN_FLOOR."""
    floor_p = max(float(floor_p), HARD_MIN_FLOOR)
    untried_p = max(float(untried_p), floor_p)
    model_p = model_p or {}
    must_cells = set(must_cells or ())
    evidence_cells = set(evidence_cells or ())
    untried_cells = set(untried_cells or ())
    predicted_noop_cells = set(predicted_noop_cells or ())  # V20_BINDS_P: cells the B head says will not change the ledger (p_binds < max)
    if exhausted_cells is None:  # V20_EXHAUSTED: cat_side-exhausted AND silent on this board -> re-checked at p_recheck
        exhausted_cells = exhausted_for_row(cat_side, tab, switch, cand, symside) if exhausted_on() else {}
    keep, settled, counts = [], {}, {"never": 0, "master_off": 0, "indirect": 0, "model_out": 0, "floor_rescued": 0, "kept": 0, "dead_end": 0, "predicted_noop": 0, "exhausted": 0, "exhausted_rechecked": 0}
    rk = row_key(tab, switch, cand)
    for h in list(hdrs or []):
        h = str(h).strip()
        verdict, why, anchor = cell_verdict(bm, tab, switch, cand, h, running_set)
        draw = floor_draw(cat_side, symside, tab, f"{rk}|{h}", rid)
        if h in must_cells:
            keep.append(h)
            settled[h] = {"reason_code": RC_KEEP_MUST, "verdict": verdict, "why": why, "p": 1.0}
            continue
        if essential:
            if verdict == "NEVER" and draw >= floor_p:
                settled[h] = {"reason_code": RC_STRUCTURAL_IDENTITY, "verdict": verdict, "why": why, "anchor": anchor, "p": floor_p}
                counts["never"] += 1
                continue
            keep.append(h)
            settled[h] = {"reason_code": RC_KEEP_ESSENTIAL, "verdict": verdict, "why": why, "p": 1.0}
            continue
        if h in evidence_cells and verdict != "NEVER":
            keep.append(h)
            settled[h] = {"reason_code": RC_KEEP_EVIDENCE, "verdict": verdict, "why": why, "p": 1.0}
            continue
        if verdict == "NEVER":
            if draw < floor_p:
                keep.append(h)
                settled[h] = {"reason_code": RC_KEEP_FLOOR, "verdict": verdict, "why": why, "p": floor_p}
                counts["floor_rescued"] += 1
            else:
                settled[h] = {"reason_code": RC_STRUCTURAL_IDENTITY, "verdict": verdict, "why": why, "anchor": anchor, "p": floor_p}
                counts["never"] += 1
            continue
        if verdict == "MASTER_OFF":
            if draw < floor_p:
                keep.append(h)
                settled[h] = {"reason_code": RC_KEEP_FLOOR, "verdict": verdict, "why": why, "p": floor_p}
                counts["floor_rescued"] += 1
            else:
                settled[h] = {"reason_code": RC_MASTER_OFF, "verdict": verdict, "why": why, "condition": anchor, "p": floor_p}
                counts["master_off"] += 1
            continue
        if verdict == "INDIRECT":
            fname = h.split("=", 1)[0]
            tag = (tab, fname)
            if indirect_seen is not None and tag in indirect_seen and draw >= floor_p:
                settled[h] = {"reason_code": RC_INDIRECT_TAB_LEVEL, "verdict": verdict, "why": why, "p": floor_p}
                counts["indirect"] += 1
                continue
            if indirect_seen is not None:
                indirect_seen.add(tag)
            keep.append(h)
            settled[h] = {"reason_code": RC_KEEP_MODEL, "verdict": verdict, "why": "INDIRECT_FIRST_OF_TAB", "p": 1.0}
            counts["kept"] += 1
            continue
        if h in exhausted_cells:  # measured, not predicted: re-checked at p_recheck (never below the floor), a positive re-opens it nightly
            p_re = max(min(float(exhausted_cells[h]), 1.0), floor_p)
            if draw < p_re:
                keep.append(h)
                settled[h] = {"reason_code": RC_KEEP_RECHECK, "verdict": verdict, "why": "EXHAUSTED_RECHECK", "p": p_re}
                counts["exhausted_rechecked"] += 1
            else:
                settled[h] = {"reason_code": RC_EXHAUSTED, "verdict": verdict, "why": why, "p": p_re}
                counts["exhausted"] += 1
            continue
        if h in predicted_noop_cells:  # learned, not structural: settled at the floor, reason-coded, audited nightly against measured noop
            if draw < floor_p:
                keep.append(h)
                settled[h] = {"reason_code": RC_KEEP_FLOOR, "verdict": verdict, "why": "PREDICTED_NOOP", "p": floor_p}
                counts["floor_rescued"] += 1
            else:
                settled[h] = {"reason_code": RC_PREDICTED_NOOP, "verdict": verdict, "why": why, "p": floor_p}
                counts["predicted_noop"] += 1
            continue
        if dead_end_row:
            if draw < floor_p:
                keep.append(h)
                settled[h] = {"reason_code": RC_KEEP_FLOOR, "verdict": verdict, "why": "DEAD_END_ROW", "p": floor_p}
                counts["floor_rescued"] += 1
            else:
                settled[h] = {"reason_code": RC_DEAD_END_ROW, "verdict": verdict, "why": why, "p": floor_p}
                counts["dead_end"] += 1
            continue
        p = model_p.get(h)
        if p is None:
            p = untried_p if h in untried_cells else 1.0
        p = max(min(float(p), 1.0), floor_p)
        if draw < p:
            keep.append(h)
            settled[h] = {"reason_code": RC_KEEP_MODEL, "verdict": verdict, "why": why, "p": p}
            counts["kept"] += 1
        else:
            settled[h] = {"reason_code": RC_MODEL_SAMPLED_OUT, "verdict": verdict, "why": why, "p": p}
            counts["model_out"] += 1
    return {"keep": keep, "settled": settled, "counts": counts}


def settle_record(symside: str, tab: str, switch: str, cand, header: str, st: dict, set_sha: str, phase: str = "MAIN", ts: str = "") -> dict:
    """The SETTLE delta-log line for a cell the selector did not send to the engine (never a fake 0: no delta field)."""
    return {"ts": ts, "sym_side": symside, "sheet": tab, "switch": switch, "cand": cand, "filter": header, "label": "SETTLE", "phase": phase, "reason_code": st.get("reason_code"), "binding_verdict": st.get("verdict"), "binding_why": st.get("why"), "binding_anchor": st.get("anchor") or st.get("condition") or "", "brain_p": st.get("p"), "set_sha256": set_sha, "cached": True}


# ---------------------------------------------------------------- §5 dead ends, literal
def dead_end_state(prof_row: dict, cat_side: str, s_min_crypto: int = 10, s_min_stocks: int = 15) -> bool:
    """Brief §6.1 literally: SETTLED_OFF/DAMAGING AND 90% interval UB < 0 AND n_syms >= s_min AND no positive sym_side AND forward/live
    not positive. Candidates only; the floor keeps the row alive and a sibling positive re-opens it (the nightly rebuild clears it)."""
    if not prof_row:
        return False
    if str(prof_row.get("state") or "") not in ("SETTLED_OFF", "DAMAGING"):
        return False
    iv = prof_row.get("interval_mean_delta") or prof_row.get("interval") or None
    if not iv or len(iv) < 2 or iv[1] is None or float(iv[1]) >= 0:
        return False
    s_min = s_min_crypto if str(cat_side).startswith("CRYPTO") else s_min_stocks
    if int(prof_row.get("n_syms") or 0) < s_min:
        return False
    if int(prof_row.get("pos_syms") or 0) > 0:
        return False
    if int(((prof_row.get("forward") or {}).get("pos")) or 0) > 0 or int(((prof_row.get("live") or {}).get("pos")) or 0) > 0:
        return False
    return True


# ---------------------------------------------------------------- §4 must-set truth
def must_refuse(ledger: dict, item: str, reason: str, start_sha: str, ts: str) -> dict:
    """A refused item gets a ledger entry so it is not re-offered on the same chain (today it is re-offered forever)."""
    ledger[item] = {"start_sha": start_sha, "refused": str(reason).split(":", 1)[0].strip(), "refused_text": str(reason), "ts": ts}
    return ledger


def must_skip_refused(items: list, ledger: dict, start_sha: str) -> tuple:
    """-> (kept, skipped_count): drop items refused on this very start_sha (a chain move re-offers them)."""
    kept, skipped = [], 0
    for it in items:
        e = (ledger or {}).get(it) or {}
        if e.get("refused") and e.get("start_sha") == start_sha:
            skipped += 1
            continue
        kept.append(it)
    return kept, skipped


def refusal_is_structural(reason: str) -> bool:
    return str(reason).split(":", 1)[0].strip() in STRUCTURAL_REFUSALS


def _lb(ci) -> float:
    try:
        return float(ci[0])
    except Exception:
        return float("-inf")


def rank_must(items: list, positives_all: dict, symside: str, cluster_of: dict | None = None, linknet_p: dict | None = None) -> list:
    """Spec §4.4: interval LOWER BOUND first (pairs: lift_ci[0]; rows: ci[0] if present else best_delta as a weak proxy flagged
    below), same correlation cluster first, linknet p only as tie-breaker. Stable; nothing dropped. Items are 'SW=c' / 'SW=c@F=o'."""
    rows = {r.get("key"): r for r in (positives_all or {}).get("rows") or [] if isinstance(r, dict)}
    pairs = {f"{p.get('key')}@{p.get('filter')}": p for p in (positives_all or {}).get("pairs") or [] if isinstance(p, dict)}
    syms = list((positives_all or {}).get("sym_sides") or [])
    my_cluster = (cluster_of or {}).get(symside)

    def _score(it):
        rec = pairs.get(it) if "@" in it else rows.get(it)
        if not rec:
            return (1, 0.0, float("-inf"), -float((linknet_p or {}).get(it) or 0.0))
        lb = _lb(rec.get("lift_ci") if "@" in it else rec.get("ci"))
        if lb == float("-inf"):
            lb = -abs(float(rec.get("best_delta") or 0.0)) * 1e-3  # weak proxy: measured positive, no interval
        same_cluster = 0.0
        if my_cluster is not None and cluster_of:
            pos_syms = [s for s in (rec.get("pos_syms") or []) if isinstance(s, str)] or [syms[i] for i in (rec.get("pos_syms_idx") or []) if isinstance(i, int) and i < len(syms)]
            same_cluster = -1.0 if any(cluster_of.get(s) == my_cluster for s in pos_syms) else 0.0
        return (0, same_cluster, -lb, -float((linknet_p or {}).get(it) or 0.0))

    return sorted(list(items), key=_score)


def losing_switch_alternatives(live_set: dict, losing: list, template_cands: dict, symside: str) -> list:
    """Spec §4.5: for every (sym_side, switch) with a negative default-relative interval on THIS sym_side, every OTHER template
    candidate of that switch becomes a must item 'SW=c'. `losing` = [{sym_side, switch, value, ci}], `template_cands` = {SW: [cands]}."""
    out = []
    for rec in losing or []:
        if str(rec.get("sym_side")) != str(symside):
            continue
        sw = str(rec.get("switch"))
        cur = _canon(live_set.get(sw, rec.get("value")))
        for c in template_cands.get(sw) or []:
            if _canon(c) != cur:
                out.append(f"{sw}={c}")
    return list(dict.fromkeys(out))


def load_positives_all_raw(cat_side: str, root=None):
    """The raw positives_all dict (rows/pairs/sym_sides) for rank_must; own cat_side only; None when absent."""
    import gzip
    cs = str(cat_side or "").upper()
    for name in (f"positives_all_{cs}.json.gz", f"positives_all_{cs}.json"):
        p = learn_path(name, root)
        if not p.exists():
            continue
        try:
            with (gzip.open(p, "rt") if name.endswith(".gz") else open(p)) as fh:
                d = json.load(fh)
        except Exception:
            return None
        if str(((d.get("_meta") or d.get("meta") or {}).get("cat_side")) or cs).upper() != cs:
            raise CatSideMismatch(f"CAT_SIDE_MISMATCH: {p}")
        return d
    return None


def load_losing(cat_side: str, root=None) -> list:
    """data/learn/losing_live_switches_<CS>.json = [{sym_side, switch, value, n, mean, ci}] (evidence base §5); [] when absent."""
    p = learn_path(f"losing_live_switches_{str(cat_side).upper()}.json", root)
    try:
        d = json.loads(p.read_text())
    except Exception:
        return []
    if isinstance(d, dict):
        if str(d.get("cat_side") or cat_side).upper() != str(cat_side).upper():
            raise CatSideMismatch(f"CAT_SIDE_MISMATCH: {p}")
        d = d.get("items") or []
    return [x for x in d if isinstance(x, dict)]


def load_clusters(cat_side: str, root=None) -> dict:
    """data/learn/clusters_<CS>.json (brief §6.2 b: 60-day 15m return-correlation clusters) -> {sym_side: cluster_id}. Prefers the
    residual clusters (`cluster_of_residual`, market factor removed) when present — the plain crypto clusters are one market-factor
    blob (131 of 182 symbols in one cluster, 2026-10-22). Own cat_side only; {} when absent (fail-open: no cluster preference)."""
    p = learn_path(f"clusters_{str(cat_side).upper()}.json", root)
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    if str(((d.get("meta") or {}).get("cat_side")) or cat_side).upper() != str(cat_side).upper():
        raise CatSideMismatch(f"CAT_SIDE_MISMATCH: {p}")
    co = d.get("cluster_of_residual") or d.get("cluster_of") or {}
    return {str(k): str(v) for k, v in co.items() if v is not None}


def template_cands_from_map(bm) -> dict:
    """{SWITCH: [cand, ...]} from the binding map's row keys 'TAB!SW=c' (the board's own template rows)."""
    out: dict = {}
    for rk in ((bm or {}).get("rows") or {}):
        try:
            sw_c = str(rk).split("!", 1)[1]
            sw, c = sw_c.split("=", 1)
        except Exception:
            continue
        lst = out.setdefault(sw.strip(), [])
        if c.strip() not in lst:
            lst.append(c.strip())
    return out


# ---------------------------------------------------------------- §7 budget (replaces the catch-up mode)
def board_budget_from_env(env=None) -> int:
    """V15_BOARD_BUDGET: 0 (or unset) = full board. Set per launch by the scheduler."""
    try:
        return max(0, int(float((env or os.environ).get("V15_BOARD_BUDGET", "0") or 0)))
    except Exception:
        return 0


def budget_plan(candidates: list, budget: int) -> dict:
    """candidates: [{id, cls, rank, cost}] with cls in ESSENTIAL < MUST < EVIDENCE < ABLATION_FLOOR < EXIT_REPAIR < NAKED < CELL < FLOOR
    (lower = claimed first), rank ascending inside a class (interval LB order), cost in evals. budget 0 = everything.
    -> {'keep': [ids], 'skipped': [{id, cls, rank, reason_code}], 'spent': n}. ESSENTIAL/MUST/EVIDENCE are NEVER skipped (law)."""
    order = {"ESSENTIAL": 0, "MUST": 1, "EVIDENCE": 2, "ABLATION_FLOOR": 3, "EXIT_REPAIR": 4, "NAKED": 5, "CELL": 6, "FLOOR": 7}
    never_skip = {"ESSENTIAL", "MUST", "EVIDENCE"}
    cands = sorted(list(candidates or []), key=lambda c: (order.get(str(c.get("cls")), 9), float(c.get("rank", 0.0)), str(c.get("id"))))
    keep, skipped, spent, protected = [], [], 0, 0
    for c in cands:
        cost = int(c.get("cost", 1) or 1)
        if str(c.get("cls")) in never_skip:  # law: outside the budget accounting, never skipped
            keep.append(c["id"])
            protected += cost
        elif budget <= 0 or spent + cost <= budget:
            keep.append(c["id"])
            spent += cost
        else:
            skipped.append({"id": c["id"], "cls": c.get("cls"), "rank": c.get("rank"), "reason_code": RC_BUDGET_SKIPPED})
    return {"keep": keep, "skipped": skipped, "spent": spent, "protected_spent": protected, "budget": budget}


def budget_forced_profile(budget: int) -> dict:
    """What a budget board turns OFF (search only, never measurement): V17 pinpoint/combo/focus/endgame/diagnose/red-retry and the
    365D-at-done step. It KEEPS: holdout/forward scoring, ablation PRE pass at its floor budget, exit repair on any family loss,
    winners, MUST (interval-ranked, budget share), ESSENTIAL rows. budget 0 -> {} (nothing forced)."""
    if budget <= 0:
        return {}
    return {"V15_V17_ROUNDS": "0", "V15_FOCUS": "0", "V15_ENDGAME_S": "0", "V15_DIAG_REPAIR_S": "0", "V15_SKIP_365D_AT_DONE": "1", "V15_RED_RETRY_S": "0", "V18_CATCHUP_EXITREP_MIN_PP": "0"}
