#!/usr/bin/env bash
######################################################################
# Install QEMU/libvirt on the GitHub runner so we can boot a Debian
# Trixie VM. Modeled on truenas_pylibzfs's setup script.
######################################################################

set -eu

echo "Setting up QEMU environment..."

export DEBIAN_FRONTEND="noninteractive"
sudo apt-get -y update
sudo apt-get install -y \
  cloud-image-utils \
  guestfs-tools \
  virt-manager \
  qemu-system-x86 \
  qemu-utils \
  libvirt-daemon-system \
  libvirt-clients \
  rsync \
  wget

# Generate ssh keys for VM access.
rm -f ~/.ssh/id_ed25519
ssh-keygen -t ed25519 -f ~/.ssh/id_ed25519 -q -N ""

# Stop services we don't need so they don't compete for resources.
sudo systemctl stop docker.socket || true
sudo systemctl stop multipathd.socket || true

mkdir -p "$HOME/.ssh"
cat <<'EOF' >> "$HOME/.ssh/config"
StrictHostKeyChecking no
ConnectTimeout 10
EOF

sudo systemctl start libvirtd
sudo systemctl enable libvirtd
sudo usermod -a -G libvirt "$USER"

echo "QEMU setup complete"
