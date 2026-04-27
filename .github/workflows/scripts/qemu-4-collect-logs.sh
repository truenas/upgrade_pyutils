#!/usr/bin/env bash
######################################################################
# Pull system logs out of the VM after the test step (success or fail)
# so a failed run has actionable diagnostics in the artifact.
######################################################################

set -eu

source /tmp/vm-info.sh

LOG_DIR="/tmp/test-logs"
mkdir -p "$LOG_DIR"

ssh "$VM_USER@$VM_IP" "sudo journalctl -n 1000" > "$LOG_DIR/journalctl.log" 2>/dev/null || true
ssh "$VM_USER@$VM_IP" "dmesg" > "$LOG_DIR/dmesg.log" 2>/dev/null || true
ssh "$VM_USER@$VM_IP" "lsmod" > "$LOG_DIR/lsmod.txt" 2>/dev/null || true
ssh "$VM_USER@$VM_IP" "sudo zpool list -v 2>&1; sudo zfs list -t all 2>&1" \
  > "$LOG_DIR/zfs-state.txt" 2>/dev/null || true
ssh "$VM_USER@$VM_IP" "cat /var/log/cloud-init.log /var/log/cloud-init-output.log 2>/dev/null" \
  > "$LOG_DIR/cloud-init.log" 2>/dev/null || true

cd /tmp
tar czf qemu-logs.tar.gz test-logs/
echo "Logs collected at /tmp/qemu-logs.tar.gz"
