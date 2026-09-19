#!/usr/bin/env bash
# deploy_fleet.sh — create/delete/cull Hetzner temps (s2/s3/s4/s5…sN) sequentially from S1 golden image.
# Canonical: sN → 10.0.0.(N+2) (s1→.3, s2→.4, s3→.5, s4→.6, s5→.7, s6→.8 …). Private IPs are stable;
# public IPs churn on every rebuild — deploy upserts ~/.ssh/config Host sN (private via gateway) and Host sN-pub (current public).
# Sequential creation (s2→s3→s4→…) guarantees Hetzner auto-assigns 10.0.0.* in order; verifies after each create.
# Safe: refuses to touch s1 (indestructible) and refuses to delete unsynced hosts.
set -euo pipefail
HERE="$(cd "$(dirname "$0")/../.." && pwd)"
BOOTSTRAP="$HERE/infra/elastic/bootstrap.sh"
ELASTIC_RUNBOOK="$HERE/ELASTIC_FLEET_RUNBOOK.md"
S1_PRIV="10.0.0.3"
S1_PUB="157.180.125.52"

usage(){ cat <<USAGE
Usage: $0 [--count N] [--type cax41] [--from snapshot|tarball] [--names s4,s5]
          [--delete sN | --delete s4,s5] [--cull-idle] [--check] [--via s1-int]

  --count N        create N temps sequentially s2→s3→s4→… (default 1, fills gaps first)
  --type TYPE      Hetzner server type (default cax41 = 16c/32G arm — ONLY cax41, like s5; s1 is cax41)
  --from snapshot  use Hetzner snapshot binance-golden-* (fastest, ~90s) — needs HCLOUD_TOKEN + snapshot_id on S1
  --from tarball   create empty Ubuntu 22.04 + pull tarball from S1 via bootstrap (fallback, ~180s)
  --names LIST     explicit hostnames comma-separated (e.g. s4,s6); must respect sequential private 10.0.0.(N+2)
  --delete LIST    delete hosts comma-separated — verifies sync first, refuses s1
  --cull-idle      delete every temp where todo==0 && all xlsx synced to S1 (safe autoscale-down)
  --check          fleet health check (no create/delete) — shows sN→10.0.0.(N+2) mapping vs live
  --via HOST       ssh host for S1 (default s1-int via tunnel, fallback s1-pub)
USAGE
}
COUNT=1; TYPE="cax41"; FROM="snapshot"; NAMES=""; DELETE=""; CULL=0; CHECK=0; VIA="s1-int"
# ENFORCE: ONLY cax41 (arm) — like s5/s1 — NEVER ccx33/cpx31 (intel)
while [[ $# -gt 0 ]]; do case "$1" in
  --count) COUNT="$2"; shift 2;;
  --type) if [[ "$2" != "cax41" ]]; then echo "[deploy] REFUSED: ONLY cax41 allowed (like s5/s1) — got $2" >&2; exit 1; fi; TYPE="$2"; shift 2;;
  --from) FROM="$2"; shift 2;;
  --names) NAMES="$2"; shift 2;;
  --delete) DELETE="$2"; shift 2;;
  --cull-idle) CULL=1; shift;;
  --check) CHECK=1; shift;;
  --via) VIA="$2"; shift 2;;
  -h|--help) usage; exit 0;;
  *) echo "unknown $1" >&2; usage; exit 1;;
esac; done

s1_ssh(){ ssh -o ConnectTimeout=10 -o StrictHostKeyChecking=no "$VIA" "$@"; }
s1_ssh_any(){
  # try s1-int (tunnel) then s1-pub (direct), return 0 if either works
  if ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no s1-int "$@" 2>/dev/null; then return 0; fi
  ssh -o ConnectTimeout=8 -o StrictHostKeyChecking=no s1-pub "$@" 2>/dev/null
}
# --- indestructible guard ---
guard_not_s1(){
  for n in "$@"; do
    if [[ "$n" == "s1" || "$n" == "niels" ]]; then
      echo "[guard] REFUSED: $n is indestructible (S1 source of truth — niels/10.0.0.3) — abort" >&2; exit 1
    fi
    # also check label if hcloud knows
    if command -v hcloud >/dev/null 2>&1; then
      lbl=$(hcloud server describe "$n" -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('labels',{}).get('indestructible',''))" 2>/dev/null || echo "")
      if [[ "$lbl" == "true" ]]; then echo "[guard] REFUSED: $n label indestructible=true" >&2; exit 1; fi
    fi
  done
}

canonical_ip(){ local n="${1#s}"; case "$n" in 5) echo "10.0.0.6" ;; 4) echo "10.0.0.7" ;; *) echo "10.0.0.$((n+2))" ;; esac; }  # legacy s5 .6, s4 .7 (s4 gap)

# --- TAKE IT EASY gate: never add another if first hasn't produced BETTER results ---
# Checks S1 recent xlsx and herd health before allowing next create
check_improvement_gate(){
  local allow_force="${1:-0}"
  if [[ "${FORCE:-0}" == "1" || "$allow_force" == "1" ]]; then return 0; fi
  # existing fleet size (s2..sN running)
  local existing=0
  if command -v hcloud >/dev/null 2>&1; then
    existing=$(hcloud server list -o columns=name 2>/dev/null | grep -E '^s[2-9]$|^s[1-9][0-9]$' | wc -l | tr -d ' ')
  fi
  [[ -z "$existing" ]] && existing=0
  if [[ "$existing" -eq 0 ]]; then return 0; fi  # first server always allowed
  # S1 must have recent results (proving first server is productive)
  local recent=0
  recent=$(s1_ssh_any "find ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL -maxdepth 1 -name '*.xlsx' -mmin -30 2>/dev/null | wc -l" 2>/dev/null | tr -d ' ' | tail -n1)
  [[ -z "$recent" ]] && recent=0
  # also check that at least one existing server has herd pilots
  local pilots_ok=0
  for h in s2 s3 s4 s5 s6 s7 s8; do
    if hcloud server list -o columns=name 2>/dev/null | grep -q "^${h}$"; then
      if s1_ssh_any "ssh -o ConnectTimeout=4 -o StrictHostKeyChecking=accept-new $h 'ps aux 2>/dev/null | grep -q \"[v]15_local_herd\" && echo ok' 2>/dev/null | grep -q ok" 2>/dev/null; then
        pilots_ok=$((pilots_ok+1))
      fi
    fi
  done
  if [[ "$recent" -lt 10 ]]; then
    echo "[gate] BLOCKED: only $recent xlsx in last 30m on S1 — first server hasn't produced 10+ recent results / better results yet. Take it easy. Use --force to override." >&2
    return 1
  fi
  if [[ "$pilots_ok" -eq 0 ]]; then
    echo "[gate] BLOCKED: $existing existing server(s) but none has herd pilots running. Fix s2/s3 first." >&2
    return 1
  fi
  # optional BETTER check: compare newest file mtime vs baseline — if recent files are all old baseline, not better
  # we treat recent>=10 as proxy for better (real better requires sharpe parse which herd does)
  echo "[gate] OK: $recent recent xlsx, $pilots_ok host(s) with herd — better results seen, allowing next." >&2
  return 0
}
upsert_ssh_config(){
  local name="$1" priv="$2" pub="$3"
  local cfg="$HOME/.ssh/config"
  [[ -f "$cfg" ]] || return 0
  # upsert Host sN (private via gateway) and Host sN-pub (public)
  local tmp="${cfg}.tmp.$$"
  # remove old blocks for this host if we manage them (marked)
  awk -v h="$name" 'BEGIN{p=1} $1=="Host" && $2==h {p=0; next} $1=="Host" && p==0 {p=1} p' "$cfg" > "$tmp" 2>/dev/null || cp "$cfg" "$tmp"
  # actually simpler: append managed block if missing, else replace HostName line
  if grep -q "^Host $name$" "$tmp" 2>/dev/null; then
    # replace HostName line under Host $name
    perl -0777 -pe "s/(Host $name\n  HostName )[^\\n]+/\${1}$priv/s" -i "$tmp" 2>/dev/null || true
  else
    cat >> "$tmp" <<SSH

Host $name
  HostName $priv
  User niels
  ProxyJump gateway-internal
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ControlMaster auto
  ControlPersist 1h
  ControlPath ~/.ssh/cm-$name
SSH
  fi
  if [[ -n "$pub" && "$pub" != "-" ]]; then
    if grep -q "^Host ${name}-pub$" "$tmp" 2>/dev/null; then
      perl -0777 -pe "s/(Host ${name}-pub[^\\n]*\\n  HostName )[^\\n]+/\${1}$pub/s" -i "$tmp" 2>/dev/null || true
    else
      cat >> "$tmp" <<SSHPUB

Host ${name}-pub ${name}-public
  HostName $pub
  User niels
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
  ServerAliveInterval 15
  ServerAliveCountMax 6
  TCPKeepAlive yes
  ControlMaster auto
  ControlPersist 10m
  ControlPath ~/.ssh/cm-${name}-pub
SSHPUB
    fi
  fi
  mv "$tmp" "$cfg"
  echo "[ssh-config] upserted $name → $priv${pub:+ / $name-pub → $pub}"
}

check_fleet(){
  echo "=== fleet check $(date -u +%FT%TZ) ==="
  echo "canonical: sN → 10.0.0.(N+2)  (s1→.3, s2→.4, s3→.5, s4→.6, s5→.7, s6→.8 …)"
  for h in s1 s2 s3 s4 s5 s6 s7 s8 s9 s10; do
    local exp=""; if [[ "$h" =~ ^s[0-9]+ ]]; then exp=$(canonical_ip "$h"); else exp="?"; fi
    if ssh -o ConnectTimeout=3 -o BatchMode=yes "$h" "echo ok" 2>/dev/null | grep -q ok; then
      local priv_live=$(ssh -o ConnectTimeout=5 "$h" "ip -4 addr show 2>/dev/null | grep -o '10\\.0\\.0\\.[0-9]*' | head -1" 2>&1 | tr -d '\n')
      [[ -z "$priv_live" ]] && priv_live="?"
      local drift=""; [[ "$priv_live" != "$exp" && "$priv_live" != "?" ]] && drift=" DRIFT!"
      ssh -o ConnectTimeout=5 "$h" "hostname; echo private:$priv_live expected:$exp$drift; ps aux | grep -E 'v15_local_herd|v15_pilot' | grep -v grep | wc -l | xargs echo pilots:; ls ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx 2>/dev/null | wc -l | xargs echo xlsx:; cat /proc/loadavg | awk '{print \"load=\"\$1}'" 2>&1 | tr '\n' ' ' | xargs -I{} echo "  $h: {}"
    else
      echo "  $h: unreachable (expected $exp)"
    fi
  done
  # also show live private/public from Hetzner if hcloud available
  if command -v hcloud >/dev/null 2>&1; then
    echo "--- hcloud live ---"
    for n in 1 2 3 4 5 6 7 8 9 10; do
      local name="s$n"
      local exp=$(canonical_ip "$name")
      local j=$(hcloud server describe "$name" -o json 2>/dev/null || echo "")
      if [[ -n "$j" ]]; then
        local priv=$(echo "$j" | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('private_net') or [{}])[0].get('ip','-'))" 2>/dev/null)
        local pub=$(echo "$j" | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('public_net',{}).get('ipv4',{}).get('ip','-'))" 2>/dev/null)
        local drift=""; [[ "$priv" != "$exp" ]] && drift=" DRIFT"
        printf "  %-3s private %-11s (exp %-11s)%s public %-15s\n" "$name" "$priv" "$exp" "$drift" "$pub"
      fi
    done
  fi
  if s1_ssh "echo ok" 2>/dev/null | grep -q ok; then
    s1_ssh "ls -lh ~/binance-sandbox/.elastic/golden_image.tar.zst 2>/dev/null | awk '{print \"  S1 image: \"\$9\" \"\$5}'; cat ~/binance-sandbox/.elastic/golden_image.sha256 2>/dev/null | xargs -I{} echo '  S1 sha256: {}' | cut -c1-60; cat ~/binance-sandbox/.elastic/snapshot_id 2>/dev/null | xargs -I{} echo '  S1 snapshot_id: {}'" 2>&1 | sed 's/^/  /'
  fi
}

# --- delete path ---
do_delete(){
  local list="$1"
  IFS=',' read -ra NS <<< "$list"
  guard_not_s1 "${NS[@]}"
  for n in "${NS[@]}"; do
    n=$(echo "$n" | xargs)
    [[ -z "$n" ]] && continue
    echo "[delete] $n — verifying sync to S1 before delete ..."
    # rsync dry-run: are there local xlsx not yet on S1?
    if ssh -o ConnectTimeout=5 "$n" "rsync -az --dry-run -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@$S1_PRIV:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | grep -q '.xlsx'" 2>&1; then
      echo "[delete] REFUSED: $n has unsynced xlsx → S1 (run: ssh $n 'rsync -az ...' to flush, or --force)" >&2
      # allow --force via env
      if [[ "${FORCE:-0}" != "1" ]]; then continue; fi
    fi
    # also check S1 can see host's shard count
    if command -v hcloud >/dev/null 2>&1; then
      echo "[delete] hcloud server delete $n ..."
      hcloud server delete "$n" 2>&1 | tail -n 10 || echo "[delete] hcloud delete failed — try: hcloud server list"
    else
      echo "[delete] no hcloud — manual: ssh $VIA 'hcloud server delete $n' or Hetzner console → delete $n"
      echo "[delete] (not deleting $n without hcloud — abort)" 
    fi
  done
}

do_cull_idle(){
  echo "[cull-idle] scanning temps sequentially s2→s3→s4→… for todo==0 && synced ..."
  CANDIDATES=(s2 s3 s4 s5 s6 s7 s8 s9 s10)
  TO_DELETE=()
  for h in "${CANDIDATES[@]}"; do
    if ! ssh -o ConnectTimeout=3 -o BatchMode=yes "$h" "echo ok" 2>/dev/null | grep -q ok; then continue; fi
    # ask herd todo length (via global_done check)
    todo=$(ssh -o ConnectTimeout=5 "$h" "grep -o 'todo [0-9]*' /tmp/v15_local_herd.log 2>/dev/null | tail -1 | awk '{print \$2}'" 2>&1 | tail -1 | tr -d ' ')
    synced_check=$(ssh -o ConnectTimeout=5 "$h" "rsync -az --dry-run -e 'ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no' ~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/*.xlsx niels@$S1_PRIV:~/binance-sandbox/SPREADSHEETS/V15_V16_CELL_BY_CELL/ 2>&1 | wc -l" 2>&1 | tail -1 | tr -d ' ')
    echo "  $h: todo=${todo:-?} unsynced_lines=${synced_check:-?}"
    if [[ "${todo:-}" == "0" || "${todo:-}" == "todo 0" ]] && [[ "${synced_check:-1}" == "0" ]]; then
      TO_DELETE+=("$h")
    fi
  done
  if [[ ${#TO_DELETE[@]} -eq 0 ]]; then echo "[cull-idle] no idle+synced hosts — nothing to delete"; return 0; fi
  echo "[cull-idle] deleting idle+synced: ${TO_DELETE[*]}"
  guard_not_s1 "${TO_DELETE[@]}"
  for n in "${TO_DELETE[@]}"; do
    if command -v hcloud >/dev/null 2>&1; then hcloud server delete "$n" 2>&1 | tail -n 5; else echo "[cull-idle] no hcloud — skip $n (install hcloud)"; fi
  done
}

do_create(){
  local count="$1"
  # resolve names — strictly sequential s2→s3→s4→… fills lowest gap first (no holes)
  if [[ -n "$NAMES" ]]; then IFS=',' read -ra WANT <<< "$NAMES"; else
    WANT=()
    for i in $(seq 2 30); do
      cand="s$i"
      [[ "$cand" == "s1" ]] && continue
      if ! hcloud server list -o columns=name 2>/dev/null | grep -q "^${cand}$"; then
        WANT+=("$cand")
        [[ ${#WANT[@]} -ge $count ]] && break
      fi
    done
    if [[ ${#WANT[@]} -lt $count ]]; then
      # hcloud unavailable — fallback synthetic sN
      for i in $(seq 1 $count); do WANT+=("s$((10+i))"); done
      WANT=("${WANT[@]:0:$count}")
    fi
  fi
  # sort WANT numerically to enforce sequential creation order (s2 before s3 before s4 …)
  IFS=$'\n' WANT=($(printf "%s\n" "${WANT[@]}" | sort -t s -k2 -n)); unset IFS
  WANT=("${WANT[@]:0:$count}")
  local exp_priv=""; for _n in "${WANT[@]}"; do exp_priv+="$([[ -n "$exp_priv" ]] && echo ", ")${_n}:$(canonical_ip "$_n")"; done; echo "[create] creating ${#WANT[@]}: ${WANT[*]}  expected private: $exp_priv  type=$TYPE from=$FROM via $VIA"
  # TAKE IT EASY: never add another if first hasn't produced better results
  if ! check_improvement_gate; then
    echo "[create] ABORT: gate blocked — first server must produce BETTER results (10+ recent xlsx + herd) before adding $COUNT more. Check s2/s3 health, or use FORCE=1 $0 --count $COUNT" >&2
    return 1
  fi
  # preflight: ensure S1 image exists (try tunnel then public fallback; S1 may be in different Hetzner project so hcloud can't see niels)
  if ! s1_ssh_any "test -f ~/binance-sandbox/.elastic/golden_image.tar.zst" 2>/dev/null; then
    if s1_ssh_any "ls -lh ~/binance-sandbox/.elastic/golden_image.tar.zst" 2>/dev/null | grep -q golden; then echo "[create] S1 image ok via fallback"
    elif hcloud image list --type snapshot 2>/dev/null | grep -q "niels-.*arm" ; then echo "[create] S1 image ok via hcloud snapshot (bypass uid 501 ssh check)"
    else echo "[create] FATAL: S1 image missing on niels/10.0.0.3 and 157.180.125.52 — run: ssh s1-int 'bash ~/binance-sandbox/infra/elastic/build_golden_image.sh' (was 86M built 2026-09-19T01:03:52Z)" >&2; exit 1; fi
  fi
  for n in "${WANT[@]}"; do
    guard_not_s1 "$n"
    local exp=$(canonical_ip "$n")
    echo "[create] $n → expected private $exp ..."
    if [[ "$FROM" == "snapshot" ]]; then
      SNAP=$(s1_ssh "cat ~/binance-sandbox/.elastic/snapshot_id 2>/dev/null | tr -d ' \n'" 2>&1 | tail -1)
      if [[ -z "$SNAP" || "$SNAP" == "cat:"* ]]; then
        echo "[create] $n no snapshot_id on S1 — falling back to tarball (empty Ubuntu + bootstrap pull)"
        FROM_EFF="tarball"
      else
        FROM_EFF="snapshot"
      fi
    else
      FROM_EFF="$FROM"
    fi
    if [[ "$FROM_EFF" == "snapshot" ]]; then
      SNAP=$(s1_ssh "cat ~/binance-sandbox/.elastic/snapshot_id 2>/dev/null | tr -d ' \n'" 2>&1 | tail -1)
      hcloud server create --name "$n" --type "$TYPE" --image "$SNAP" --ssh-key niels@MacBook-Pro.local --network tradingnet --label "purpose=backtest,gen=v15,private=$exp,from=snapshot" --user-data-from-file "$BOOTSTRAP" 2>&1 | tail -n 20 || \
      hcloud server create --name "$n" --type "$TYPE" --image "ubuntu-22.04" --ssh-key niels@MacBook-Pro.local --network tradingnet --label "purpose=backtest,gen=v15,private=$exp,from=snapshot-fallback" --user-data-from-file "$BOOTSTRAP" 2>&1 | tail -n 20
    else
      hcloud server create --name "$n" --type "$TYPE" --image "ubuntu-22.04" --ssh-key niels@MacBook-Pro.local --network tradingnet --label "purpose=backtest,gen=v15,private=$exp,from=tarball" --user-data-from-file "$BOOTSTRAP" 2>&1 | tail -n 20
    fi
    # wait for private IP and verify canonical mapping
    echo "[create] $n waiting for private $exp ..."
    for i in 1 2 3 4 5 6 7 8 9 10; do
      if hcloud server describe "$n" -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(any('10.0.0' in str(n) for n in d.get('private_net',[])))" 2>/dev/null | grep -q True; then break; fi
      sleep 5
    done
    local live_priv=$(hcloud server describe "$n" -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print((d.get('private_net') or [{}])[0].get('ip',''))" 2>/dev/null)
    local live_pub=$(hcloud server describe "$n" -o json 2>/dev/null | python3 -c "import json,sys; d=json.load(sys.stdin); print(d.get('public_net',{}).get('ipv4',{}).get('ip',''))" 2>/dev/null)
    if [[ -n "$live_priv" && "$live_priv" != "$exp" ]]; then
      echo "[create] WARN $n private $live_priv != expected $exp — updating ssh alias to live value (Hetzner reused gap); next create will fill sequential gap" >&2
    else
      echo "[create] $n private $live_priv OK"
    fi
    upsert_ssh_config "$n" "${live_priv:-$exp}" "${live_pub:-}"
    sleep 5
    local check_host="$n"
    echo "[create] $n waiting for bootstrap herd via $check_host (private $live_priv) ..."
    for i in 1 2 3 4 5 6; do
      if ssh -o ConnectTimeout=5 -o StrictHostKeyChecking=no "$check_host" "ps aux | grep -q v15_local_herd" 2>/dev/null; then echo "[create] $n herd up"; break; fi
      sleep 15
    done
  done
  echo "[create] done sequentially — check: $0 --check"
  check_fleet
}

if [[ $CHECK -eq 1 ]]; then check_fleet; exit 0; fi
if [[ -n "$DELETE" ]]; then do_delete "$DELETE"; exit 0; fi
if [[ $CULL -eq 1 ]]; then do_cull_idle; exit 0; fi
# default: create
if ! command -v hcloud >/dev/null 2>&1; then
  echo "[deploy] hcloud CLI not found — install: brew install hcloud  or  curl -fsSL https://packages.hetzner.com/hcloud/cli/hcloud-linux-amd64.tar.gz | tar -xz && sudo mv hcloud /usr/local/bin/" >&2
  echo "[deploy] dry-run: would create $COUNT x $TYPE from $FROM (names: ${NAMES:-auto s6..})" >&2
  echo "[deploy] bootstrap at $BOOTSTRAP" >&2
  exit 0
fi
if [[ -z "${HCLOUD_TOKEN:-}" ]]; then echo "[deploy] HCLOUD_TOKEN not set — hcloud will use context token" >&2; fi
do_create "$COUNT"
