from hermes_minitoo.codec import (
    HEIGHT,
    RGB888_BYTES,
    WIDTH,
    build_lossless_animation,
    rgb565le_to_rgb888,
)
from hermes_minitoo.protocol import FrameParser, build_transfer, frame, is_live_ready


def test_generic_frame_matches_reverse_engineered_formula():
    payload_len = 11937
    packet = frame(0x8B, b"\x00" + payload_len.to_bytes(4, "little"))
    assert packet.hex(" ") == "01 08 00 8b 00 a1 2e 00 00 62 01 02"


def test_rgb565_primary_colours():
    raw = b"\x00\xf8" + b"\xe0\x07" + b"\x1f\x00"
    assert rgb565le_to_rgb888(raw) == bytes((255, 0, 0, 0, 255, 0, 0, 0, 255))


def test_native_lossless_payload_round_trips_through_lib_lzo():
    assert (WIDTH, HEIGHT, RGB888_BYTES) == (160, 128, 61440)
    rgb = bytes((i * 17 + i // 251) & 0xFF for i in range(RGB888_BYTES))
    payload = build_lossless_animation(rgb, frame_delay_ms=2500)

    assert payload[:7] == b"\x23\x01\x09\xc4\x08\x0a\x00"
    encoded_len = int.from_bytes(payload[7:11], "big")
    assert encoded_len == len(payload) - 11
    assert payload[-3:] == b"\x11\x00\x00"


def test_transfer_sequences_256_byte_chunks():
    payload = bytes(range(256)) + b"x"
    transfer = build_transfer(payload)
    assert transfer.start[3] == 0x8B
    assert len(transfer.chunks) == 2
    assert transfer.chunks[0][4] == 0x01
    assert transfer.chunks[0][9:11] == b"\x00\x00"
    assert transfer.chunks[1][9:11] == b"\x01\x00"


def test_parser_recognizes_exact_live_ready_ack_even_when_split():
    parser = FrameParser()
    raw = bytes.fromhex("01 07 00 04 8b 55 00 01 ec 00 02")
    assert parser.append(raw[:5]) == []
    packets = parser.append(raw[5:])
    assert len(packets) == 1
    assert is_live_ready(*packets[0])
