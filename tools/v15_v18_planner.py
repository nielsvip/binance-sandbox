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

    def note(self, key_hash: int, cum_before: float, tag: str) -> None:
        self.first.setdefault((key_hash, float(cum_before)), tag)

    def lookup(self, key_hash: int, cum_before: float):
        return self.first.get((key_hash, float(cum_before)))

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
