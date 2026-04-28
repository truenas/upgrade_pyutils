from __future__ import annotations

import os
import subprocess

import pytest

from upgrade_pyutils.rootfs import ReadonlyRootfsManager

pytestmark = pytest.mark.integration


def _zfs_get(prop: str, dataset: str) -> str:
    proc = subprocess.run(
        ["zfs", "get", "-H", "-o", "value", prop, dataset],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def _zfs_get_source(prop: str, dataset: str) -> str:
    proc = subprocess.run(
        ["zfs", "get", "-H", "-o", "source", prop, dataset],
        capture_output=True, text=True, check=True,
    )
    return proc.stdout.strip()


def test_make_writeable_flips_readonly_off(zfs_be_root):
    root = zfs_be_root["root"]
    usr_ds = zfs_be_root["usr_ds"]
    be_ds = zfs_be_root["be_ds"]

    subprocess.run(["zfs", "set", "readonly=on", be_ds], check=True)
    subprocess.run(["zfs", "set", "readonly=on", usr_ds], check=True)
    subprocess.run(["mount", "-o", "ro,remount", root], check=True)
    subprocess.run(["mount", "-o", "ro,remount", os.path.join(root, "usr")], check=True)

    with ReadonlyRootfsManager(root) as mgr:
        mgr.make_writeable()
        assert _zfs_get("readonly", be_ds) == "off"
        assert _zfs_get("readonly", usr_ds) == "off"
        # We can actually write to the BE root now.
        canary = os.path.join(root, "canary.txt")
        with open(canary, "w") as f:
            f.write("hello")
        assert os.path.exists(canary)
        os.unlink(canary)


def test_exit_restores_initial_readonly_state(zfs_be_root):
    root = zfs_be_root["root"]
    be_ds = zfs_be_root["be_ds"]
    usr_ds = zfs_be_root["usr_ds"]

    subprocess.run(["zfs", "set", "readonly=on", be_ds], check=True)
    subprocess.run(["zfs", "set", "readonly=on", usr_ds], check=True)
    subprocess.run(["mount", "-o", "ro,remount", root], check=True)
    subprocess.run(["mount", "-o", "ro,remount", os.path.join(root, "usr")], check=True)

    with ReadonlyRootfsManager(root) as mgr:
        mgr.make_writeable()

    assert _zfs_get("readonly", be_ds) == "on"
    assert _zfs_get("readonly", usr_ds) == "on"


def test_inherited_readonly_unchanged(zfs_be_root):
    """If a dataset's `readonly` source is inherited (not local), the manager
    must leave it alone — this matches the installer-time behavior the upstream
    code documents in `_set_state`.

    Only the BE dataset has inherited-readonly here; we leave usr_ds at its
    default writable state. That keeps the BE path exercising the
    skip-if-not-local branch without dragging in `_handle_usr`, which assumes
    /usr is writable when it runs (and would EROFS on the dpkg chmod if /usr
    were also readonly via inheritance — a state TrueNAS BEs don't actually
    produce in practice, so it isn't worth testing here).
    """
    root = zfs_be_root["root"]
    be_ds = zfs_be_root["be_ds"]
    usr_ds = zfs_be_root["usr_ds"]
    pool = zfs_be_root["pool"]

    # readonly=on on the pool, inherit on be_ds only. Force usr_ds to
    # readonly=off locally so it doesn't also inherit from the pool.
    subprocess.run(["zfs", "set", "readonly=on", pool], check=True)
    subprocess.run(["zfs", "inherit", "readonly", be_ds], check=True)
    subprocess.run(["zfs", "set", "readonly=off", usr_ds], check=True)

    assert _zfs_get("readonly", be_ds) == "on"
    assert _zfs_get_source("readonly", be_ds) != "local"
    assert _zfs_get("readonly", usr_ds) == "off"
    assert _zfs_get_source("readonly", usr_ds) == "local"

    with ReadonlyRootfsManager(root) as mgr:
        mgr.make_writeable()
        # be_ds stays 'on' (inherited): manager refuses to touch non-local.
        # usr_ds stays 'off' because it was already writable — no flip,
        # no remount, _handle_usr never triggered.
        assert _zfs_get("readonly", be_ds) == "on"
        assert _zfs_get("readonly", usr_ds) == "off"


def test_no_change_when_already_writeable(zfs_be_root):
    root = zfs_be_root["root"]
    be_ds = zfs_be_root["be_ds"]
    usr_ds = zfs_be_root["usr_ds"]

    # Both datasets already readonly=off (the fixture default).
    with ReadonlyRootfsManager(root) as mgr:
        mgr.make_writeable()
        assert _zfs_get("readonly", be_ds) == "off"
        assert _zfs_get("readonly", usr_ds) == "off"
