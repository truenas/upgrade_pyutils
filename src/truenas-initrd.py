#!/usr/bin/env python3

# !!
# !! STOP. READ THIS BEFORE YOU TOUCH THIS SCRIPT.
# !!
# !! This file runs across boot environment (BE) boundaries. During an upgrade,
# !! the *currently running* (OLD) TrueNAS Python interpreter executes the
# !! script and modules that live inside the *newly extracted* (NEW) BE's
# !! filesystem. During a fresh install it's the install ISO's interpreter
# !! against the new BE. The interpreter and the code are from DIFFERENT
# !! TrueNAS versions and DIFFERENT Python builds.
# !!
# !! We learned this the hard way. Earlier versions of this script imported
# !! `middlewared`, `truenas_os_pyutils`, and `truenas_pylibvirt` from the new
# !! BE's dist-packages. Real upgrades blew up because:
# !!   - new code used type-annotation syntax the old interpreter couldn't even
# !!     parse (PEP-604 unions, PEP-695 generics, `match`, `Self`, etc.)
# !!   - C extensions (`truenas_os`, `pyudev`, ...) were built against the new
# !!     Python's ABI and failed to load — or worse, loaded and silently misbehaved
# !!     under the old kernel.
# !! The result was bricked upgrades with no UI to recover from. This is why
# !! the script keeps shrinking and why anything BE-aware now lives in
# !! middlewared and persists state to /data/subsystems/...
# !!
# !! THE RULES (do not break them, no exceptions):
# !!
# !! 1. STDLIB ONLY. The script and the sibling `upgrade_pyutils/` package may
# !!    import NOTHING outside the Python standard library. Not `pydantic`, not
# !!    `requests`, not `typing_extensions`, not `truenas_*`, not `middlewared`.
# !!    If you reach for a third-party import, the work belongs in middleware,
# !!    not here.
# !!
# !! 2. NO MIDDLEWARE LOGIC. Do not add functionality that reads or reconciles
# !!    TrueNAS configuration. If you need to bake something into the initrd,
# !!    have middlewared write it to a stable path under
# !!    /data/subsystems/initramfs/ and add an initramfs-tools hook (shipped
# !!    by the truenas-files package) that copies it into the initrd at
# !!    `update-initramfs` time. That pattern is *the entire reason* this
# !!    script exists in its current minimal form.
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
# !!    upgrade. There is no recovery UI at that point — they're on a serial
# !!    console or reinstalling. Treat every line you add like it has to run
# !!    on the oldest supported version's Python against the newest version's
# !!    filesystem, because that is literally what happens.
# !!
# !! If you are unsure whether something belongs here: it doesn't. Put it in
# !! middleware.
# !!
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
