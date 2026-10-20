"""V18 planner helpers for v15_pilot.py (work item E4, doc 17 §4.1 / §4.3 / §3.4).

Three independent kill switches, all default OFF in code; the S2 version gate decides the production default
(data/v15_gate_flags.json, injected by v15_pilot._load_gate_flags):

  V18_IDENTITY_SETTLE=1  a row cell whose materialised override set (and baseline) was already booked in the delta log
                         is settled INERT_IDENTITY from that same cached engine result: no pool submit, no per-cell
                         delta-log line; one ROW_SETTLED line per row + progress done[key]["settled"].
  V18_IO_THROTTLE=1      xlsx saves only at tab boundaries (still time-throttled) / forced points / publish; the diagnose
                         checkpoint rides the PROGRESS_JSON_EVERY_S throttle; no full workbook re-load to count F cells.
  V18_LEDGER_WRITEBACK=1 START / PROMOTED / FINAL sets: one include_ledger=True eval each at the end of the fill, written
                         as compact rows to <progress_dir>/v15_trade_log/<SYM_SIDE>.jsonl.

Every number written here is a real engine result for the exact override set it is attached to (BIBLE §19, NO-LIES).
"""
import hashlib
import json
import os
from pathlib import Path

LEDGER_COLS = ("bar_entry", "bar_exit", "bars_held", "entry_reason", "exit_reason", "type", "pnl_pct", "pnl_dollars", "deployed", "mfe_pct", "mae_pct", "unrealised_mark")


def flag_on(name: str) -> bool:
    return os.environ.get(name, "0") == "1"


def identity_settle_on() -> bool:
    return flag_on("V18_IDENTITY_SETTLE")


def io_throttle_on() -> bool:
    return flag_on("V18_IO_THROTTLE")


def ledger_writeback_on() -> bool:
    return flag_on("V18_LEDGER_WRITEBACK")


def xlsx_save_allowed(io_on: bool, at_tab_boundary: bool) -> bool:
    """With V18_IO_THROTTLE the periodic workbook save may only run at a tab boundary (it stays time-throttled there)."""
    return (not io_on) or bool(at_tab_boundary)


def canonical_json(ov: dict) -> str:
    return json.dumps(ov, sort_keys=True, default=str, separators=(",", ":"))


def set_sha256(ov: dict) -> str:
    return hashlib.sha256(canonical_json(dict(ov or {})).encode()).hexdigest()


class IdentityBook:
    """Per-board register of (override set, baseline) pairs already booked in the delta log with a real result.

    A later consumption of the same pair inside a main-pass row is an identity: same dict + same NPZ = the same engine
    result (the pilot's _EVAL_CACHE holds it), same baseline = the same delta. It is settled with that cached result:
    INERT_IDENTITY when the set equals the chain's own set, INERT_IDENTITY→<first tag> when it equals another candidate.
    Keys are hashes of the pilot's cache key; the number always comes from the cache entry of the exact key."""

    def __init__(self):
        self.first = {}
        self.row = None
        self.totals = {"rows_settled": 0, "cells_settled": 0, "INERT_IDENTITY": 0, "INERT_IDENTITY_SHARED": 0}

    @staticmethod
    def _key(key_hash, cum_before):
        try:
            return (key_hash, float(cum_before))
        except Exception:
            return (key_hash, repr(cum_before))

    def note(self, key_hash: int, cum_before: float, tag: str) -> None:
        self.first.setdefault(self._key(key_hash, cum_before), tag)

    def lookup(self, key_hash: int, cum_before: float):
        return self.first.get(self._key(key_hash, cum_before))

    def begin_row(self, row_key: str, chain_hash_fn) -> None:
        self.row = {"key": row_key, "chain_fn": chain_hash_fn, "chain": None, "cells": {}, "INERT_IDENTITY": 0, "INERT_IDENTITY_SHARED": 0}

    def settle(self, key_hash: int, cum_before: float, label: str):
        if self.row is None:
            return None
        tag = self.lookup(key_hash, cum_before)
        if tag is None:
            return None
        if self.row["chain"] is None:
            try:
                self.row["chain"] = self.row["chain_fn"]()
            except Exception:
                self.row["chain"] = False
        if self.row["chain"] is not False and key_hash == self.row["chain"]:
            code, kind = "INERT_IDENTITY", "INERT_IDENTITY"
        else:
            code, kind = f"INERT_IDENTITY→{tag}", "INERT_IDENTITY_SHARED"
        self.row[kind] += 1
        self.row["cells"][str(label)] = code
        return code

    def end_row(self):
        row, self.row = self.row, None
        if not row or not row["cells"]:
            return None
        n = len(row["cells"])
        self.totals["rows_settled"] += 1
        self.totals["cells_settled"] += n
        self.totals["INERT_IDENTITY"] += row["INERT_IDENTITY"]
        self.totals["INERT_IDENTITY_SHARED"] += row["INERT_IDENTITY_SHARED"]
        shared = sorted({c.split("→", 1)[1] for c in row["cells"].values() if "→" in c})
        return {"settled": {"INERT_IDENTITY": row["INERT_IDENTITY"], "INERT_IDENTITY_SHARED": row["INERT_IDENTITY_SHARED"]}, "n": n, "cells": dict(row["cells"]), "shared_evals": shared}


def row_settled_record(base: dict, summary: dict) -> dict:
    return {**base, "label": "ROW_SETTLED", "settled": summary["settled"], "n_settled": summary["n"], "shared_evals": summary["shared_evals"], "cells": summary["cells"]}


def ledger_rows(res: dict) -> list:
    """Compact realised ledger (+ the window-edge mark, flagged) of one include_ledger=True eval, columns = LEDGER_COLS."""
    out = []
    for t in (res or {}).get("ledger") or []:
        if not isinstance(t, dict) or "pnl_dollars" not in t or t.get("seeded_prewindow"):
            continue
        be, bx = t.get("bar_entry"), t.get("bar_exit")
        held = t.get("bars_held")
        if held is None and be is not None and bx is not None:
            try:
                held = int(bx) - int(be)
            except Exception:
                held = None
        mfe = t.get("mfe_pct", t.get("mfe"))
        mae = t.get("mae_pct", t.get("mae"))
        out.append([be, bx, held, t.get("entry_reason"), t.get("exit_reason") or t.get("reason"), t.get("type"), t.get("pnl_pct"), t.get("pnl_dollars"), t.get("deployed"), mfe, mae, bool(t.get("unrealised"))])
    return out


def trade_log_record(kind: str, tag: str, symside: str, cat_side: str, ov_sanitized: dict, res: dict, window_days, npz_md5, npz_id, ts: str) -> dict:
    res = res or {}
    return {"ts": ts, "sym_side": symside, "cat_side": cat_side, "kind": kind, "tag": tag, "set_sha256": set_sha256(ov_sanitized), "n_keys": len(ov_sanitized or {}), "npz_md5": npz_md5, "npz_id": npz_id, "window_days": window_days, "gain_pct": res.get("gain_pct"), "trades": res.get("trades"), "tim_pct": res.get("tim_pct"), "max_dd_pct": res.get("max_dd_pct"), "wr_pct": res.get("wr_pct"), "valid": res.get("valid"), "fp": (res.get("behavior_fingerprint") or "")[:16], "cols": list(LEDGER_COLS), "rows": ledger_rows(res)}


def trade_log_path(progress_dir, symside: str) -> Path:
    return Path(progress_dir) / "v15_trade_log" / f"{symside}.jsonl"


def append_jsonl(path, rec: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a") as fh:
        fh.write(json.dumps(rec, default=str) + "\n")


_MD5_CACHE: dict = {}


def file_md5(path):
    try:
        st = os.stat(str(path))
        key = (str(path), st.st_mtime_ns, st.st_size)
        if key not in _MD5_CACHE:
            h = hashlib.md5()
            with open(str(path), "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            _MD5_CACHE[key] = h.hexdigest()
        return _MD5_CACHE[key]
    except Exception:
        return None


def plan_writeback(pending: list, written_keys: set, max_evals: int):
    """pending = [(kind, tag, sanitized set)]; returns ([(sha, set, [(kind, tag), ...])] in first-seen order, n_capped).
    One ledger eval per distinct set; a (kind, sha) already written for this board is not written again."""
    groups, order, capped = {}, [], 0
    for kind, tag, ov in pending:
        sha = set_sha256(ov)
        if (kind, sha) in written_keys:
            continue
        if sha not in groups:
            if len(order) >= max_evals:
                capped += 1
                continue
            groups[sha] = (ov, [])
            order.append(sha)
        groups[sha][1].append((kind, tag))
    return [(sha, groups[sha][0], groups[sha][1]) for sha in order], capped


# ── Principle 12 (USER 2026-10-20): every (switch=cand, filter=opt) cell and every naked row keeps a floor chance ──────────
FLOOR_ROW_POLICY_PREFIXES = ("CARRIED_DEAD", "BRAIN_SAMPLED_OUT", "SKIPPED_SAMPLING", "ZERO_FORMULA_ROW")
FLOOR_CELL_LISTS = ("inert_filters", "prior_skipped", "coarse_deferred_filters", "sampled_filters", "zero_skipped", "v17_deferred")


def floor_on() -> bool:
    return flag_on("V18_FLOOR")


def floor_p() -> float:
    try:
        return min(1.0, max(0.0, float(os.environ.get("V18_FLOOR_PER_1000", "5")) / 1000.0))
    except Exception:
        return 0.005


def floor_draw(cat_side, symside, tab, key, rid) -> float:
    """Deterministic uniform [0,1) draw: sha1(cat_side|sym_side|tab|key|round) — every cell rotates in across the fleet."""
    h = hashlib.sha1(f"{cat_side}|{symside}|{tab}|{key}|{rid}".encode()).hexdigest()
    return int(h[:13], 16) / float(16 ** 13)


def floor_row_policy_skip(reason) -> bool:
    return str(reason or "").startswith(FLOOR_ROW_POLICY_PREFIXES)


def floor_apply(info: dict, keep_fn, p: float) -> dict:
    """Pure: overrides every POLICY skip of one row with the floor draw. keep_fn("row", None) / keep_fn("cell", hdr) -> bool.

    - a row skipped by a policy (carry / brain / possym / zero-formula) is evaluated when its own draw OR any of its cells'
      draws keeps it; its cells are evaluated only where their own draw keeps them, the rest stay settled with their p;
    - inside an evaluated row every cell some policy skipped is moved back into hdrs when its draw keeps it.
    Structural skips (no candidate, unwired, GLOBAL_ONLY, ABLATION_NEVER_ON, MAXAUG, type law, not in config) never change.
    Returns the per-row stats; info gets 'v18_floor' {p, rescued_row, rescued_cells, settled_n, overrode}."""
    st = {"rows_seen": 0, "rows_policy_skipped": 0, "rows_rescued": 0, "cells_policy_skipped": 0, "cells_rescued": 0, "cells_settled": 0}
    hdrs = list(info.get("hdrs") or [])
    if info.get("kind") == "skip":
        reason = str(info.get("reason") or "")
        if not floor_row_policy_skip(reason):
            return st
        st["rows_seen"] = 1
        st["rows_policy_skipped"] = 1
        pool = list(dict.fromkeys(hdrs + [h for lst in FLOOR_CELL_LISTS for h in (info.get(lst) or [])]))
        st["cells_policy_skipped"] = len(pool)
        kept = [h for h in pool if keep_fn("cell", h)]
        row_keep = bool(keep_fn("row", None))
        if not row_keep and not kept:
            st["cells_settled"] = len(pool)
            info["v18_floor"] = {"p": p, "rescued_row": False, "rescued_cells": [], "settled_n": len(pool), "overrode": None}
            return st
        rest = [h for h in pool if h not in kept]
        for lst in FLOOR_CELL_LISTS:
            info.pop(lst, None)
        info["kind"], info["reason"] = "eval", ""
        info.pop("g", None)
        info["hdrs"] = kept
        if rest:
            info["sampled_filters" if reason.startswith("SKIPPED_SAMPLING") else "coarse_deferred_filters"] = rest
        st["rows_rescued"] = 1
        st["cells_rescued"] = len(kept)
        st["cells_settled"] = len(rest)
        info["v18_floor"] = {"p": p, "rescued_row": True, "row_draw": row_keep, "rescued_cells": list(kept), "settled_n": len(rest), "overrode": reason[:160]}
        return st
    if info.get("kind") != "eval":
        return st
    st["rows_seen"] = 1
    rescued, settled = [], 0
    for lst in FLOOR_CELL_LISTS:
        cur = list(info.get(lst) or [])
        if not cur:
            continue
        stay = []
        for h in cur:
            if h in hdrs or h in rescued:
                continue
            st["cells_policy_skipped"] += 1
            if keep_fn("cell", h):
                rescued.append(h)
            else:
                stay.append(h)
        settled += len(stay)
        if lst in info:
            info[lst] = stay
    if rescued:
        info["hdrs"] = hdrs + rescued
        if isinstance(info.get("brain_skipped"), list):
            info["brain_skipped"] = [h for h in info["brain_skipped"] if h not in rescued]
        if isinstance(info.get("sampled_skip_p"), dict):
            for h in rescued:
                info["sampled_skip_p"].pop(h, None)
    st["cells_rescued"] = len(rescued)
    st["cells_settled"] = settled
    if rescued or settled:
        info["v18_floor"] = {"p": p, "rescued_row": False, "rescued_cells": list(rescued), "settled_n": settled, "overrode": None}
    return st


def floor_add(total: dict, st: dict) -> dict:
    for k, v in (st or {}).items():
        total[k] = int(total.get(k, 0)) + int(v)
    return total


# ── Principle 13 (USER 2026-10-20): four cat_sides strictly apart; digests are the ONLY learned input ────────────────────
CAT_SIDES = ("CRYPTO_LONG", "CRYPTO_SHORT", "STOCKS_LONG", "STOCKS_SHORT")


def digest_dir(root=None) -> Path:
    if os.environ.get("V18_DIGEST_DIR"):
        return Path(os.environ["V18_DIGEST_DIR"])
    return Path(root or Path(__file__).resolve().parents[1]) / "data" / "learn"


def load_digest(cat_side: str, root=None):
    """-> (digest | None, why). Fail-open (absent / unreadable -> None). A digest whose meta.cat_side is not this board's
    cat_side is REFUSED (principle 13: nothing from another cat_side may steer this board)."""
    cs = str(cat_side or "").upper()
    if cs not in CAT_SIDES:
        return None, f"unknown cat_side {cat_side!r}"
    p = digest_dir(root) / f"digest_{cs}.json"
    if not p.exists():
        return None, f"absent {p}"
    try:
        d = json.loads(p.read_text())
    except Exception as e:
        return None, f"unreadable {p}: {e}"[:200]
    if not isinstance(d, dict):
        return None, "not a dict"
    meta_cs = str((d.get("meta") or {}).get("cat_side") or "").upper()
    if meta_cs != cs:
        return None, f"CAT_SIDE_MISMATCH meta={meta_cs or None} board={cs} — refused (principle 13)"
    return d, f"ok {p.name} built_at={(d.get('meta') or {}).get('built_at')} n_boards={(d.get('meta') or {}).get('n_boards')}"


def _f(x, default=0.0) -> float:
    try:
        return float(x)
    except Exception:
        return float(default)


def winners_take(digest, k: int) -> list:
    """Top-k winners of THIS cat_side's digest by score (desc), entries without switch/cand dropped."""
    w = [x for x in ((digest or {}).get("winners") or []) if isinstance(x, dict) and x.get("switch") and "cand" in x]
    w.sort(key=lambda x: -_f(x.get("score")))
    return w[: max(0, int(k))]


def xpoll_take(digest, own_cat_side: str, k: int) -> list:
    """At most k cross-pollination candidates; an entry that names this board's own cat_side (or none) is not cross."""
    own = str(own_cat_side or "").upper()
    out = []
    for x in ((digest or {}).get("xpoll") or []):
        if not isinstance(x, dict) or not x.get("switch") or "cand" not in x:
            continue
        frm = str(x.get("from_cat_side") or "").upper()
        if not frm or frm == own or frm not in CAT_SIDES:
            continue
        out.append(x)
    out.sort(key=lambda x: -_f(x.get("score")))
    return out[: max(0, int(k))]


def companions_of(entry) -> list:
    """[(filter, opt)] of a digest entry (winners / repair_menu), order-stable, one option per filter."""
    out, seen = [], set()
    for c in (entry or {}).get("companions") or []:
        if isinstance(c, dict) and c.get("filter") and "opt" in c and c["filter"] not in seen:
            seen.add(c["filter"])
            out.append((str(c["filter"]).strip(), c["opt"]))
    return out


def winners_pack(winners: list) -> list:
    """Union of the winners' (key, raw) parts in score order; the first (highest-score) value of a key wins."""
    out, seen = [], set()
    for w in winners or []:
        for k, v in [(str(w["switch"]).strip(), w["cand"])] + companions_of(w):
            if k in seen:
                continue
            seen.add(k)
            out.append((k, v))
    return out


# ── R1/R2/R3 gates beyond the pilot's _v17_ok (valid, trade floor, TIM guard, trades guard, block lists) ────────────────
HARD_SET_KEYS = frozenset({"GAP_MOC_EXIT_ENABLED", "DC_HARD_STOP_15M_ENABLED"})  # tools/v15_suspect_row_rerun.HARD_SET (USER 2026-10-16)
PROTECTED_OFF = {"DC_HARD_STOP_15M_ENABLED": (False, 0), "MTF_ATR_TRAIL_ENABLED": (False,), "BB_FROZEN_STOP_ENABLED": (False,), "FROZEN_STOP_FILTER_TF": ("OFF",), "MTF_ATR_TRAIL_FILTER_TF": ("OFF",)}
DENY_KEYS = frozenset({"MODE", "VENUE", "IS_TRADIER", "SIMPLE_PRICE_GT0_ENABLED"})


def deny_reason(delta_ov: dict) -> str:
    """Hard-set / protective-stop-off / engine-artefact / ABLATION keys never become V18 candidates."""
    for k, v in (delta_ov or {}).items():
        k = str(k).strip()
        if k in DENY_KEYS:
            return f"DENY_ARTEFACT:{k}"
        if k.startswith("ABLATION_"):
            return f"ABLATION_IS_A_LEVER:{k}"
        if k in HARD_SET_KEYS:
            return f"HARD_SET:{k}"
        offs = PROTECTED_OFF.get(k)
        if offs is not None and any((v is o) or (not isinstance(v, bool) and not isinstance(o, bool) and str(v).strip().upper() == str(o).strip().upper()) for o in offs):
            return f"PROTECTED_STOP_OFF:{k}"
    return ""


def extra_gate_reason(res: dict, chain_dd=None, dd_max: float = 30.0) -> str:
    """DD <= dd_max (or, on a chain already above it, not worse) and WR measured — on top of the pilot's gates."""
    r = res or {}
    if r.get("wr_pct") is None:
        return "WR_NOT_MEASURED"
    dd = r.get("max_dd_pct")
    if dd is None:
        return "DD_UNKNOWN"
    try:
        ddf = float(dd)
    except Exception:
        return "DD_UNKNOWN"
    if ddf > float(dd_max):
        try:
            if chain_dd is not None and float(chain_dd) > float(dd_max) and ddf < float(chain_dd):
                return ""
        except Exception:
            pass
        return f"DD {ddf:.2f} > {float(dd_max):.0f}"
    return ""


def lift_pm(lift: float, secs: float) -> float:
    return float(lift) / max(1e-6, float(secs) / 60.0)


def min_lift_pm() -> float:
    return _f(os.environ.get("V15_V17_MIN_LIFT_PM", "0.3"), 0.3)


# ── R2 EXIT REPAIR (doc 17 §3.3) ──────────────────────────────────────────────────────────────────────────────────────
STOP_EXECUTION_FAMILIES = ("VEC_STALE_FLATTEN", "VEC_EXIT_OWED", "VEC_CONVERGE")
# curated start of the repair menu (doc 17 §3.2): family -> TEMPLATE switch rows; values are the template's own candidates
CURATED_REPAIR = {
    "EXIT_VELOCITY_WT": ("EXIT_VELOCITY_WT_MIN_TFS", "EXIT_VELOCITY_WT_MIN_HOLD_ENABLED", "WT_4H_VEL_EXIT_REQUIRE_PROFIT", "WT_CROSS_EXIT_REQUIRE_15M_CONFIRM"),
    "DC_HOPELESS": ("DC_HOPELESS_EXIT_MIN_AGE_S",),
    "_DRIFT": ("HARD_BREAKEVEN_FLOOR", "BREAKEVEN_GAIN_EROSION_MIN_GAIN", "WIN_TRAIL_EROSION_PCT", "PEAK_GIVEBACK_HARD_ZERO_ENABLED"),
    "_GIVEBACK": ("PEAK_GIVEBACK_SCOPE", "MTF_ATR_TRAIL_MULT", "MTF_ATR_TRAIL_TF", "DC_DAYTRADE_TARGET_PCT"),
    "_STOP": ("BB_FROZEN_STOP_ENABLED", "BB_FROZEN_STOP_FIELD", "BB_FROZEN_STOP_TF", "DC_LOW_FROZEN_STOP_TF", "DC_HARD_STOP_TF", "TECHNICAL_DC_STOP_TF", "ATR_ADAPTIVE_STOP_TF"),
    "STATEFUL_PORTED_EXIT": ("STRUCTURAL_EXIT_GATE_ENABLED", "VEC_GENERIC_EXIT_MIN_GAIN_PCT"),
    "REDUCE_TO_FLAT": ("STRUCTURAL_EXIT_GATE_ENABLED", "VEC_GENERIC_EXIT_MIN_GAIN_PCT"),
}


def board_family_table(trades: list, fam_fn, drift_mfe_pct: float = 1.0) -> dict:
    """Per exit family of THIS board's ledger (rows of v15_trade_autopsy.scaled_rows + classify: pnl in gain pp, pnl_pct,
    mfe_pct, post_pct, cls, be, bx): n, losers, loss_sum_pct, one_bar_share, mean_bars_held, giveback_sum, premature_sum,
    drift_into_loss_n/_sum (MFE >= drift_mfe_pct then closed <= 0)."""
    out = {}
    for t in trades or []:
        fam = fam_fn(t.get("exit_reason") or "") or "UNKNOWN"
        e = out.setdefault(fam, {"n": 0, "losers": 0, "loss_sum_pct": 0.0, "one_bar": 0, "bars": 0, "giveback_sum": 0.0, "premature_sum": 0.0, "drift_into_loss_n": 0, "drift_into_loss_sum": 0.0})
        pnl = _f(t.get("pnl"))
        held = max(0, int(t.get("bx", 0)) - int(t.get("be", 0)))
        e["n"] += 1
        e["bars"] += held
        e["one_bar"] += 1 if held <= 1 else 0
        if pnl < 0:
            e["losers"] += 1
            e["loss_sum_pct"] += -pnl
        cls = t.get("cls") or []
        if "GIVEBACK" in cls and pnl > 0:
            e["giveback_sum"] += max(0.0, _f(t.get("mfe_pct")) - _f(t.get("pnl_pct")))
        if "PREMATURE_EXIT" in cls:
            e["premature_sum"] += _f(t.get("post_pct"))
        if _f(t.get("mfe_pct")) >= float(drift_mfe_pct) and _f(t.get("pnl_pct")) <= 0.0:
            e["drift_into_loss_n"] += 1
            e["drift_into_loss_sum"] += -min(0.0, pnl)
    for e in out.values():
        e["one_bar_share"] = round(e["one_bar"] / e["n"], 4) if e["n"] else 0.0
        e["mean_bars_held"] = round(e["bars"] / e["n"], 3) if e["n"] else 0.0
        for k in ("loss_sum_pct", "giveback_sum", "premature_sum", "drift_into_loss_sum"):
            e[k] = round(e[k], 4)
        e.pop("one_bar", None)
        e.pop("bars", None)
    return out


def merge_family_rank(board: dict, digest_rank: list, w_board: float = 1.0, w_digest: float = 0.5) -> list:
    """Rank exit families by loss: score = w_board * (family share of THIS board's loss) + w_digest * (share of the cat_side
    digest's loss over the board + live_hermetic columns); stop/execution families are reported, never repair targets.
    Secondary key = drift-into-loss. Shares make one board and a fleet-wide digest comparable. -> [(family, score, detail)]"""
    def _stop(f):
        return str(f).startswith(STOP_EXECUTION_FAMILIES) or str(f).startswith("FIXED_STOP") or str(f).startswith("MAKER_PROFIT_EXIT_FIXED_STOP")
    tb = sum(_f(e.get("loss_sum_pct")) for f, e in (board or {}).items() if not _stop(f))
    dig = {}
    for r in digest_rank or []:
        if not isinstance(r, dict) or not r.get("family") or str(r.get("column") or "board") not in ("board", "live_hermetic"):
            continue
        e = dig.setdefault(str(r["family"]), {"loss": 0.0, "drift": 0.0, "n": 0})
        e["loss"] += _f(r.get("loss_sum_pct"))
        e["drift"] += _f(r.get("drift_into_loss_sum"))
        e["n"] += int(_f(r.get("n")))
    td = sum(e["loss"] for f, e in dig.items() if not _stop(f))
    fams = set(board or {}) | set(dig)
    out = []
    for f in fams:
        if _stop(f):
            continue
        sb = (_f((board.get(f) or {}).get("loss_sum_pct")) / tb) if tb > 0 else 0.0
        sd = (dig.get(f, {}).get("loss", 0.0) / td) if td > 0 else 0.0
        drift = _f((board.get(f) or {}).get("drift_into_loss_sum")) + 0.5 * dig.get(f, {}).get("drift", 0.0)
        score = float(w_board) * sb + float(w_digest) * sd
        if score <= 0 and drift <= 0:
            continue
        out.append((f, round(score, 6), {"board_share": round(sb, 4), "digest_share": round(sd, 4), "drift": round(drift, 4), "board": board.get(f), "digest_n": dig.get(f, {}).get("n", 0)}))
    out.sort(key=lambda t: (-t[1], -t[2]["drift"], t[0]))
    return out


def curated_switches_for(family: str, detail: dict | None = None) -> list:
    """Curated menu switches for one family (exact family, then the symptom classes the family's own numbers show)."""
    out = list(CURATED_REPAIR.get(str(family), ()))
    b = (detail or {}).get("board") or {}
    if _f(b.get("drift_into_loss_n")) > 0:
        out += list(CURATED_REPAIR["_DRIFT"])
    if _f(b.get("giveback_sum")) > 0:
        out += list(CURATED_REPAIR["_GIVEBACK"])
    if _f(b.get("losers")) > 0:
        out += list(CURATED_REPAIR["_STOP"])
    return list(dict.fromkeys(out))


def repair_entries(digest, family: str) -> list:
    """The digest's repair menu paths for one family (switch, cand, companions), support-ranked."""
    m = ((digest or {}).get("repair_menu") or {}).get(str(family)) or []
    m = [e for e in m if isinstance(e, dict) and e.get("switch") and "cand" in e]
    m.sort(key=lambda e: (-_f(e.get("forward_factor"), 0.5) * _f(e.get("support_n"), 0.0), str(e.get("switch"))))
    return m


def fixed_stop_entries(digest) -> list:
    out = []
    for fam, lst in ((digest or {}).get("repair_menu") or {}).items():
        if str(fam).startswith("FIXED_STOP"):
            out += [e for e in (lst or []) if isinstance(e, dict) and e.get("switch") and "cand" in e]
    return out


def attribution_card(base_trades: list, cand_trades: list, base_res: dict, cand_res: dict, attribute_fn, scorecard_fn, moves=None) -> dict:
    """losers fixed / winners hurt / $ saved / $ hurt (v15_trade_autopsy.attribute + scorecard on entry-bar-keyed trades)
    plus Δgain ΔDD ΔWR ΔTIM ΔTRADES from the two real engine results."""
    a = attribute_fn(base_trades, cand_trades, moves)
    s = scorecard_fn(base_trades, a)
    out = {k: (round(v, 4) if isinstance(v, float) else v) for k, v in s.items()}
    for k, lab in (("gain_pct", "d_gain"), ("max_dd_pct", "d_dd"), ("wr_pct", "d_wr"), ("tim_pct", "d_tim"), ("trades", "d_trades")):
        try:
            out[lab] = round(float((cand_res or {}).get(k)) - float((base_res or {}).get(k)), 4)
        except Exception:
            out[lab] = None
    return out


# ── R3 ABLATION by group with remedies (doc 17 §5) ────────────────────────────────────────────────────────────────────
GROUP_TABS = {
    "ENTRY": ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES"),
    "MTF_HTF": ("ENTRY_REVERSAL_BOUNCE", "ENTRY_BREAKOUT_CHANNEL", "ENTRY_CONFIRMATION_GATES", "REENTRY_WINDOWED", "REENTRY_ADAPTIVE"),
    "REENTRY": ("REENTRY_WINDOWED", "REENTRY_ADAPTIVE"),
    "EXIT": ("EXIT_STRUCTURAL", "EXIT_VELOCITY", "REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"),
    "AUGMENT": ("AUGMENT_TREND", "AUGMENT_RISK_SIZING"),
    "REDUCE": ("REDUCE_PROFIT_LOCK", "REDUCE_SIGNAL_RATER"),
}
EXIT_GROUP_PREFIXES = ("EXIT", "REDUCE")


def ablation_groups(groups: dict, row_tab_of: dict) -> dict:
    """filter_ablation_groups.GROUPS -> {group_name: [filters]}; the 214-filter ENTRY group is split by the ENTRY tab its
    filter row lives in (row_tab_of: filter -> first tab where it is a template row), rest = ENTRY:OTHER. Order-stable."""
    out = {}
    for flag, members in (groups or {}).items():
        g = str(flag).replace("ABLATION_DISABLE_FILTER_", "")
        if g == "ENTRY":
            for m in members:
                t = (row_tab_of or {}).get(m)
                sub = f"ENTRY:{t}" if t in GROUP_TABS["ENTRY"] else "ENTRY:OTHER"
                out.setdefault(sub, []).append(m)
        else:
            out.setdefault(g, []).extend(list(members))
    order = [f"ENTRY:{t}" for t in GROUP_TABS["ENTRY"]] + ["ENTRY:OTHER", "MTF_HTF", "REENTRY", "EXIT", "AUGMENT", "REDUCE"]
    return {k: out[k] for k in order if out.get(k)} | {k: v for k, v in out.items() if k not in order and v}


def group_tabs(group: str) -> tuple:
    return GROUP_TABS.get(str(group).split(":", 1)[0], ())


def group_category(group: str) -> str:
    g = str(group).split(":", 1)[0]
    return {"ENTRY": "entry", "MTF_HTF": "entry", "REENTRY": "reentry", "EXIT": "exit", "AUGMENT": "augment", "REDUCE": "exit"}.get(g, "")


def is_exit_group(group: str) -> bool:
    return str(group).startswith(EXIT_GROUP_PREFIXES)


def scoped_exemption(switch: str, cand, filt: str, category: str, exm_map: dict, filter_keys=(), augment_keys=()):
    """-> (override dict, scope label) or (None, ''). Scoped FILTER_EXEMPTIONS for this path only: the exemption map's entry
    for switch=cand / switch first, else the engine's own path ids (REENTRY for reentry paths, AUGMENT for augment paths)
    when the filter is a member the engine can force off there. Entry families without a mapped path id have no scope."""
    ex_sw = (exm_map or {}).get(f"{switch}={cand}") or (exm_map or {}).get(str(switch)) or {}
    e = ex_sw.get(filt) if isinstance(ex_sw, dict) else None
    if isinstance(e, dict) and e:
        return dict(e), "EXEMPT:map"
    if category == "reentry" and filt in set(filter_keys or ()):
        return {"FILTER_EXEMPTIONS": {"REENTRY": [filt]}}, "EXEMPT:REENTRY"
    if category == "augment" and filt in set(augment_keys or ()):
        return {"FILTER_EXEMPTIONS": {"AUGMENT": [filt]}}, "EXEMPT:AUGMENT"
    return None, ""


def veto_candidates(digest, max_n: int = 40) -> list:
    """The digest's filter_veto entries as ready-made (switch=cand, filter, remedy) candidates, best evidence first."""
    out = []
    for v in ((digest or {}).get("filter_veto") or []):
        if not isinstance(v, dict) or not v.get("switch") or "cand" not in v or not isinstance((v.get("remedy") or {}).get("override"), dict):
            continue
        kind = str((v.get("remedy") or {}).get("kind") or "")
        if kind not in ("exemption", "reset", "global_off"):
            continue
        out.append(v)
    out.sort(key=lambda v: (-(_f(v.get("vetoed_in_missed_moves")) - _f(v.get("vetoed_losers"))), -_f(v.get("support_syms"))))
    return out[: max(0, int(max_n))]


REMEDY_LABEL = {"exemption": "EXEMPT", "reset": "GLOBAL_RESET", "global_off": "GLOBAL_OFF"}


def abl_budget(tier_budget_s: float, share=None, floor_s=None) -> float:
    s = _f(os.environ.get("V18_ABL_SHARE", "0.30") if share is None else share, 0.30)
    fl = _f(os.environ.get("V18_ABL_FLOOR_S", "240") if floor_s is None else floor_s, 240.0)
    return max(fl, s * float(tier_budget_s))


def extend_deadline(rate_pm: float, now: float, deadline: float, window_s: float, hard: float, chunk_s: float = 120.0) -> float:
    """Pure: a phase that lifted >= V15_V17_MIN_LIFT_PM in its last window and is within 60 s of its deadline gets one more
    chunk (max(chunk_s, window)), clipped by hard."""
    if rate_pm < min_lift_pm() or deadline - now >= 60.0 or now >= hard - 30.0:
        return deadline
    return min(hard, now + max(float(chunk_s), float(window_s)))


def untried_p() -> float:
    """USER law 'every combination keeps its chance': a pair with n < 3 across the cat_side gets V18_UNTRIED_PER_1000 (50)."""
    try:
        return min(1.0, max(0.0, float(os.environ.get("V18_UNTRIED_PER_1000", "50")) / 1000.0))
    except Exception:
        return 0.05


PHASE_PREFIXES = (("WINNERS", "WINNERS"), ("XPOLL", "XPOLL"), ("EXITREP", "EXITREP"), ("ABL", "ABL"), ("FOCUS", "FOCUS"), ("SURGEON", "SURGEON"), ("V17_", "V17"), ("COMBO", "COMBO"), ("RED_RETRY", "RED_RETRY"), ("RECALC", "MAIN"), ("JOINT", "MAIN"))


def phase_of(sheet, label, switch_sheets=()) -> str:
    """Delta-log `phase` field: MAIN for template-row evals, else the phase prefix of the label (E8 hook spec §4)."""
    lab = str(label or "")
    for pre, ph in PHASE_PREFIXES:
        if lab.startswith(pre):
            return ph
    if sheet in set(switch_sheets or ()):
        return "MAIN"
    return str(sheet or "OTHER").split(":", 1)[0]


# ── E5 (doc 17 §4.2 / §4.4): speculative tab batching + both pair sides concurrent ─────────────────────────────────────
def spec_batch_on() -> bool:
    return flag_on("V18_SPEC_BATCH")


def pair_concurrent_on() -> bool:
    return flag_on("V18_PAIR_CONCURRENT")


def tab_span(queue: list, qi: int, cap: int) -> int:
    """Index one past the last row of the tab that queue[qi] belongs to (queue = [(sheet, row, switch, cand)]), at most cap
    rows ahead. The speculative window never crosses a tab boundary (the sacred repass / tab-start E3 run there)."""
    if qi >= len(queue):
        return qi
    tab = queue[qi][0]
    j = qi
    while j < len(queue) and queue[j][0] == tab and j - qi < int(cap):
        j += 1
    return j


def spec_cancel_stale(inflight: dict) -> int:
    """After the chain moved, every in-flight speculative result was submitted against the OLD running set: cancel the ones
    not started yet and drop their entries (a started one finishes into the cache under its own exact key, harmless).
    inflight = {ck: (future, t0)}; one future can carry several cks (batched task). Returns the number of cks dropped."""
    n = 0
    dead = set()
    for ck, (f, _t0) in list(inflight.items()):
        if id(f) in dead:
            inflight.pop(ck, None)
            n += 1
            continue
        try:
            if not f.done() and f.cancel():
                dead.add(id(f))
                inflight.pop(ck, None)
                n += 1
        except Exception:
            continue
    return n
