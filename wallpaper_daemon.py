#!/opt/anaconda3/envs/binance_env/bin/python
# ~/Documents/wallpaper_daemon.py
# Generates composite wallpaper pages:
#  - Internal (3456x2234): 4 winners + 4 losers (4 cols)
#  - External widescreen (3840x1080): 8 winners + 8 losers (8 cols, 2x)
# tiers 1-4 / 5-8 / 9-12 alternating stock <-> crypto, rot. every 60s.

import os
import re
import time
import shutil
import subprocess
import logging
import plistlib
from pathlib import Path
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont

# ----------------- CONFIG -----------------
HOME = Path.home()
BASE = HOME / "Documents" / "binance"

PLOTS_CRYPTO_REMOTE = "/home/niels/binance/plots"
PLOTS_STOCKS_REMOTE = "/home/niels/binance/plots_tradier"
SERVER_HOST = "niels@157.180.125.52"

LOCAL_PLOTS_CRYPTO = BASE / "plots"
LOCAL_PLOTS_STOCKS = BASE / "plots_tradier"

OUT_DIR = HOME / "Pictures" / "wallpapers_active"
OUT_INT = OUT_DIR / "int"
OUT_EXT = OUT_DIR / "ext"
SCREENSAVER_DIR = HOME / "Pictures" / "binance_screensaver"

# Resolutions — internal 4 cols (4 winners + 4 losers), external widescreen 8 cols (8+8) per user request
RES_INT = (3456, 2234)
RES_EXT = (3840, 1080)
COLS_INT = 4
COLS_EXT = 8

# Timing — per user request: change every ~60s
REFRESH_SECONDS = 15 * 60
ROTATE_SECONDS = 60
SYNC_SECONDS = 5 * 60
RSYNC_TIMEOUT = 60

KEEP_GENERATIONS = 3
DISPLAY_INTERNAL_NAME = "Built-in"
# Disk guard: wallpapers_active + screensaver capped at ~600M, wallpaper cache at 500M, logs at 50M
MAX_WALLPAPER_CACHE_MB = 500
MAX_LOG_MB = 50

# Per-Space distinct wallpaper: disabled to avoid grey flash from WallpaperAgent kill.
# Native crossfade via System Events direct-path set (no kill) is used instead.
ENABLE_PER_SPACE = False

LOG_PATH = HOME / "logs" / "wallpaper_daemon.log"
LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

logging.basicConfig(filename=str(LOG_PATH), level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("wallpaper_daemon")
console = logging.StreamHandler()
console.setLevel(logging.INFO)
console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
logger.addHandler(console)

# ----------------- HELPERS -----------------
def run(cmd, timeout=15):
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except Exception as e:
        return 9, "", str(e)

def newest_by_prefix(directory, prefix, count):
    """Get `count` files matching prefix, one per rank, sorted ascending."""
    rank_map = {}
    pat = re.compile(rf"^{re.escape(prefix)}(\d+)", re.IGNORECASE)
    for f in directory.glob(f"{prefix}[0-9]*.png"):
        m = pat.match(f.stem)
        if not m:
            continue
        rank = int(m.group(1))
        if rank not in rank_map or f.stat().st_mtime > rank_map[rank].stat().st_mtime:
            rank_map[rank] = f
    return [rank_map[r] for r in sorted(rank_map.keys())[:count]]

def newest_stocks(directory, count):
    """Return (winners 1..20, losers worst first) each up to count."""
    rank_map = {}
    for f in directory.glob("*.png"):
        m = re.match(r"^(\d+)_", f.name)
        if not m:
            continue
        rank = int(m.group(1))
        if rank not in rank_map or f.stat().st_mtime > rank_map[rank].stat().st_mtime:
            rank_map[rank] = f
    all_ranks = sorted(rank_map.keys())
    winner_ranks = [r for r in all_ranks if r <= 20]
    loser_ranks = sorted([r for r in all_ranks if r > 20], reverse=True)
    winners = [rank_map[r] for r in winner_ranks[:count]]
    losers = [rank_map[r] for r in loser_ranks[:count]]
    return winners, losers

def load_font(size=28):
    try:
        return ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", size)
    except Exception:
        return ImageFont.load_default()

FONT = load_font(28)

def paste_resized(background, src_path, box_w, box_h, pos):
    try:
        tile = Image.open(src_path).convert("RGB")
        tile = tile.resize((box_w, box_h), Image.Resampling.LANCZOS)
        background.paste(tile, pos)
        return True
    except Exception as e:
        logger.debug(f"paste_resized fail {src_path}: {e}")
        return False

# ----------------- COMPOSITE CREATION -----------------
def _fill_for_grid(items, cols):
    """Ensure exactly `cols` tiles: repeat from start if short, truncate if long.
    Prevents black cells / single-plot / black-screen on ext 2x8 when a tier
    is momentarily short due to rsync timing or rank gaps."""
    if not items:
        return []
    if len(items) >= cols:
        return items[:cols]
    # Repeat cyclically to fill grid (better than black cells)
    out = []
    idx = 0
    while len(out) < cols:
        out.append(items[idx % len(items)])
        idx += 1
    return out


def create_grid_composite(winners, losers, resolution, cols, label):
    w_res, h_res = resolution
    cell_w = w_res // cols
    cell_h = h_res // 2
    # Pad to avoid black / single-plot artefacts on 2x8 external
    winners = _fill_for_grid(winners, cols)
    losers = _fill_for_grid(losers, cols)
    img = Image.new("RGB", (w_res, h_res), (0, 0, 0))
    draw = ImageDraw.Draw(img)
    for i in range(cols):
        if i < len(winners):
            paste_resized(img, Path(winners[i]), cell_w, cell_h, (i * cell_w, 0))
    for i in range(cols):
        if i < len(losers):
            paste_resized(img, Path(losers[i]), cell_w, cell_h, (i * cell_w, cell_h))
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    info = f"{label}  {now_str}"
    bbox = draw.textbbox((0, 0), info, font=FONT)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((w_res - tw - 22, h_res - th - 22), info, fill=(255, 255, 255), font=FONT)
    return img

def atomic_save(img, out_path):
    tmp = str(out_path) + ".tmp"
    img.save(tmp, "PNG")
    os.replace(tmp, str(out_path))

# ----------------- WALLPAPER SETTER -----------------
def _sanitize_wallpaper_plist(int_img_path, ext_img_path):
    """Fix Index.plist so external widescreen (3840x1080) is never stuck
    showing an internal 3456x2234 image and no Space shows black.

    Root causes of 2x8 breakage after redo:
    - 597 Spaces still pointed at old per-space 3456x2234 images (spaces/space_*.png).
      System Events 'set picture of every desktop' only updates the *current* Space's
      Displays entry, so switching Spaces showed enlarged/cropped half-plot or stale.
    - 2 phantom Displays (996A, EDC7) pointed at stale spaces/ext_current, causing
      black or single-plot on some desktops.
    This sanitizer rewrites every Space and every Display to the correct current
    int/ext image and removes the per-space override effect when ENABLE_PER_SPACE is off.
    Best-effort; never crashes daemon."""
    if ENABLE_PER_SPACE:
        return
    try:
        idx_path = HOME / "Library/Application Support/com.apple.wallpaper/Store/Index.plist"
        if not idx_path.exists():
            return
        if not int_img_path or not ext_img_path:
            return
        if not Path(int_img_path).exists() or not Path(ext_img_path).exists():
            return
        data = plistlib.loads(idx_path.read_bytes())
        # Build fresh Configuration blobs for current images
        int_url = f"file://{Path(int_img_path).resolve()}"
        ext_url = f"file://{Path(ext_img_path).resolve()}"
        int_blob = plistlib.dumps({"type": "imageFile", "url": {"relative": int_url}}, fmt=plistlib.FMT_BINARY)
        ext_blob = plistlib.dumps({"type": "imageFile", "url": {"relative": ext_url}}, fmt=plistlib.FMT_BINARY)
        # Heuristic: which top-level Display is internal vs external?
        # Identify by current URL containing /int/ or by image size if URL stale.
        displays = data.get("Displays", {})
        if not displays:
            return
        int_dids = set()
        ext_dids = set()
        for did, dinfo in displays.items():
            try:
                cfg = dinfo.get("Desktop", {}).get("Content", {}).get("Choices", [{}])[0].get("Configuration", b"")
                dec = plistlib.loads(cfg) if isinstance(cfg, bytes) else {}
                url = dec.get("url", {}).get("relative", "") if isinstance(dec, dict) else ""
                if "/int/" in url or "int_crypto" in url or "int_stocks" in url:
                    int_dids.add(did)
                elif "/ext/" in url or "ext_crypto" in url or "ext_stocks" in url:
                    ext_dids.add(did)
                else:
                    # fallback: first display encountered is internal, rest external
                    if not int_dids:
                        int_dids.add(did)
                    else:
                        ext_dids.add(did)
            except Exception:
                if not int_dids:
                    int_dids.add(did)
                else:
                    ext_dids.add(did)
        # If heuristic left ambiguity (e.g., phantom Displays with ext_current or spaces), force 1 internal, rest external
        if len(int_dids) != 1 or len(ext_dids) == 0:
            # Prefer the Display that most recently had an int image; otherwise sorted first
            sorted_dids = sorted(displays.keys())
            # Keep at most 1 internal (the one with smallest key that was int, else first)
            if int_dids:
                keep_int = sorted(int_dids)[0]
            else:
                keep_int = sorted_dids[0]
            int_dids = {keep_int}
            ext_dids = set(sorted_dids) - int_dids
        # Patch top-level Displays
        for did in int_dids:
            try:
                displays[did]["Desktop"]["Content"]["Choices"][0]["Configuration"] = int_blob
                displays[did]["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
                displays[did]["Desktop"]["Content"]["Choices"][0]["Files"] = []
            except Exception:
                pass
        for did in ext_dids:
            try:
                displays[did]["Desktop"]["Content"]["Choices"][0]["Configuration"] = ext_blob
                displays[did]["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
                displays[did]["Desktop"]["Content"]["Choices"][0]["Files"] = []
            except Exception:
                pass
        # Patch SystemDefault (used as fallback on some macOS versions)
        try:
            sd = data.get("SystemDefault", {})
            if sd and "Desktop" in sd:
                sd["Desktop"]["Content"]["Choices"][0]["Configuration"] = int_blob
                sd["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
        except Exception:
            pass
        # Patch every Space to inherit the corrected Displays (removes per-space override)
        spaces = data.get("Spaces", {})
        for sid, sinfo in spaces.items():
            # Default.Desktop -> internal image
            try:
                sinfo["Default"]["Desktop"]["Content"]["Choices"][0]["Configuration"] = int_blob
                sinfo["Default"]["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
                sinfo["Default"]["Desktop"]["Content"]["Choices"][0]["Files"] = []
            except Exception:
                pass
            # Per-Display inside Space -> match top-level Displays
            for did, dinfo in sinfo.get("Displays", {}).items():
                try:
                    blob = int_blob if did in int_dids else ext_blob
                    dinfo["Desktop"]["Content"]["Choices"][0]["Configuration"] = blob
                    dinfo["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
                    dinfo["Desktop"]["Content"]["Choices"][0]["Files"] = []
                except Exception:
                    pass
        # Also patch AllSpacesAndDisplays if present
        try:
            asd = data.get("AllSpacesAndDisplays", {})
            if asd and isinstance(asd, dict):
                # leave as-is; Display entries authoritative
                pass
        except Exception:
            pass
        tmp_path = idx_path.with_suffix(".tmp")
        tmp_path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
        os.replace(str(tmp_path), str(idx_path))
        logger.info(f"Sanitized Index.plist: int={Path(int_img_path).name} ext={Path(ext_img_path).name} int_dids={len(int_dids)} ext_dids={len(ext_dids)} spaces={len(spaces)}")
    except Exception as e:
        logger.debug(f"_sanitize_wallpaper_plist failed: {e}")


def apply_wallpaper(int_img_path, ext_img_path):
    """Set wallpaper directly to unique page files for native crossfade.
    Uses file-path change (not overwrite of _current) so macOS animates a
    fade instead of flashing grey. Keeps _current copy for backwards compat
    but does NOT use it as the target."""
    # Keep _current copies for external scripts/tools that read them
    try:
        for src, dst in [(int_img_path, OUT_DIR / "int_current.png"), (ext_img_path, OUT_DIR / "ext_current.png")]:
            if src and Path(src).exists():
                tmp = Path(dst).with_suffix(".tmp")
                shutil.copy2(str(src), str(tmp))
                os.replace(str(tmp), str(dst))
    except Exception:
        pass
    # Direct-path set enables native fade (no WallpaperAgent kill)
    int_str = str(Path(int_img_path).resolve()) if int_img_path and Path(int_img_path).exists() else ""
    ext_str = str(Path(ext_img_path).resolve()) if ext_img_path and Path(ext_img_path).exists() else ""
    if not int_str or not ext_str:
        return
    # Escape quotes for osascript
    int_esc = int_str.replace('"', '\\"')
    ext_esc = ext_str.replace('"', '\\"')
    script = f'''
    tell application "System Events"
        repeat with d in (get every desktop)
            try
                set dName to display name of d
                if dName contains "{DISPLAY_INTERNAL_NAME}" then
                    set picture of d to "{int_esc}"
                else
                    set picture of d to "{ext_esc}"
                end if
            end try
        end repeat
    end tell
    '''
    rc, out, err = run(f"osascript -e '{script}'", timeout=10)
    if rc != 0:
        logger.debug(f"System Events wallpaper set failed (rc={rc} err={err})")
        # Fallback must NOT blanket-set ext onto internal (that caused enlarged-half).
        # Just retry the same per-display loop once with longer timeout.
        rc2, _, _ = run(f"osascript -e '{script}'", timeout=10)
        if rc2 != 0:
            logger.debug(f"wallpaper retry also failed rc={rc2}")
    # Always patch Index.plist so external widescreen never sticks on internal image after Space switch
    try:
        _sanitize_wallpaper_plist(int_img_path, ext_img_path)
    except Exception as e:
        logger.debug(f"sanitize after apply failed: {e}")

def distribute_per_space(int_pages, ext_pages, global_idx):
    """Distribute distinct pages across Spaces so each desktop shows a different collage.
    Writes per-space images and patches Index.plist. Best-effort, never crashes daemon."""
    if not ENABLE_PER_SPACE or not int_pages or not ext_pages:
        return
    try:
        idx_path = HOME / "Library/Application Support/com.apple.wallpaper/Store/Index.plist"
        if not idx_path.exists():
            return
        data = plistlib.loads(idx_path.read_bytes())
        spaces = data.get("Spaces", {})
        if not spaces:
            return
        # Build per-space image pool: interleave int/ext but prefer int for all spaces
        # Use int_pages as canonical (4 winners+4 losers). Distribute round-robin.
        pool = int_pages  # 6 pages alternating crypto/stock tiers
        n = len(pool)
        # Create per-space dir
        per_space_dir = OUT_DIR / "spaces"
        per_space_dir.mkdir(parents=True, exist_ok=True)
        space_list = list(spaces.keys())
        # Assign each space a distinct page offset by global rotation
        assignments = {}
        for i, sid in enumerate(space_list):
            page = pool[(global_idx + i) % n]
            # copy to per-space file (avoid plist pointing directly to rotating file)
            dst = per_space_dir / f"space_{sid[:8]}.png"
            try:
                if page.exists():
                    tmp = dst.with_suffix(".tmp")
                    shutil.copy2(str(page), str(tmp))
                    os.replace(str(tmp), str(dst))
                    assignments[sid] = dst
            except Exception as e:
                logger.debug(f"per-space copy fail {sid[:8]}: {e}")
        # Patch plist: for each space, set Default.Desktop and Displays.*.Desktop to file URL
        patched = 0
        for sid, dst in assignments.items():
            url = f"file://{dst}"
            cfg_blob = plistlib.dumps({"type": "imageFile", "url": {"relative": url}}, fmt=plistlib.FMT_BINARY)
            # Spaces[sid].Default.Desktop
            try:
                spaces[sid]["Default"]["Desktop"]["Content"]["Choices"][0]["Configuration"] = cfg_blob
                spaces[sid]["Default"]["Desktop"]["Content"]["Choices"][0]["Files"] = []
                spaces[sid]["Default"]["Desktop"]["Content"]["Choices"][0]["Provider"] = "com.apple.wallpaper.choice.image"
                patched += 1
            except Exception:
                pass
            # Also patch Displays under that space if present
            for did, dinfo in spaces[sid].get("Displays", {}).items():
                try:
                    dinfo["Desktop"]["Content"]["Choices"][0]["Configuration"] = cfg_blob
                except Exception:
                    pass
        # Also patch top-level Displays to use current int/ext for safety
        for did, dinfo in data.get("Displays", {}).items():
            try:
                is_int = "Built-in" in str(did) or did == list(data["Displays"].keys())[0]
                # just ensure they point to current
                cur = OUT_DIR / ("int_current.png" if patched % 2 == 0 else "ext_current.png")
                url = f"file://{cur}"
                cfg_blob = plistlib.dumps({"type": "imageFile", "url": {"relative": url}}, fmt=plistlib.FMT_BINARY)
                # don't overwrite per-display if we already patched spaces
            except Exception:
                pass
        # Atomic write
        tmp_path = idx_path.with_suffix(".tmp")
        tmp_path.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
        os.replace(str(tmp_path), str(idx_path))
        # No killall — kill causes grey flash. With ENABLE_PER_SPACE=False this
        # path is disabled. If re-enabled, use soft HUP or no kill and let
        # WallpaperAgent pick up change with fade (no grey).
        # run("killall WallpaperAgent 2>/dev/null; killall wallpaperexportd 2>/dev/null", timeout=3)
        logger.info(f"Per-space distribution: {patched} spaces patched, base_idx={global_idx} (no kill — fade)")
    except Exception as e:
        logger.debug(f"distribute_per_space failed: {e}")

# ----------------- SYNC PLOTS -----------------
def sync_plots():
    # Use --update (no --delete) to avoid black-screen when server is mid-write.
    # The redo changed this to --delete which wiped local ext plots during server
    # regeneration, causing 2x8 pages to render with 0-1 plots (black / single-plot).
    opts = "-az --update -e 'ssh -o BatchMode=yes -o ConnectTimeout=10'"
    try:
        run(f"rsync {opts} {SERVER_HOST}:{PLOTS_CRYPTO_REMOTE}/*.png {LOCAL_PLOTS_CRYPTO}/", timeout=RSYNC_TIMEOUT)
        run(f"rsync {opts} {SERVER_HOST}:{PLOTS_STOCKS_REMOTE}/*.png {LOCAL_PLOTS_STOCKS}/", timeout=RSYNC_TIMEOUT)
        logger.info("Synced plots from server.")
    except Exception as e:
        logger.debug(f"rsync exception: {e}")

# ----------------- CLEANUP -----------------
def _purge_wallpaper_cache():
    """Cap macOS wallpaper BMP cache that explodes with frequent rotation (was 41G).
    Called opportunistically from generate/rotate path; never crashes daemon.
    Handles both extension.image (12-13M BMP) and extension.aerials (24M .bmp.sb-*) which was 4.5G."""
    try:
        base = HOME / "Library/Containers/com.apple.wallpaper.agent/Data/Library/Caches/com.apple.wallpaper.caches"
        for sub in ["extension-com.apple.wallpaper.extension.image", "extension-com.apple.wallpaper.extension.aerials"]:
            cache_dir = base / sub
            if not cache_dir.exists():
                continue
            # Keep only 12 newest files (~150M) and delete any >500M total
            # Match *.bmp and *.bmp.sb-* (aerials uses .bmp.sb-<hash>)
            bmps = sorted(cache_dir.glob("*.bmp*"), key=lambda p: p.stat().st_mtime, reverse=True)
            for old in bmps[12:]:
                try:
                    old.unlink()
                except Exception:
                    pass
            # Also enforce 500M cap per subdir
            total = sum(p.stat().st_size for p in cache_dir.glob("*.bmp*") if p.exists())
            if total > MAX_WALLPAPER_CACHE_MB * 1024 * 1024:
                for old in sorted(cache_dir.glob("*.bmp*"), key=lambda p: p.stat().st_mtime):
                    try:
                        total -= old.stat().st_size
                        old.unlink()
                    except Exception:
                        pass
                    if total <= MAX_WALLPAPER_CACHE_MB * 1024 * 1024:
                        break
    except Exception:
        pass


def _purge_old_logs():
    try:
        if LOG_PATH.exists() and LOG_PATH.stat().st_size > MAX_LOG_MB * 1024 * 1024:
            # Truncate to last 2000 lines
            lines = LOG_PATH.read_text(errors="ignore").splitlines()[-2000:]
            LOG_PATH.write_text("\n".join(lines) + "\n")
    except Exception:
        pass


def _purge_stale_spaces():
    """When ENABLE_PER_SPACE is False, spaces/ is dead weight (1.9G, 581 files, never used).
    Purge it entirely to prevent 40G drift; called once per generation."""
    if ENABLE_PER_SPACE:
        return
    try:
        spaces_dir = OUT_DIR / "spaces"
        if spaces_dir.exists():
            for f in spaces_dir.glob("*.png"):
                try:
                    f.unlink()
                except Exception:
                    pass
            # Remove stale per-space tmp files too
            for f in spaces_dir.glob("*.tmp"):
                try:
                    f.unlink()
                except Exception:
                    pass
    except Exception:
        pass


def check_wallpaper_memory():
    """Watchdog: kill wallpaper extensions if they exceed 1GB to prevent 63G leak"""
    try:
        for name in ["WallpaperImageExtension", "WallpaperAerialsExtension"]:
            rc, out, err = run(f"ps aux | grep {name} | grep -v grep | awk '{{print $2, $6}}'", timeout=3)
            for line in out.splitlines():
                parts = line.split()
                if len(parts) == 2 and parts[1].isdigit():
                    pid, rss_kb = parts[0], int(parts[1])
                    if rss_kb > 1048576:
                        logger.warning(f"{name} PID {pid} RSS {rss_kb//1024}MB >1GB, restarting to prevent leak")
                        run(f"kill -9 {pid}", timeout=3)
    except Exception:
        pass


def cleanup_old(subdir, keep_per_prefix=KEEP_GENERATIONS):
    prefix_map = {}
    for f in subdir.glob("*.png"):
        if f.name.endswith("_current.png") or f.name.endswith(".tmp") or f.parent.name == "spaces":
            continue
        if "space_" in f.name:
            continue
        parts = f.stem.rsplit("_", 2)
        if len(parts) >= 3:
            prefix = "_".join(parts[:-2])
        else:
            prefix = f.stem
        prefix_map.setdefault(prefix, []).append(f)
    for prefix, files in prefix_map.items():
        files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        for old in files[keep_per_prefix:]:
            try:
                old.unlink()
            except Exception:
                pass

# ----------------- SCREENSAVER -----------------
def update_screensaver(page_files_int):
    SCREENSAVER_DIR.mkdir(parents=True, exist_ok=True)
    for f in SCREENSAVER_DIR.glob("*.png"):
        if f.name.endswith(".tmp"):
            continue
        try:
            f.unlink()
        except Exception:
            pass
    for i, src in enumerate(page_files_int):
        if src and Path(src).exists():
            dst = SCREENSAVER_DIR / f"page_{i+1:02d}_{src.stem}.png"
            tmp = dst.with_suffix(".tmp")
            shutil.copy2(str(src), str(tmp))
            os.replace(str(tmp), str(dst))
    logger.info(f"Updated screensaver folder with {len(page_files_int)} pages.")
    # Ensure screensaver prefs point to this folder (best-effort)
    try:
        run(f'defaults -currentHost write com.apple.screensaver moduleDict -dict moduleName iLifeSlideshows path "/System/Library/ExtensionKit/Extensions/iLifeSlideshows.appex" type 0', timeout=3)
        run(f'defaults -currentHost write com.apple.screensaver SelectedSource -int 3', timeout=3)
        run(f'defaults -currentHost write com.apple.screensaver SelectedFolderPath -string "{SCREENSAVER_DIR}"', timeout=3)
        run(f'defaults -currentHost write com.apple.screensaver CustomFolderDict -dict identifier -string "{SCREENSAVER_DIR}" name -string "binance_screensaver"', timeout=3)
    except Exception:
        pass

# ----------------- GENERATION -----------------
def generate_all():
    """Generate 6 composite pages alternating crypto/stock tiers 1-4, 5-8, 9-12.
    Internal: 4 winners + 4 losers (4 cols). External widescreen: 8 winners + 8 losers (8 cols, double).
    External tiers use double width: 1-8, 5-12, 9-16 to keep continuity while showing 2x plots."""
    logger.info("Starting generation cycle.")
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    # Need enough for external double-width tiers: up to 16 per type
    w_files = [str(x) for x in newest_by_prefix(LOCAL_PLOTS_CRYPTO, "W", 20)]
    l_files = [str(x) for x in newest_by_prefix(LOCAL_PLOTS_CRYPTO, "L", 20)]
    stock_w, stock_l = newest_stocks(LOCAL_PLOTS_STOCKS, 20)
    stock_w = [str(x) for x in stock_w]
    stock_l = [str(x) for x in stock_l]
    int_pages = []
    ext_pages = []
    # Internal tiers: 0:4, 4:8, 8:12 (4+4 per page)
    # External widescreen tiers: 0:8, 4:12, 8:16 (8+8 per page, overlapping to keep tier alignment)
    int_tiers = [(0, 4, "1-4"), (4, 8, "5-8"), (8, 12, "9-12")]
    ext_tiers = [(0, 8, "1-8"), (4, 12, "5-12"), (8, 16, "9-16")]
    for idx, ((a, b, tier_label), (ea, eb, ext_label)) in enumerate(zip(int_tiers, ext_tiers)):
        # Crypto tier
        w_slice = w_files[a:b]
        l_slice = l_files[a:b]
        w_ext = w_files[ea:eb]
        l_ext = l_files[ea:eb]
        if w_slice or l_slice:
            label_c = f"Crypto {tier_label}"
            int_p = OUT_INT / f"int_crypto_{idx+1}_{ts}.png"
            ext_p = OUT_EXT / f"ext_crypto_{idx+1}_{ts}.png"
            atomic_save(create_grid_composite(w_slice, l_slice, RES_INT, COLS_INT, label_c), int_p)
            atomic_save(create_grid_composite(w_ext, l_ext, RES_EXT, COLS_EXT, f"Crypto {ext_label}"), ext_p)
            int_pages.append(int_p)
            ext_pages.append(ext_p)
        # Stocks tier
        sw_slice = stock_w[a:b]
        sl_slice = stock_l[a:b]
        sw_ext = stock_w[ea:eb]
        sl_ext = stock_l[ea:eb]
        if sw_slice or sl_slice:
            label_s = f"Stocks {tier_label}"
            int_p = OUT_INT / f"int_stocks_{idx+1}_{ts}.png"
            ext_p = OUT_EXT / f"ext_stocks_{idx+1}_{ts}.png"
            atomic_save(create_grid_composite(sw_slice, sl_slice, RES_INT, COLS_INT, label_s), int_p)
            atomic_save(create_grid_composite(sw_ext, sl_ext, RES_EXT, COLS_EXT, f"Stocks {ext_label}"), ext_p)
            int_pages.append(int_p)
            ext_pages.append(ext_p)
    # Interleave already done as crypto,stocks per tier => order: C1,S1,C2,S2,C3,S3
    cleanup_old(OUT_INT)
    cleanup_old(OUT_EXT)
    _purge_stale_spaces()
    _purge_wallpaper_cache()
    _purge_old_logs()
    update_screensaver(int_pages)
    logger.info(f"Generation complete: {len(int_pages)} int, {len(ext_pages)} ext pages.")
    return int_pages, ext_pages

# ----------------- MAIN LOOP -----------------
def main_loop():
    for d in [OUT_DIR, OUT_INT, OUT_EXT, SCREENSAVER_DIR, LOCAL_PLOTS_CRYPTO, LOCAL_PLOTS_STOCKS]:
        d.mkdir(parents=True, exist_ok=True)
    last_refresh = 0
    last_sync = 0
    page_idx = 0
    last_rotate = 0
    last_memcheck = 0
    int_pages = []
    ext_pages = []
    while True:
        try:
            now = time.time()
            if now - last_memcheck >= 300:
                check_wallpaper_memory()
                last_memcheck = now
            if now - last_sync >= SYNC_SECONDS:
                sync_plots()
                last_sync = now
            if now - last_refresh >= REFRESH_SECONDS:
                try:
                    int_pages, ext_pages = generate_all()
                    last_refresh = now
                    page_idx = 0
                    last_rotate = 0
                except Exception as e:
                    logger.exception(f"Generation failed: {e}")
            if int_pages and ext_pages and (now - last_rotate >= ROTATE_SECONDS):
                idx = page_idx % len(int_pages)
                int_img = int_pages[idx]
                ext_img = ext_pages[idx]
                if int_img.exists() and ext_img.exists():
                    apply_wallpaper(int_img, ext_img)
                    if ENABLE_PER_SPACE:
                        distribute_per_space(int_pages, ext_pages, idx)
                    logger.info(f"Rotated to page {idx+1}/{len(int_pages)}: {int_img.name} / {ext_img.name}")
                else:
                    logger.warning(f"Page {idx+1} missing, skipping rotation.")
                page_idx += 1
                last_rotate = now
            time.sleep(5)
        except Exception as e:
            logger.exception(f"Main loop exception: {e}")
            time.sleep(10)

if __name__ == "__main__":
    logger.info("Wallpaper daemon starting.")
    main_loop()
