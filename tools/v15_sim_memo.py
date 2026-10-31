"""Exact whole-simulation memo for v12_quick_engine.simulate_one (gate candidate E, 2026-10-28).

A simulation's result is a deterministic function of the frozen NPZ, (sym, side, seed) and the VALUES of the cfg attributes the
run actually reads. A board evaluates thousands of cells that differ from an earlier cell only in switches the run never reads
(or reads with the same value), so their result is already known. This module records the read set of every run through a lazy
proxy (one Python call per DISTINCT attribute, then plain C lookups) and returns the stored result when a later cfg agrees with
an entry on every recorded read. Nothing is approximated: a hit is the same computation the engine would repeat.

Exactness rules:
- key = engine NPZ fingerprint (_npz_memo_fp) + sym + side + seed + value of every attribute read (same type and ==, the
  engine's own _memo_same semantics; the hash index only admits plain immutable scalars, tuples/lists/dicts of them, MISSING);
- template resolution happens before proxying, matching ensure_template_layer's exact-QuickConfig check. Side-channel writes
  target the resolved config object (which may be a copy), are never inputs, and are replayed (deep-copied) on a hit;
- NPZ keys the run adds or replaces are replayed on a hit; results are deep-copied on store and on hit;
- a copy of the cfg made inside the run (crypto lane copy) keeps its own writes local, its reads still record.

V12_SIM_MEMO=2 enables (install() is a no-op otherwise). The engine file is not modified, so its md5 and the eval cache stay valid.
"""

import copy as _cp
import dataclasses as _dcs
import hashlib as _hh
import os

_STATE = {"installed": False, "orig": None, "hits": 0, "runs": 0}
_INDEX = {}
_LINEAR = {}
_CLASSES = {}
_PLAIN = (bool, int, float, str, type(None))
_CAP = int(os.environ.get("V12_SIM_MEMO_CAP", "128"))


def enabled() -> bool:
    return os.environ.get("V12_SIM_MEMO", "0") == "2"


def stats() -> dict:
    return {"hits": _STATE["hits"], "runs": _STATE["runs"]}


def _hkey(v, missing):
    if v is missing:
        return (None, "<MISSING>")
    t = type(v)
    if t in _PLAIN:
        return (t, v)
    if (t is tuple or t is list) and all(type(x) in _PLAIN for x in v):
        return (t, tuple(v))
    if t is dict and all(type(x) in _PLAIN for x in v) and all(type(x) in _PLAIN for x in v.values()):
        return (t, tuple(sorted(v.items())))
    raise TypeError


class _Lazy:
    __slots__ = ("name",)

    def __init__(self, name):
        self.name = name

    def __get__(self, inst, owner):
        name = self.name
        if inst is None:
            return getattr(owner.__mro__[1], name)
        d = inst.__dict__
        st = d.get("_trk_state")
        if st is None:
            v = getattr(type(inst).__mro__[1], name)
            d[name] = v
            return v
        try:
            v = object.__getattribute__(st["src"], name)
        except AttributeError:
            st["reads"].setdefault(name, st["missing"])
            raise
        st["reads"].setdefault(name, v)
        d[name] = v
        return v


def _proxy_class(base, names):
    key = (base, names)
    c = _CLASSES.get(key)
    if c is not None:
        return c

    def __getattr__(self, name):
        st = self.__dict__.get("_trk_state")
        if st is None or name.startswith("__"):
            raise AttributeError(name)
        try:
            v = object.__getattribute__(st["src"], name)
        except AttributeError:
            st["reads"].setdefault(name, st["missing"])
            raise
        st["reads"].setdefault(name, v)
        self.__dict__[name] = v
        return v

    def __setattr__(self, name, value):
        st = self.__dict__.get("_trk_state")
        if st is not None and self is st["root"]:
            st["writes"][name] = value
            setattr(st["src"], name, value)
        object.__setattr__(self, name, value)

    ns = {n: _Lazy(n) for n in names}
    ns.update(__getattr__=__getattr__, __setattr__=__setattr__, __module__=base.__module__, __qualname__=base.__qualname__)
    c = type(base.__name__, (base,), ns)
    _CLASSES[key] = c
    return c


def _replay(ent, npz, cfg):
    for k, v in ent[2].items():
        npz[k] = v
    for k, v in ent[4].items():
        setattr(cfg, k, _cp.deepcopy(v))
    return _cp.deepcopy(ent[3])


def _hash_npz_value(h, value):
    try:
        import numpy as _np
        if isinstance(value, _np.ndarray):
            a = _np.asarray(value)
            h.update(b"A"); h.update(a.dtype.str.encode()); h.update(repr(a.shape).encode())
            if a.dtype.hasobject:
                _hash_npz_value(h, a.tolist())
            else:
                h.update(memoryview(_np.ascontiguousarray(a)).cast("B"))
            return True
        if isinstance(value, _np.generic):
            return _hash_npz_value(h, value.item())
    except Exception:
        return False
    if value is None or type(value) in _PLAIN:
        h.update(type(value).__name__.encode()); h.update(repr(value).encode())
        return True
    if type(value) in (tuple, list):
        h.update(type(value).__name__.encode()); h.update(str(len(value)).encode())
        return all(_hash_npz_value(h, item) for item in value)
    if type(value) is dict:
        h.update(b"dict"); h.update(str(len(value)).encode())
        for key in sorted(value, key=lambda item: repr(item)):
            if not _hash_npz_value(h, key) or not _hash_npz_value(h, value[key]):
                return False
        return True
    return False


def _npz_content_fp(V, npz, is_long):
    base = V._npz_memo_fp(npz, 0, is_long)
    if base is None:
        return None
    h = _hh.blake2b(digest_size=32)
    h.update(repr(base).encode())
    if not isinstance(npz, dict):
        return None
    for key in sorted(npz, key=lambda item: repr(item)):
        if not _hash_npz_value(h, key) or not _hash_npz_value(h, npz[key]):
            return None
    return h.digest()


def _memo_simulate(V, orig, npz, sym, is_long, cfg, force_initial_seed=False):
    # Match normal engine template resolution before proxying. The engine only
    # layers exact QuickConfig instances; a proxy subclass would skip it.
    # Mutations must target the resolved config: the resolver may return a copy,
    # and uncached engine writes then do not reach the caller's original cfg.
    resolve = getattr(V, "ensure_template_layer", None)
    resolved_cfg = resolve(cfg, sym, is_long) if callable(resolve) else cfg
    missing = V._MISSING
    fp = _npz_content_fp(V, npz, is_long)
    if fp is None:
        return orig(npz, sym, is_long, resolved_cfg, force_initial_seed)
    tag = (sym, bool(is_long), bool(force_initial_seed))
    idx = _INDEX.setdefault(tag, {})
    for (ifp, ikeys), imap in idx.items():
        if ifp != fp:
            continue
        try:
            vals = []
            for k in ikeys:
                try:
                    vals.append(_hkey(object.__getattribute__(resolved_cfg, k), missing))
                except AttributeError:
                    vals.append(_hkey(missing, missing))
            ent = imap.get(tuple(vals))
        except TypeError:
            ent = None
        if ent is not None:
            _STATE["hits"] += 1
            return _replay(ent, npz, resolved_cfg)
    lin = _LINEAR.setdefault(tag, [])
    for ent in lin:
        if ent[0] != fp:
            continue
        ok = True
        for k, v in ent[1].items():
            try:
                cur = object.__getattribute__(resolved_cfg, k)
            except AttributeError:
                cur = missing
            if not V._memo_same(cur, v):
                ok = False
                break
        if ok:
            _STATE["hits"] += 1
            return _replay(ent, npz, resolved_cfg)
    try:
        names = tuple(sorted(set(f.name for f in _dcs.fields(resolved_cfg)) | set(k for k in resolved_cfg.__dict__ if not k.startswith("__"))))
    except Exception:
        return orig(npz, sym, is_long, cfg, force_initial_seed)
    proxy = object.__new__(_proxy_class(type(resolved_cfg), names))
    st = {"src": resolved_cfg, "reads": {}, "writes": {}, "root": proxy, "missing": missing}
    object.__setattr__(proxy, "_trk_state", st)
    before = {k: id(v) for k, v in npz.items()}
    _STATE["runs"] += 1
    res = orig(npz, sym, is_long, proxy, force_initial_seed)
    ent = (fp, dict(st["reads"]), {k: v for k, v in npz.items() if before.get(k) != id(v)}, _cp.deepcopy(res), _cp.deepcopy(st["writes"]))
    try:
        ikeys = tuple(sorted(ent[1]))
        ival = tuple(_hkey(ent[1][k], missing) for k in ikeys)
        imap = idx.setdefault((fp, ikeys), {})
        imap[ival] = ent
        while len(imap) > _CAP:
            imap.pop(next(iter(imap)))
    except TypeError:
        lin.insert(0, ent)
        del lin[48:]
    return res


def install(V=None) -> bool:
    """Wrap V.simulate_one (module attribute, so evaluate_v12 and the engine's own nested calls both route through it)."""
    if not enabled() or _STATE["installed"]:
        return _STATE["installed"]
    if V is None:
        import v12_quick_engine as V
    for need in ("_npz_memo_fp", "_memo_same", "_MISSING", "simulate_one"):
        if not hasattr(V, need):
            return False
    orig = V.simulate_one

    def simulate_one(npz, sym, is_long, cfg, force_initial_seed=False):
        return _memo_simulate(V, orig, npz, sym, is_long, cfg, force_initial_seed)

    simulate_one.__wrapped__ = orig
    V.simulate_one = simulate_one
    _STATE.update(installed=True, orig=orig)
    return True


def _stats_dir(parent_pid: int) -> str:
    return os.path.join(os.environ.get("TMPDIR", "/tmp"), f"v15_sim_memo_{int(parent_pid)}")


def dump_stats() -> None:
    """forked pool worker: publish this worker's cumulative hits/runs for the parent (one small file per worker, overwritten)."""
    if not _STATE["installed"]:
        return
    try:
        import json as _json

        d = _stats_dir(os.getppid())
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, f".{os.getpid()}.tmp")
        with open(tmp, "w") as fh:
            _json.dump(stats(), fh)
        os.replace(tmp, os.path.join(d, f"{os.getpid()}.json"))
    except Exception:
        pass


def collect_stats() -> dict:
    """parent: own counters + every forked worker's last published counters."""
    tot = dict(stats())
    tot["workers"] = 0
    try:
        import json as _json

        d = _stats_dir(os.getpid())
        for f in os.listdir(d):
            if f.endswith(".json"):
                with open(os.path.join(d, f)) as fh:
                    s = _json.load(fh)
                tot["hits"] += int(s.get("hits", 0))
                tot["runs"] += int(s.get("runs", 0))
                tot["workers"] += 1
    except Exception:
        pass
    n = tot["hits"] + tot["runs"]
    tot["hit_rate"] = round(tot["hits"] / n, 4) if n else 0.0
    return tot
