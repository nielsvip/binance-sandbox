#!/opt/anaconda3/envs/binance_env/bin/python
# pylint: disable=W,C,R,I
"""agent_inbox_poller.py — fallback channel for cloud routines that can't git push.

Cloud routines double-write their JSON output:
  1. git push to handoff repo (preferred)
  2. Gmail draft via mcp__claude_ai_Gmail__create_draft (fallback)

This poller runs every 5 min via cron, reads Gmail Drafts folder via IMAP, finds drafts whose
subject matches one of our agent-output patterns, extracts the JSON body, and writes the file
into ~/binance-agent-handoff/ (overwriting the older copy from git push if newer).

After successful processing, the draft is moved to the [Gmail]/Trash folder so we don't reprocess.

Subject patterns (must be exact prefix match):
  - "[TRC_ADVISORIES_v1] <iso-timestamp>"  -> trc_advisories.json
  - "[OPTIONS_BRIEFING_v1] <iso-timestamp>" -> options_briefing.json
"""
import email
import imaplib
import json
import logging
import os
import re
import ssl
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

GMAIL_USER = "nielsvip@gmail.com"
HANDOFF_REPO = Path.home() / "binance-agent-handoff"
LOG_PATH = Path.home() / "logs" / "agent_inbox_poller.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
SUBJECT_TO_FILE = {
    "[TRC_ADVISORIES_v1]": "trc_advisories.json",
    "[OPTIONS_BRIEFING_v1]": "options_briefing.json",
    "[MORNING_NEWSLETTER_v1]": None,
    "[EVENING_RECAP_v1]": None,
}
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()])
log = logging.getLogger("agent_inbox_poller")


def _password():
    try:
        return subprocess.check_output(["security", "find-generic-password", "-a", GMAIL_USER, "-s", "gmail-app-password", "-w"], stderr=subprocess.DEVNULL).decode().strip()
    except Exception:
        pass
    for path in (Path.home() / ".config" / "binance-agent" / "gmail_app_password", Path("/etc/binance-agent/gmail_app_password")):
        try:
            if path.exists():
                return path.read_text().strip()
        except Exception:
            continue
    log.error("no gmail app password found (keychain + ~/.config/binance-agent/gmail_app_password both empty)")
    return None


def _connect():
    pw = _password()
    if not pw:
        return None
    ctx = ssl.create_default_context()
    M = imaplib.IMAP4_SSL("imap.gmail.com", 993, ssl_context=ctx)
    M.login(GMAIL_USER, pw)
    return M


def _decode_payload(msg):
    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_type() == "text/plain":
                payload = part.get_payload(decode=True)
                if payload:
                    return payload.decode(part.get_content_charset() or "utf-8", errors="replace")
    else:
        payload = msg.get_payload(decode=True)
        if payload:
            return payload.decode(msg.get_content_charset() or "utf-8", errors="replace")
    return ""


def _extract_json(body):
    body = body.strip()
    if body.startswith("{") or body.startswith("["):
        try:
            json.loads(body)
            return body
        except json.JSONDecodeError:
            pass
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", body, re.DOTALL)
    if m:
        candidate = m.group(1).strip()
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            pass
    start = body.find("{")
    end = body.rfind("}")
    if start != -1 and end != -1 and end > start:
        candidate = body[start:end + 1]
        try:
            json.loads(candidate)
            return candidate
        except json.JSONDecodeError:
            return None
    return None


def _git_push():
    git_env = os.environ.copy()
    git_env["GIT_SSH_COMMAND"] = f"ssh -i {Path.home()}/.ssh/id_ed25519_github -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"
    git_env["HOME"] = str(Path.home())
    cmds = [
        ["git", "-C", str(HANDOFF_REPO), "add", "-A"],
        ["git", "-C", str(HANDOFF_REPO), "commit", "-m", f"inbox-poller {datetime.now(timezone.utc).isoformat(timespec='seconds')}"],
        ["git", "-C", str(HANDOFF_REPO), "push", "origin", "main"],
    ]
    for cmd in cmds:
        rv = subprocess.run(cmd, env=git_env, capture_output=True, text=True, timeout=60)
        if rv.returncode != 0:
            if "nothing to commit" in (rv.stdout + rv.stderr):
                return True
            log.warning("git step failed: %s\n%s", " ".join(cmd), (rv.stdout + rv.stderr)[:300])
            return False
    return True


def _process_drafts(M):
    rv, _ = M.select('"[Gmail]/Drafts"', readonly=False)
    if rv != "OK":
        log.warning("could not select drafts folder: %s", rv)
        return 0
    rv, data = M.search(None, "ALL")
    if rv != "OK":
        return 0
    ids = data[0].split()
    handled = 0
    for raw_id in ids:
        rv, msg_data = M.fetch(raw_id, "(RFC822)")
        if rv != "OK" or not msg_data or not msg_data[0]:
            continue
        msg = email.message_from_bytes(msg_data[0][1])
        subject = (msg.get("Subject") or "").strip()
        matched_prefix = None
        for prefix in SUBJECT_TO_FILE:
            if subject.startswith(prefix):
                matched_prefix = prefix
                break
        if not matched_prefix:
            continue
        target = SUBJECT_TO_FILE[matched_prefix]
        if target is None:
            log.info("draft '%s' (informational only, no file write)", subject)
            handled += 1
            continue
        body = _decode_payload(msg)
        json_text = _extract_json(body)
        if json_text is None:
            log.warning("draft '%s' had no parseable JSON", subject)
            continue
        try:
            parsed = json.loads(json_text)
        except json.JSONDecodeError as e:
            log.warning("draft '%s' JSON invalid: %s", subject, e)
            continue
        out_path = HANDOFF_REPO / target
        existing_ts = None
        if out_path.exists():
            try:
                existing_ts = json.loads(out_path.read_text()).get("generated_at_utc")
            except Exception:
                existing_ts = None
        new_ts = parsed.get("generated_at_utc") if isinstance(parsed, dict) else None
        if existing_ts and new_ts and new_ts <= existing_ts:
            log.info("draft '%s' is older than current %s (existing %s vs new %s) — skip write, trash", subject, target, existing_ts, new_ts)
        else:
            out_path.write_text(json.dumps(parsed, indent=2, sort_keys=True) + "\n")
            log.info("wrote %s from draft '%s' (%d bytes)", target, subject, out_path.stat().st_size)
            if _git_push():
                log.info("pushed %s to handoff repo", target)
        M.copy(raw_id, '"[Gmail]/Trash"')
        M.store(raw_id, "+FLAGS", r"(\Deleted)")
        handled += 1
    M.expunge()
    return handled


def main():
    M = _connect()
    if not M:
        sys.exit(1)
    try:
        n = _process_drafts(M)
        log.info("processed %d matching drafts", n)
    finally:
        try:
            M.logout()
        except Exception:
            pass


if __name__ == "__main__":
    main()
