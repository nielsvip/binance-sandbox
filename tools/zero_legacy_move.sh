cd ~/v15_run17_20260930 || exit 1
mkdir -p legacy_old_code/progress legacy_old_code/chain
for f in progress/*_v14_progress.json; do
  ss=$(basename $f _v14_progress.json)
  python3 - "$f" <<P || continue
import json,sys
d=json.load(open(sys.argv[1]))
sys.exit(0 if (d.get("final_gain") is not None and "naked_binding" not in json.dumps(d)) else 1)
P
  sym=${ss%_LONG}; sym=${sym%_SHORT}
  if pgrep -f "[-]-sym-side ${sym}_" >/dev/null 2>&1; then echo "skip(running) $ss"; continue; fi
  mv $f legacy_old_code/progress/ && echo "moved $ss"
  find chain -name "${ss}*" -maxdepth 3 2>/dev/null | while read c; do mkdir -p legacy_old_code/$(dirname $c); mv "$c" legacy_old_code/$c 2>/dev/null; done
  mkdir -p ~/v15_mac_done/_legacy_fake_zeros; mv ~/v15_mac_done/${ss}_bh*_30d_* ~/v15_mac_done/_legacy_fake_zeros/ 2>/dev/null
done
