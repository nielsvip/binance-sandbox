#!/usr/bin/env python3
"""
export_conversations.py — Export all Claude Code conversation history to structured markdown.
Auto-detects MacBook vs server environment.

Produces in memory/conversations/:
  - session_<date>_<uuid_short>.md   (one per session)
  - INDEX.md                          (master index)
  - FULL_KNOWLEDGE_BASE.md            (compact per-session first-msg reference)
  - SCRIPT_STATE.md                   (current state per .py file)
  - TOPIC_STATE.md                    (current state per topic/system area)

Run: python3 export_conversations.py [--summarize]
  --summarize  uses Claude API to generate a 3-line summary per session (needs ANTHROPIC_API_KEY)
"""
import json
import os
import platform
import sys
import re
from pathlib import Path
from datetime import datetime, timezone
from collections import defaultdict

def detect_env():
    home = Path.home()
    # MacBook
    macbook_sessions = home / ".claude/projects/-Users-niels-Documents-binance"
    macbook_out = Path("/Users/niels/Documents/binance/memory/conversations")
    # Server
    server_sessions = home / ".claude/projects/-home-niels-binance"
    server_out = Path("/home/niels/binance/memory/conversations")
    if macbook_sessions.exists():
        return macbook_sessions, macbook_out
    if server_sessions.exists():
        return server_sessions, server_out
    # Fallback: search for any binance project directory
    claude_projects = home / ".claude/projects"
    if claude_projects.exists():
        candidates = [d for d in claude_projects.iterdir() if "binance" in d.name.lower() and d.is_dir()]
        if candidates:
            sessions_dir = max(candidates, key=lambda d: sum(1 for _ in d.glob("*.jsonl")))
            out_dir = Path(str(sessions_dir).replace(".claude/projects/", "binance/").replace("-", "/").lstrip("/"))
            out_dir = Path("/") / out_dir / "memory/conversations"
            # Simpler: just put output next to this script
            out_dir = Path(__file__).parent / "memory/conversations"
            return sessions_dir, out_dir
    print("ERROR: No Claude binance project directory found under ~/.claude/projects/")
    sys.exit(1)

SESSIONS_DIR, OUT_DIR = detect_env()
print(f"Sessions: {SESSIONS_DIR}")
print(f"Output:   {OUT_DIR}")
OUT_DIR.mkdir(parents=True, exist_ok=True)

SUMMARIZE = "--summarize" in sys.argv

# Known scripts to track state for
KNOWN_SCRIPTS = [
    "ez_manage.py", "ez_positions_quick.py", "ez_positions_service.py",
    "ez_rankings.py", "ez_indicators.py", "ez_market_data.py",
    "ez_prices.py", "ez_klines.py", "ez_gain_protector.py",
    "ez_gap_filler.py", "ez_crosses.py", "ez_double.py",
    "ez_news_scanner.py", "ez_mark_prices.py", "ez_share_ind.py",
    "tradier_manage.py", "tradier_indicators.py", "tradier_rankings.py",
    "tradier_positions.py", "tradier_api.py", "tradier_prices.py",
    "ez_positions.py", "config.py", "utils.py", "trade_analytics.py",
    "ez_positions_realtime.py",
]

# Topic keywords → topic name
TOPIC_KEYWORDS = {
    "hedge": ["HEDGE", "hedge_engine", "HedgeEngine", "HEDGE_MODE", "HEDGE_ACCOUNTS", "get_hottest_hedge", "hedge_candidate", "HEDGE_POSTMORTEM"],
    "staleness": ["STALE", "stale_indicator", "STALE_INDICATORS", "tick_ts", "_tick_ts", "staleness", "hot_metrics", "bridge", "get_hot_state"],
    "ratio": ["RATIO_RECOVERY", "ratio_recovery", "L/S ratio", "long_short_ratio", "rebalance", "RATIO_REBALANCE"],
    "positions": ["position_key", "parse_position_key", "pk_is_long", "pk_is_short", "positionAmt", "STRICT_NO_LOSS", "position dict"],
    "klines": ["klines_cache", "resample", "tradier_indicators", "kline", "OHLCV", "1800 klines"],
    "orders": ["verify_trade", "handle_filled_maker", "maker", "webhook", "execution lock", "MAKER_AUG_TIMEOUT"],
    "pnl_jsonl": ["JSONL", "_append_to_history", "dedup", "duplicate", "trade_analytics", "PnL"],
    "redis": ["Redis", "redis", "price_cache", "hot_metrics", "inter-service"],
    "news_scanner": ["news_scanner", "CoinGecko", "Finnhub", "sentiment", "F&G", "Fear", "RSS"],
    "scalp": ["scalp", "SCALP", "BASIS_CONDITION", "aug", "augment", "transition_to_entry"],
}


def extract_text(content):
    """Extract readable text from message content (str or list of blocks)."""
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if not isinstance(block, dict):
                continue
            btype = block.get("type", "")
            if btype == "text":
                parts.append(block.get("text", "").strip())
            elif btype == "tool_use":
                name = block.get("name", "?")
                inp = block.get("input", {})
                if name in ("Bash", "Edit", "Write"):
                    cmd = inp.get("command") or inp.get("file_path") or ""
                    parts.append(f"[Tool:{name} {cmd[:80]}]")
                else:
                    parts.append(f"[Tool:{name}]")
        return "\n".join(p for p in parts if p).strip()
    return ""


def load_session(path: Path):
    """Load a session JSONL and return list of (role, text, timestamp) tuples."""
    messages = []
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            mtype = obj.get("type")
            if mtype not in ("user", "assistant"):
                continue
            msg = obj.get("message", {})
            role = msg.get("role", mtype)
            content = msg.get("content", "")
            text = extract_text(content)
            ts = obj.get("timestamp", "")
            if text:
                messages.append((role, text, ts))
    return messages


def session_start_time(messages):
    for _, _, ts in messages:
        if ts:
            try:
                return datetime.fromisoformat(ts.replace("Z", "+00:00"))
            except Exception:
                pass
    return datetime.now(timezone.utc)


def slugify_first_user_msg(messages):
    for role, text, _ in messages:
        if role == "user":
            slug = re.sub(r"[^\w\s-]", "", text[:60]).strip()
            slug = re.sub(r"\s+", "_", slug)[:50]
            return slug
    return "no_title"


def make_summary_with_api(messages):
    """Call Claude API for a 3-bullet summary. Requires ANTHROPIC_API_KEY."""
    try:
        import anthropic
        client = anthropic.Anthropic()
        conv_text = "\n\n".join(f"**{role.upper()}**: {text[:500]}" for role, text, _ in messages[:40])
        resp = client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            messages=[{"role": "user", "content": ("Summarize this Claude Code session in 3 bullet points. Focus on: what was fixed/changed, what files were edited, any rules/decisions made. Be specific (include file names, line numbers if mentioned). Output ONLY the 3 bullets, no preamble.\n\n" + conv_text)}]
        )
        return resp.content[0].text.strip()
    except Exception as e:
        return f"(summary unavailable: {e})"


def write_session_md(path: Path, session_id: str, messages, summary):
    start = session_start_time(messages)
    date_str = start.strftime("%Y-%m-%d %H:%M UTC")
    slug = slugify_first_user_msg(messages)
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"# Session: {slug}\n")
        f.write(f"**Date**: {date_str}  \n")
        f.write(f"**Session ID**: `{session_id}`  \n")
        f.write(f"**Messages**: {len(messages)}\n\n")
        if summary:
            f.write("## Auto-Summary\n")
            f.write(summary + "\n\n")
        f.write("## Conversation\n\n")
        for role, text, ts in messages:
            short_ts = ts[11:16] if len(ts) >= 16 else ""
            label = f"**{'USER' if role == 'user' else 'CLAUDE'}**"
            if short_ts:
                label += f" _{short_ts}_"
            f.write(f"{label}\n\n")
            if role == "assistant" and len(text) > 1500:
                text = text[:1500] + "\n... [truncated]"
            f.write(text + "\n\n---\n\n")


def is_tool_noise(text):
    """Return True if the message is mostly tool calls with no real content."""
    tool_calls = len(re.findall(r"\[Tool:", text))
    non_tool_chars = re.sub(r"\[Tool:[^\]]+\]", "", text).strip()
    return tool_calls > 0 and len(non_tool_chars) < 80


def extract_script_mentions(all_messages):
    """
    Returns dict: script_name -> list of (date_str, role, excerpt, session_short_id)
    Focuses on meaningful text content, skips pure tool-call messages.
    """
    mentions = defaultdict(list)
    for date_str, session_id, role, text in all_messages:
        if is_tool_noise(text):
            continue
        for script in KNOWN_SCRIPTS:
            if script not in text:
                continue
            idx = text.find(script)
            start = max(0, idx - 120)
            end = min(len(text), idx + 280)
            excerpt = text[start:end].replace("\n", " ").strip()
            # Skip excerpts that are still mostly tool-call refs
            if excerpt.count("[Tool:") > 2:
                continue
            mentions[script].append((date_str, role, excerpt, session_id[:8]))
    return mentions


def extract_topic_mentions(all_messages):
    """
    Returns dict: topic_name -> list of (date_str, role, excerpt, session_short_id)
    """
    mentions = defaultdict(list)
    for date_str, session_id, role, text in all_messages:
        if is_tool_noise(text):
            continue
        for topic, keywords in TOPIC_KEYWORDS.items():
            if not any(kw in text for kw in keywords):
                continue
            for kw in keywords:
                if kw in text:
                    idx = text.find(kw)
                    start = max(0, idx - 80)
                    end = min(len(text), idx + 220)
                    excerpt = text[start:end].replace("\n", " ").strip()
                    if excerpt.count("[Tool:") <= 1:
                        mentions[topic].append((date_str, role, excerpt, session_id[:8]))
                    break
    return mentions


def condense_mentions(mentions_list, max_entries=20):
    """
    Keep the most recent N mentions, dedup near-identical excerpts.
    Returns condensed list sorted by date descending.
    """
    # Sort by date desc
    sorted_m = sorted(mentions_list, key=lambda x: x[0], reverse=True)
    # Dedup: skip if excerpt is 80% substring of an already-included one
    seen = []
    out = []
    for item in sorted_m:
        excerpt = item[2]
        key = re.sub(r"\s+", " ", excerpt[:80]).lower()
        if any(key in s or s in key for s in seen):
            continue
        seen.append(key)
        out.append(item)
        if len(out) >= max_entries:
            break
    return out


def write_script_state(script_mentions, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Script State — Current Knowledge Per File\n\n")
        f.write("_Auto-generated from all conversation history. Most recent activity shown first._\n\n")
        f.write("---\n\n")
        for script in KNOWN_SCRIPTS:
            mentions = script_mentions.get(script, [])
            if not mentions:
                continue
            condensed = condense_mentions(mentions)
            f.write(f"## {script}\n\n")
            f.write(f"_Mentioned in {len(mentions)} conversation messages. Showing {len(condensed)} most recent/distinct:_\n\n")
            for date_str, role, excerpt, sid in condensed:
                label = "USER" if role == "user" else "CLAUDE"
                f.write(f"**{date_str} [{label} `{sid}`]**\n> {excerpt[:300]}\n\n")
            f.write("---\n\n")


def write_topic_state(topic_mentions, out_path: Path):
    with open(out_path, "w", encoding="utf-8") as f:
        f.write("# Topic State — Current Knowledge Per System Area\n\n")
        f.write("_Auto-generated from all conversation history. Most recent activity shown first._\n\n")
        f.write("---\n\n")
        for topic in TOPIC_KEYWORDS:
            mentions = topic_mentions.get(topic, [])
            if not mentions:
                continue
            condensed = condense_mentions(mentions, max_entries=15)
            f.write(f"## {topic}\n\n")
            f.write(f"_Found in {len(mentions)} messages. Showing {len(condensed)} most recent/distinct:_\n\n")
            for date_str, role, excerpt, sid in condensed:
                label = "USER" if role == "user" else "CLAUDE"
                f.write(f"**{date_str} [{label} `{sid}`]**\n> {excerpt[:300]}\n\n")
            f.write("---\n\n")


def main():
    jsonl_files = sorted(SESSIONS_DIR.glob("*.jsonl"))
    print(f"Found {len(jsonl_files)} session files")

    session_meta = []
    all_flat_messages = []  # (date_str, session_id, role, text) for digest

    for jf in jsonl_files:
        session_id = jf.stem
        messages = load_session(jf)
        if not messages:
            continue
        start = session_start_time(messages)
        date_slug = start.strftime("%Y%m%d_%H%M")
        date_str = start.strftime("%Y-%m-%d %H:%M UTC")
        short_id = session_id[:8]
        out_name = f"session_{date_slug}_{short_id}.md"
        out_path = OUT_DIR / out_name
        session_meta.append((start, session_id, out_path, messages))
        for role, text, _ in messages:
            all_flat_messages.append((date_str, session_id, role, text))

    session_meta.sort(key=lambda x: x[0])
    print(f"Processing {len(session_meta)} sessions ({len(all_flat_messages)} total messages)...")

    all_summaries = []

    for i, (start, session_id, out_path, messages) in enumerate(session_meta):
        print(f"  [{i+1}/{len(session_meta)}] {out_path.name} ({len(messages)} msgs)")
        summary = make_summary_with_api(messages) if SUMMARIZE else None
        write_session_md(out_path, session_id, messages, summary)
        slug = slugify_first_user_msg(messages)
        first_user = next((text for role, text, _ in messages if role == "user"), "")
        all_summaries.append((start, session_id, out_path.name, slug, len(messages), summary, first_user))

    # INDEX.md
    index_path = OUT_DIR / "INDEX.md"
    with open(index_path, "w", encoding="utf-8") as f:
        f.write("# Conversation Index\n\n")
        f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n")
        f.write(f"Total sessions: {len(all_summaries)}\n\n")
        f.write("| Date | Session | Topic | Msgs | File |\n")
        f.write("|------|---------|-------|------|------|\n")
        for start, sid, fname, slug, nmsg, _, _fu in all_summaries:
            date_str = start.strftime("%Y-%m-%d")
            f.write(f"| {date_str} | `{sid[:8]}` | {slug[:50]} | {nmsg} | [{fname}]({fname}) |\n")
    print(f"Written: {index_path}")

    # FULL_KNOWLEDGE_BASE.md
    kb_path = OUT_DIR / "FULL_KNOWLEDGE_BASE.md"
    with open(kb_path, "w", encoding="utf-8") as f:
        f.write("# Full Knowledge Base — All Sessions\n\n")
        f.write(f"Generated: {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}  \n")
        f.write(f"Sessions: {len(all_summaries)}\n\n---\n\n")
        for start, sid, fname, slug, nmsg, summary, first_user in all_summaries:
            date_str = start.strftime("%Y-%m-%d %H:%M UTC")
            f.write(f"## {date_str} — {slug}\n\nSession: `{sid[:8]}` | {nmsg} messages | File: `{fname}`\n\n")
            if summary:
                f.write("**Summary:**\n" + summary + "\n\n")
            if first_user:
                preview = first_user[:400].replace("\n", " ")
                f.write(f"**First message:** {preview}\n\n")
            f.write("---\n\n")
    print(f"Written: {kb_path}")

    # SCRIPT_STATE.md
    print("Building script state digest...")
    script_mentions = extract_script_mentions(all_flat_messages)
    script_state_path = OUT_DIR / "SCRIPT_STATE.md"
    write_script_state(script_mentions, script_state_path)
    print(f"Written: {script_state_path}")

    # TOPIC_STATE.md
    print("Building topic state digest...")
    topic_mentions = extract_topic_mentions(all_flat_messages)
    topic_state_path = OUT_DIR / "TOPIC_STATE.md"
    write_topic_state(topic_mentions, topic_state_path)
    print(f"Written: {topic_state_path}")

    print(f"\nDone. Output: {OUT_DIR}/")
    print("  INDEX.md                  — session index table")
    print("  FULL_KNOWLEDGE_BASE.md    — compact per-session reference")
    print("  SCRIPT_STATE.md           — current state per .py file")
    print("  TOPIC_STATE.md            — current state per topic/system area")
    print("  session_*.md              — full per-session transcripts")
    if not SUMMARIZE:
        print("\nTip: run with --summarize to add AI-generated summaries (needs ANTHROPIC_API_KEY)")


if __name__ == "__main__":
    main()
