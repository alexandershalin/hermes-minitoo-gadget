"""Divoom MiniToo RFCOMM framing and live 0x8B transfer helpers."""

from __future__ import annotations

from dataclasses import dataclass

LIVE_COMMAND = 0x8B
CHUNK_SIZE = 256
START_BYTE = 0x01
END_BYTE = 0x02


def frame(command: int, body: bytes = b"") -> bytes:
    if not 0 <= command <= 0xFF:
        raise ValueError("command must fit in one byte")
    if len(body) > 0xFFFF - 3:
        raise ValueError("body is too large for one MiniToo frame")
    out = bytearray(7 + len(body))
    out[0] = START_BYTE
    out[1:3] = (len(body) + 3).to_bytes(2, "little")
    out[3] = command
    out[4 : 4 + len(body)] = body
    checksum = sum(out[1:-3]) & 0xFFFF
    out[-3:-1] = checksum.to_bytes(2, "little")
    out[-1] = END_BYTE
    return bytes(out)


def valid_frame(raw: bytes) -> bool:
    if len(raw) < 7 or raw[0] != START_BYTE or raw[-1] != END_BYTE:
        return False
    declared = int.from_bytes(raw[1:3], "little")
    if declared < 3 or len(raw) != declared + 4:
        return False
    expected = int.from_bytes(raw[-3:-1], "little")
    return (sum(raw[1:-3]) & 0xFFFF) == expected


class FrameParser:
    """Incremental parser for coalesced or split RFCOMM packets."""

    def __init__(self) -> None:
        self.buffer = bytearray()

    def append(self, data: bytes) -> list[tuple[int, bytes]]:
        self.buffer.extend(data)
        packets: list[tuple[int, bytes]] = []
        while True:
            try:
                start = self.buffer.index(START_BYTE)
            except ValueError:
                self.buffer.clear()
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < 3:
                break
            declared = int.from_bytes(self.buffer[1:3], "little")
            if declared < 3:
                del self.buffer[0]
                continue
            total = declared + 4
            if len(self.buffer) < total:
                break
            candidate = bytes(self.buffer[:total])
            if candidate[-1] != END_BYTE:
                del self.buffer[0]
                continue
            del self.buffer[:total]
            if not valid_frame(candidate):
                continue
            packets.append((candidate[3], candidate[4:-3]))
        return packets


def is_live_ready(command: int, arguments: bytes) -> bool:
    """Ready response emitted after a 0x8B announce."""
    return command == 0x04 and arguments.startswith(b"\x8b\x55\x00\x01")


@dataclass(frozen=True)
class Transfer:
    start: bytes
    chunks: tuple[bytes, ...]


def build_transfer(payload: bytes, *, chunk_size: int = CHUNK_SIZE) -> Transfer:
    if not payload:
        raise ValueError("payload cannot be empty")
    if not 1 <= chunk_size <= 0xFFFF:
        raise ValueError("invalid chunk_size")
    total = len(payload)
    start = frame(LIVE_COMMAND, b"\x00" + total.to_bytes(4, "little"))
    chunks = []
    for seq, offset in enumerate(range(0, total, chunk_size)):
        if seq > 0xFFFF:
            raise ValueError("payload requires too many chunks")
        chunk = payload[offset : offset + chunk_size]
        body = (
            b"\x01"
            + total.to_bytes(4, "little")
            + seq.to_bytes(2, "little")
            + chunk
        )
        chunks.append(frame(LIVE_COMMAND, body))
    return Transfer(start=start, chunks=tuple(chunks))
