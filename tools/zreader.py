"""zreader — transparent .zst / .gz reading for the corpora the brains train on.

WHY THIS EXISTS (2026-10-27). tools/v20_absorb_cycle.py could only compress a delta log once its newest row was
older than the brain window (14 d) plus a 7 d margin, because v15_brain.py and v18_brain.py glob `*.jsonl` and call
plain open(). That made ~25 GB of run33/34/35 logs untouchable for another two weeks while the fleet wrote more --
the disk was growing faster than the corpus was being absorbed. With the readers zst-aware, a compressed log is
still a first-class training input, so a finished round can be compressed the day it finishes and nothing the brain
would have read is lost.

zstd is 6x-29x on these jsonl files (measured by tools/gcs_cold_store.py), so this is the cheapest disk the fleet has.

Decoding prefers the `zstandard` module and falls back to the `zstd` binary the absorb cycle already requires. Both
yield the exact original bytes -- the absorb cycle additionally proves that with a sha256 round-trip before it
unlinks an original, so a reader that silently truncated could not go unnoticed.
"""
import glob
import gzip
import io
import os
import subprocess

SUFFIXES = (".zst", ".gz")


def is_compressed(path):
    return str(path).endswith(SUFFIXES)


def plain_name(path):
    """'x.jsonl.zst' -> 'x.jsonl' (what the file was before compression)."""
    p = str(path)
    for s in SUFFIXES:
        if p.endswith(s):
            return p[: -len(s)]
    return p


def open_binary(path):
    """A binary line-iterable stream for a plain, .gz or .zst file."""
    p = str(path)
    if p.endswith(".gz"):
        return gzip.open(p, "rb")
    if p.endswith(".zst"):
        try:
            import zstandard
        except ImportError:
            proc = subprocess.Popen(["zstd", "-dcq", "--", p], stdout=subprocess.PIPE)
            return _ProcStream(proc)
        fh = open(p, "rb")
        return zstandard.ZstdDecompressor().stream_reader(fh, closefd=True)
    return open(p, "rb")


def open_text(path, errors="replace"):
    """A text line-iterable stream for a plain, .gz or .zst file."""
    raw = open_binary(path)
    if not isinstance(raw, io.BufferedIOBase):
        raw = io.BufferedReader(raw)
    return io.TextIOWrapper(raw, errors=errors)


def expand_globs(globs):
    """Every match of each glob, plus its .zst and .gz forms, so a compressed corpus is still found.

    A plain file and its own .zst are never both returned: when both exist the compression was interrupted before
    the original was unlinked, and reading both would feed every row to the brain twice.
    """
    found = set()
    for g in globs or []:
        g = os.path.expanduser(g)
        for pattern in (g,) + tuple(g + s for s in SUFFIXES):
            found.update(glob.glob(pattern))
    out = {f for f in found if not is_compressed(f) or plain_name(f) not in found}
    return sorted(out, key=lambda f: _mtime(f))


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


class _ProcStream(io.RawIOBase):
    """`zstd -dcq` as a readable stream; used only when the zstandard module is absent."""

    def __init__(self, proc):
        self.proc = proc

    def readable(self):
        return True

    def readinto(self, b):
        chunk = self.proc.stdout.read(len(b))
        if not chunk:
            return 0
        b[: len(chunk)] = chunk
        return len(chunk)

    def close(self):
        try:
            if self.proc.stdout:
                self.proc.stdout.close()
        finally:
            if self.proc.poll() is None:
                self.proc.terminate()
            self.proc.wait()
        super().close()
