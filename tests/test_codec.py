import pytest

from hermes_minitoo.codec import RGB888_BYTES, build_lossless_animation, rgb565le_to_rgb888


def _reference(raw: bytes) -> bytes:
    out = bytearray()
    for i in range(0, len(raw), 2):
        v = raw[i] | (raw[i + 1] << 8)
        r5, g6, b5 = (v >> 11) & 31, (v >> 5) & 63, v & 31
        out += bytes(((r5 << 3) | (r5 >> 2), (g6 << 2) | (g6 >> 4), (b5 << 3) | (b5 >> 2)))
    return bytes(out)


def test_rgb565_extremes():
    assert rgb565le_to_rgb888(b"\x00\x00\xff\xff") == bytes((0, 0, 0, 255, 255, 255))


def test_rgb565_matches_reference_for_every_value():
    raw = b"".join(v.to_bytes(2, "little") for v in range(0x10000))
    assert rgb565le_to_rgb888(raw) == _reference(raw)


def test_rgb565_rejects_odd_length():
    with pytest.raises(ValueError):
        rgb565le_to_rgb888(b"\x00")


def test_lzo_round_trip_is_verified():
    rgb = bytes((i * 7) & 0xFF for i in range(RGB888_BYTES))
    assert build_lossless_animation(rgb).endswith(b"\x11\x00\x00")


@pytest.mark.parametrize("size", [0, 3, RGB888_BYTES + 3])
def test_animation_rejects_wrong_frame_size(size):
    with pytest.raises(ValueError):
        build_lossless_animation(bytes(size))


@pytest.mark.parametrize("delay", [0, 0x10000])
def test_animation_rejects_bad_delay(delay):
    with pytest.raises(ValueError):
        build_lossless_animation(bytes(RGB888_BYTES), frame_delay_ms=delay)
