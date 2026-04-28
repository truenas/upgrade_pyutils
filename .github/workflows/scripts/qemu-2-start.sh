#!/usr/bin/env bash

######################################################################
# Boot an Ubuntu 24.04 cloud VM. ZFS lives in Ubuntu's main kernel
# (no DKMS, no module build), so the VM is ready to `modprobe zfs`
# as soon as zfsutils-linux is installed.
######################################################################

set -eu

OS="ubuntu-noble"
URL="https://cloud-images.ubuntu.com/noble/current/noble-server-cloudimg-amd64.img"
VM_NAME="upgrade-pyutils-test"
VM_IP="192.168.122.10"
VM_MAC="52:54:00:83:79:11"

WORK_DIR="/tmp/qemu-work"
mkdir -p "$WORK_DIR"
cd "$WORK_DIR"

echo "Downloading Ubuntu 24.04 cloud image..."
if [ ! -f "noble.qcow2" ]; then
  wget -q --show-progress "$URL" -O noble.qcow2
fi

echo "Creating VM disk..."
qemu-img create -f qcow2 -F qcow2 -b "$WORK_DIR/noble.qcow2" "$WORK_DIR/vm-disk.qcow2" 40G

PUBKEY=$(cat ~/.ssh/id_ed25519.pub)

cat <<EOF > /tmp/user-data
#cloud-config

hostname: $OS

users:
- name: ubuntu
  sudo: ALL=(ALL) NOPASSWD:ALL
  shell: /bin/bash
  ssh_authorized_keys:
    - $PUBKEY

packages:
  - python3
  - python3-pytest
  - rsync
  - zfsutils-linux

runcmd:
  - echo "VM initialization complete"

growpart:
  mode: auto
  devices: ['/']
  ignore_growroot_disabled: false
EOF

sudo virsh net-update default add ip-dhcp-host \
  "<host mac='$VM_MAC' ip='$VM_IP'/>" --live --config || true

echo "Starting VM..."
sudo virt-install \
  --name "$VM_NAME" \
  --os-variant ubuntu24.04 \
  --cpu host-passthrough \
  --virt-type=kvm \
  --vcpus=4 \
  --memory 4096 \
  --graphics none \
  --network bridge=virbr0,model=virtio,mac="$VM_MAC" \
  --cloud-init user-data=/tmp/user-data \
  --disk path="$WORK_DIR/vm-disk.qcow2",format=qcow2,bus=virtio \
  --import \
  --noautoconsole >/dev/null

echo "Waiting for VM ssh..."
for i in {1..60}; do
  if ssh -o ConnectTimeout=2 ubuntu@$VM_IP "echo 'VM ready'" 2>/dev/null; then
    echo "VM is accessible via SSH"
    break
  fi
  echo "Waiting for VM... ($i/60)"
  sleep 5
done

if ! ssh ubuntu@$VM_IP "uname -a"; then
  echo "ERROR: VM is not accessible"
  exit 1
fi

echo "Waiting for cloud-init package install to finish..."
ssh ubuntu@$VM_IP "cloud-init status --wait"

echo "$VM_IP vm-test" | sudo tee -a /etc/hosts >/dev/null

cat <<EOF > /tmp/vm-info.sh
export VM_IP="$VM_IP"
export VM_NAME="$VM_NAME"
export WORK_DIR="$WORK_DIR"
export VM_USER="ubuntu"
EOF

echo "VM started successfully at $VM_IP"
