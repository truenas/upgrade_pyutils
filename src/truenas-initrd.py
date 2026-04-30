#!/usr/bin/env python3

# TrueNAS installs each version into its own boot environment (BE). This script
# regenerates the initramfs for a *target* BE whose rootfs path is passed as the
# `chroot` argument. The target BE is not necessarily the same as the environment
# executing this script: it can be invoked from
#   - a fresh-install ISO (host = installer environment, target = newly-extracted BE),
#   - an upgrade running on an existing TrueNAS (host = old/currently-running BE,
#     target = newly-extracted new BE), or
#   - the running system itself for a runtime regen (host = target, `chroot` = "/").
#
# In the first two cases the host's Python interpreter executes the *target* BE's
# script (no wrapping `chroot`), and the host and target may have different Python
# interpreter versions or ABIs. The script and the sibling `upgrade_pyutils`
# package are pure-Python and stdlib-only by design — they import nothing outside
# the standard library, so they load cleanly under whichever Python the host
# happens to provide. Both ship together at /usr/local/bin/ (the script and a
# sibling `upgrade_pyutils/` directory). Python prepends the script's directory
# to `sys.path[0]` automatically when run by path, so `upgrade_pyutils` resolves
# to the copy that ships beside this script (i.e. the target BE's copy when
# invoked cross-BE).
from __future__ import annotations

import argparse
import logging
import os
import subprocess

from upgrade_pyutils.db import FREENAS_DATABASE, query_config_table, query_table
from upgrade_pyutils.io import atomic_write
from upgrade_pyutils.rootfs import ReadonlyRootfsManager

logger = logging.getLogger(__name__)


def update_zfs_default(root: str, readonly_rootfs: ReadonlyRootfsManager) -> bool:
    # Older versions wrote ZFS_INITRD_POST_MODPROBE_SLEEP=15 here when the boot pool was on
    # USB, to let USB enumeration finish before zpool import. USB boot is no longer supported,
    # so this function only strips the line from upgraded installs; can be removed once
    # versions that wrote it are past EOL.
    zfs_config_path = os.path.join(root, "etc/default/zfs")
    with open(zfs_config_path) as f:
        original_config = f.read()
        lines = original_config.rstrip().split("\n")

    zfs_var_name = "ZFS_INITRD_POST_MODPROBE_SLEEP"
    lines = [line for line in lines if not line.startswith(f"{zfs_var_name}=")]

    new_config = "\n".join(lines) + "\n"
    if new_config != original_config:
        readonly_rootfs.make_writeable()
        with atomic_write(zfs_config_path, "w") as f:
            f.write(new_config)

        return True

    return False


def update_zfs_module_config(
    root: str,
    readonly_rootfs: ReadonlyRootfsManager,
    database: str,
) -> bool:
    options = []
    for tunable in query_table("system_tunable", database, "tun_"):
        if tunable["type"] != "ZFS":
            continue
        if not tunable["enabled"]:
            continue

        options.append(f"{tunable['var']}={tunable['value']}")

    config = f"options zfs {' '.join(options)}\n" if options else None

    config_path = os.path.join(root, "etc", "modprobe.d", "zfs.conf")
    try:
        with open(config_path) as f:
            existing_config = f.read()
    except FileNotFoundError:
        existing_config = None

    if existing_config != config:
        readonly_rootfs.make_writeable()

        if config is None:
            os.unlink(config_path)
        else:
            with atomic_write(config_path, "w", tmppath=os.path.join(root, "etc")) as f:
                f.write(config)

        return True

    return False


if __name__ == "__main__":
    p = argparse.ArgumentParser(
        description=(
            "Regenerate the initramfs for a target TrueNAS boot environment (BE). "
            "TrueNAS installs each version into its own BE; this script can run in "
            "several contexts: from a fresh-install ISO targeting a newly-extracted "
            "BE, from an existing TrueNAS upgrading to a new BE, or against the "
            "currently-running BE for a runtime regen. The path to the target BE's "
            "rootfs is passed as the `chroot` argument. The script and its companion "
            "`upgrade_pyutils` package are stdlib-only and ship together, so they "
            "load under whatever Python interpreter the host provides; `chroot` is "
            "used only to run `update-initramfs` against the target BE."
        ),
    )
    p.add_argument(
        "chroot", nargs=1,
        help=(
            "Path to the target boot environment's rootfs. During fresh installs and "
            "upgrades this is the mountpoint of the newly-extracted target BE; pass `/` "
            "to operate on the currently-running BE."
        ),
    )
    p.add_argument(
        "--database", "-d", default="",
        help=(
            "Path to the TrueNAS configuration database to read configuration from. "
            "Defaults to the database located inside the target BE's rootfs."
        ),
    )
    p.add_argument(
        "--force", "-f", action="store_true",
        help=(
            "Regenerate the initramfs in the target BE for every kernel even if no "
            "configuration changed."
        ),
    )
    args = p.parse_args()
    root = args.chroot[0]

    update_required = False
    with ReadonlyRootfsManager(root) as readonly_rootfs:
        try:
            database = args.database or os.path.join(root, FREENAS_DATABASE[1:])

            adv_config = query_config_table("system_advanced", database, "adv_")
            debug_kernel = adv_config["debugkernel"]

            update_required = any((
                update_zfs_default(root, readonly_rootfs),
                update_zfs_module_config(root, readonly_rootfs, database),
            ))

            for kernel in os.listdir(f"{root}/boot"):
                if not kernel.startswith("vmlinuz-"):
                    continue

                kernel_name = kernel.removeprefix("vmlinuz-")
                if "debug" in kernel_name and not debug_kernel:
                    continue

                initrd_path = f"{root}/boot/initrd.img-{kernel_name}"
                if args.force or update_required or not os.path.exists(initrd_path):
                    readonly_rootfs.make_writeable()
                    subprocess.run(
                        ["chroot", root, "update-initramfs", "-k", kernel_name, "-u"],
                        check=True,
                    )
        except Exception:
            logger.error("Failed to update initramfs", exc_info=True)
            exit(2)

    # Exit code 1 means the initramfs was updated and the caller should reboot before the changes take
    # effect (e.g. after a database upload). Exit code 0 means nothing changed; exit code 2 means an error.
    exit(int(update_required))
