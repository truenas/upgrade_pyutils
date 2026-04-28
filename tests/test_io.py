from __future__ import annotations

import os

import pytest

from upgrade_pyutils.io import atomic_write


def test_writes_new_file(tmp_path):
    target = tmp_path / "out.txt"
    with atomic_write(str(target)) as f:
        f.write("hello")
    assert target.read_text() == "hello"


def test_replaces_existing_file_atomically(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("old")
    old_inode = target.stat().st_ino

    with atomic_write(str(target)) as f:
        f.write("new")

    assert target.read_text() == "new"
    # os.replace creates a new inode for the target (the old one is unlinked).
    assert target.stat().st_ino != old_inode


def test_perms_applied(tmp_path):
    target = tmp_path / "out.txt"
    with atomic_write(str(target), perms=0o600) as f:
        f.write("x")
    assert (target.stat().st_mode & 0o777) == 0o600


def test_executable_perms_applied(tmp_path):
    target = tmp_path / "script.sh"
    with atomic_write(str(target), perms=0o755) as f:
        f.write("#!/bin/sh\n")
    assert (target.stat().st_mode & 0o777) == 0o755


def test_asymmetric_perms_applied(tmp_path):
    # 0o741 isn't a default for anything (file 0o644, dir 0o755, mkstemp 0o600)
    # so a passing assertion here proves perms= is being threaded through to
    # fchmod rather than incidentally matching an unrelated default.
    target = tmp_path / "weird.txt"
    with atomic_write(str(target), perms=0o741) as f:
        f.write("x")
    assert (target.stat().st_mode & 0o777) == 0o741


@pytest.mark.skipif(os.geteuid() != 0, reason="fchown requires root")
def test_uid_gid_applied(tmp_path):
    target = tmp_path / "out.txt"
    with atomic_write(str(target), uid=12345, gid=12345) as f:
        f.write("x")
    st = target.stat()
    assert st.st_uid == 12345
    assert st.st_gid == 12345


def test_body_raise_leaves_no_temp_and_target_unchanged(tmp_path):
    target = tmp_path / "out.txt"
    target.write_text("original")

    with pytest.raises(RuntimeError), atomic_write(str(target)) as f:
        f.write("partial")
        raise RuntimeError("boom")

    assert target.read_text() == "original"
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".atomic_write_")]
    assert leftovers == []


def test_body_raise_after_initial_create_leaves_no_temp(tmp_path):
    target = tmp_path / "new.txt"
    with pytest.raises(RuntimeError), atomic_write(str(target)) as f:
        f.write("partial")
        raise RuntimeError("boom")

    assert not target.exists()
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".atomic_write_")]
    assert leftovers == []


@pytest.mark.parametrize("mode", ["r", "rb", "a", "r+", "wt"])
def test_invalid_modes_rejected(tmp_path, mode):
    with pytest.raises(ValueError, match="invalid mode"), atomic_write(  # type: ignore[arg-type]
        str(tmp_path / "out.txt"), mode=mode,
    ):
        pass


def test_binary_mode(tmp_path):
    target = tmp_path / "out.bin"
    with atomic_write(str(target), mode="wb") as f:
        f.write(b"\x00\x01\x02")
    assert target.read_bytes() == b"\x00\x01\x02"


def test_multiple_writes(tmp_path):
    target = tmp_path / "multi.txt"
    with atomic_write(str(target)) as f:
        f.write("line1\n")
        f.write("line2\n")
    assert target.read_text() == "line1\nline2\n"


def test_empty_write(tmp_path):
    target = tmp_path / "empty.txt"
    with atomic_write(str(target)):
        pass
    assert target.exists()
    assert target.stat().st_size == 0


def test_default_tmppath_is_target_dir(tmp_path):
    sub = tmp_path / "sub"
    sub.mkdir()
    target = sub / "out.txt"
    with atomic_write(str(target)) as f:
        f.write("x")
    assert target.read_text() == "x"


def test_explicit_tmppath(tmp_path):
    # The script passes the target's grandparent as tmppath in some places
    # (e.g. tmppath=get_path("etc") for a target under etc/modprobe.d/). Verify
    # that flow: tmppath != dirname(target) but on the same filesystem.
    target_dir = tmp_path / "deep" / "nested"
    target_dir.mkdir(parents=True)
    target = target_dir / "config"

    with atomic_write(str(target), tmppath=str(tmp_path)) as f:
        f.write("cfg")

    assert target.read_text() == "cfg"
    leftovers = [p for p in tmp_path.iterdir() if p.name.startswith(".atomic_write_")]
    assert leftovers == []
