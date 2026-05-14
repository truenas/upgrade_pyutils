# upgrade_pyutils

Self-contained home for the cross-BE upgrade helper scripts
(`truenas-initrd.py`, `truenas-grub.py`) and the small handful of utility
modules they need. The scripts and the `upgrade_pyutils` package import
nothing outside the Python standard library — by design.

## !! READ THIS BEFORE YOU TOUCH ANYTHING IN THIS REPO !!

This code runs across boot environment (BE) boundaries. During a TrueNAS
upgrade, the **currently running (OLD) BE's Python interpreter** executes
`truenas-initrd.py` and the modules in `upgrade_pyutils/` that live inside
the **newly extracted (NEW) BE's filesystem**. During a fresh install it's
the install ISO's interpreter against the new BE. **The interpreter and the
code are from different TrueNAS versions and different Python builds.**

We learned this the hard way. Earlier versions of this script imported
`middlewared`, `truenas_os_pyutils`, and `truenas_pylibvirt` out of the
new BE's `dist-packages`. Real-world upgrades blew up because:

- New code used type-annotation syntax the old interpreter couldn't even
  *parse* — PEP-604 unions, PEP-695 generics, `match` statements, `Self`,
  etc. The script failed at import time, mid-upgrade.
- C extensions in the import graph (`truenas_os`, `pyudev`, and friends)
  were compiled against the new Python's ABI and either failed to load
  under the old interpreter — or worse, loaded and silently misbehaved
  when they hit kernel features the old kernel didn't have.
- Bricked upgrades. There is no recovery UI at that point. Users were
  dropping to a serial console or reinstalling.

This repo exists so that never happens again. Everything in it is pure
Python and stdlib-only, end to end. Anything BE-aware (config reconciliation,
device enumeration, etc.) has been moved into middlewared, which writes
state to stable paths under `/data/subsystems/...` that initramfs-tools
hooks (shipped by the `truenas-files` package) read at `update-initramfs`
time. **That pattern is the entire reason this script keeps shrinking.**

## !! Hard rules — do not break them !!

1. **STDLIB ONLY.** Not `pydantic`, not `requests`, not `typing_extensions`,
   not `truenas_*`, not `middlewared`, not anything you `pip install`.
   Adding *any* third-party import breaks the cross-interpreter contract.
   If you find yourself wanting one, **the work belongs in middleware,
   not here.**
2. **NO MIDDLEWARE LOGIC.** Do not add functionality that reads or
   reconciles TrueNAS configuration. If you need something baked into
   the initrd, have middlewared write it to `/data/subsystems/initramfs/`
   and add an initramfs-tools hook in the `truenas-files` package that
   copies it into the initrd.
3. **PYTHON 3.10 SYNTAX FLOOR.** All code must parse and run under Python
   3.10. Forbidden:
   - `typing.Self`, `typing.Never`, `typing.assert_type`,
     `typing.LiteralString`, `Required`/`NotRequired` (3.11+)
   - `tomllib`, `ExceptionGroup`, `except*` (3.11+)
   - PEP-695 generics (`type X = ...`, `class C[T]:`),
     `@typing.override` (3.12+)
   - `match` statements only with extreme caution (3.10+, parses fine,
     but if you can use `if/elif` instead, do)

   `from __future__ import annotations` is **mandatory** at the top of
   every module so annotations are strings at runtime.
4. **NO C EXTENSIONS, EVER. EVEN TRANSITIVELY.** Every compiled `.so` is
   one more chance to dlopen a symbol that doesn't exist on the host.
5. **BLAST RADIUS.** A bug here means the user's TrueNAS won't boot after
   upgrade. Treat every line you add like it has to run on the oldest
   supported version's Python against the newest version's filesystem —
   because that is literally what happens.

If you're unsure whether something belongs here: **it doesn't.** Put it
in middleware.

## Layout

```
README.md
.github/workflows/ci.yml       # ruff + mypy + py_compile matrix + zfs
mypy.ini
pyproject.toml                 # ruff + pytest config (no install metadata yet)
src/
  truenas-initrd.py            # entry point; bootstraps sibling upgrade_pyutils
  truenas-grub.py              # entry point; lays down /etc/default/grub.d/truenas.cfg
  upgrade_pyutils/
    __init__.py                # empty
    rootfs.py                  # ReadonlyRootfsManager
tests/
  integration/
    conftest.py                # file-backed zpool fixture
    test_rootfs.py             # ReadonlyRootfsManager against a real pool
```

## How `upgrade_pyutils` is loaded

When Python runs a script by path, it automatically prepends the script's own
directory to `sys.path[0]`. That makes `from upgrade_pyutils.rootfs import
ReadonlyRootfsManager` resolve to the package that ships next to
`truenas-initrd.py`, with no `sys.path` manipulation in the script itself and
no dependency on the host BE's `dist-packages`.

## What the scripts do

### `truenas-initrd.py`

Regenerates the initramfs of a *target* BE whose rootfs path is passed as
the `chroot` argument:

1. Reads `<root>/data/subsystems/initramfs/debug_kernel` (written by
   middlewared) to decide whether to include the debug kernel. Missing →
   default `False`.
2. For each `vmlinuz-*` kernel under `<root>/boot/`, runs `chroot <root>
   update-initramfs -k <kernel> -u` if `--force` was passed or the
   corresponding `initrd.img-*` is missing.

All TrueNAS-specific data baked into the initrd (vfio PCI slot list, ZFS
modprobe options, debug-kernel toggle, etc.) is written by middlewared to
stable paths under `/data/subsystems/initramfs/`. Per-feature
initramfs-tools hooks shipped by the `truenas-files` package read those
paths at `update-initramfs` time and copy the contents into the initrd.
This script is intentionally unaware of those features — it just
orchestrates `update-initramfs`.

Exits 0 if nothing was rebuilt, 1 if any initrd was regenerated (caller
should reboot), 2 on error.

### `truenas-grub.py`

Lays down `/etc/default/grub.d/truenas.cfg` in the target BE from a
pre-rendered snapshot at `<root>/data/subsystems/grub/truenas.cfg`. The
live middleware materializes the snapshot whenever any input (system_advanced
config, vendor, serial hardware, memory) changes, and `/data` is rsynced
into the new BE during upgrades, so the snapshot is always present for
upgrade paths.

On fresh installs the snapshot is missing — the script writes a baked-in
default grub config (no serial console, no kdump, no kernel extras), and
middlewared overwrites it with the real bytes on first boot via its
`system.ready` reconciliation handler.

## Running tests locally

```bash
# Lint
ruff check src/ tests/

# Type check
mypy --config-file mypy.ini

# Integration (requires root + ZFS userspace; uses a file-backed zpool)
sudo -E pytest tests/integration -m integration
```

CI runs all of the above plus a `python -m compileall` matrix across
Python 3.10, 3.11, and 3.13 to catch language-feature drift.

## Contributing

- No third-party Python deps. Ever. Verify with
  `python3 -I -c "import sys; sys.path.insert(0, 'src'); import upgrade_pyutils.rootfs"`
  — `-I` isolates from site-packages and will fail if a non-stdlib import slipped in.
- Test under Python 3.10 before pushing. CI will catch regressions but local
  verification is faster.
- `ruff check --fix` and `mypy` should both pass clean before opening a PR.
