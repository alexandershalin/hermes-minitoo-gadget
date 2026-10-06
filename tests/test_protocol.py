from hermes_minitoo.protocol import build_transfer, frame, rgb565le_to_rgb888


def test_generic_frame_matches_reverse_engineered_sender_formula():
    payload_len = 11937
    packet = frame(0x8B, b"\x00" + payload_len.to_bytes(4, "little"))
    # The public PROTOCOL.md example currently shows 0x01ae here, but the
    # repository's actual validated sender computes a byte-sum checksum: 0x0162.
    assert packet.hex(" ") == "01 08 00 8b 00 a1 2e 00 00 62 01 02"


def test_rgb565_primary_colours():
    raw = b"\x00\xf8" + b"\xe0\x07" + b"\x1f\x00"
    assert rgb565le_to_rgb888(raw) == bytes((255, 0, 0, 0, 255, 0, 0, 0, 255))


def test_transfer_sequences_chunks():
    transfer = build_transfer(bytes(range(256)) + b"x")
    assert transfer.start[3] == 0x8B
    assert len(transfer.chunks) == 2
    assert transfer.chunks[0][4] == 0x01
    assert transfer.chunks[0][9:11] == b"\x00\x00"
    assert transfer.chunks[1][9:11] == b"\x01\x00"
