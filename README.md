# upgrade_pyutils

Self-contained home for `truenas-initrd.py` and the small handful of utility
modules it needs. Both the script and the `upgrade_pyutils` package import
nothing outside the Python standard library — by design.

## Why this repo exists

`truenas-initrd.py` runs during TrueNAS installs and upgrades, and it is
frequently invoked across boot environment (BE) boundaries: a *host* BE's
Python interpreter executes the script that lives in a *target* BE's rootfs
(fresh-install ISO → newly-extracted BE, or running BE → newly-extracted
upgrade BE). The host and target may differ in:

- **Python interpreter version** — modules pulled in from the target BE can
  use language features the host's interpreter doesn't understand.
- **C-extension ABI** — compiled `.so` files in the target's `dist-packages`
  are built against the target's Python and may fail to load under the host's.
- **Kernel features** — kernel-dependent C extensions can fail at call time
  on an older host kernel.

Until now `truenas-initrd.py` (shipped under `middleware/`) lazy-imported
`truenas_os_pyutils`, `truenas_pylibvirt`, and `middlewared` from the target
BE's `dist-packages`. That arrangement is fragile: every transitively-imported
C extension is a separate seam that can break at runtime — `truenas_os`,
`pyudev`, and friends all sit in the import graph.

This repo solves the problem by encapsulating every non-stdlib symbol the
script needs into a sibling `upgrade_pyutils/` package and shipping them
together. The script is invoked under whichever Python the host provides; both
modules and script are pure Python and stdlib-only, so they load reliably.

## Constraints (do not violate)

- **Stdlib only.** No third-party Python packages — not `pyudev`, not
  `truenas_os`, not `typing_extensions`, nothing. Adding any third-party
  import breaks the cross-interpreter contract.
- **Python 3.10 floor.** All code must parse and run under Python 3.10. In
  particular avoid:
  - `typing.Self`, `typing.Never`, `typing.assert_type`, `typing.LiteralString`,
    `Required`/`NotRequired` (3.11+)
  - `tomllib`, `ExceptionGroup`, `except*` (3.11+)
  - PEP 695 generics (`type X = ...`, `class C[T]:`), `@typing.override` (3.12+)

  Use `from __future__ import annotations` at the top of every module so
  annotations are strings at runtime.

## Layout

```
README.md
.github/workflows/ci.yml       # ruff + mypy + py_compile matrix + pytest + zfs
mypy.ini
pyproject.toml                 # ruff + pytest config (no install metadata yet)
src/
  truenas-initrd.py            # entry point; bootstraps sibling upgrade_pyutils
  upgrade_pyutils/
    __init__.py                # empty
    io.py                      # atomic_write
    rootfs.py                  # ReadonlyRootfsManager
    db.py                      # FREENAS_DATABASE, query_config_table, query_table
    gpu.py                     # get_gpus (sysfs-only, no pyudev)
tests/
  test_io.py
  test_db.py
  test_gpu.py
  integration/
    conftest.py                # file-backed zpool fixture
    test_rootfs.py             # ReadonlyRootfsManager against a real pool
```

## How `upgrade_pyutils` is loaded

When Python runs a script by path, it automatically prepends the script's own
directory to `sys.path[0]`. That makes `from upgrade_pyutils.io import
atomic_write` resolve to the package that ships next to `truenas-initrd.py`,
with no `sys.path` manipulation in the script itself and no dependency on the
host BE's `dist-packages`.

## What the script does

`truenas-initrd.py` regenerates the initramfs of a *target* BE whose rootfs
path is passed as the `chroot` argument:

1. Reads the TrueNAS configuration database (`/data/freenas-v1.db` inside the
   target BE by default; `--database` overrides).
2. Reconciles a few config files inside the target rootfs (`etc/default/zfs`,
   `boot/initramfs_config.json`, the vfio-bind init-top script and module
   files, `etc/modprobe.d/zfs.conf`) against the values in the database.
3. For each `vmlinuz-*` kernel under `<root>/boot/`, runs `chroot <root>
   update-initramfs -k <kernel> -u` if any config file changed, `--force` was
   passed, or the corresponding `initrd.img-*` is missing.

Exits 0 if nothing changed, 1 if the initramfs was regenerated (caller
should reboot), 2 on error.

## Running tests locally

```bash
# Lint
ruff check src/ tests/

# Type check
mypy --config-file mypy.ini

# Unit tests (no ZFS required)
pytest tests/ -m "not integration"

# Integration (requires root + ZFS userspace; uses a file-backed zpool)
sudo -E pytest tests/integration -m integration
```

CI runs all of the above plus a `python -m compileall` matrix across
Python 3.10, 3.11, and 3.13 to catch language-feature drift.

## Contributing

- No third-party Python deps. Ever. Verify with
  `python3 -I -c "import sys; sys.path.insert(0, 'src'); import upgrade_pyutils.io, upgrade_pyutils.rootfs, upgrade_pyutils.db, upgrade_pyutils.gpu"`
  — `-I` isolates from site-packages and will fail if a non-stdlib import slipped in.
- Test under Python 3.10 before pushing. CI will catch regressions but local
  verification is faster.
- `ruff check --fix` and `mypy` should both pass clean before opening a PR.
