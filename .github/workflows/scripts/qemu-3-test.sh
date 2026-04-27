#!/usr/bin/env bash

######################################################################
# Install the .deb in the VM, copy the source for tests, modprobe zfs,
# and run the integration suite. ZFS is part of Ubuntu's main kernel,
# so no module build is required.
######################################################################

set -eu

source /tmp/vm-info.sh

echo "Copying .deb and source to VM..."
DEB=$(ls "$GITHUB_WORKSPACE"/truenas-initrd_*.deb)
scp "$DEB" "$VM_USER@$VM_IP:/tmp/truenas-initrd.deb"

ssh "$VM_USER@$VM_IP" "mkdir -p ~/upgrade_pyutils"
rsync -az --delete \
  --exclude='.git' \
  --exclude='.mypy_cache' \
  --exclude='.pytest_cache' \
  --exclude='.ruff_cache' \
  --exclude='__pycache__' \
  "$GITHUB_WORKSPACE/" "$VM_USER@$VM_IP:~/upgrade_pyutils/"

ssh "$VM_USER@$VM_IP" 'sudo bash -s' <<'REMOTE_SCRIPT'
set -eu

echo "=========================================="
echo "Loading ZFS kernel module"
echo "=========================================="
modprobe zfs || {
  echo "ERROR: modprobe zfs failed"
  dmesg | tail -40
  exit 1
}
if ! lsmod | grep -q '^zfs '; then
  echo "ERROR: zfs module not loaded after modprobe"
  exit 1
fi
lsmod | grep zfs

echo "=========================================="
echo "Installing the .deb"
echo "=========================================="
# --force-depends because initramfs-tools isn't relevant in the test env;
# we only care that the script + upgrade_pyutils package land at the
# correct paths and that the script's --help works end to end.
dpkg -i --force-depends /tmp/truenas-initrd.deb
/usr/local/bin/truenas-initrd.py --help | head -1

echo "=========================================="
echo "Running integration tests"
echo "=========================================="
cd /home/ubuntu/upgrade_pyutils
python3 -m pytest tests/integration -m integration -v
REMOTE_SCRIPT
