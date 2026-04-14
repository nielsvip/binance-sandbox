#!/usr/bin/env python3
# pylint: disable=W,C,R,I
"""YouTube Strategy Scanner — finds traders who share verified trade lists.

Searches YouTube for crypto/stock traders who:
1. Show their actual trade history (screenshots, spreadsheets, broker statements)
2. Share links to Myfxbook, TradingView, or exchange leaderboard profiles
3. Post regular P&L updates with verifiable timestamps

Uses yt-dlp for search (no API key) + requests for page scraping.
Extracts trade data links and trader profile URLs for our pipeline.
"""
import json
import logging
import os
import re
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse, parse_qs

import requests
from bs4 import BeautifulSoup

sys.path.insert(0, str(Path(__file__).resolve().parent))
from config import Config

config = Config()
BASE_PATH = config.BASE_PATH
DATA_DIR = config.DATA_DIR / "youtube_strategies"
DATA_DIR.mkdir(parents=True, exist_ok=True)

logger = logging.getLogger("youtube_scanner")
logger.setLevel(logging.INFO)
if not logger.handlers:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(handler)

FINDINGS_FILE = DATA_DIR / "findings.jsonl"
CHANNELS_FILE = DATA_DIR / "tracked_channels.json"
TRADE_LINKS_FILE = DATA_DIR / "trade_links.jsonl"

# Search queries targeting traders who share verifiable results
SEARCH_QUERIES = [
    # Traders sharing actual trade lists
    "crypto trading results verified trades 2026",
    "my crypto trades this week proof",
    "bybit trading history results",
    "binance futures trading results proof",
    "bitget copy trading results",
    "crypto trading P&L statement",
    "my trading journal entries crypto",
    "live trading account results crypto",
    # Specific exchange leaderboard content
    "bybit copy trading top trader",
    "bitget copy trading best trader 2026",
    "binance leaderboard top trader interview",
    "OKX copy trading best performers",
    # Strategy verification
    "trading strategy backtest real results",
    "crypto scalping results proof trades",
    "futures trading real account profit",
    "day trading crypto real money results",
    # Stock traders with verifiable results
    "stock trading results broker statement",
    "options trading P&L real account",
    "swing trading results verified",
    "tradier account results",
    # Short-form content (often has trade lists)
    "crypto trading results shorts",
    "my trades this month crypto",
]

# Patterns that indicate verifiable trade data
VERIFICATION_PATTERNS = [
    # Exchange profile links
    r"bitget\.com/(?:copy-trading|copytrading)/trader/\w+",
    r"bybit\.com/(?:copy-trading|copytrading)/\w+",
    r"okx\.com/(?:copy-trading|copytrading)/\w+",
    r"binance\.com/(?:en/futures-activity|futures)/leaderboard",
    # Trade tracking platforms
    r"myfxbook\.com/(?:members|portfolio)/\w+",
    r"tradingview\.com/(?:u|chart)/\w+",
    r"(?:3commas|cornix|zignaly)\.com/\w+",
    r"coinstats\.app/\w+",
    # Spreadsheet/doc links with trade data
    r"docs\.google\.com/spreadsheets?/d/[\w-]+",
    r"docs\.google\.com/document/d/[\w-]+",
    r"notion\.so/[\w-]+",
    r"airtable\.com/[\w-]+",
    # Telegram/Discord trade channels
    r"t\.me/[\w]+",
    r"discord\.gg/[\w]+",
]

# Keywords in video titles that suggest verifiable trades
TITLE_KEYWORDS_POSITIVE = [
    "results", "p&l", "pnl", "profit", "trades", "trade history",
    "account statement", "broker statement", "my trades", "real account",
    "proof", "verified", "leaderboard", "copy trading", "trade journal",
    "this week", "this month", "weekly results", "monthly results",
    "live trading", "real money", "no fake",
]

TITLE_KEYWORDS_NEGATIVE = [
    "how to", "tutorial", "course", "paid group", "signal group",
    "guaranteed", "1000x", "millionaire in", "get rich",
    "airdrop", "free money", "giveaway",
]


def _yt_dlp_path() -> str:
    """Find yt-dlp binary."""
    for path in ["/opt/anaconda3/envs/binance_env/bin/yt-dlp", "/usr/local/bin/yt-dlp", "/home/niels/.conda/envs/binance_env/bin/yt-dlp", "/home/niels/miniconda3/envs/binance_env/bin/yt-dlp"]:
        if os.path.exists(path):
            return path
    return "yt-dlp"


def search_youtube(query: str, max_results: int = 20) -> List[Dict]:
    """Search YouTube using yt-dlp and return video metadata."""
    ytdlp = _yt_dlp_path()
    try:
        cmd = [ytdlp, f"ytsearch{max_results}:{query}", "--dump-json", "--flat-playlist", "--no-download", "--no-warnings", "--quiet"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        videos = []
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            try:
                v = json.loads(line)
                videos.append({"video_id": v.get("id", ""), "title": v.get("title", ""), "channel": v.get("channel", v.get("uploader", "")), "channel_id": v.get("channel_id", ""), "upload_date": v.get("upload_date", ""), "duration": v.get("duration", 0), "view_count": v.get("view_count", 0), "url": v.get("webpage_url", v.get("url", "")), "description": v.get("description", "")[:2000]})
            except json.JSONDecodeError:
                continue
        return videos
    except subprocess.TimeoutExpired:
        logger.warning(f"yt-dlp search timed out for: {query}")
        return []
    except Exception as e:
        logger.warning(f"yt-dlp search failed: {e}")
        return []


def get_video_description(video_id: str) -> str:
    """Get full video description (may contain trade links)."""
    ytdlp = _yt_dlp_path()
    try:
        cmd = [ytdlp, f"https://www.youtube.com/watch?v={video_id}", "--dump-json", "--no-download", "--no-warnings", "--quiet"]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.stdout.strip():
            data = json.loads(result.stdout.strip())
            return data.get("description", "")
    except Exception:
        pass
    return ""


def score_video(video: Dict) -> Tuple[float, List[str]]:
    """Score a video for likelihood of containing verifiable trade data.
    Returns (score 0-100, list of reasons)."""
    score = 0.0
    reasons = []
    title = (video.get("title", "") or "").lower()
    desc = (video.get("description", "") or "").lower()
    combined = f"{title} {desc}"
    # Positive title keywords
    for kw in TITLE_KEYWORDS_POSITIVE:
        if kw in title:
            score += 8
            reasons.append(f"title:{kw}")
    # Negative title keywords (spam/scam)
    for kw in TITLE_KEYWORDS_NEGATIVE:
        if kw in title:
            score -= 15
            reasons.append(f"spam:{kw}")
    # Verification links in description
    for pattern in VERIFICATION_PATTERNS:
        matches = re.findall(pattern, combined)
        if matches:
            score += 20
            reasons.append(f"link:{matches[0][:60]}")
    # View count (more views = more credible, but not too much = likely clickbait)
    views = video.get("view_count", 0) or 0
    if 500 < views < 100000:
        score += 5
        reasons.append(f"views:{views}")
    elif views > 500000:
        score -= 5
        reasons.append("viral_suspicious")
    # Recent upload (prefer fresh content)
    upload = video.get("upload_date", "")
    if upload and upload >= "20260101":
        score += 5
        reasons.append("recent_2026")
    # Duration (too short = clickbait, too long = course pitch)
    dur = video.get("duration", 0) or 0
    if 120 < dur < 1800:
        score += 3
    elif dur < 30:
        score -= 5
    # Exchange mentions in description
    for ex in ["bitget", "bybit", "binance", "okx", "kraken"]:
        if ex in combined:
            score += 3
            reasons.append(f"exchange:{ex}")
    # Trade-specific numbers (e.g., "$5,234 profit", "87% win rate")
    pnl_matches = re.findall(r'\$[\d,]+(?:\.\d+)?(?:\s*(?:profit|pnl|p&l|gain))', combined)
    wr_matches = re.findall(r'(\d{2,3})%?\s*(?:win\s*rate|wr|accuracy)', combined)
    if pnl_matches:
        score += 10
        reasons.append(f"pnl_mention:{pnl_matches[0][:30]}")
    if wr_matches:
        score += 5
        reasons.append(f"wr_mention:{wr_matches[0]}%")
    return max(0, min(100, score)), reasons


def extract_trade_links(text: str) -> List[Dict]:
    """Extract verifiable trade/profile links from text."""
    links = []
    for pattern in VERIFICATION_PATTERNS:
        matches = re.findall(pattern, text)
        for m in matches:
            url = m if m.startswith("http") else f"https://{m}"
            link_type = "unknown"
            if "bitget" in m:
                link_type = "bitget_profile"
            elif "bybit" in m:
                link_type = "bybit_profile"
            elif "okx" in m:
                link_type = "okx_profile"
            elif "binance" in m:
                link_type = "binance_leaderboard"
            elif "myfxbook" in m:
                link_type = "myfxbook"
            elif "tradingview" in m:
                link_type = "tradingview"
            elif "google.com/spreadsheet" in m:
                link_type = "google_sheet"
            elif "t.me" in m:
                link_type = "telegram"
            elif "discord" in m:
                link_type = "discord"
            links.append({"url": url, "type": link_type, "raw_match": m})
    # Also extract generic URLs that might be trade platforms
    url_pattern = r'https?://[^\s<>"\']+(?:trade|pnl|result|history|leaderboard|portfolio)[^\s<>"\']*'
    for m in re.findall(url_pattern, text, re.IGNORECASE):
        if not any(l["url"] == m for l in links):
            links.append({"url": m, "type": "potential_trade_url", "raw_match": m[:80]})
    return links


def scan_full_cycle(max_queries: int = 0) -> Dict[str, Any]:
    """Run full YouTube scan cycle. Returns summary dict."""
    start = time.time()
    queries = SEARCH_QUERIES if max_queries <= 0 else SEARCH_QUERIES[:max_queries]
    logger.info(f"Starting YouTube scan: {len(queries)} search queries")
    all_videos = {}
    scored_videos = []
    all_trade_links = []
    for i, query in enumerate(queries):
        logger.info(f"  [{i+1}/{len(queries)}] Searching: {query}")
        videos = search_youtube(query, max_results=15)
        logger.info(f"    Found {len(videos)} results")
        for v in videos:
            vid = v.get("video_id", "")
            if not vid or vid in all_videos:
                continue
            all_videos[vid] = v
            score, reasons = score_video(v)
            v["score"] = score
            v["reasons"] = reasons
            if score >= 25:
                scored_videos.append(v)
        time.sleep(1.5)
    logger.info(f"Unique videos: {len(all_videos)}, scored >=25: {len(scored_videos)}")
    # Deep-scan top scored videos for trade links
    scored_videos.sort(key=lambda x: x["score"], reverse=True)
    top_videos = scored_videos[:50]
    logger.info(f"Deep-scanning top {len(top_videos)} videos for trade links...")
    for i, v in enumerate(top_videos):
        vid = v["video_id"]
        if not v.get("description"):
            logger.info(f"  [{i+1}/{len(top_videos)}] Fetching description for {v['title'][:50]}...")
            v["description"] = get_video_description(vid)
            time.sleep(1)
        links = extract_trade_links(v.get("description", "") + " " + v.get("title", ""))
        if links:
            for link in links:
                link["source_video"] = vid
                link["source_title"] = v.get("title", "")
                link["source_channel"] = v.get("channel", "")
                link["video_score"] = v["score"]
                all_trade_links.append(link)
            v["trade_links"] = links
            logger.info(f"    Found {len(links)} trade links in: {v['title'][:60]}")
    # Save findings
    now = datetime.now(timezone.utc)
    findings = {"timestamp": now.isoformat(), "queries_run": len(queries), "videos_found": len(all_videos), "videos_scored": len(scored_videos), "trade_links_found": len(all_trade_links), "elapsed_seconds": round(time.time() - start), "top_videos": [{"video_id": v["video_id"], "title": v["title"], "channel": v["channel"], "score": v["score"], "reasons": v["reasons"], "trade_links": v.get("trade_links", []), "url": v.get("url", "")} for v in top_videos[:30]]}
    with open(FINDINGS_FILE, "a") as f:
        f.write(json.dumps(findings) + "\n")
    if all_trade_links:
        with open(TRADE_LINKS_FILE, "a") as f:
            for link in all_trade_links:
                link["found_at"] = now.isoformat()
                f.write(json.dumps(link) + "\n")
    # Track channels that consistently produce verifiable content
    channel_scores = {}
    for v in scored_videos:
        ch = v.get("channel", "unknown")
        if ch not in channel_scores:
            channel_scores[ch] = {"name": ch, "channel_id": v.get("channel_id", ""), "videos": 0, "total_score": 0, "trade_links": 0}
        channel_scores[ch]["videos"] += 1
        channel_scores[ch]["total_score"] += v["score"]
        channel_scores[ch]["trade_links"] += len(v.get("trade_links", []))
    top_channels = sorted(channel_scores.values(), key=lambda x: x["total_score"], reverse=True)[:20]
    with open(CHANNELS_FILE, "w") as f:
        json.dump(top_channels, f, indent=2)
    # CSV export of trade links for pipeline
    if all_trade_links:
        csv_path = DATA_DIR / f"trade_links_{now.strftime('%Y%m%d')}.csv"
        import csv
        with open(csv_path, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=["url", "type", "source_video", "source_title", "source_channel", "video_score"])
            writer.writeheader()
            for link in all_trade_links:
                writer.writerow({k: link.get(k, "") for k in writer.fieldnames})
        logger.info(f"Trade links CSV: {csv_path}")
    logger.info(f"YouTube scan complete in {time.time()-start:.0f}s — {len(all_trade_links)} trade links from {len(scored_videos)} scored videos")
    return findings


def show_latest():
    """Show latest scan findings."""
    if not FINDINGS_FILE.exists():
        print("No findings yet. Run a scan first.")
        return
    with open(FINDINGS_FILE) as f:
        lines = f.readlines()
    if lines:
        latest = json.loads(lines[-1])
        print(f"\n{'='*60}")
        print(f"  YouTube Strategy Scan — {latest['timestamp']}")
        print(f"{'='*60}")
        print(f"  Queries: {latest['queries_run']} | Videos: {latest['videos_found']} | Scored: {latest['videos_scored']} | Trade Links: {latest['trade_links_found']}")
        print(f"\n  Top Videos:")
        for v in latest.get("top_videos", [])[:10]:
            links_str = f" [{len(v.get('trade_links', []))} links]" if v.get("trade_links") else ""
            print(f"    [{v['score']:4.0f}] {v['title'][:60]} — {v['channel']}{links_str}")
        print(f"{'='*60}\n")


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="YouTube Strategy Scanner")
    parser.add_argument("--scan", action="store_true", help="Run full scan cycle")
    parser.add_argument("--latest", action="store_true", help="Show latest findings")
    parser.add_argument("--queries", type=int, default=0, help="Limit number of search queries (0=all)")
    args = parser.parse_args()
    if args.latest:
        show_latest()
    else:
        scan_full_cycle(max_queries=args.queries)
