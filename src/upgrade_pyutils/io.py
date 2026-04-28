from __future__ import annotations

import contextlib
import os
import tempfile
from collections.abc import Generator
from typing import IO, Any, Literal

# Stdlib-only atomic_write. Drops the openat2 RESOLVE_NO_SYMLINKS protection that
# the C-extension-backed `truenas_os_pyutils.io.atomic_write` provides upstream.
# That tradeoff is acceptable in our caller (truenas-initrd.py): the target rootfs
# has just been unsquashed during install/upgrade and is not under attacker
# influence at the points where this is invoked.


@contextlib.contextmanager
def atomic_write(
    target: str,
    mode: Literal["w", "wb"] = "w",
    *,
    tmppath: str | None = None,
    uid: int = 0,
    gid: int = 0,
    perms: int = 0o644,
) -> Generator[IO[Any], None, None]:
    if mode not in ("w", "wb"):
        raise ValueError(f'{mode}: invalid mode. Only "w" and "wb" are supported.')

    if tmppath is None:
        tmppath = os.path.dirname(target)

    fd, tmp_name = tempfile.mkstemp(dir=tmppath, prefix=".atomic_write_")
    committed = False
    try:
        with os.fdopen(fd, mode) as f:
            os.fchown(f.fileno(), uid, gid)
            os.fchmod(f.fileno(), perms)
            yield f
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_name, target)
        committed = True
    finally:
        if not committed:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp_name)
