#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# Sweep Server Setup — Run on new Hetzner box after provisioning
# Sets up conda env, syncs code + data, starts sweep immediately
#
# Usage FROM MACBOOK:
#   ssh root@<NEW_IP> 'bash -s' < sweep_server_setup.sh
#
# Or copy + run:
#   scp sweep_server_setup.sh root@<NEW_IP>:~/
#   ssh root@<NEW_IP> bash ~/sweep_server_setup.sh
# ═══════════════════════════════════════════════════════════════════

set -e

EXISTING_SERVER="10.0.0.2"  # Existing Hetzner server with all data
NEW_USER="niels"

echo "═══ STEP 1: Create user + SSH keys ═══"
useradd -m -s /bin/bash $NEW_USER 2>/dev/null || true
mkdir -p /home/$NEW_USER/.ssh
# Copy authorized_keys from root (Hetzner puts your key there)
cp /root/.ssh/authorized_keys /home/$NEW_USER/.ssh/ 2>/dev/null || true
chown -R $NEW_USER:$NEW_USER /home/$NEW_USER/.ssh
chmod 700 /home/$NEW_USER/.ssh
chmod 600 /home/$NEW_USER/.ssh/authorized_keys 2>/dev/null || true

echo "═══ STEP 2: System packages ═══"
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv rsync screen htop nfs-common 2>/dev/null || \
    yum install -y python3 python3-pip rsync screen htop nfs-utils 2>/dev/null || true

echo "═══ STEP 3: Install Miniconda ═══"
if [ ! -d "/home/$NEW_USER/.conda" ] && [ ! -d "/home/$NEW_USER/miniconda3" ]; then
    su - $NEW_USER -c '
        wget -q https://repo.anaconda.com/miniconda/Miniconda3-latest-Linux-x86_64.sh -O /tmp/miniconda.sh
        bash /tmp/miniconda.sh -b -p $HOME/miniconda3
        rm /tmp/miniconda.sh
        $HOME/miniconda3/bin/conda init bash
        $HOME/miniconda3/bin/conda create -n binance_env python=3.11 -y
    '
fi

echo "═══ STEP 4: SSH key for internal network ═══"
su - $NEW_USER -c "
    [ -f ~/.ssh/id_ed25519 ] || ssh-keygen -t ed25519 -N '' -f ~/.ssh/id_ed25519
    echo 'Add this key to $EXISTING_SERVER authorized_keys:'
    cat ~/.ssh/id_ed25519.pub
"

echo ""
echo "════════════════════════════════════════════════════════════════"
echo "  MANUAL STEP NEEDED:"
echo "  Copy the public key above to $EXISTING_SERVER:"
echo "    ssh niels@$EXISTING_SERVER 'cat >> ~/.ssh/authorized_keys' <<< 'KEY'"
echo ""
echo "  Then run sweep_server_data.sh to sync data and start sweeping."
echo "════════════════════════════════════════════════════════════════"
