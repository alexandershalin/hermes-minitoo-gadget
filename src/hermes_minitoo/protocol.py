"""Divoom MiniToo SPP image protocol.

The packet format is independently implemented from the publicly documented
reverse engineering notes in alvinunreal/divoom-minitoo-osx.
"""

from __future__ import annotations

from dataclasses import dataclass

IMAGE_COMMAND = 0x8B
IMAGE_WIDTH = 128
IMAGE_HEIGHT = 128
IMAGE_BYTES = IMAGE_WIDTH * IMAGE_HEIGHT * 3
CHUNK_SIZE = 256


def frame(command: int, body: bytes = b"") -> bytes:
    """Wrap a MiniToo command body in the generic 0x01...0x02 frame."""
    if not 0 <= command <= 0xFF:
        raise ValueError("command must fit in one byte")
    out = bytearray(7 + len(body))
    out[0] = 0x01
    out[1:3] = (len(out) - 4).to_bytes(2, "little")
    out[3] = command
    out[4 : 4 + len(body)] = body
    checksum = sum(out[1:-3]) & 0xFFFF
    out[-3:-1] = checksum.to_bytes(2, "little")
    out[-1] = 0x02
    return bytes(out)


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


def _compress_zstd(data: bytes, *, level: int, window_log: int) -> bytes:
    try:
        import zstandard as zstd
    except ImportError as exc:
        raise RuntimeError("install the zstandard package to encode MiniToo images") from exc

    params = zstd.ZstdCompressionParameters.from_level(level, window_log=window_log)
    return zstd.ZstdCompressor(compression_params=params).compress(data)


def encode_still_rgb888(
    rgb: bytes,
    *,
    speed_ms: int = 1000,
    zstd_level: int = 17,
    zstd_window_log: int = 17,
) -> bytes:
    """Build MiniToo's encoded one-frame 128x128 image payload."""
    if len(rgb) != IMAGE_BYTES:
        raise ValueError(f"MiniToo image must be exactly {IMAGE_WIDTH}x{IMAGE_HEIGHT} RGB888")
    if not 1 <= speed_ms <= 0xFFFF:
        raise ValueError("speed_ms must be between 1 and 65535")
    compressed = _compress_zstd(rgb, level=zstd_level, window_log=zstd_window_log)
    return (
        b"\x25\x01"
        + speed_ms.to_bytes(2, "big")
        + b"\x08\x08"
        + len(compressed).to_bytes(4, "big")
        + compressed
    )


@dataclass(frozen=True)
class Transfer:
    start: bytes
    chunks: tuple[bytes, ...]


def build_transfer(payload: bytes, *, chunk_size: int = CHUNK_SIZE) -> Transfer:
    """Build the image start packet and sequenced 0x8b data packets."""
    if not payload:
        raise ValueError("payload cannot be empty")
    if not 1 <= chunk_size <= 0xFFFF:
        raise ValueError("invalid chunk_size")
    total = len(payload)
    start = frame(IMAGE_COMMAND, b"\x00" + total.to_bytes(4, "little"))
    chunks = []
    for seq, offset in enumerate(range(0, total, chunk_size)):
        if seq > 0xFFFF:
            raise ValueError("payload requires too many chunks")
        body = (
            b"\x01"
            + total.to_bytes(4, "little")
            + seq.to_bytes(2, "little")
            + payload[offset : offset + chunk_size]
        )
        chunks.append(frame(IMAGE_COMMAND, body))
    return Transfer(start=start, chunks=tuple(chunks))
