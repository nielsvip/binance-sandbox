# Elastic Fleet Runbook — s2/s3/s4/s5…sN on S1 Image

S1 (`157.180.125.52` / `10.0.0.3` — `s1-int`) is **INDESTRUCTIBLE** and the single source of truth.
All temp workers are **sequentially numbered** `s2, s3, s4, s5 … sN` with **deterministic private IPs** `10.0.0.(N+2)` on `tradingnet` `10.0.0.0/24` (gw `10.0.0.1`). Public IPs are ephemeral and change on every rebuild — only the `10.0.0.*` is stable. Temps boot from a *minimal system image* held on S1, then sync latest code+data from S1 at boot.

## 1. What s2/s3/s4/s5…sN actually are

| Host | Public | Private | Spec | Role |
|------|--------|---------|------|------|
| s1 | 157.180.125.52 | 10.0.0.3 | 32c/64G | **Permanent** — holds image, NPZ, ORDER, results. Never deleted. |
| s2 | *ephemeral* (changes on rebuild) | 10.0.0.4 | 4-16c/8-32G | Ephemeral worker — `s2` ≡ `10.0.0.4` |
| s3 | *ephemeral* | 10.0.0.5 | 16c/32G | Ephemeral — `s3` ≡ `10.0.0.5` |
| s4 | *ephemeral* | 10.0.0.6 | 16c/32G | Ephemeral — `s4` ≡ `10.0.0.6` (gap filled; legacy `s5` at `.6` migrates to `.7` on next rebuild) |
| s5 | *ephemeral* | 10.0.0.7 | 16c/32G | Ephemeral — `s5` ≡ `10.0.0.7` |
| s6 | *ephemeral* | 10.0.0.8 | CCX33 | Ephemeral — `s6` ≡ `10.0.0.8` |
| sN | *ephemeral* | 10.0.0.(N+2) | CCX33/CPX31 | Ephemeral — `sN` ≡ `10.0.0.(N+2)` |

Canonical rule: **`sN` → `10.0.0.(N+2)`** (`s1→.3`, `s2→.4`, … `s10→.12`). Hetzner `tradingnet` auto-assigns the next free IP in creation order — `deploy_fleet.sh` creates in sequential order `s2→s3→s4→…` so the assignment matches the canonical rule, verifies it after create, and **upserts** `~/.ssh/config` so `ssh sN` always resolves to the live private IP.

Public IPs (`65.108.53.160`, `65.108.49.184`, `46.62.137.232` …) are **never** relied on — they churn on every `hcloud server rebuild/create`. Use `sN` (private) for all herd rsync (`10.0.0.3`) and `ssh sN` from MacBook via gateway.

### Access from MacBook — three paths (all respect `10.0.0.*` private)

1. **Via gateway (primary):** `Host 10.0.0.*` + `Host sN` → `ProxyJump gateway-internal` (`157.90.168.35` → `10.0.0.2`). From Mac: `ssh s4` = `ssh -J gateway-internal 10.0.0.6`. This is Hetzner’s recommended internal routing and bypasses public-IP brute-force.
2. **Via S1 (fallback if gateway down):** `Host s1-via-gateway` (`10.0.0.3` via `gateway-internal`) is itself a jump host. Use `ssh -J s1-via-gateway s4` or `ssh -J s1-int s4` (tunnel `127.0.0.1:2201`). Bootstrap also falls back `10.0.0.3 → 157.180.125.52` if private unreachable at boot.
3. **Direct public (emergency):** `Host sN-pub` (`HostName <current public>`) — updated by `deploy_fleet.sh` after each create. Use only when private net is down: `ssh s4-pub`.

All three respect the **same** private net; public IP change never breaks `sN`.

All temps share the same wiring:
- Ubuntu 22.04, `.venv` at `~/binance-sandbox/.venv` (fallback `.conda/envs/binance_env`)
- Code at `~/binance-sandbox` — **only system** in the image (see §2)
- Herd: `tools/v15_local_herd.py` as `@reboot` daemon + `* * * * * --cron-check` + `*/5 rsync → S1`
- Coordination via `S1:10.0.0.3` → `SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx` (`global_done_set`), fallback `hash(sym)%4` shard if S1 unreachable
- Results pushed after each sym via `push_to_s1()` (xlsx + `data/reports/lifecycle_pilot/*.json`)

## 2. Golden Image — minimal system only

**Stored on S1:** `~/binance-sandbox/.elastic/golden_image.tar.zst` + Hetzner snapshot `binance-golden-<date>` (both on S1, snapshot ID in `.elastic/snapshot_id`).

Image **contains**:
- OS + `apt` deps, `python3.11`, `rsync`, `git`, `htop`, `nvme` tuned
- `~/binance-sandbox/.venv` with `requirements.txt` (numpy, pandas, openpyxl, scipy, numba — no NPZ)
- `~/binance-sandbox` skeleton: `*.py` (`v12_quick_engine`, `v15_pilot*`, `config*`, `ez_*`, `tradier_*`), `tools/`, `SPREADSHEETS/TEMPLATE*.xlsx`, `BACKTEST_BIBLE.md`, but **no** bulky data
- `tools/v15_local_herd.py` + `infra/elastic/bootstrap.sh` + cron template + systemd unit

Image **excludes** (synced at boot from S1):
- `data/matrix_npz/**` (91M stocks + 4GB archive on S1 — pulled per-NPZ on demand)
- `SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx` (results)
- `SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt` + `V15_FULL_354.txt` (order)
- `data/reports/lifecycle_pilot/*.json` (progress)
- `data/sweep_results/**`, `logs/**`, `*.log`, `__pycache__`

Why: image stays <2.5GB compressed → boot in ~45s, then delta-rsync from S1 over 10G private net (10.0.0.3) at 300+ MB/s.

## 3. Build / Refresh the Golden Image

On **S1** (never on temp):

```bash
ssh s1-int 'bash -s' < infra/elastic/build_golden_image.sh
# or locally via tunnel:
./infra/elastic/build_golden_image.sh --via s1-int
```

What it does:
1. `rsync --checksum` current Mac `*.py`+`tools`+`TEMPLATE*.xlsx` → S1 (so image is latest code version)
2. On S1: create `/tmp/golden_staging` as copy of `~/binance-sandbox` with excludes above, `pip freeze` pinned, strip logs/caches, `tar --zstd` → `~/.elastic/golden_image.tar.zst` (`sha256` in `.elastic/golden_image.sha256`)
3. Optionally `hcloud image create --type snapshot --server s1 --description binance-golden-$(date +%Y%m%d)` → ID saved to `.elastic/snapshot_id` (requires `HCLOUD_TOKEN`)
4. Verifies: `tar -tf` lists `.venv`, `v15_local_herd.py`, `TEMPLATE.xlsx` and **no** `matrix_npz/*.npz`

Rebuild when: `requirements.txt` changes, new `TEMPLATE.xlsx`, or herd logic changes. NPZ/data changes do **not** require rebuild — they sync at boot.

## 4. Boot — what a temp does in <3 min

`infra/elastic/bootstrap.sh` is the `cloud-init --user-data` (or `hcloud server create --user-data-from-file`). On first boot:

```
1. wait for network + set hostname htz-v15-s{2,3,5,N}
2. if snapshot image: already has .venv + skeleton — skip; if empty VM: curl -sf http://10.0.0.3:8787/golden_image.tar.zst | tar -xzf → ~/binance-sandbox (fallback 157.180.125.52:8787)
3. rsync --checksum from S1 (private 10.0.0.3, fallback public):
     SPREADSHEETS/V15_RUNNING_ORDER_TRB_FLZ.txt
     SPREADSHEETS/V15_FULL_354.txt
     SPREADSHEETS/TEMPLATE*.xlsx
     SPREADSHEETS/V15_SERVER_QUEUE_S*.txt (if using queue shards)
     data/matrix_npz/stocks_repaired_20260725_c2/*.npz  (or --on-demand: herd pulls per-sym NPZ at launch via scp)
     config.py, config_tradier.py, symbols_*.json
4. verify: python -c "import v12_quick_engine; import openpyxl" && ls data/matrix_npz/.../*.npz | head
5. install cron:
     @reboot sleep 15; nohup .venv/bin/python -u tools/v15_local_herd.py >> /tmp/v15_local_herd.log 2>&1 &
     * * * * * .venv/bin/python tools/v15_local_herd.py --cron-check >> /tmp/v15_local_herd_cron.log 2>&1
     */5 * * * * rsync -az SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@10.0.0.3:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/
     */2 * * * * .venv/bin/python tools/elastic_idle_watchdog.py >> /tmp/elastic_idle.log 2>&1
6. nohup herd & — herd immediately does global_done_set() against S1 and fills todo with latest ORDER (latest data, latest code version)
```

Freshness guarantee: bootstrap **always** rsyncs ORDER + TEMPLATE + NPZ from S1 at boot — never uses stale image data. A new ORDER pushed to S1 is picked up by the next boot or by herd's 60s `global-refresh`.

## 5. Deploy more / fewer in minutes

### Prerequisites (once)

```bash
# Hetzner token (read/write) — holds S1 snapshot and creates temps
export HCLOUD_TOKEN="…"
hcloud context create binance
# SSH key already on all servers: ~/.ssh/id_ed25519.pub (added to Hetzner as binance-main)
hcloud ssh-key list
# Ensure S1 http serves image for empty-VM fallback (runs on S1):
ssh s1-int 'nohup python3 -m http.server 8787 --directory ~/binance-sandbox/.elastic >> ~/logs/elastic_http.log 2>&1 &'
```

### Scale up — 1 command

```bash
# create 2 more workers (s6, s7) from snapshot, auto-bootstrapped
./infra/elastic/deploy_fleet.sh --count 2 --type ccx33 --from snapshot

# or from tarball on empty Ubuntu (no snapshot — pure S1 pull, ~90s slower)
./infra/elastic/deploy_fleet.sh --count 3 --type ccx33 --from tarball

# pin specific hostnames / private IPs
./infra/elastic/deploy_fleet.sh --names s6,s7 --type ccx33
```

`deploy_fleet.sh` does **sequentially** (one `hcloud server create` at a time, in numeric order `s2→s3→s4→…`): `hcloud server create --name sN --type ccx33 --image <golden|ubuntu-22.04> --ssh-key binance-main --network tradingnet --label purpose=backtest,gen=v15,private=10.0.0.(N+2) --user-data-from-file infra/elastic/bootstrap.sh`. Hetzner auto-assigns the next free `10.0.0.*` in creation order — sequential creation guarantees `sN` gets `10.0.0.(N+2)`. After each create it fetches the live private/public IPs (`hcloud server describe sN -o json`) and **verifies** `private == 10.0.0.(N+2)` (warns if drift) and **upserts** `~/.ssh/config` (`Host sN` → private via `gateway-internal`, `Host sN-pub` → current public). Then waits for `ssh sN "ps aux | grep v15_local_herd"` up.

Time: snapshot → herd running in **~90s**; tarball → **~180s** (includes 1.2G venv pull at 300MB/s private).

### Scale down — auto + manual

**Auto (preferred):** each temp runs `tools/elastic_idle_watchdog.py` every 2 min via cron. It checks:

```python
todo = combined_done == len(order)  # or hash-shard todo == 0
synced = rsync --dry-run xlsx → S1 shows 0 files to send AND progress json synced
idle_for = now - last_launch > 10min
```

If `todo==0 && synced && idle_for>10m` → writes `~/binance-sandbox/.elastic/ELASTIC_DONE` + `touch /tmp/ELASTIC_DONE`, then **self-deletes** via Hetzner API if `HCLOUD_TOKEN` + `HCLOUD_SERVER_ID` env are present (`curl -X DELETE https://api.hetzner.cloud/v1/servers/$ID -H "Authorization: Bearer $TOKEN"`), else just exits herd (S1 cron `s1_pull_from_s2s3s5.sh` + Mac `deploy_fleet.sh --cull-idle` will delete).

**Manual:**

```bash
# delete specific idle hosts (only if synced — script verifies S1 has their xlsx)
./infra/elastic/deploy_fleet.sh --delete s6
./infra/elastic/deploy_fleet.sh --cull-idle   # deletes every temp where todo==0 && synced

# emergency — keep results, just stop herd
ssh s3 'pkill -f v15_local_herd; pkill -f v15_pilot'
```

Safety: script **refuses** to delete if `rsync --dry-run` shows unsynced `*.xlsx` or `*.json` → prints `-would lose N files` and aborts unless `--force`.

## 6. S1 indestructible — protection

Run once (and after any Hetzner project change):

```bash
./infra/elastic/s1_protect.sh
```

It:
- `hcloud server update s1 --delete-protection true --rebuild-protection true`
- Adds label `indestructible=true` + `role=s1-source-of-truth`
- Verifies `~/.elastic/golden_image.tar.zst` + `.sha256` + `snapshot_id` exist and are readable via both `10.0.0.3` and `157.180.125.52`
- Creates `~/binance-sandbox/.elastic/S1_DO_NOT_DELETE` sentinel checked by every delete script (any `deploy_fleet.sh --delete` with `s1` in list hard-fails)
- Cron on S1: `0 * * * * ~/binance-sandbox/infra/elastic/s1_protect.sh --check` → alerts if protection dropped

Never run `hcloud server delete s1`; `deploy_fleet.sh` contains `if [[ $name == s1* ]]; then echo "REFUSED: s1 indestructible" >&2; exit 1; fi`.

## 7. Sync-back guarantee — no result loss

- Per-sym: `push_to_s1()` after each xlsx close (inside herd). Retry on next 60s loop if S1 was down.
- Periodic: `*/5 rsync -az *.xlsx → S1` (cron) — catches any missed push.
- At idle: `elastic_idle_watchdog.py` does final `rsync -az --checksum` + verifies `ssh s1 "ls *.xlsx | wc -l"` equals local count before allowing delete.
- S1 also pulls: `tools/s1_pull_from_s2s3s5.sh` every 2 min (existing) — second path, so even if temp's push failed, S1 still gets it.
- Mac mirrors: `tools/mac_v15_pull_new.sh` pulls S1 → Mac every 15 min.

Delete only happens when the third check passes: `local_xlsx == s1_xlsx` for that host's shard.

## 8. Latest version + latest data at any moment

| What | Source | When synced |
|------|--------|-------------|
| Backtest code (`v12_quick_engine`, `v15_pilot*`, herd) | Mac → S1 (`rsync_to_sandbox.sh --checksum`) → temp bootstrap rsync | Image build pins version; bootstrap re-rsyncs before herd start, so a temp booted 1h after a pilot fix gets the fix |
| `TEMPLATE.xlsx` | S1 | Bootstrap + herd launch picks template by existence |
| `V15_RUNNING_ORDER_TRB_FLZ.txt` / `V15_FULL_354.txt` | S1 | Bootstrap + herd `find_order()` + 60s `global_done_set` |
| NPZ `data/matrix_npz/stocks_repaired_20260725_c2/*.npz` | S1 `data/matrix_npz` | Bootstrap bulk rsync + per-sym fallback `scp niels@10.0.0.3:~/binance-sandbox/backtest_v8/indicators/*.npz` at launch if missing |
| `config.py` / `symbols_*.json` | S1 | Bootstrap |

If you push a new ORDER or TEMPLATE to S1 while temps are running, they pick it up within 60s via `global-refresh` without reboot. For a new engine version, either `rsync_to_sandbox.sh` to S1 + `ssh s{2,3,5} 'rsync -az niels@10.0.0.3:~/binance-sandbox/v12_quick_engine.py ~/binance-sandbox/' && pkill -HUP -f v15_local_herd` or just rebuild image and roll new temps.

## 9. Quick reference

```bash
# Check fleet health (from Mac) — sequential s2..sN via private net (gateway or s1)
./tools/fleet_monitor.sh; cat /tmp/fleet_monitor.log
# or
for h in s1 s2 s3 s4 s5 s6 s7 s8; do echo "== $h =="; ssh -o ConnectTimeout=5 $h "hostname; ip -4 addr show eth1 2>/dev/null | grep -o '10.0.0.[0-9]*' | head -1 | xargs echo private:; ps aux | grep -E 'v15_local_herd|v15_pilot' | grep -v grep | wc -l | xargs echo pilots:; ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | wc -l | xargs echo xlsx:"; done
# verify canonical mapping sN→10.0.0.(N+2)
for n in 1 2 3 4 5 6 7 8; do echo -n "s$n expected 10.0.0.$((n+2)) live "; hcloud server describe s$n -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('private_net',[{}])[0].get('ip','-'))" 2>/dev/null || echo "?"; done

# Image age
ssh s1-int 'ls -lh ~/binance-sandbox/.elastic/golden_image.* && cat ~/binance-sandbox/.elastic/golden_image.sha256'

# Deploy 1 more for tonight
./infra/elastic/deploy_fleet.sh --count 1 --type ccx33 --from snapshot

# Cull idle (safe — verifies sync)
./infra/elastic/deploy_fleet.sh --cull-idle

# Protect S1
./infra/elastic/s1_protect.sh --check
```

## 10. File map

```
infra/elastic/build_golden_image.sh   — build .elastic/golden_image.tar.zst on S1 (+ optional snapshot)
infra/elastic/bootstrap.sh            — cloud-init user-data: sync from S1 + start herd
infra/elastic/deploy_fleet.sh         — create/delete/cull Hetzner servers (Mac or S1)
infra/elastic/s1_protect.sh           — set delete/rebuild protection + sentinel
tools/elastic_idle_watchdog.py        — per-temp cron: push, verify, self-delete when done
ELASTIC_FLEET_RUNBOOK.md              — this file
```

All deletes refuse `s1` by name and by label `indestructible=true`.
