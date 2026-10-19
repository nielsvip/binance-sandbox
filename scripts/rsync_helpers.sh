#!/bin/bash
# Helper functions for rsync scripts with size limits

MAX_SIZE_GB=15
MAX_SIZE_BYTES=$((MAX_SIZE_GB * 1024 * 1024 * 1024))

check_and_clean_dir() {
    local dir="$1"
    if [ ! -d "$dir" ]; then return 0; fi
    local current_size=$(du -sb "$dir" 2>/dev/null | cut -f1 || echo 0)
    if [ "$current_size" -lt "$MAX_SIZE_BYTES" ]; then return 0; fi
    echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️  $dir exceeds ${MAX_SIZE_GB}GB, NOT cleaning (2026-10-19 USER: never delete data to make a sync fit — warn only)" >&2
    return 0
}

check_remote_size() {
    local remote_host="$1"
    local remote_path="$2"
    local size_raw=$(ssh -o ConnectTimeout=5 "$remote_host" "du -sb '$remote_path' 2>/dev/null | cut -f1" 2>/dev/null || echo 0)
    local size=${size_raw:-0}
    if ! [[ "$size" =~ ^[0-9]+$ ]]; then size=0; fi
    if [ "$size" -gt "$MAX_SIZE_BYTES" ] 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️  Remote $remote_path exceeds ${MAX_SIZE_GB}GB, syncing anyway (2026-10-19 USER: ALWAYS SYNC)" >&2
    fi
    return 0
}

check_local_size() {
    local local_path="$1"
    local size_raw=$(du -sb "$local_path" 2>/dev/null | cut -f1)
    local size=${size_raw:-0}
    if ! [[ "$size" =~ ^[0-9]+$ ]]; then size=0; fi
    if [ "$size" -gt "$MAX_SIZE_BYTES" ] 2>/dev/null; then
        echo "$(date '+%Y-%m-%d %H:%M:%S') - ⚠️  Local $local_path exceeds ${MAX_SIZE_GB}GB, cleaning before sync" >&2
        check_and_clean_dir "$local_path"
    fi
    return 0
}

