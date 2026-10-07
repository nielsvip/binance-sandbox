#!/bin/bash
# one-shot (cron on s1, 11:35Z): apply staged *.par001 (lifecycle_pilot, v12_pilot, v15_pilot) to s1/s2/s5 sandbox, only when the target is still the known pre-change md5; md5 verify; log /tmp/par001_apply.log; removes own cron line.
[ "$(date -u +%H%M)" -lt 1130 ] && [ "$(date -u +%H%M)" -gt 1000 ] && exit 0
declare -A NEW=( [tools/opt/lifecycle_pilot.py]=03b13b7c5e328f015c085a386ee9b34d [tools/opt/v12_pilot.py]=5b91afd80946cf88b46a046ea20fd30a [v15_pilot.py]=d90bb265b49895437a1c7b0fb91f955b )
declare -A OLD=( [tools/opt/lifecycle_pilot.py]=3d71c190a97363d188b91472d1bc6a59 [tools/opt/v12_pilot.py]=fdf10544ae3642228223c578273906e4 [v15_pilot.py]=756607ae767fa4f395b9c1b5adb444ee )
for H in local niels@10.0.0.4 niels@10.0.0.5; do
  for F in "${!NEW[@]}"; do
    CMD="cd ~/binance-sandbox; s=$F.par001; [ \"\$(md5sum \$s | cut -c1-32)\" = ${NEW[$F]} ] || { echo \$(hostname) $F STAGE_BAD; exit 0; }; cur=\$(md5sum $F | cut -c1-32); if [ \$cur = ${NEW[$F]} ]; then echo \$(hostname) $F ALREADY; elif [ \$cur = ${OLD[$F]} ]; then cp $F $F.before_par001; cp \$s $F; echo \$(hostname) $F APPLIED \$(md5sum $F | cut -c1-32); else echo \$(hostname) $F SKIP_DIVERGED \$cur; fi"
    if [ $H = local ]; then bash -c "$CMD"; else ssh -o ConnectTimeout=8 $H "$CMD"; fi
  done
done >> /tmp/par001_apply.log 2>&1
crontab -l | grep -v PAR001_APPLY | crontab -
