#!/opt/anaconda3/envs/binance_env/bin/python
"""Wallpaper daemon: composites from latest trading plots → desktop wallpaper.

Picks the newest plot per rank from local (and optionally server), deduplicates
symbols across all composites so you never see the same chart twice.

Internal display: 4-col grid (3456×2234)
External display: 8-col grid (3840×1080)

Pages rotate every ROTATE_SECONDS; composites regenerate every REFRESH_SECONDS.
"""

import os
import re
import time
import subprocess
from pathlib import Path
from datetime import datetime
from collections import defaultdict
from PIL import Image, ImageDraw, ImageFont
import logging

# ----------------- CONFIG -----------------
HOME = Path.home()
BASE = HOME / "Documents" / "binance"

LOCAL_PLOTS_CRYPTO = BASE / "plots"
LOCAL_PLOTS_STOCKS = BASE / "plots_tradier"

SERVER_HOST = "s1-int"
SERVER_PLOTS_CRYPTO = "/home/niels/binance/plots"
SERVER_PLOTS_STOCKS = "/home/niels/binance/plots_tradier"

OUT_DIR = HOME / "Pictures" / "wallpapers_active"
OUT_INT = OUT_DIR / "int"
OUT_EXT = OUT_DIR / "ext"
OUT_DIR.mkdir(parents=True, exist_ok=True)
OUT_INT.mkdir(parents=True, exist_ok=True)
OUT_EXT.mkdir(parents=True, exist_ok=True)

RES_INT = (3456, 2234)
RES_EXT = (3840, 1080)

REFRESH_SECONDS = 15 * 60
ROTATE_SECONDS = 10
RSYNC_TIMEOUT = 30

LOG_PATH = HOME / "logs" / "wallpaper_daemon.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(filename=str(LOG_PATH), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wallpaper_daemon")

# Regex patterns for plot filenames
# Crypto: W01_SYMBOL.png, WR01_SYMBOL.png, L01_SYMBOL.png, LR01_SYMBOL.png
RE_CRYPTO = re.compile(r'^(WR|LR|W|L)(\d+)_(.+)\.png$')
# Stocks: 01_SYMBOL.png
RE_STOCK = re.compile(r'^(\d+)_(.+)\.png$')

# ----------------- HELPERS -----------------
def run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 9, "", str(e)


def load_font(size=28):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except Exception:
        return ImageFont.load_default()


FONT = load_font(28)


def get_newest_per_rank(directory, prefix_pattern, count=20):
    """Get the newest file per rank for a given prefix. Returns list of Paths sorted by rank."""
    if not directory.exists():
        return []
    best = {}
    for f in directory.glob("*.png"):
        m = RE_CRYPTO.match(f.name)
        if not m:
            continue
        pfx, rank_str, symbol = m.groups()
        if pfx != prefix_pattern:
            continue
        rank = int(rank_str)
        mtime = f.stat().st_mtime
        if rank not in best or mtime > best[rank][1]:
            best[rank] = (f, mtime, symbol)
    return [(info[0], info[2]) for rank, info in sorted(best.items())[:count]]


def get_newest_stocks(directory, count=20):
    """Get newest file per rank for stock plots. Returns (winners, losers) split at rank 20."""
    if not directory.exists():
        return [], []
    best = {}
    for f in directory.glob("*.png"):
        m = RE_STOCK.match(f.name)
        if not m:
            continue
        rank_str, symbol = m.groups()
        rank = int(rank_str)
        mtime = f.stat().st_mtime
        if rank not in best or mtime > best[rank][1]:
            best[rank] = (f, mtime, symbol)
    sorted_all = [(info[0], info[2]) for rank, info in sorted(best.items())]
    # Split: rank 1-20 = winners, rest = losers (by rank order)
    winners = []
    losers = []
    for rank, info in sorted(best.items()):
        entry = (info[0], info[2])
        if rank <= 20:
            winners.append(entry)
        else:
            losers.append(entry)
    return winners[:count], losers[:count]


def pick_unique(candidates, seen_symbols, count):
    """Pick up to `count` items from candidates, skipping symbols already in seen_symbols. Updates seen_symbols."""
    result = []
    for path, symbol in candidates:
        if symbol in seen_symbols:
            continue
        seen_symbols.add(symbol)
        result.append(path)
        if len(result) >= count:
            break
    return result


# ----------------- IMAGE UTIL -----------------
def paste_resized(background, src_path, box_w, box_h, pos):
    try:
        tile = Image.open(src_path).convert("RGB")
        tile = tile.resize((box_w, box_h), Image.Resampling.LANCZOS)
        background.paste(tile, pos)
        return True
    except Exception as e:
        logger.debug(f"paste_resized fail {src_path}: {e}")
        return False


def create_grid_composite(winners, losers, resolution, cols, out_path, label):
    """Create a grid: top row = winners, bottom row = losers."""
    w_res, h_res = resolution
    cell_w = w_res // cols
    cell_h = h_res // 2
    img = Image.new("RGB", (w_res, h_res), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(min(cols, len(winners))):
        paste_resized(img, winners[i], cell_w, cell_h, (i * cell_w, 0))
    for i in range(min(cols, len(losers))):
        paste_resized(img, losers[i], cell_w, cell_h, (i * cell_w, cell_h))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    info = f"{label}  {now_str}"
    bbox = draw.textbbox((0, 0), info, font=FONT)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((w_res - tw - 22, h_res - th - 22), info, fill=(255, 255, 255), font=FONT)
    tmp = str(out_path) + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, str(out_path))
    return True


# ----------------- WALLPAPER SETTER -----------------
_PREV_INT = None
_PREV_EXT = None


def apply_wallpapers(int_img: Path, ext_img: Path):
    """Set wallpaper. macOS caches by path, so we use rotating symlinks with unique names to force refresh."""
    global _PREV_INT, _PREV_EXT
    if not int_img or not ext_img or not int_img.exists() or not ext_img.exists():
        return False
    import shutil
    tick = int(time.time())
    int_link = OUT_DIR / f"int_active_{tick % 2}.png"
    ext_link = OUT_DIR / f"ext_active_{tick % 2}.png"
    shutil.copy2(str(int_img), str(int_link))
    shutil.copy2(str(ext_img), str(ext_link))
    int_path = str(int_link.resolve())
    ext_path = str(ext_link.resolve())
    if _PREV_INT and _PREV_INT != int_link and _PREV_INT.exists():
        try:
            _PREV_INT.unlink()
        except Exception:
            pass
    if _PREV_EXT and _PREV_EXT != ext_link and _PREV_EXT.exists():
        try:
            _PREV_EXT.unlink()
        except Exception:
            pass
    _PREV_INT, _PREV_EXT = int_link, ext_link
    script = f'''tell application "System Events"
    repeat with d in (get every desktop)
        try
            set dName to display name of d
            if dName contains "Built-in" then
                set picture of d to "{int_path}"
            else
                set picture of d to "{ext_path}"
            end if
        end try
    end repeat
end tell'''
    rc, out, err = run(f"osascript -e '{script}'", timeout=5)
    if rc != 0:
        logger.debug(f"System Events wallpaper set failed (rc={rc} err={err})")
        run(f'osascript -e \'tell application "Finder" to set desktop picture to POSIX file "{ext_path}"\'', timeout=3)
    return True


# ----------------- SYNC FROM SERVER -----------------
def sync_from_server():
    """Rsync newer plots from server (incremental, only if newer)."""
    opts = "-az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=10'"
    try:
        run(f"rsync {opts} {SERVER_HOST}:{SERVER_PLOTS_CRYPTO}/*.png {LOCAL_PLOTS_CRYPTO}/", timeout=RSYNC_TIMEOUT)
        run(f"rsync {opts} {SERVER_HOST}:{SERVER_PLOTS_STOCKS}/*.png {LOCAL_PLOTS_STOCKS}/", timeout=RSYNC_TIMEOUT)
        logger.info("Rsync from server complete.")
    except Exception as e:
        logger.debug(f"rsync exception: {e}")


# ----------------- MAIN GENERATION -----------------
def generate_all():
    """Generate all composite pages. Deduplicates symbols across ALL pages."""
    logger.info("Starting generation cycle.")
    seen_symbols = set()
    # Crypto winners/losers by prefix, newest per rank
    wr_cands = get_newest_per_rank(LOCAL_PLOTS_CRYPTO, "WR", 20)
    lr_cands = get_newest_per_rank(LOCAL_PLOTS_CRYPTO, "LR", 20)
    w_cands = get_newest_per_rank(LOCAL_PLOTS_CRYPTO, "W", 20)
    l_cands = get_newest_per_rank(LOCAL_PLOTS_CRYPTO, "L", 20)
    # Stock winners/losers
    stock_w_cands, stock_l_cands = get_newest_stocks(LOCAL_PLOTS_STOCKS, 20)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # === EXT pages (3840x1080, 8 cols) ===
    # EXT P1: Crypto short-term (WR top / LR bottom)
    wr_picks = pick_unique(wr_cands, seen_symbols, 8)
    lr_picks = pick_unique(lr_cands, seen_symbols, 8)
    ext_p1 = OUT_EXT / f"ext_crypto_st_{ts}.png"
    create_grid_composite(wr_picks, lr_picks, RES_EXT, 8, ext_p1, "Crypto ST")
    # EXT P2: Crypto long-term (W top / L bottom) — symbols already in ST are skipped
    w_picks = pick_unique(w_cands, seen_symbols, 8)
    l_picks = pick_unique(l_cands, seen_symbols, 8)
    ext_p2 = OUT_EXT / f"ext_crypto_lt_{ts}.png"
    create_grid_composite(w_picks, l_picks, RES_EXT, 8, ext_p2, "Crypto LT")
    # EXT P3: Stocks (winners top / losers bottom)
    sw_picks = pick_unique(stock_w_cands, seen_symbols, 8)
    sl_picks = pick_unique(stock_l_cands, seen_symbols, 8)
    ext_p3 = OUT_EXT / f"ext_stocks_{ts}.png"
    create_grid_composite(sw_picks, sl_picks, RES_EXT, 8, ext_p3, "Stocks")
    # === INT pages (3456x2234, 4 cols) ===
    # INT P1: Crypto (W top / L bottom — using remaining unique symbols)
    w_int = pick_unique(w_cands, seen_symbols, 4)
    l_int = pick_unique(l_cands, seen_symbols, 4)
    # If not enough unique W/L, pull from WR/LR
    if len(w_int) < 4:
        w_int += pick_unique(wr_cands, seen_symbols, 4 - len(w_int))
    if len(l_int) < 4:
        l_int += pick_unique(lr_cands, seen_symbols, 4 - len(l_int))
    int_p1 = OUT_INT / f"int_crypto_{ts}.png"
    create_grid_composite(w_int, l_int, RES_INT, 4, int_p1, "Crypto")
    # INT P2: Stocks (remaining unique)
    sw_int = pick_unique(stock_w_cands, seen_symbols, 4)
    sl_int = pick_unique(stock_l_cands, seen_symbols, 4)
    int_p2 = OUT_INT / f"int_stocks_{ts}.png"
    create_grid_composite(sw_int, sl_int, RES_INT, 4, int_p2, "Stocks")
    logger.info(f"Generated composites. {len(seen_symbols)} unique symbols used.")
    # Apply first page
    try:
        apply_wallpapers(int_p1, ext_p1)
    except Exception as e:
        logger.error(f"Failed to apply wallpapers: {e}")
    # Cleanup old composites — keep 10 per subdir
    for subdir in [OUT_EXT, OUT_INT]:
        files = sorted(subdir.glob("*.png"), key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[10:]:
            try:
                old.unlink()
            except Exception:
                pass
    return [int_p1, int_p2], [ext_p1, ext_p2, ext_p3]


# ----------------- MAIN LOOP -----------------
def main_loop():
    last_refresh = 0
    rotate_index = 0
    int_pages, ext_pages = [], []
    while True:
        try:
            now = time.time()
            if now - last_refresh > REFRESH_SECONDS:
                sync_from_server()
                int_pages, ext_pages = generate_all()
                last_refresh = now
                logger.info(f"Refreshed: {len(int_pages)} int pages, {len(ext_pages)} ext pages.")
            if int_pages and ext_pages:
                try:
                    int_choice = int_pages[rotate_index % len(int_pages)]
                    ext_choice = ext_pages[rotate_index % len(ext_pages)]
                    if int_choice.exists() and ext_choice.exists():
                        apply_wallpapers(int_choice, ext_choice)
                except Exception as e:
                    logger.debug(f"rotation apply error: {e}")
            rotate_index += 1
            time.sleep(ROTATE_SECONDS)
        except Exception as e:
            logger.exception(f"Main loop exception: {e}")
            time.sleep(10)


if __name__ == "__main__":
    logger.info("Wallpaper daemon starting.")
    # Generate once immediately, then loop
    main_loop()
