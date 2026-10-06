"""Run Hermes Gadget's Linux client with the MiniToo display backend."""

from __future__ import annotations

import signal
import threading
from pathlib import Path

from .config import load_config, sdk_config
from .display import MiniTooDisplay


def install_display_backend() -> None:
    import hermes_gadget.linux.display as upstream_display

    upstream_display.Display = MiniTooDisplay


def run(config_path: Path, state_dir: Path) -> None:
    install_display_backend()
    from hermes_gadget.linux.control import run as run_upstream

    config = sdk_config(load_config(config_path))
    stop = threading.Event()
    previous = {sig: signal.signal(sig, lambda *_: stop.set()) for sig in (signal.SIGINT, signal.SIGTERM)}
    try:
        run_upstream(config, state_dir, stop)
    finally:
        for sig, handler in previous.items():
            signal.signal(sig, handler)
