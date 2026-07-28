"""Shared data-source age accounting for the email digests.

CLAUDE.md NO-LIES MANDATE. A digest section that renders a number read off a file
without stating how old that file is, is a lie whenever the producer of that file has
stopped running. This module is the single place that measures the age of a digest's
inputs and renders that age next to the numbers, so no section can silently present a
frozen artifact as a current result.

Usage pattern in a digest:

    import digest_freshness as fresh
    chk = fresh.check(path, "premarket scanner", fresh_h=24, stale_h=72)
    html += fresh.banner_html([chk], "Pre-Market Scanner")
    if not fresh.is_usable(chk):
        return html          # refuse to render numbers off a dead source

`fresh_h` / `stale_h` / `dead_h` are per-source because cadence differs: a mark-price
file is stale at 1h, a nightly campaign digest is fine at 20h.
"""
import os
import time

FRESH = "FRESH"
AGING = "AGING"
STALE = "STALE"
DEAD = "DEAD"
MISSING = "MISSING"

_STATE_COLOR = {FRESH: "#2e7d32", AGING: "#ef6c00", STALE: "#c62828", DEAD: "#6a1b9a", MISSING: "#616161"}
_STATE_BG = {FRESH: "#e8f5e9", AGING: "#fff8e1", STALE: "#ffebee", DEAD: "#f3e5f5", MISSING: "#eeeeee"}
_STATE_WORD = {FRESH: "current", AGING: "aging", STALE: "STALE", DEAD: "DEAD SOURCE", MISSING: "MISSING"}


def age_seconds(path):
    """Seconds since path was last written, or None when it does not exist."""
    try:
        return max(0.0, time.time() - os.path.getmtime(str(path)))
    except Exception:
        return None


def fmt_age(seconds):
    if seconds is None:
        return "no file"
    if seconds < 5400:
        return "%.0fmin old" % (seconds / 60.0)
    if seconds < 172800:
        return "%.1fh old" % (seconds / 3600.0)
    return "%.1fd old" % (seconds / 86400.0)


def classify(seconds, fresh_h=24.0, stale_h=72.0, dead_h=336.0):
    if seconds is None:
        return MISSING
    hours = seconds / 3600.0
    if hours <= fresh_h:
        return FRESH
    if hours <= stale_h:
        return AGING
    if hours <= dead_h:
        return STALE
    return DEAD


def check(path, label, fresh_h=24.0, stale_h=72.0, dead_h=336.0, note=""):
    """Measure one data source. Returns a dict consumed by the render helpers below."""
    seconds = age_seconds(path)
    return {"path": str(path), "label": label, "age_s": seconds, "age": fmt_age(seconds), "state": classify(seconds, fresh_h, stale_h, dead_h), "note": note}


def is_usable(chk):
    """True when the source is recent enough that its numbers may be shown as current."""
    return chk["state"] in (FRESH, AGING)


def worst(checks):
    order = [FRESH, AGING, STALE, DEAD, MISSING]
    return max((c["state"] for c in checks), key=order.index) if checks else FRESH


def badge_html(chk):
    color = _STATE_COLOR[chk["state"]]
    return '<span style="display:inline-block;padding:1px 6px;border-radius:3px;font-size:10px;font-weight:700;color:#fff;background:%s">%s &middot; %s</span>' % (color, _STATE_WORD[chk["state"]], chk["age"])


def banner_html(checks, section_name):
    """Warning box naming every non-current input of a section, with its measured age.

    Returns "" when every input is FRESH, so a healthy section stays uncluttered.
    """
    bad = [c for c in checks if c["state"] != FRESH]
    if not bad:
        return ""
    state = worst(bad)
    rows = "".join('<li><b>%s</b> &mdash; %s <span style="color:#888;font-size:10px">(%s)</span>%s</li>' % (c["label"], c["age"], c["path"], (" &mdash; " + c["note"]) if c["note"] else "") for c in bad)
    verdict = {
        AGING: "Numbers below are older than this section's normal refresh cadence.",
        STALE: "Numbers below are NOT current. Treat them as historical, not as today's state.",
        DEAD: "The producer of this data has stopped running. Numbers below are a frozen snapshot, not a current result.",
        MISSING: "Input file absent on this host — the section below is empty or partial, not a zero result.",
    }[state]
    return '<div style="border-left:4px solid %s;background:%s;padding:8px 12px;margin:6px 0;font-size:12px"><b>%s data check &mdash; %s</b><br>%s<ul style="margin:4px 0 0 0">%s</ul></div>' % (_STATE_COLOR[state], _STATE_BG[state], section_name, verdict, "", rows)


def text_block(checks, section_name):
    """Plain-text equivalent of banner_html, for digests that email text not HTML."""
    bad = [c for c in checks if c["state"] != FRESH]
    if not bad:
        return ""
    lines = ["[DATA CHECK] %s — inputs that are not current:" % section_name]
    for c in bad:
        lines.append("  - %-34s %-14s %s%s" % (c["label"], _STATE_WORD[c["state"]], c["age"], (" — " + c["note"]) if c["note"] else ""))
    return "\n".join(lines) + "\n"


def stale_suffix(chk):
    """Short inline marker to append to a heading whose numbers come from chk."""
    if chk["state"] == FRESH:
        return ""
    return ' <span style="color:%s;font-size:11px;font-weight:700">[%s &middot; %s]</span>' % (_STATE_COLOR[chk["state"]], _STATE_WORD[chk["state"]], chk["age"])
