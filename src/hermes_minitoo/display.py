"""Hermes Gadget Linux display adapter for a Divoom MiniToo."""

from __future__ import annotations

import logging
import os
import threading
import time

from .codec import HEIGHT, WIDTH, rgb565le_to_rgb888
from .transport import RFCOMMTransport

LOG = logging.getLogger(__name__)

INDICATOR_FILE = os.path.expanduser("~/.cache/minitoo-indicator")
REC_COLOR = (220, 30, 30)
REC_MAX_SECONDS = 60.0  # защита от «залипшего» красного экрана
DONE_COLOR = (30, 200, 60)
DONE_SECONDS = 3.0


class MiniTooDisplay:
    """Drop-in replacement for hermes_gadget.linux.display.Display."""

    def __init__(self, config: dict):
        self.width = WIDTH
        self.height = HEIGHT
        self.touch = False
        self.round = False
        self.dirty = True
        self._last_ind = None

        self.update_interval_ms = int(config.get("update_interval_ms", 2500))
        self.last_queued = -self.update_interval_ms
        self.retry_window_s = float(config.get("retry_window_s", 60))
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

    def _indicator(self):
        """Цвет индикатора записи из файла статуса (rec:<ts> / done:<ts>) или None."""
        try:
            kind, _, ts = open(INDICATOR_FILE).read().strip().partition(":")
            age = time.time() - float(ts)
        except (OSError, ValueError):
            return None
        if kind == "rec" and age < REC_MAX_SECONDS:
            return REC_COLOR
        if kind == "done" and age < DONE_SECONDS:
            return DONE_COLOR
        return None

    def present(self, device, now_ms: int) -> None:
        ind = self._indicator()
        if ind != self._last_ind:
            self._last_ind = ind
            if ind is not None:
                frame = bytes(ind) * (self.width * self.height)
                with self._condition:
                    self._pending = frame
                    self._condition.notify()
                return
            self.dirty = True
            self.last_queued = -self.update_interval_ms
        elif ind is not None:
            return
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
            started = time.monotonic()
            attempt = 0
            while True:
                try:
                    self.transport.send_rgb888(rgb)
                    if attempt:
                        LOG.info("MiniToo display recovered after %d retries (%.1fs)",
                                 attempt, time.monotonic() - started)
                    break
                except Exception as exc:
                    attempt += 1
                    elapsed = time.monotonic() - started
                    # Transient outages (e.g. BT profile switch after recording):
                    # retry the same frame until it lands, unless a newer one arrived.
                    if elapsed > self.retry_window_s:
                        LOG.warning("MiniToo display update failed after %.0fs: %s", elapsed, exc)
                        # Не терять экран: пусть present() заново возьмёт актуальный кадр.
                        self.dirty = True
                        self.last_queued = -self.update_interval_ms
                        break
                    LOG.warning("MiniToo display retry %d: %r", attempt, exc)
                    with self._condition:
                        if self._pending is not None or self._closed:
                            break
                    time.sleep(1.0)

    def close(self) -> None:
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        self._worker.join(timeout=2)
        self.transport.close()
