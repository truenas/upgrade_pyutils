from __future__ import annotations

import json
import os
import shutil
import subprocess
import uuid

import pytest

POOL_NAME = "upgrade_pyutils_test"


def _zfs_usable() -> tuple[bool, str]:
    """Verify zpool/zfs binaries exist and the kernel module is usable.

    Returns (usable, reason). When not usable, reason is a short string suitable
    for `pytest.skip`.
    """
    if shutil.which("zpool") is None or shutil.which("zfs") is None:
        return False, "zpool/zfs binaries not found"
    try:
        # `zpool list` is the cheapest probe; it loads /dev/zfs.
        proc = subprocess.run(
            ["zpool", "list", "-H"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return False, f"zpool list failed: {e}"
    if proc.returncode != 0:
        return False, f"zpool list rc={proc.returncode}: {proc.stderr.strip() or proc.stdout.strip()}"
    return True, ""


def _run(*cmd: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), capture_output=True, text=True, check=True)


def _maybe_run(*cmd: str) -> None:
    subprocess.run(list(cmd), capture_output=True, text=True, check=False)


@pytest.fixture
def zfs_be_root(tmp_path):
    """Create a single-vdev zpool with two datasets emulating a TrueNAS BE
    layout (`<be>` and `<be>/usr`), mounted under `tmp_path/root`.

    Yields the root path (a string) plus a small handle exposing dataset names
    so tests can manipulate them directly. On teardown, destroys the pool and
    removes the vdev file.
    """
    if os.geteuid() != 0:
        pytest.skip("ZFS integration tests require root")

    usable, reason = _zfs_usable()
    if not usable:
        pytest.skip(f"ZFS not usable: {reason}")

    pool = f"{POOL_NAME}_{uuid.uuid4().hex[:8]}"
    vdev = tmp_path / "pool.vdev"
    root = tmp_path / "root"
    root.mkdir()
    usr = root / "usr"
    usr.mkdir()
    conf_dir = root / "conf"
    conf_dir.mkdir()

    # 256 MiB sparse file as the vdev.
    with open(vdev, "wb") as f:
        f.truncate(256 * 1024 * 1024)

    be_ds = f"{pool}/be0"
    usr_ds = f"{pool}/be0/usr"

    try:
        _run(
            "zpool", "create", "-f",
            "-O", "mountpoint=none",
            "-O", "readonly=off",
            pool, str(vdev),
        )
        _run("zfs", "create", "-o", f"mountpoint={root}", be_ds)
        _run("zfs", "create", "-o", f"mountpoint={usr}", usr_ds)

        # The manager reads conf/truenas_root_ds.json from inside the BE.
        # ZFS mounted the dataset on top of root so the conf dir we made
        # earlier is hidden — recreate it on the live mountpoint.
        live_conf = root / "conf"
        live_conf.mkdir(exist_ok=True)
        (live_conf / "truenas_root_ds.json").write_text(json.dumps([
            {"ds": usr_ds, "fhs_entry": {"name": "usr"}},
        ]))

        # ReadonlyRootfsManager._handle_usr unconditionally chmods
        # <root>/usr/bin/dpkg when toggling state. On a real TrueNAS rootfs
        # that file always exists; our fake BE is empty, so create a stub.
        # The companion `usr/local/bin/dpkg{,.bak}` rename calls are wrapped
        # in `contextlib.suppress(FileNotFoundError)` upstream, so missing
        # paths there don't need stubs.
        usr_bin = root / "usr" / "bin"
        usr_bin.mkdir(parents=True, exist_ok=True)
        dpkg_stub = usr_bin / "dpkg"
        dpkg_stub.touch()
        dpkg_stub.chmod(0o755)
        (root / "usr" / "local" / "bin").mkdir(parents=True, exist_ok=True)

        yield {
            "root": str(root),
            "be_ds": be_ds,
            "usr_ds": usr_ds,
            "pool": pool,
        }
    finally:
        _maybe_run("zpool", "destroy", "-f", pool)
        if vdev.exists():
            vdev.unlink()
