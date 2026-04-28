from __future__ import annotations

import os

import pytest

from upgrade_pyutils import gpu as gpu_mod


def _build_fake_sysfs(root, devices, iommu_groups):
    """Build a fake sysfs tree.

    devices: dict[str, str] mapping pci addr -> hex class string ("0x030000").
    iommu_groups: dict[int, list[str]] mapping group id -> member addrs.
    """
    pci_root = os.path.join(root, "pci/devices")
    iommu_root = os.path.join(root, "iommu_groups")
    os.makedirs(pci_root, exist_ok=True)

    addr_to_group = {}
    for gid, members in iommu_groups.items():
        gdir = os.path.join(iommu_root, str(gid), "devices")
        os.makedirs(gdir, exist_ok=True)
        for addr in members:
            with open(os.path.join(gdir, addr), "w"):
                pass
            addr_to_group[addr] = gid

    for addr, class_hex in devices.items():
        ddir = os.path.join(pci_root, addr)
        os.makedirs(ddir, exist_ok=True)
        with open(os.path.join(ddir, "class"), "w") as f:
            f.write(class_hex + "\n")
        if addr in addr_to_group:
            target = os.path.join(iommu_root, str(addr_to_group[addr]))
            os.symlink(target, os.path.join(ddir, "iommu_group"))

    return pci_root, iommu_root


@pytest.fixture
def fake_sysfs(tmp_path, monkeypatch):
    def make(devices, iommu_groups):
        pci_root, iommu_root = _build_fake_sysfs(str(tmp_path), devices, iommu_groups)
        monkeypatch.setattr(gpu_mod, "PCI_DEVICES_DIR", pci_root)
        monkeypatch.setattr(gpu_mod, "IOMMU_GROUPS_DIR", iommu_root)
    return make


def test_no_pci_devices_dir_returns_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_mod, "PCI_DEVICES_DIR", str(tmp_path / "missing"))
    assert gpu_mod.get_gpus() == []


def test_single_gpu_with_iommu_siblings(fake_sysfs):
    fake_sysfs(
        devices={
            "0000:01:00.0": "0x030000",  # VGA — GPU
            "0000:01:00.1": "0x040300",  # HDA audio (sibling)
        },
        iommu_groups={12: ["0000:01:00.0", "0000:01:00.1"]},
    )

    result = gpu_mod.get_gpus()

    assert result == [{
        "addr": {"pci_slot": "0000:01:00.0"},
        "devices": [
            {"pci_slot": "0000:01:00.0"},
            {"pci_slot": "0000:01:00.1"},
        ],
    }]


def test_non_gpu_devices_excluded(fake_sysfs):
    fake_sysfs(
        devices={
            "0000:00:1f.2": "0x010601",  # AHCI storage — not a GPU
            "0000:02:00.0": "0x020000",  # Ethernet — not a GPU
        },
        iommu_groups={1: ["0000:00:1f.2"], 2: ["0000:02:00.0"]},
    )
    assert gpu_mod.get_gpus() == []


def test_3d_and_display_class_codes_included(fake_sysfs):
    fake_sysfs(
        devices={
            "0000:01:00.0": "0x030200",  # 3D controller
            "0000:02:00.0": "0x038000",  # Display controller (other)
        },
        iommu_groups={1: ["0000:01:00.0"], 2: ["0000:02:00.0"]},
    )

    result = gpu_mod.get_gpus()

    slots = sorted(g["addr"]["pci_slot"] for g in result)
    assert slots == ["0000:01:00.0", "0000:02:00.0"]


def test_gpu_without_iommu_group_falls_back_to_self(fake_sysfs):
    fake_sysfs(
        devices={"0000:01:00.0": "0x030000"},
        iommu_groups={},  # no group
    )

    result = gpu_mod.get_gpus()

    assert result == [{
        "addr": {"pci_slot": "0000:01:00.0"},
        "devices": [{"pci_slot": "0000:01:00.0"}],
    }]


def test_malformed_directory_names_ignored(fake_sysfs):
    # Build a tree manually with an extra junk entry.
    fake_sysfs(
        devices={"0000:01:00.0": "0x030000"},
        iommu_groups={1: ["0000:01:00.0"]},
    )
    junk = os.path.join(gpu_mod.PCI_DEVICES_DIR, "not-a-pci-addr")
    os.makedirs(junk, exist_ok=True)

    result = gpu_mod.get_gpus()

    assert [g["addr"]["pci_slot"] for g in result] == ["0000:01:00.0"]


def test_multi_gpu_in_different_iommu_groups(fake_sysfs):
    # Two discrete GPUs each with their own audio function in separate IOMMU
    # groups. Mirrors truenas_pylibvirt's `multi_gpu_different_groups` topology.
    # Expect both GPUs returned, each with only its own audio sibling.
    fake_sysfs(
        devices={
            "0000:01:00.0": "0x030000",  # GPU A (VGA)
            "0000:01:00.1": "0x040300",  # GPU A audio
            "0000:02:00.0": "0x030000",  # GPU B (VGA)
            "0000:02:00.1": "0x040300",  # GPU B audio
        },
        iommu_groups={
            10: ["0000:01:00.0", "0000:01:00.1"],
            11: ["0000:02:00.0", "0000:02:00.1"],
        },
    )

    result = gpu_mod.get_gpus()

    by_slot = {g["addr"]["pci_slot"]: g for g in result}
    assert sorted(by_slot.keys()) == ["0000:01:00.0", "0000:02:00.0"]
    assert [d["pci_slot"] for d in by_slot["0000:01:00.0"]["devices"]] == [
        "0000:01:00.0", "0000:01:00.1",
    ]
    assert [d["pci_slot"] for d in by_slot["0000:02:00.0"]["devices"]] == [
        "0000:02:00.0", "0000:02:00.1",
    ]


def test_gpu_sharing_iommu_group_with_non_audio_sibling(fake_sysfs):
    # GPU sharing an IOMMU group with an SMBus controller (or any non-audio
    # device). truenas_pylibvirt would flag this as `uses_system_critical_devices`,
    # but our minimal port does not — and for the script's purpose (vfio binding)
    # we still want every group member listed in `devices` so they all get
    # bound to vfio-pci together. Mirrors the `gpu_with_smbus` topology.
    fake_sysfs(
        devices={
            "0000:01:00.0": "0x030000",  # GPU
            "0000:01:00.1": "0x040300",  # Audio (GPU sibling)
            "0000:00:1f.4": "0x0c0500",  # SMBus (also in group)
        },
        iommu_groups={2: ["0000:00:1f.4", "0000:01:00.0", "0000:01:00.1"]},
    )

    result = gpu_mod.get_gpus()

    assert len(result) == 1
    gpu = result[0]
    assert gpu["addr"]["pci_slot"] == "0000:01:00.0"
    # All three IOMMU group members surface as devices, including the SMBus.
    assert [d["pci_slot"] for d in gpu["devices"]] == [
        "0000:00:1f.4",
        "0000:01:00.0",
        "0000:01:00.1",
    ]
