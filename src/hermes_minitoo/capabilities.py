"""Known MiniToo capabilities and research placeholders.

This registry intentionally separates features implemented here from capabilities
demonstrated or investigated by other MiniToo reverse-engineering projects.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class Capability:
    status: str
    summary: str
    reference: str


CAPABILITIES: dict[str, Capability] = {
    "display.compat_zstd_128": Capability(
        "implemented",
        "128x128 RGB888/Zstandard live image path used by the initial backend",
        "research/HACKS.md#31-current-project-path-128128-rgb888--zstandard",
    ),
    "display.native_lzo_160x128": Capability(
        "planned",
        "native 160x128 lossless RGB888/MiniLZO live path over 0x8B",
        "research/HACKS.md#32-native-160128-lossless-live-frames",
    ),
    "display.native_zstd_160x128": Capability(
        "research",
        "newer reports of native-resolution RGB/Zstd; format needs reconciliation",
        "research/HACKS.md#33-native-160128-rgbzstd-reports",
    ),
    "device.brightness": Capability(
        "planned",
        "JSON Channel/SetBrightness and binary opcode 0x32",
        "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.screen_power": Capability(
        "planned",
        "screen on/off while preserving current view",
        "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.volume": Capability(
        "research",
        "proprietary volume command exists; OS A2DP may remain preferred",
        "research/HACKS.md#5-brightness-screen-and-audio-control-commands",
    ),
    "device.tools": Capability(
        "research",
        "native stopwatch, scoreboard, noise meter and countdown views",
        "research/HACKS.md#6-built-in-tools",
    ),
    "device.games": Capability(
        "research",
        "built-in game/Tetris modes via opcode 0xA0",
        "research/HACKS.md#7-built-in-views-and-games",
    ),
    "custom_face.switch": Capability(
        "planned",
        "instant switch between already-installed faces by real ClockId",
        "research/HACKS.md#4-persistent-custom-faces-and-fast-state-switching",
    ),
    "custom_face.install": Capability(
        "research",
        "persistent custom-face installation/file workflow",
        "research/HACKS.md#4-persistent-custom-faces-and-fast-state-switching",
    ),
    "photo.album": Capability(
        "research",
        "create/manage local photo albums and local image files",
        "research/HACKS.md#8-photo-albums",
    ),
    "notification.text_icon": Capability(
        "planned",
        "short ANCS-style text notification with built-in icon",
        "research/HACKS.md#9-notifications",
    ),
    "firmware.metadata": Capability(
        "research",
        "read-only firmware acquisition/hash metadata; no binary redistribution",
        "research/FIRMWARE.md",
    ),
    "firmware.custom_code": Capability(
        "blocked",
        "FORTH code-cave work is simulation-proven but lacks practical physical flashing",
        "research/FIRMWARE.md#the-forth-code-cave-project",
    ),
    "firmware.dfu_jtag": Capability(
        "research",
        "hardware/DFU/JTAG paths remain unresolved and require independent verification",
        "research/FIRMWARE.md#candidate-deliverydebug-routes",
    ),
}


def capabilities_dict() -> dict[str, dict[str, str]]:
    return {name: asdict(cap) for name, cap in CAPABILITIES.items()}
