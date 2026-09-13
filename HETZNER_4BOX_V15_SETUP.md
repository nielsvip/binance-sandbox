# HETZNER 4-BOX V15 — 40 SYMS IN 2 HOURS — AGENT RUNBOOK

> Sequential bring-up: S2 verified producing → snapshot → clone S3/S4 in <60s. S1 = box1. First NPZ starts S2 instantly while rest rsync in background. All results atomically to S1, then MacBook. Delete boxes when done, keep replica.

## 0. What you are building

| Role | Host alias | Hetzner name | Runs | SSH from Mac + S1 |
|------|------------|--------------|------|-------------------|
| S1 (existing) | `s1` 157.180.125.52 / s1-int 127.0.0.1:2201 | htz-s1 | 10 of 40 | `s1-int` |
| S2 (first new) | `s2` 10.0.0.x via gw | `htz-v15-s2` | 10 of 40 | `s2` |
| S3 | `s3` | `htz-v15-s3` | 10 of 40 | `s3` |
| S4 | `s4` | `htz-v15-s4` | 10 of 40 | `s4` |

*Total 4 boxes = S1 + 3 new (S2/S3/S4) = 40 sym_sides in ~2h (3041 rows × 0.5s /16 workers ≈7-10 min/sym, 10 syms ≈100 min/box). If you make a 5th (S5) do shard 5 — same pattern.*

**The 40:**
```
BTCUSDC_LONG CRWV_SHORT BTCUSDC_SHORT MSTR_LONG BTCDOMUSDT_LONG AMAT_SHORT BTCDOMUSDT_SHORT VLO_LONG ETHUSDC_LONG BWXT_SHORT
ETHUSDC_SHORT GLD_LONG BNBUSDC_LONG NKE_SHORT BNBUSDC_SHORT SNDK_LONG SOLUSDC_LONG BABA_SHORT SOLUSDC_SHORT MU_LONG
XRPUSDC_LONG ETN_SHORT XRPUSDC_SHORT BMNR_LONG HYPEUSDT_LONG GME_SHORT HYPEUSDT_SHORT AMD_LONG DOGEUSDC_LONG LOW_SHORT
DOGEUSDC_SHORT SLV_LONG ZECUSDC_LONG AXTI_SHORT ZECUSDC_SHORT NVDA_LONG WLDUSDC_LONG AMZN_SHORT WLDUSDC_SHORT MRVL_LONG
```

**Public key on ALL boxes (MacBook):**
```
ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOoHTe2eVQYQxsnZviTVmbCXnnNZ4HqWvFKZBx8EhcRc niels@MacBook-Pro.local
```

---

## 1. Divide the 40 — single source of truth

```bash
cat > /tmp/split40.sh << 'EOS'
cat << 'LIST' > /tmp/40.txt
BTCUSDC_LONG
CRWV_SHORT
BTCUSDC_SHORT
MSTR_LONG
BTCDOMUSDT_LONG
AMAT_SHORT
BTCDOMUSDT_SHORT
VLO_LONG
ETHUSDC_LONG
BWXT_SHORT
ETHUSDC_SHORT
GLD_LONG
BNBUSDC_LONG
NKE_SHORT
BNBUSDC_SHORT
SNDK_LONG
SOLUSDC_LONG
BABA_SHORT
SOLUSDC_SHORT
MU_LONG
XRPUSDC_LONG
ETN_SHORT
XRPUSDC_SHORT
BMNR_LONG
HYPEUSDT_LONG
GME_SHORT
HYPEUSDT_SHORT
AMD_LONG
DOGEUSDC_LONG
LOW_SHORT
DOGEUSDC_SHORT
SLV_LONG
ZECUSDC_LONG
AXTI_SHORT
ZECUSDC_SHORT
NVDA_LONG
WLDUSDC_LONG
AMZN_SHORT
WLDUSDC_SHORT
MRVL_LONG
LIST
# interleave crypt/stock/long/short for even load — already interleaved above, just round-robin 4
for i in 0 1 2 3; do awk "NR%4==$((i+1))" /tmp/40.txt > "V15_SHARD_$((i+1))of4.txt" && echo "shard $((i+1)): $(wc -l < V15_SHARD_$((i+1))of4.txt)"; done
# assign: S1=shard1, S2=shard2, S3=shard3, S4=shard4
EOS
bash /tmp/split40.sh
# commit to repo + S1
rsync -avz V15_SHARD_*of4.txt /tmp/40.txt s1-int:~/binance-sandbox/
```

*Result: 10 sym_sides/box, no overlap, no SNDK/NVDA special-case unless you mark them reliable — keep them in shards.*

---

## 2. Hetzner — create S2 ONLY first

```bash
# once
hcloud ssh-key create --name macbook-niels --public-key-from-file ~/.ssh/id_ed25519.pub

# S2 only — verify before cloning
hcloud server create --name htz-v15-s2 --type cx33 --image ubuntu-22.04 --ssh-key macbook-niels --location nbg1
# wait 30s, get IP
hcloud server list | grep htz-v15-s2
# add to ~/.ssh/config on BOTH Mac and S1 (see §7)
```

**Spec:** `cx33` (2 vCPU, 4GB, 80GB) — `V12_NPZ_CACHE=32` (1.2GB RAM) fits 3 concurrent pilots; if OOM bump to `cpx31` (4 vCPU, 8GB). Image `ubuntu-22.04`, region `nbg1` (same as S1), firewall 22 only.

---

## 3. Bootstrap S2 — record-time path (<4 min)

**On S2 as root → niels (run via `ssh niels@<s2-ip>`):**

```bash
useradd -m -s /bin/bash niels; usermod -aG sudo niels
mkdir -p /home/niels/.ssh; chmod 700 /home/niels/.ssh
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOoHTe2eVQYQxsnZviTVmbCXnnNZ4HqWvFKZBx8EhcRc niels@MacBook-Pro.local' >> /home/niels/.ssh/authorized_keys
# S1 pubkey so S1 can rsync directly (no Mac hop)
ssh s1-int "cat ~/.ssh/id_ed25519.pub" >> /home/niels/.ssh/authorized_keys
chmod 600 /home/niels/.ssh/authorized_keys; chown -R niels:niels /home/niels/.ssh
echo 'niels ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/niels
apt update && apt install -y git rsync htop tmux python3-pip python3-venv build-essential
```

**Checkout — no NPZ yet (keep image small):**

```bash
# as niels on S2
mkdir -p ~/binance-sandbox
# S1 is canonical — exclude heavy
rsync -avz --exclude='.git' --exclude='__pycache__' --exclude='*.pyc' --exclude='*.npz' --exclude='data/cache' \
  s1-int:~/binance-sandbox/ ~/binance-sandbox/
# S1 equivalent if you are on S1: rsync -avz --exclude='*.npz' ~/binance-sandbox/ niels@s2:~/binance-sandbox/

# verify
ls ~/binance-sandbox/v15_pilot.py ~/binance-sandbox/SPREADSHEETS/TEMPLATE.xlsx
# TEMPLATE.xlsx 776K 23 sheets — NEVER overwrite; RESTORE=DEATH PENALTY

# venv
cd ~/binance-sandbox && python3 -m venv .venv && source .venv/bin/activate
pip install --upgrade pip && pip install openpyxl numpy pandas pyarrow fastparquet python-dotenv psutil
python3 -c "import openpyxl,numpy; print(openpyxl.__version__)"
python3 -c "import backtest_v12_engine; print('engine ok')"

# dirs + env
mkdir -p ~/binance-sandbox/backtest_v8/indicators ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL ~/binance-sandbox/data/reports/lifecycle_pilot
echo 'export V12_NPZ_CACHE=32' >> ~/.bashrc; echo 'export PYTHONPATH=/home/niels/binance-sandbox:$PYTHONPATH' >> ~/.bashrc
sudo ln -sfn /home/niels /niels 2>/dev/null || true; ln -sfn /home/niels ~/niels 2>/dev/null || true
```

**STOP — snapshot before any NPZ (this is your replica):**

```bash
# on MacBook, after S2 bootstrap verified (no NPZ yet, ~1.2GB disk)
hcloud server poweroff htz-v15-s2
hcloud image create --type snapshot --server htz-v15-s2 --description "v15-clean-s2-before-npz-$(date +%Y%m%d)"
hcloud server poweron htz-v15-s2
# note image ID: hcloud image list | grep v15-clean
```

*Why:* S3/S4 from snapshot boot in 45s with venv+code ready — no 4-min bootstrap per box. This is how you go from 1 to 4 boxes in record time.

---

## 4. First NPZ → S2 produces instantly, rest in background

```bash
# on S1 — first symbol of S2's shard
SHARD2_SYM=$(head -1 ~/binance-sandbox/V15_SHARD_2of4.txt | sed 's/_.*//')
# ensure 15m-only stripped NPZ exists (S1 has 880 files)
ls -lh ~/binance-sandbox/backtest_v8/indicators/${SHARD2_SYM}.npz
# push ONE
rsync -avz --progress ~/binance-sandbox/backtest_v8/indicators/${SHARD2_SYM}.npz niels@s2:~/binance-sandbox/backtest_v8/indicators/
ssh niels@s2 "ls -lh ~/binance-sandbox/backtest_v8/indicators/${SHARD2_SYM}.npz"

# launch S2 on that ONE symbol immediately (while rest copies)
ssh niels@s2 "source ~/binance-sandbox/.venv/bin/activate; export V12_NPZ_CACHE=32; nohup python3 -u ~/binance-sandbox/v15_pilot.py --sym-side $(head -1 ~/binance-sandbox/V15_SHARD_2of4.txt) --window-days 30 --vector-only > /tmp/v15_\$(head -1 ~/binance-sandbox/V15_SHARD_2of4.txt).log 2>&1 & echo \$!; sleep 2; tail -n 20 /tmp/v15_*.log"

# background copy remaining NPZs — bwlimit so production not starved, tmux so survives
ssh s1-int "tmux new -d -s npz_s2 'rsync -avz --progress --bwlimit=8000 --partial --inplace ~/binance-sandbox/backtest_v8/indicators/*.npz niels@s2:~/binance-sandbox/backtest_v8/indicators/'"
# monitor: ssh s1-int "tmux attach -t npz_s2"  then Ctrl-b d
# also:
ssh niels@s2 "watch -n 2 'ls ~/binance-sandbox/backtest_v8/indicators/*.npz | wc -l; ls -lh ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>&1 | tail'"
```

---

## 5. Gate: S2 must produce ONE correct xlsx before you clone

**Do NOT create S3 until S2 passes — 3 min check:**

```bash
ssh niels@s2 "ls -lh ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*_30d_matrix*.xlsx 2>&1 | tail"
ssh niels@s2 "ls -lh ~/binance-sandbox/data/reports/lifecycle_pilot/*_progress.json 2>&1 | tail"
# strict checker (agent must run, not claim)
ssh niels@s2 "python3 -c \"
import openpyxl, pathlib, json
p = sorted(pathlib.Path('~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL').expanduser().glob('*_30d_matrix*.xlsx'))[-1]
wb = openpyxl.load_workbook(str(p), data_only=True)
print('sheets', len(wb.sheetnames), wb.sheetnames[:3])
# Results_Deltas col5 not empty/repeated
ws = wb['Results_Deltas']
vals = [ws.cell(r,5).value for r in range(2,12) if ws.cell(r,5).value not in (None,'')]
print('Results_Deltas col5', vals[:5], 'repeated?', len(set(vals))==1 and len(vals)>=5)
# L:BI yellows
sh='ENTRY_REVERSAL_BOUNCE'
if sh in wb.sheetnames:
    ws=wb[sh]
    yellows = any(ws.cell(3,c).value not in (None,'') for c in range(12,18))
    print('L:BI yellows', yellows)
wb.close()
# progress
import json
q = sorted(pathlib.Path('~/binance-sandbox/data/reports/lifecycle_pilot').expanduser().glob('*_progress.json'))[-1]
j=json.loads(q.read_text())
print('progress done', len(j.get('done',{})), 'cum', round(j.get('cumulative_gain',0),2))
\""
# if strict fails → STOP, repair, rm bad xlsx, do NOT promote to S1
# on pass: rsync that ONE verified xlsx back to S1 immediately
rsync -avz --progress niels@s2:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx s1-int:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/
rsync -avz --progress niels@s2:~/binance-sandbox/data/reports/lifecycle_pilot/*.json s1-int:~/binance-sandbox/data/reports/lifecycle_pilot/
```

---

## 6. Clone S3/S4 from snapshot — <60s each

```bash
# use snapshot from §3 — no re-bootstrap
SNAP=$(hcloud image list | grep v15-clean | head -1 | awk '{print $1}')

hcloud server create --name htz-v15-s3 --type cx33 --image $SNAP --ssh-key macbook-niels --location nbg1 &
hcloud server create --name htz-v15-s4 --type cx33 --image $SNAP --ssh-key macbook-niels --location nbg1 &
wait
hcloud server list | grep htz-v15

# add S3/S4 to ~/.ssh/config on BOTH Mac + S1 (see §7), then:
for h in s3 s4; do ssh niels@$h "hostname; ls ~/binance-sandbox/v15_pilot.py && echo ok"; done

# push ONE first NPZ per box + launch, same as §4 but for S3/S4 shards
for i in 3 4; do
  SYM=$(head -1 ~/binance-sandbox/V15_SHARD_${i}of4.txt | sed 's/_.*//')
  rsync -avz --progress ~/binance-sandbox/backtest_v8/indicators/${SYM}.npz niels@s${i}:~/binance-sandbox/backtest_v8/indicators/
  ssh niels@s${i} "source ~/binance-sandbox/.venv/bin/activate; export V12_NPZ_CACHE=32; nohup python3 -u ~/binance-sandbox/v15_pilot.py --sym-side $(head -1 ~/binance-sandbox/V15_SHARD_${i}of4.txt) --window-days 30 --vector-only > /tmp/v15_\$(head -1 ~/binance-sandbox/V15_SHARD_${i}of4.txt).log 2>&1 &"
  ssh s1-int "tmux new -d -s npz_s${i} 'rsync -avz --bwlimit=8000 --partial --inplace ~/binance-sandbox/backtest_v8/indicators/*.npz niels@s${i}:~/binance-sandbox/backtest_v8/indicators/'"
done
```

---

## 7. Run contract — 40 syms, 2 hours

**Per box (S1 + S2/S3/S4):**

```bash
source ~/binance-sandbox/.venv/bin/activate; export V12_NPZ_CACHE=32
# shard loop — each box ONLY its shard
for symside in $(cat ~/binance-sandbox/V15_SHARD_1of4.txt); do  # 2of4 on S2, etc.
  python3 -u v15_pilot.py --sym-side $symside --window-days 30 --vector-only
done
# logs: /tmp/v15_*.log  heartbeats: /tmp/v14_heartbeat_*.txt  progress: data/reports/lifecycle_pilot/*_progress.json
```

**Cell-by-cell fills:** `L:BI` yellows + `Results_Deltas` col5/col8, `E` blank until `F>0`, `F` combined switch+ALL pos yellows, `C` ALL pos — `BASELINE` stays empty until POS, `wb_keep` open per sheet, `_atomic_save` tmp+fsync+rename+.bak, `MAX_ROWS 50000`, `workers 16`, `per_cell 1.0s` (30D) / `0.5s` (7D).

**Monitor (any host):**
```bash
watch -n 5 'for h in s1 s2 s3 s4; do echo "== $h =="; ssh niels@$h "ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>&1 | wc -l; cat ~/binance-sandbox/data/reports/lifecycle_pilot/*_progress.json 2>&1 | python3 -c \"import json,glob; print([(p, len(json.loads(open(p).read()).get(\"done\",{}))) for p in glob.glob(\"/home/niels/binance-sandbox/data/reports/lifecycle_pilot/*_progress.json\")][:2])" 2>&1 | head; done'
```

---

## 8. Results — keep on S1, sync to MacBook (boxes ephemeral)

**Every 15 min or per-symbol, box → S1 → Mac:**

```bash
# box → S1 (from box, or from S1 pulling)
rsync -avz --progress ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx s1-int:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/
rsync -avz --progress ~/binance-sandbox/data/reports/lifecycle_pilot/*.json s1-int:~/binance-sandbox/data/reports/lifecycle_pilot/

# S1 → MacBook (from Mac, via s1-int tunnel; ensure ssh -fNT s1-sftp running)
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" s1-int:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx /Users/niels/Documents/binance/SPREADSHEETS/V15_V16_CELL_BY_CELL/
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" s1-int:~/binance-sandbox/data/reports/lifecycle_pilot/*.json /Users/niels/Documents/binance/data/reports/lifecycle_pilot/
rsync -avz -e "ssh -p 2201 -i ~/.ssh/id_ed25519" s1-int:~/binance-sandbox/SPREADSHEETS/*.xlsx /Users/niels/Documents/binance/SPREADSHEETS/
```

Automate on S1 via cron every 15 min (see `INFRASTRUCTURE.md` BOX 135 pattern) or `box_sync_all.sh`.

---

## 9. SSH matrix — complete connectivity

**On MacBook `~/.ssh/config`:**
```
Host s1-int
  HostName 127.0.0.1
  Port 2201
  User niels
  IdentityFile ~/.ssh/id_ed25519

Host s2
  HostName <s2 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
Host s3
  HostName <s3 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
Host s4
  HostName <s4 public IP>
  User niels
  IdentityFile ~/.ssh/id_ed25519
```

**On S1 `~/.ssh/config` — identical `s2`/`s3`/`s4` entries** (so `ssh s2` works from S1). Test: `ssh s2 "hostname; ls ~/binance-sandbox/v15_pilot.py"` from both Mac and S1.

**Ensure `~/.ssh/id_ed25519` exists on Mac and S1 pubkey is in every box's `authorized_keys` (done §3).**

---

## 10. Teardown — keep replica, delete boxes, keep results

```bash
# after all 40 done and verified on S1+Mac (ls counts match, strict checker passes on S1)
hcloud server delete htz-v15-s2
hcloud server delete htz-v15-s3
hcloud server delete htz-v15-s4
# KEEP snapshot v15-clean-s2-before-npz for next run — do NOT delete image
hcloud image list | grep v15-clean
# KEEP S1 forever — canonical
# Tag project v15-cell-by-cell for billing, delete project if empty
```

---

## 11. Agent step order (copy-paste, 1 box at a time)

1. Split 40 → `V15_SHARD_*of4.txt` (§1) commit to S1+Mac.
2. `hcloud server create htz-v15-s2` only.
3. Bootstrap S2 §3 (no NPZ), snapshot §3 before NPZ.
4. Push ONE first NPZ §4, launch S2 on that ONE, background rsync rest.
5. **Gate §5** — verify S2 ONE xlsx strictly, rsync to S1. **Do NOT proceed if fail.**
6. Clone S3/S4 from snapshot §6 (<60s).
7. Push ONE NPZ + launch per box, background rest.
8. Run shard loops §7 — 10 syms/box → ~2h total.
9. Sync §8 every 15 min to S1 then Mac.
10. Teardown §10 — delete boxes, keep snapshot + all results on S1/Mac.

> RESTORE=DEATH PENALTY: never restore `TEMPLATE.xlsx` without diff. DEAT PENALTY on empty/repeated/0.0 — checker must STOP and repair. Keep `hcloud image` replica before data files to make next 4-box spin-up <2 min.
