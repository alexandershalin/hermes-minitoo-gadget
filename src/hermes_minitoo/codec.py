"""Native-resolution lossless MiniToo frame codec.

The MiniToo live path accepts a 0x23 animation payload containing exact
160x128 RGB888 pixels compressed as a complete LZO1X stream.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from functools import lru_cache

WIDTH = 160
HEIGHT = 128
RGB888_BYTES = WIDTH * HEIGHT * 3
ANIMATION_MAGIC = 0x23
CELL_ROWS = 0x08
CELL_COLUMNS = 0x0A
LOSSLESS_ENCODING = 0x00


class LzoError(RuntimeError):
    pass


class _Lzo1x:
    """Minimal ctypes binding to the system liblzo2 runtime."""

    OK = 0

    def __init__(self) -> None:
        name = ctypes.util.find_library("lzo2")
        if not name:
            raise LzoError("liblzo2 is required; install the liblzo2-2 package")
        self.lib = ctypes.CDLL(name)
        size_t = ctypes.c_size_t
        self.lib.lzo1x_1_compress.argtypes = [
            ctypes.c_void_p, size_t, ctypes.c_void_p, ctypes.POINTER(size_t), ctypes.c_void_p
        ]
        self.lib.lzo1x_1_compress.restype = ctypes.c_int
        self.lib.lzo1x_decompress_safe.argtypes = [
            ctypes.c_void_p, size_t, ctypes.c_void_p, ctypes.POINTER(size_t), ctypes.c_void_p
        ]
        self.lib.lzo1x_decompress_safe.restype = ctypes.c_int

    def compress_verified(self, raw: bytes) -> bytes:
        # Standard LZO1X output bound used by miniLZO examples.
        bound = len(raw) + len(raw) // 16 + 64 + 3
        src = ctypes.create_string_buffer(raw, len(raw))
        dst = ctypes.create_string_buffer(bound)
        dst_len = ctypes.c_size_t(bound)

        # LZO1X_1 work memory is far below 1 MiB on supported architectures.
        # Over-allocating avoids depending on private compile-time typedef sizes.
        work = ctypes.create_string_buffer(1 << 20)
        rc = self.lib.lzo1x_1_compress(
            src, len(raw), dst, ctypes.byref(dst_len), work
        )
        if rc != self.OK:
            raise LzoError(f"lzo1x_1_compress failed with code {rc}")

        compressed = bytes(dst.raw[: dst_len.value])
        if len(compressed) < 4 or compressed[-3:] != b"\x11\x00\x00":
            raise LzoError("liblzo2 returned an incomplete LZO1X stream")

        restored = ctypes.create_string_buffer(len(raw))
        restored_len = ctypes.c_size_t(len(raw))
        compressed_buf = ctypes.create_string_buffer(compressed, len(compressed))
        rc = self.lib.lzo1x_decompress_safe(
            compressed_buf,
            len(compressed),
            restored,
            ctypes.byref(restored_len),
            None,
        )
        if rc != self.OK:
            raise LzoError(f"lzo1x_decompress_safe failed with code {rc}")
        if restored_len.value != len(raw) or bytes(restored.raw[: restored_len.value]) != raw:
            raise LzoError("lossless frame failed byte-for-byte LZO round-trip verification")
        return compressed


@lru_cache(maxsize=1)
def _lzo() -> _Lzo1x:
    return _Lzo1x()


def rgb565le_to_rgb888(raw: bytes) -> bytes:
    """Convert little-endian RGB565 pixels from Hermes Gadget to RGB888."""
    if len(raw) % 2:
        raise ValueError("RGB565 input must contain complete 16-bit pixels")
    out = bytearray((len(raw) // 2) * 3)
    j = 0
    for i in range(0, len(raw), 2):
        value = raw[i] | (raw[i + 1] << 8)
        r5 = (value >> 11) & 0x1F
        g6 = (value >> 5) & 0x3F
        b5 = value & 0x1F
        out[j] = (r5 << 3) | (r5 >> 2)
        out[j + 1] = (g6 << 2) | (g6 >> 4)
        out[j + 2] = (b5 << 3) | (b5 >> 2)
        j += 3
    return bytes(out)


def build_lossless_animation(rgb888: bytes, *, frame_delay_ms: int = 2500) -> bytes:
    """Build one native 160x128 MiniToo lossless live-animation payload."""
    if len(rgb888) != RGB888_BYTES:
        raise ValueError(
            f"MiniToo native frame must be exactly {WIDTH}x{HEIGHT} RGB888 "
            f"({RGB888_BYTES} bytes)"
        )
    if not 1 <= frame_delay_ms <= 0xFFFF:
        raise ValueError("frame_delay_ms must be between 1 and 65535")

    compressed = _lzo().compress_verified(rgb888)
    return (
        bytes((ANIMATION_MAGIC, 0x01))
        + frame_delay_ms.to_bytes(2, "big")
        + bytes((CELL_ROWS, CELL_COLUMNS, LOSSLESS_ENCODING))
        + len(compressed).to_bytes(4, "big")
        + compressed
    )
