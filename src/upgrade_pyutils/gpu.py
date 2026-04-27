from __future__ import annotations

import os
import re
from typing import Any

# 16-bit class IDs (upper 16 bits of the 24-bit class register) that identify
# graphics devices we treat as GPUs for the purpose of vfio isolation.
# Source: pciutils/lib/header.h.
GPU_CLASS_CODES = {
    0x0300,  # VGA compatible controller
    0x0302,  # 3D controller
    0x0380,  # Display controller (other)
}

# Sysfs roots, factored out as module-level constants so unit tests can point
# them at a fake tree under tmp_path.
PCI_DEVICES_DIR = "/sys/bus/pci/devices"
IOMMU_GROUPS_DIR = "/sys/kernel/iommu_groups"

_PCI_ADDR_RE = re.compile(r"\w+:\w+:\w+\.\w+")


def _read_sysfs_class_id(pci_dir: str) -> int | None:
    """Read /sys/bus/pci/devices/<addr>/class and return the upper 16 bits.

    The class file holds a 24-bit hex value like '0x030000'. Returns None if
    the file is missing or unreadable as hex.
    """
    try:
        with open(os.path.join(pci_dir, "class")) as f:
            return (int(f.read().strip(), 16) >> 8) & 0xFFFF
    except (FileNotFoundError, ValueError):
        return None


def _read_iommu_group_number(pci_dir: str) -> int | None:
    """Resolve the iommu_group symlink under a PCI device dir to its group id."""
    link = os.path.join(pci_dir, "iommu_group")
    try:
        target = os.readlink(link)
    except (FileNotFoundError, OSError):
        return None
    name = os.path.basename(target)
    try:
        return int(name)
    except ValueError:
        return None


def _list_iommu_group_devices(group_id: int) -> list[str]:
    devices_dir = os.path.join(IOMMU_GROUPS_DIR, str(group_id), "devices")
    try:
        entries = sorted(os.listdir(devices_dir))
    except FileNotFoundError:
        return []
    return [e for e in entries if _PCI_ADDR_RE.fullmatch(e)]


def get_gpus() -> list[dict[str, Any]]:
    """Return GPUs found in /sys/bus/pci with their IOMMU group siblings.

    Minimal stdlib-only port of `truenas_pylibvirt.utils.gpu.get_gpus` covering
    only the fields `truenas-initrd.py` consumes:
        [{"addr": {"pci_slot": <addr>}, "devices": [{"pci_slot": <sibling>}, ...]}]
    """
    gpus: list[dict[str, Any]] = []
    try:
        device_names = sorted(os.listdir(PCI_DEVICES_DIR))
    except FileNotFoundError:
        return gpus

    for name in device_names:
        if not _PCI_ADDR_RE.fullmatch(name):
            continue
        pci_dir = os.path.join(PCI_DEVICES_DIR, name)
        class_id = _read_sysfs_class_id(pci_dir)
        if class_id not in GPU_CLASS_CODES:
            continue

        group_id = _read_iommu_group_number(pci_dir)
        if group_id is None:
            siblings: list[str] = [name]
        else:
            siblings = _list_iommu_group_devices(group_id) or [name]

        gpus.append({
            "addr": {"pci_slot": name},
            "devices": [{"pci_slot": s} for s in siblings],
        })

    return gpus
