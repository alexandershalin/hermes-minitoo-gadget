"""Hermes Gadget Linux display adapter for a Divoom MiniToo."""

from __future__ import annotations

import logging
import threading
import time

from .codec import HEIGHT, WIDTH, rgb565le_to_rgb888
from .transport import RFCOMMTransport

LOG = logging.getLogger(__name__)


class MiniTooDisplay:
    """Drop-in replacement for hermes_gadget.linux.display.Display."""

    def __init__(self, config: dict):
        self.width = WIDTH
        self.height = HEIGHT
        self.touch = False
        self.round = False
        self.dirty = True

        self.update_interval_ms = int(config.get("update_interval_ms", 2500))
        self.last_queued = -self.update_interval_ms
        self.transport = RFCOMMTransport(
            config["address"],
            channel=int(config.get("channel", 1)),
            frame_delay_ms=self.update_interval_ms,
            chunk_delay_ms=int(config.get("chunk_delay_ms", 5)),
            ready_timeout_ms=int(config.get("ready_timeout_ms", 8000)),
            reconnect_delay_ms=int(config.get("reconnect_delay_ms", 2000)),
            max_payload_bytes=int(config.get("max_payload_bytes", 600000)),
        )

        self._condition = threading.Condition()
        self._pending: bytes | None = None
        self._closed = False
        self._worker = threading.Thread(target=self._run, name="minitoo-display", daemon=True)
        self._worker.start()

    def poll(self, device) -> bool:
        return True

    def present(self, device, now_ms: int) -> None:
        if not self.dirty or now_ms - self.last_queued < self.update_interval_ms:
            return

        raw = device.framebuffer_rows()
        expected = self.width * self.height * 2
        if len(raw) != expected:
            raise RuntimeError(
                f"unexpected Hermes framebuffer size {len(raw)}; expected {expected}"
            )
        rgb = rgb565le_to_rgb888(raw)
        with self._condition:
            # Latest-frame-wins: never build a backlog of stale Hermes screens.
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
