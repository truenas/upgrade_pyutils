#!/usr/bin/env bash
######################################################################
# Print a one-line VM-test status to the GHA log so the summary view
# is informative without expanding the test step.
######################################################################

set -eu

echo "=========================================="
echo "VM Test Summary"
echo "=========================================="

if [ -f /tmp/test-logs/journalctl.log ]; then
  echo "Logs collected: yes"
else
  echo "Logs collected: no (VM may not have come up)"
fi

if [ -f /tmp/qemu-logs.tar.gz ]; then
  echo "Artifact: /tmp/qemu-logs.tar.gz ($(stat -c %s /tmp/qemu-logs.tar.gz) bytes)"
fi
echo "=========================================="
