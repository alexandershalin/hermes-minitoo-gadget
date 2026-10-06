"""Hermes Gadget Linux display adapter for a Divoom MiniToo."""

from __future__ import annotations

import logging
import threading
import time

from .protocol import IMAGE_HEIGHT, IMAGE_WIDTH, rgb565le_to_rgb888
from .transport import RFCOMMTransport

LOG = logging.getLogger(__name__)


class MiniTooDisplay:
    """Drop-in replacement for hermes_gadget.linux.display.Display.

    The Hermes Gadget core stays on the service thread. Only copied framebuffer
    bytes cross into the worker thread; no NativeDevice method is called there.
    """

    def __init__(self, config: dict):
        self.width = IMAGE_WIDTH
        self.height = IMAGE_HEIGHT
        self.touch = False
        self.round = False
        self.dirty = True
        self.max_fps = float(config.get("max_fps", 2.0))
        if not 0.1 <= self.max_fps <= 10.0:
            raise ValueError("minitoo.max_fps must be between 0.1 and 10")
        self.min_interval_ms = int(1000 / self.max_fps)
        self.last_queued = -self.min_interval_ms
        self.transport = RFCOMMTransport(
            config["address"],
            channel=int(config.get("channel", 1)),
            packet_delay_ms=int(config.get("packet_delay_ms", 12)),
            request_timeout_ms=int(config.get("request_timeout_ms", 250)),
            reconnect_delay_ms=int(config.get("reconnect_delay_ms", 2000)),
            zstd_level=int(config.get("zstd_level", 17)),
            zstd_window_log=int(config.get("zstd_window_log", 17)),
        )
        self._condition = threading.Condition()
        self._pending: bytes | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="minitoo-display", daemon=True)
        self._worker.start()

    def poll(self, device) -> bool:
        return True

    def present(self, device, now_ms: int) -> None:
        if not self.dirty or now_ms - self.last_queued < self.min_interval_ms:
            return
        raw = device.framebuffer_rows()
        if len(raw) != self.width * self.height * 2:
            raise RuntimeError(
                f"unexpected Hermes framebuffer size {len(raw)}; expected {self.width*self.height*2}"
            )
        rgb = rgb565le_to_rgb888(raw)
        with self._condition:
            self._pending = rgb
            self._condition.notify()
        self.dirty = False
        self.last_queued = now_ms

    def _run(self) -> None:
        while True:
            with self._condition:
                self._condition.wait_for(lambda: self._pending is not None or self._closed)
                if self._closed:
                    return
                rgb = self._pending
                self._pending = None
            assert rgb is not None
            try:
                self.transport.send_rgb888(rgb)
            except Exception as exc:
                LOG.warning("MiniToo display update failed: %s", exc)
                time.sleep(0.1)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=2)
        self.transport.close()
