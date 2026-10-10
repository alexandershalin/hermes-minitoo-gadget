"""Single-instance lock for the state directory (Linux)."""

from __future__ import annotations

import fcntl
from contextlib import contextmanager
from pathlib import Path


@contextmanager
def device_lock(directory: Path):
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    directory.chmod(0o700)
    with (directory / "service.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError("another MiniToo gadget service is using this state directory") from None
        yield
