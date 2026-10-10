"""Persistent Linux Bluetooth Classic RFCOMM transport for MiniToo."""

from __future__ import annotations

import logging
import socket
import threading
import time

from ...codec import build_lossless_animation
from ...protocol import FrameParser, build_transfer, is_live_ready

LOG = logging.getLogger(__name__)


def _open_rfcomm(address: str, channel: int, timeout: float) -> socket.socket:
    """Open an RFCOMM socket; fall back to ctypes when Python lacks AF_BLUETOOTH."""
    family = getattr(socket, "AF_BLUETOOTH", None)
    proto = getattr(socket, "BTPROTO_RFCOMM", None)
    if family is not None and proto is not None:
        sock = socket.socket(family, socket.SOCK_STREAM, proto)
        try:
            sock.settimeout(timeout)
            sock.connect((address, channel))
            return sock
        except Exception:
            sock.close()
            raise
    import ctypes
    import errno
    import os
    import select
    import struct

    raw = bytes(int(x, 16) for x in address.split(":"))[::-1]
    if len(raw) != 6:
        raise ValueError(f"invalid Bluetooth address {address!r}")
    libc = ctypes.CDLL(None, use_errno=True)
    fd = libc.socket(31, socket.SOCK_STREAM, 3)  # AF_BLUETOOTH, BTPROTO_RFCOMM
    if fd < 0:
        raise OSError(ctypes.get_errno(), "RFCOMM socket() failed")
    try:
        os.set_blocking(fd, False)
        addr = struct.pack("<H6sBx", 31, raw, channel)
        buf = ctypes.create_string_buffer(addr, len(addr))
        rc = libc.connect(fd, buf, len(addr))
        err = ctypes.get_errno() if rc < 0 else 0
        if rc < 0 and err != errno.EINPROGRESS:
            raise OSError(err, os.strerror(err))
        if rc < 0:
            _, w, _ = select.select([], [fd], [], timeout)
            if not w:
                raise TimeoutError("RFCOMM connect timed out")
            e = ctypes.c_int(0)
            ln = ctypes.c_uint(4)
            libc.getsockopt(fd, 1, 4, ctypes.byref(e), ctypes.byref(ln))  # SOL_SOCKET, SO_ERROR
            if e.value:
                raise OSError(e.value, os.strerror(e.value))
        sock = socket.socket(31, socket.SOCK_STREAM, 3, fileno=fd)
        sock.settimeout(timeout)
        return sock
    except Exception:
        try:
            os.close(fd)
        except OSError:
            pass
        raise


class RFCOMMTransport:
    def __init__(
        self,
        address: str,
        *,
        channel: int = 1,
        frame_delay_ms: int = 2500,
        chunk_delay_ms: int = 5,
        ready_timeout_ms: int = 8000,
        reconnect_delay_ms: int = 2000,
        max_payload_bytes: int = 600000,
    ) -> None:
        if not address or ":" not in address:
            raise ValueError("minitoo.address must be a Bluetooth MAC address")
        if not 1 <= channel <= 30:
            raise ValueError("minitoo.channel must be between 1 and 30")

        self.address = address
        self.channel = channel
        self.frame_delay_ms = frame_delay_ms
        self.chunk_delay = chunk_delay_ms / 1000.0
        self.ready_timeout = ready_timeout_ms / 1000.0
        self.reconnect_delay = reconnect_delay_ms / 1000.0
        self.max_payload_bytes = max_payload_bytes

        # The socket timeout outside the ready-ACK wait (connect and chunk writes).
        self.io_timeout = max(1.0, self.ready_timeout)

        self.sock: socket.socket | None = None
        self.parser = FrameParser()
        self.last_failure = float("-inf")  # not 0.0: monotonic() is small right after boot
        self._aborted = False
        self._abort_lock = threading.Lock()

    def connect(self) -> None:
        if self._aborted:
            raise ConnectionError("MiniToo transport is shutting down")
        if self.sock is not None:
            return
        now = time.monotonic()
        if now - self.last_failure < self.reconnect_delay:
            raise ConnectionError("MiniToo reconnect backoff is active")
        try:
            sock = _open_rfcomm(self.address, self.channel, self.io_timeout)
        except Exception:
            self.last_failure = time.monotonic()
            raise
        with self._abort_lock:
            if self._aborted:  # abort() ran while we were connecting
                sock.close()
                raise ConnectionError("MiniToo transport is shutting down")
            self.sock = sock
        self.parser = FrameParser()
        LOG.info("connected to MiniToo %s RFCOMM channel %d", self.address, self.channel)

    def abort(self) -> None:
        """Shutdown only: wake a sender blocked in recv/sendall and refuse new connects.

        Called from another thread; close() still has to be called afterwards.
        """
        with self._abort_lock:
            self._aborted = True
            sock = self.sock
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            finally:
                self.sock = None
                self.parser = FrameParser()

    def _wait_for_ready(self, timeout: float | None = None) -> None:
        sock = self.sock
        assert sock is not None
        started = time.monotonic()
        deadline = started + (self.ready_timeout if timeout is None else timeout)
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("MiniToo did not send the 0x8B ready ACK")
                sock.settimeout(remaining)
                try:
                    data = sock.recv(4096)
                except socket.timeout as exc:
                    raise TimeoutError("MiniToo did not send the 0x8B ready ACK") from exc
                if not data:
                    raise ConnectionError("MiniToo closed RFCOMM connection")
                for command, arguments in self.parser.append(data):
                    if is_live_ready(command, arguments):
                        elapsed_ms = (time.monotonic() - started) * 1000
                        LOG.log(logging.INFO if elapsed_ms > 1000 else logging.DEBUG,
                                "MiniToo ready ACK in %.0f ms", elapsed_ms)
                        return
        finally:
            # Chunk writes must not inherit what was left of the ACK deadline.
            sock.settimeout(self.io_timeout)

    def send_rgb888(self, rgb: bytes, *, ready_timeout: float | None = None) -> None:
        """Upload one frame. ready_timeout (seconds) overrides ready_timeout_ms once."""
        payload = build_lossless_animation(rgb, frame_delay_ms=self.frame_delay_ms)
        if len(payload) > self.max_payload_bytes:
            raise ValueError(
                f"MiniToo payload is {len(payload)} bytes, above safety limit "
                f"{self.max_payload_bytes}"
            )
        transfer = build_transfer(payload)

        # connect() сам выставляет last_failure при неудаче; ошибка «backoff active»
        # не должна продлевать паузу, иначе она никогда не кончится при повторах раз в секунду.
        self.connect()
        try:
            assert self.sock is not None

            self.sock.sendall(transfer.start)
            self._wait_for_ready(ready_timeout)

            for index, packet in enumerate(transfer.chunks):
                self.sock.sendall(packet)
                if self.chunk_delay and index + 1 < len(transfer.chunks):
                    time.sleep(self.chunk_delay)
        except Exception:
            self.last_failure = time.monotonic()
            self.close()
            raise
