# HETZNER 4-BOX V15 CELL-BY-CELL SETUP — AGENT RUNBOOK

> Sequential bring-up: Box1 fully verified → clone to Box2-4. One box producing while NPZs copy in background. All results land on S1 and sync to MacBook. No overwrites. No empty workbooks.

## 0. Naming & IPs

| Role | Host alias | Suggested Hetzner name | SSH alias (Mac + S1) |
|------|------------|------------------------|-----------------------|
| S1 (existing) | `s1` = 157.180.125.52 / s1-int 127.0.0.1:2201 | htz-s1 | `s1-int`, `s1-pub` |
| Box2 | s2 | htz-v15-s2 | `s2` (10.0.0.4 via gw) — KEEP but do NOT reuse; new = `htz-s2` |
| Box3 | s3 | htz-v15-s3 | `htz-s3` |
| Box4 | s4 | htz-v15-s4 | `htz-s4` |
| Box5 | s5 | htz-v15-s5 | `htz-s5` |

> User asked: `s_2/3/4/5/` with direct SSH from S1 and MacBook. After bring-up each box must be reachable as `ssh niels@<public-ip>` from both hosts via `~/.ssh/id_ed25519` (pubkey below).

**Public key to install on ALL boxes (MacBook):**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOoHTe2eVQYQxsnZviTVmbCXnnNZ4HqWvFKZBx8EhcRc niels@MacBook-Pro.local
```

---

## 1. Workload — 1/5 each (after union, round-robin)

Union = `symbols_trb_long (63)` + `symbols_trb_short (62)` + `symbols_tradier_recommended 137 (obligatory TRB universe)` + `symbols_men (36)` + `symbols_fin (38)` + `symbols_ang_long (23)` + `symbols_ang_short (12)` — deduped, then LONG/SHORT expanded where the source is side-agnostic, alternating crypt/stock/long/short per `V15_RUNNING_ORDER_*`.

**Canonical generator (run on Mac or S1, single source of truth):**

```bash
python3 -c "
import json, pathlib, itertools
trb_l=json.loads(open('symbols_trb_long.json').read())
trb_s=json.loads(open('symbols_trb_short.json').read())
rec=json.loads(open('symbols_tradier_recommended.json').read())  # obligatory TRB
men=json.loads(open('symbols_men.json').read())
fin=json.loads(open('symbols_fin.json').read())
ang_l=json.loads(open('symbols_ang_long.json').read())
ang_s=json.loads(open('symbols_ang_short.json').read())
# expand men/fin (side-agnostic) to LONG+SHORT
men_sides=[f'{s}_LONG' for s in men]+[f'{s}_SHORT' for s in men]
fin_sides=[f'{s}_LONG' for s in fin]+[f'{s}_SHORT' for s in fin]
trb_sides=[f'{s}_LONG' for s in trb_l]+[f'{s}_SHORT' for s in trb_s]
ang_sides=[f'{s}_LONG' for s in ang_l]+[f'{s}_SHORT' for s in ang_s]
rec_sides=[f'{s}_LONG' for s in rec]+[f'{s}_SHORT' for s in rec]  # if rec is bare tickers; if already sided keep as-is
# prefer rec as bare — dedup later
raw=trb_sides+rec_sides+men_sides+fin_sides+ang_sides
# dedup preserve order
seen=set(); union=[]
for x in raw:
    if x not in seen:
        seen.add(x); union.append(x)
# alternate crypt/stock/long/short: sort key = (is_crypto, side) interleaved
def is_crypto(s): return s.endswith('USDT') or s.endswith('USDC')
# stable round-robin: crypt LONG, stock LONG, crypt SHORT, stock SHORT, repeat
buckets={('C','LONG'):[],('S','LONG'):[],('C','SHORT'):[],('S','SHORT'):[]}
for s in union:
    side='LONG' if s.endswith('_LONG') else 'SHORT'
    typ='C' if is_crypto(s) else 'S'
    buckets[(typ,side)].append(s)
order=[]
for tup in itertools.islice(itertools.cycle([('C','LONG'),('S','LONG'),('C','SHORT'),('S','SHORT')]), len(union)):
    if buckets[tup]:
        order.append(buckets[tup].pop(0))
open('V15_RUNNING_ORDER_TRB_FLZ.txt','w').write('\n'.join(order))
print(f'union {len(union)} -> ordered {len(order)}')
print(order[:20])
"
# then shard 1/5:
python3 -c "
order=open('V15_RUNNING_ORDER_TRB_FLZ.txt').read().splitlines()
for i in range(5):
    shard=order[i::5]
    open(f'V15_SHARD_{i+1}of5.txt','w').write('\n'.join(shard))
    print(f'shard {i+1}: {len(shard)}')
"
```

- Commit `V15_RUNNING_ORDER_TRB_FLZ.txt` + `V15_SHARD_*` to repo and S1.
- **Box assignment:** S1=shard1, htz-s2=shard2, htz-s3=shard3, htz-s4=shard4, htz-s5=shard5. Each box runs ONLY its shard (passed as `--shard V15_SHARD_Xof5.txt` or enumerated `--sym-side` loop).

---

## 2. Hetzner Box Spec

- **Type:** `cx33` (2 vCPU, 4 GB RAM, 80 GB SSD) — user asked 4x cx33. If OOM with `V12_NPZ_CACHE=32` (880 prepared), bump to `cpx31` (4 vCPU, 8 GB) — agent must note and ask.
- **Image:** Ubuntu 22.04
- **Location:** `nbg1` or `fsn1` (same as S1 region for fast rsync)
- **SSH key:** add MacBook `id_ed25519.pub` at create time via `hcloud ssh-key` or console.
- **Firewall:** open 22 only; no other ports.

**Create via hcloud CLI (MacBook):**
```bash
hcloud ssh-key create --name macbook-niels --public-key-from-file ~/.ssh/id_ed25519.pub  # once
hcloud server create --name htz-v15-s2 --type cx33 --image ubuntu-22.04 --ssh-key macbook-niels --location nbg1
hcloud server create --name htz-v15-s3 --type cx33 --image ubuntu-22.04 --ssh-key macbook-niels --location nbg1
# ... but SEQUENTIAL: create s2 first, verify (section 6), then create s3/s4/s5
```

---

## 3. Per-Box Bootstrap (run on EACH box via ssh niels@<ip>)

### 3.1 User & SSH

```bash
# as root on fresh box:
useradd -m -s /bin/bash niels
usermod -aG sudo niels
mkdir -p /home/niels/.ssh && chmod 700 /home/niels/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOoHTe2eVQYQxsnZviTVmbCXnnNZ4HqWvFKZBx8EhcRc niels@MacBook-Pro.local' >> /home/niels/.ssh/authorized_keys
# also allow S1 to ssh in: copy S1's /home/niels/.ssh/id_ed25519.pub to authorized_keys
chmod 600 /home/niels/.ssh/authorized_keys; chown -R niels:niels /home/niels/.ssh
echo 'niels ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/niels
# disable root password ssh if needed
```

Variant: if box was created with niels key already, just ensure `niels` exists and S1 key is appended.

Pull S1 pubkey:
```bash
# on Mac: ssh s1-int "cat ~/.ssh/id_ed25519.pub" >> /tmp/s1.pub && cat /tmp/s1.pub
# on box: append that line to /home/niels/.ssh/authorized_keys
```

### 3.2 Base packages

```bash
sudo apt update && sudo apt install -y git rsync htop tmux python3-pip python3-venv build-essential libssl-dev
# conda/mamba not required — use system python + venv matching S1: python3.11
python3 --version
```

### 3.3 Project checkout

```bash
# S1 is sandbox only — do NOT clone from GitHub if repo has no remote. Use rsync from S1 (canonical).
# On box as niels:
mkdir -p ~/binance-sandbox
# From Mac (or S1) push canonical files:
rsync -avz -e "ssh -i ~/.ssh/id_ed25519" \
  --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' \
  --exclude='data/cache' --exclude='backtest_v8/indicators/*.npz' \
  /Users/niels/Documents/binance/ niels@<box-ip>:~/binance-sandbox/
# S1 equivalent:
rsync -avz --exclude='*.npz' /home/niels/binance-sandbox/ niels@<box-ip>:~/binance-sandbox/
```

**Required files on box (verify after rsync):**
```
binance-sandbox/
  v15_pilot.py
  tools/v15_pilot_sheet_runner.py
  SPREADSHEETS/TEMPLATE.xlsx   # 776K, 23 sheets, 13 switch sheets + INSTRUCTIONS — NEVER overwrite; RESTORE=DEATH PENALTY
  backtest_v11_engine.py / backtest_v12_engine.py / tools/opt/evaluate_v12.py
  config.py / config_tradier.py
  symbols_*.json (all)
  data/reports/lifecycle_pilot/   # progress dir
  SPREADSHEETS/V15_V16_CELL_BY_CELL/  # output dir
```

### 3.4 Python env

```bash
cd ~/binance-sandbox
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install openpyxl numpy pandas pyarrow fastparquet python-dotenv
# verify engines import:
python3 -c "import openpyxl, numpy; print(openpyxl.__version__, numpy.__version__)"
python3 -c "import backtest_v12_engine; print('engine ok')"
```

Add to `~/.bashrc`:
```bash
export V12_NPZ_CACHE=32
export PYTHONPATH=/home/niels/binance-sandbox:$PYTHONPATH
```

### 3.5 Directories

```bash
mkdir -p ~/binance-sandbox/backtest_v8/indicators
mkdir -p ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL
mkdir -p ~/binance-sandbox/data/reports/lifecycle_pilot
mkdir -p ~/niels  # user asked /niels/ account and folder — keep as symlink for convenience
ln -sfn /home/niels ~/niels 2>/dev/null; sudo ln -sfn /home/niels /niels 2>/dev/null || true
```

---

## 4. NPZ Strategy — Start Producing While Copying

> MacBook has NO NPZ. Source of truth is S1 `/home/niels/binance-sandbox/backtest_v8/indicators/*.npz` (and fallback `/home/niels/binance-sandbox/backtest_v8/indicators`) plus `/Volumes/TOSHIBA_EXT/backtest_npz_master/v4_indicators/` for cold fill. Do NOT bulk-copy from TOSHIBA_EXT over WAN.

### 4.1 First NPZ only → start Box2 immediately

```bash
# On S1, pick first shard symbol's base (e.g. first line of V15_SHARD_2of5.txt strip _LONG/_SHORT)
SYM=$(head -1 ~/binance-sandbox/V15_SHARD_2of5.txt | sed 's/_.*//')
# Ensure that NPZ is 15m-only stripped (no _3m keys, 880 files target). If not, strip on S1 first.
# Then push ONLY that one:
rsync -avz --progress /home/niels/binance-sandbox/backtest_v8/indicators/${SYM}.npz niels@<box-ip>:~/binance-sandbox/backtest_v8/indicators/
# Verify size >100K and mtime fresh:
ssh niels@<box-ip> "ls -lh ~/binance-sandbox/backtest_v8/indicators/${SYM}.npz"
```

### 4.2 Launch Box2 on that single symbol while rest copies

```bash
# On Box2 as niels, venv active:
nohup python3 -u v15_pilot.py --sym-side ${SYM}_LONG --window-days 30 --vector-only > /tmp/v15_${SYM}.log 2>&1 &
# heartbeat: tail -f /tmp/v15_*.log ; watch /tmp/v14_heartbeat_${SYM}.txt
```

### 4.3 Background copy remaining NPZs (bw-limited, resumable)

```bash
# From S1, in tmux, bwlimit so production not starved:
tmux new -s npz_copy_htz-s2
rsync -avz --progress --bwlimit=8000 --partial --inplace \
  /home/niels/binance-sandbox/backtest_v8/indicators/*.npz niels@<box-ip>:~/binance-sandbox/backtest_v8/indicators/
# Ctrl-b d to detach; monitor with: tmux attach -t npz_copy_htz-s2
```

Repeat for each new box (s3/s4/s5) after Box2 is verified.

---

## 5. Run Contract (per box)

- **Env:** `V12_NPZ_CACHE=32`, `ALL_PREPARED` in RAM (880 arrays), workers 16, per_cell_timeout 60, MAX_ROWS 50000, `wb_keep` open per sheet.
- **Never overwrite TEMPLATE.xlsx** — clone per symbol: `SPREADSHEETS/V15_V16_CELL_BY_CELL/{SYM}_30d_matrix_pilot_*.xlsx` via `_atomic_save` tmp+fsync+rename+.bak, progress `data/reports/lifecycle_pilot/{SYM}_v14_progress.json` via `_atomic_write_json`.
- **Cell-by-cell fills:** L:BI yellows (ALL 15 yellows when WT_15M_BOUNCE_OPEN_ENABLED, else top-5), `Results_Deltas` col5/col8, E BASELINE (empty until POS delta), F VECTOR_DELTA (combined switch+ALL pos yellows), C override (ALL pos). BASELINE STAYS EMPTY only fills when POS DELTA appears and overrides C populated only on POS.
- **Sizing:** NPZ 15m-only >=365D (9490 stock 365*26, 35040 crypto 365*96); 30D via slice.
- **Offline chart:** single-file `file://` zoomable `height:62vh` per sheet.

Example shard loop:
```bash
source ~/binance-sandbox/.venv/bin/activate
export V12_NPZ_CACHE=32
for symside in $(cat ~/binance-sandbox/V15_SHARD_2of5.txt); do
  python3 -u v15_pilot.py --sym-side $symside --window-days 30 --vector-only
done
```

---

## 6. Gate: One Box Fully Verified Before Cloning

**Do NOT create Box3 until Box2 has produced ONE correct xlsx.**

Verification checklist (agent must run, not just claim):

1. `ls -lh ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix_pilot_*.xlsx` — size ~750-850K, not empty (check `python3 -c "import openpyxl; wb=openpyxl.load_workbook(p); print(len(wb.sheetnames), wb.sheetnames[:3])"` → 23 sheets, 13 switch sheets).
2. Strict checker: `Results_Deltas` col5 not empty/repeated/0.0 across synced sheets, L:BI yellows filled, E blank unless F>0 then higher, F combined switch+ALL pos, C ALL pos.
3. `data/reports/lifecycle_pilot/*_progress.json` shows `done 200/200` (or `MAX_ROWS` variant) no pkill residue.
4. If ANY sheet fails strict check → STOP, repair, unlink bad xlsx (10s startup guard), do NOT promote to S1.
5. Only on pass: rsync that verified xlsx back to S1 (section 7), then proceed to provision next box.

---

## 7. Results — Keep on S1, Sync to MacBook

**Boxes NEVER keep final results — S1 is canonical.**

After each symbol completes (or batch daily):

```bash
# Box -> S1 (from box):
rsync -avz --progress ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@157.180.125.52:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/
rsync -avz --progress ~/binance-sandbox/data/reports/lifecycle_pilot/*.json niels@157.180.125.52:~/binance-sandbox/data/reports/lifecycle_pilot/
# S1 -> MacBook (from Mac, via s1-int tunnel; ensure ssh -fNT s1-sftp running):
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" niels@127.0.0.1:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" niels@127.0.0.1:~/binance-sandbox/data/reports/lifecycle_pilot/*.json /Users/niels/Documents/binance/data/reports/lifecycle_pilot/
# Also mirror to regular SPREADSHEETS (pilot xlsx):
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" niels@127.0.0.1:~/binance-sandbox/SPREADSHEETS/*.xlsx /Users/niels/Documents/binance/SPREADSHEETS/
```

Automate with `box_sync_all.sh` style cron on S1 every 15 min (see `INFRASTRUCTURE.md` BOX 135 pattern).

---

## 8. SSH Access Matrix

On **MacBook** `~/.ssh/config` add:

```
Host htz-s2
  HostName <htz-s2 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes

Host htz-s3
  HostName <htz-s3 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes

Host htz-s4
  HostName <htz-s4 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes

Host htz-s5
  HostName <htz-s5 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

On **S1** `~/.ssh/config` add identical `htz-s2`..`htz-s5` entries (so `ssh htz-s2` works from S1). Test: `ssh htz-s2 "hostname; ls ~/binance-sandbox/v15_pilot.py"` from both Mac and S1.

---

## 9. Teardown & Cost Control

- Boxes are ephemeral. When shard done, rsync final, verify on S1/Mac, then `hcloud server delete htz-v15-sX`.
- Keep S1 as permanent canonical. Do NOT delete S1.
- Tag Hetzner project `v15-cell-by-cell` for billing.

---

## 10. Agent Step Order (copy-paste)

1. Ensure `V15_RUNNING_ORDER_TRB_FLZ.txt` + 5 shards exist on S1 and Mac.
2. `hcloud server create htz-v15-s2` (cx33, nbg1).
3. Bootstrap s2 §3 (user, packages, rsync without NPZ, venv).
4. Push single first NPZ §4.1, launch s2 on that symbol §4.2.
5. Background rsync remaining NPZs §4.3 while s2 produces.
6. Gate §6 — verify s2's first xlsx strictly. If fail → fix, no next box.
7. On pass: pull results to S1+Mac §7.
8. Repeat 2-7 for htz-v15-s3, s4, s5.
9. Monitor via `tmux` + `/tmp/v14_heartbeat_*.txt` + `data/reports/lifecycle_pilot/*.json`.

> RESTORE=DEATH PENALTY: never restore TEMPLATE.xlsx from backup over newer without diff. DEAT PENALTY on empty/repeated/0.0 — checker must STOP script and repair. BTFS remount for TOSHIBA_EXT if needed — not relevant to boxes (S1 source).
