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

from upgrade_pyutils.db import FREENAS_DATABASE, query_config_table
from upgrade_pyutils.rootfs import ReadonlyRootfsManager

logger = logging.getLogger(__name__)


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

    rebuilt = False
    with ReadonlyRootfsManager(root) as readonly_rootfs:
        try:
            database = args.database or os.path.join(root, FREENAS_DATABASE[1:])
            adv_config = query_config_table("system_advanced", database, "adv_")
            debug_kernel = adv_config["debugkernel"]
            for kernel in os.listdir(f"{root}/boot"):
                if not kernel.startswith("vmlinuz-"):
                    continue

                kernel_name = kernel.removeprefix("vmlinuz-")
                if "debug" in kernel_name and not debug_kernel:
                    continue

                initrd_path = f"{root}/boot/initrd.img-{kernel_name}"
                if args.force or not os.path.exists(initrd_path):
                    readonly_rootfs.make_writeable()
                    subprocess.run(
                        ["chroot", root, "update-initramfs", "-k", kernel_name, "-u"],
                        check=True,
                    )
                    rebuilt = True
        except Exception:
            logger.error("Failed to update initramfs", exc_info=True)
            exit(2)

    # Exit 1 if any initrd was (re)generated so the caller knows to reboot before
    # changes take effect. Exit 0 if nothing was rebuilt; exit 2 on error.
    exit(int(rebuilt))
