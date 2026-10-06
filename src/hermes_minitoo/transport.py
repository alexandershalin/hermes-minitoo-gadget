"""Linux Bluetooth Classic RFCOMM transport for MiniToo."""

from __future__ import annotations

import logging
import socket
import time

from .protocol import build_transfer, encode_still_rgb888

LOG = logging.getLogger(__name__)


class RFCOMMTransport:
    def __init__(
        self,
        address: str,
        *,
        channel: int = 1,
        packet_delay_ms: int = 12,
        request_timeout_ms: int = 250,
        reconnect_delay_ms: int = 2000,
        zstd_level: int = 17,
        zstd_window_log: int = 17,
    ) -> None:
        if not address or ":" not in address:
            raise ValueError("minitoo.address must be a Bluetooth MAC address")
        if not 1 <= channel <= 30:
            raise ValueError("minitoo.channel must be between 1 and 30")
        self.address = address
        self.channel = channel
        self.packet_delay = max(0, packet_delay_ms) / 1000.0
        self.request_timeout = max(0, request_timeout_ms) / 1000.0
        self.reconnect_delay = max(0, reconnect_delay_ms) / 1000.0
        self.zstd_level = zstd_level
        self.zstd_window_log = zstd_window_log
        self.sock: socket.socket | None = None
        self.last_failure = 0.0

    def connect(self) -> None:
        if self.sock is not None:
            return
        now = time.monotonic()
        if now - self.last_failure < self.reconnect_delay:
            raise ConnectionError("MiniToo reconnect backoff is active")
        try:
            family = getattr(socket, "AF_BLUETOOTH")
            proto = getattr(socket, "BTPROTO_RFCOMM")
        except AttributeError as exc:
            raise RuntimeError("Python was built without Linux Bluetooth socket support") from exc
        sock = socket.socket(family, socket.SOCK_STREAM, proto)
        try:
            sock.settimeout(max(0.1, self.request_timeout))
            sock.connect((self.address, self.channel))
            self.sock = sock
            LOG.info("connected to MiniToo %s RFCOMM channel %d", self.address, self.channel)
        except Exception:
            self.last_failure = time.monotonic()
            sock.close()
            raise

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None

    def _recv_until_request_or_timeout(self) -> bytes:
        if self.sock is None or self.request_timeout <= 0:
            return b""
        end = time.monotonic() + self.request_timeout
        data = bytearray()
        while time.monotonic() < end:
            self.sock.settimeout(max(0.01, end - time.monotonic()))
            try:
                chunk = self.sock.recv(1024)
            except socket.timeout:
                break
            if not chunk:
                raise ConnectionError("MiniToo closed RFCOMM connection")
            data.extend(chunk)
            if b"\x04\x8b" in data:
                break
        return bytes(data)

    def send_rgb888(self, rgb: bytes) -> None:
        payload = encode_still_rgb888(
            rgb,
            zstd_level=self.zstd_level,
            zstd_window_log=self.zstd_window_log,
        )
        transfer = build_transfer(payload)
        try:
            self.connect()
            assert self.sock is not None
            self.sock.sendall(transfer.start)
            try:
                self._recv_until_request_or_timeout()
            except socket.timeout:
                pass
            for packet in transfer.chunks:
                self.sock.sendall(packet)
                if self.packet_delay:
                    time.sleep(self.packet_delay)
        except Exception:
            self.last_failure = time.monotonic()
            self.close()
            raise
