#!/usr/bin/env python3

# !!
# !! STOP. READ THIS BEFORE YOU TOUCH THIS SCRIPT.
# !!
# !! This file runs across boot environment (BE) boundaries. During an upgrade,
# !! the *currently running* (OLD) TrueNAS Python interpreter executes the
# !! script that lives inside the *newly extracted* (NEW) BE's filesystem.
# !! During a fresh install it's the install ISO's interpreter against the new
# !! BE. The interpreter and the code are from DIFFERENT TrueNAS versions and
# !! DIFFERENT Python builds.
# !!
# !! We learned this the hard way. Earlier versions of this script (and its
# !! sibling truenas-initrd.py) imported `middlewared`, `truenas_os_pyutils`,
# !! and `truenas_pylibvirt` from the new BE's dist-packages. Real upgrades
# !! blew up because:
# !!   - new code used type-annotation syntax the old interpreter couldn't even
# !!     parse (PEP-604 unions, PEP-695 generics, `match`, `Self`, etc.)
# !!   - C extensions (`truenas_os`, `pyudev`, ...) were built against the new
# !!     Python's ABI and failed to load - or worse, loaded and silently
# !!     misbehaved under the old kernel.
# !! The result was bricked upgrades with no UI to recover from. This is why
# !! the script keeps shrinking and why anything BE-aware now lives in
# !! middlewared and persists state to /data/subsystems/...
# !!
# !! THE RULES (do not break them, no exceptions):
# !!
# !! 1. STDLIB ONLY. The script may import NOTHING outside the Python standard
# !!    library. Not `pydantic`, not `requests`, not `typing_extensions`, not
# !!    `truenas_*`, not `middlewared`. If you reach for a third-party import,
# !!    the work belongs in middleware, not here.
# !!
# !! 2. NO MIDDLEWARE LOGIC. Do not add functionality that reads or reconciles
# !!    TrueNAS configuration. Middlewared on the live system writes the
# !!    fully-rendered `truenas.cfg` to `/data/subsystems/grub/truenas.cfg`
# !!    whenever any input (system_advanced row, vendor, serial hardware,
# !!    memory) changes. /data is rsynced into the new BE by the installer,
# !!    so this script's only job is to lay that pre-rendered file down at
# !!    /etc/default/grub.d/truenas.cfg.
# !!
# !! 3. PYTHON 3.10 SYNTAX FLOOR. The oldest supported BE we may execute under
# !!    runs Python 3.10. Do not use `tomllib`, `ExceptionGroup`, `except*`,
# !!    `Self`/`Never`/`LiteralString`, PEP-695 generics, or `@typing.override`.
# !!    `from __future__ import annotations` is mandatory so all annotations
# !!    are strings at runtime.
# !!
# !! 4. NO C EXTENSIONS, EVER. Even transitively. Every C extension is one
# !!    more chance to dlopen a symbol that doesn't exist on the host.
# !!
# !! 5. BLAST RADIUS. A bug here means the user's TrueNAS won't boot after
# !!    upgrade. There is no recovery UI at that point - they're on a serial
# !!    console or reinstalling.
# !!
# !! If you are unsure whether something belongs here: it doesn't. Put it in
# !! middleware.
# !!
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil

logger = logging.getLogger(__name__)

# Path inside the target BE. No leading slash - joined with `root` (the chroot
# arg) via os.path.join. A leading "/" would make os.path.join discard `root`
# and silently read the host's /data instead of the target BE's. During
# upgrades that would read the OLD BE's snapshot, not the NEW BE's. Keep it
# relative.
GRUB_CFG_SNAPSHOT = "data/subsystems/grub/truenas.cfg"
GRUB_CFG_DEST = "etc/default/grub.d/truenas.cfg"
VENDOR_FILE = "data/.vendor"
# Baked-in fallback for fresh installs where the live middleware hasn't yet
# materialized the snapshot. Mirrors the bare-minimum grub config the live
# system would generate with all defaults (no serial console, no kdump, no
# kernel extras). Middlewared reconciles to the real values on first boot via
# its `system.ready` handler, which writes a fresh snapshot AND triggers
# `etc.generate('grub')` -> `update-grub`, so the booted system converges to
# the user's configured values within seconds of first boot.
KERNEL_PARAMS = (
    "libata.allow_tpm=1",
    "amd_iommu=on",
    "iommu=pt",
    "kvm_amd.npt=1",
    "kvm_amd.avic=1",
    "intel_iommu=on",
    "zfsforce=1",
    "nvme_core.multipath=N",
)
FALLBACK_TEMPLATE = """\
GRUB_DISTRIBUTOR="{vendor}"
GRUB_TIMEOUT=10
GRUB_DISABLE_RECOVERY="true"
GRUB_CMDLINE_LINUX_DEFAULT="{cmdline}"
GRUB_TERMINAL_INPUT="console"
GRUB_TERMINAL_OUTPUT="console"
GRUB_CMDLINE_LINUX=""
"""


def read_vendor(root: str) -> str:
    try:
        with open(os.path.join(root, VENDOR_FILE)) as f:
            return json.load(f).get("name") or "TrueNAS Scale"
    except FileNotFoundError:
        return "TrueNAS Scale"
    except Exception:
        logger.warning("failed to parse %r; falling back to default vendor", VENDOR_FILE, exc_info=True)
        return "TrueNAS Scale"


def main(root: str) -> int:
    src = os.path.join(root, GRUB_CFG_SNAPSHOT)
    dst = os.path.join(root, GRUB_CFG_DEST)
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    if os.path.exists(src):
        shutil.copyfile(src, dst)
        return 0

    # Fresh install / first boot path: snapshot doesn't exist yet because the
    # live middleware has never run on this BE. Write the bare-minimum default
    # so `update-grub` produces a bootable grub.cfg; middlewared will overwrite
    # this on first boot with the real config.
    vendor = read_vendor(root)
    with open(dst, "w") as f:
        f.write(FALLBACK_TEMPLATE.format(vendor=vendor, cmdline=" ".join(KERNEL_PARAMS)))
    return 0


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description=(
            "Lay down /etc/default/grub.d/truenas.cfg for a target TrueNAS boot environment (BE). "
            "The bytes come from /data/subsystems/grub/truenas.cfg, which the live middleware "
            "materializes whenever any input (system_advanced config, vendor, serial hardware, "
            "memory) changes. /data is rsynced into the new BE during upgrades, so the snapshot "
            "is always present for upgrade paths. On fresh installs the snapshot is missing and a "
            "baked-in default is written instead; middlewared reconciles on first boot."
        ),
    )
    p.add_argument(
        "chroot",
        nargs=1,
        help=(
            "Path to the target boot environment's rootfs. During fresh installs and upgrades this "
            "is the mountpoint of the newly-extracted target BE; pass `/` to operate on the "
            "currently-running BE."
        ),
    )
    args = p.parse_args()
    exit(main(args.chroot[0]))
