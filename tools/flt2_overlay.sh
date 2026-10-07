#!/bin/bash
# flt2_overlay.sh HOST STAGE_DIR [NAME]  — build /tmp/<NAME> on HOST: symlink farm of ~/binance-sandbox with REAL copies of the staged files overlaid (never symlink the engine: base path would resolve to the sandbox).
H=$1; STAGE=$2; NAME=${3:-flt2_sb}
rsync -az --exclude __pycache__ -e "ssh -S none -o StrictHostKeyChecking=accept-new" "$STAGE"/ $H:/tmp/${NAME}_files/ || exit 1
cat > /tmp/${NAME}_mk.sh <<EOS
set -e
SB=\$HOME/binance-sandbox; D=/tmp/$NAME
rm -rf \$D; mkdir -p \$D/tools/opt
find \$SB -maxdepth 1 -mindepth 1 -print0 | while IFS= read -r -d '' f; do b=\$(basename "\$f"); case "\$b" in v12_quick_engine.py|vec_decisions|tools|__pycache__) ;; *) ln -s "\$f" "\$D/\$b";; esac; done
find \$SB/tools -maxdepth 1 -mindepth 1 -print0 | while IFS= read -r -d '' f; do b=\$(basename "\$f"); [ "\$b" = opt ] || ln -s "\$f" "\$D/tools/\$b"; done
find \$SB/tools/opt -maxdepth 1 -mindepth 1 -print0 | while IFS= read -r -d '' f; do ln -s "\$f" "\$D/tools/opt/\$(basename "\$f")"; done
rm -f \$D/tools/opt/evaluate_v12.py; cp \$SB/tools/opt/evaluate_v12.py \$D/tools/opt/evaluate_v12.py; cp -r \$SB/vec_decisions \$D/vec_decisions; cp \$SB/v12_quick_engine.py \$D/v12_quick_engine.py
rsync -a --exclude data/ /tmp/${NAME}_files/ \$D/
find \$D -name __pycache__ -prune -exec rm -rf {} + 2>/dev/null || true
echo overlay ok; md5sum \$D/v12_quick_engine.py
EOS
scp -q -o StrictHostKeyChecking=accept-new /tmp/${NAME}_mk.sh $H:/tmp/${NAME}_mk.sh && ssh -S none $H "bash /tmp/${NAME}_mk.sh"
