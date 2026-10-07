#!/usr/bin/env python3
"""xlsx_atomic — the ONLY sanctioned way for tools/ to write an .xlsx.

USER ORDER 2026-09-29: "corrupt badzip has been a problem all along you need to
fix the writer once and for all." Invariant: no code path may leave an invalid
xlsx at a destination path. Save to a same-directory dot-tmp, validate the zip
(CRC test + >=10 entries for real workbooks), then os.replace atomically. On any
failure the tmp is deleted and the previous file (if any) survives untouched.

Companion rule for transfers: xlsx files may only be rsynced WITHOUT --inplace
(default temp+rename), and arrivals should be zip-validated before trusting.
"""
from __future__ import annotations
import os
import zipfile
from pathlib import Path


def atomic_wb_save(wb, path, min_entries: int = 10):
    path = Path(path)
    tmp = path.parent / f".{path.name}.{os.getpid()}.atomic.tmp"
    try:
        wb.save(str(tmp))
        with zipfile.ZipFile(tmp, "r") as z:
            names = z.namelist()
            if len(names) < min_entries:
                raise zipfile.BadZipFile(f"{len(names)} entries < {min_entries}")
            bad = z.testzip()
            if bad is not None:
                raise zipfile.BadZipFile(f"CRC fail at {bad}")
        os.replace(tmp, path)
        return path
    except Exception:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass
        raise


def validate_xlsx(path, min_entries: int = 10) -> bool:
    try:
        with zipfile.ZipFile(path, "r") as z:
            return len(z.namelist()) >= min_entries and z.testzip() is None
    except Exception:
        return False


def quarantine_if_bad(path, quarantine_dir) -> bool:
    """Returns True if the file was bad and moved to quarantine."""
    path = Path(path)
    if validate_xlsx(path):
        return False
    qd = Path(quarantine_dir)
    qd.mkdir(parents=True, exist_ok=True)
    os.replace(path, qd / path.name)
    return True
